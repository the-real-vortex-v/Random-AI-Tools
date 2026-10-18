import os
import sys
import json
import time
import argparse
import subprocess
import lmstudio as lms

TEST_PROMPT = "Write a comprehensive, thread-safe matrix multiplication unit in Object Pascal for Lazarus."

def get_installed_models():
    """Queries the lms CLI to dynamically fetch a list of downloaded models."""
    models = []
    try:
        with open("lms_list.json", "w", encoding="utf-8") as f_out:
            subprocess.run(["cmd.exe", "/c", "lms ls --json"], stdout=f_out, stderr=subprocess.DEVNULL, check=True)
            
        with open("lms_list.json", "r", encoding="utf-8") as f_in:
            json_data = json.load(f_in)
            
        if isinstance(json_data, list):
            for item in json_data:
                if "path" in item: models.append(str(item["path"]).strip())
                elif "id" in item: models.append(str(item["id"]).strip())
                elif "name" in item: models.append(str(item["name"]).strip())
        elif isinstance(json_data, dict) and "models" in json_data:
            for item in json_data["models"]:
                if "path" in item: models.append(str(item["path"]).strip())
    except Exception:
        try:
            with open("lms_list.txt", "w", encoding="utf-8") as f_txt:
                subprocess.run(["cmd.exe", "/c", "lms ls"], stdout=f_txt, stderr=subprocess.DEVNULL)
            with open("lms_list.txt", "r", encoding="utf-8") as f_in:
                for line in f_in.splitlines():
                    clean_line = line.strip()
                    if clean_line and not any(clean_line.startswith(h) for h in ["NAME", "ID", "VERSION", "TYPE"]):
                        parts = clean_line.split()
                        if parts: models.append(parts)
        except Exception:
            pass
    finally:
        for temp_file in ["lms_list.json", "lms_list.txt"]:
            if os.path.exists(temp_file): 
                os.remove(temp_file)

    models = sorted(list(set([m for m in models if m])))
    if not models:
        return ["qwen2.5-coder-7b-instruct", "llama-3.1-8b-instruct"]
    return models

def generate_pascal_context(approx_tokens):
    """Generates synthetic Object Pascal unit framework to tax the KV Cache."""
    lines = ["unit ComplexMatrixMath;", "interface uses SysUtils, Classes, Math;", "implementation"]
    for i in range(max(1, approx_tokens // 40)):
        lines.append(f"procedure DummyNode{i}(var A: array of Double); begin end;")
    lines.append("end.")
    return "\n".join(lines)

def probe_configuration(model_key, gpu_ratio, context_size):
    """
    Natively initializes and benchmarks a specific hardware slice configuration.
    Bypasses shell command loading completely to eliminate background thread deadlocks.
    """
    model_key = str(model_key).strip()
    print(f"  [PROBE] Staging via SDK -> GPU Ratio: {gpu_ratio:.2f} | Context: {context_size} tokens")
    
    context_data = generate_pascal_context(context_size - 600)
    final_prompt = f"CONTEXT:\n```pascal\n{context_data}\n```\n\nPROMPT:\n{TEST_PROMPT}"
    tps = -1.0
    
    try:
        # Establish the explicit background network environment connection
        with lms.Client() as client:
            
            # Programmatically trigger the model load using explicit runtime parameters
            # Converts the ratio float to match the internal configuration expectations
            model = client.llm.load_new_instance(
                model_key,
                config={
                    "contextLength": context_size,
                    "gpuOffload": gpu_ratio,
                    "ttl": 60  # Retains a healthy lifespan safety threshold across iterations
                }
            )
            
            # Allow hardware registers to balance baseline states
            time.sleep(2)
            
            start_time = time.perf_counter()
            time_to_first_token = None
            output_buffer = ""

            stream = model.respond_stream(final_prompt, config={"temperature": 0.1})
            for fragment in stream:
                if time_to_first_token is None:
                    time_to_first_token = time.perf_counter() - start_time
                output_buffer += fragment.content
                
            total_duration = time.perf_counter() - start_time
            total_tokens = len(model.tokenize(output_buffer))
            
            gen_window = total_duration - time_to_first_token if time_to_first_token else 0
            tps = total_tokens / gen_window if gen_window > 0 else 0
            print(f"    -> [SUCCESS] Generation complete: {tps:.2f} tokens/sec")
            
            # Explicitly drop the instance via the SDK handle to free VRAM for subsequent sweeps
            print("    -> Purging engine weights natively...")
            model.unload()
            
    except Exception as e:
        print(f"    -> [CRASH] Hardware slice layout failed or hit an OOM barrier: {e}")
        tps = -1.0
        
    time.sleep(4)  # Cooling buffer gap to allow clean physical hardware memory collection
    return tps

def parse_cli_arguments():
    parser = argparse.ArgumentParser(description="LM Studio Automation Hardware Profile Benchmarker")
    parser.add_argument("--test-all-gpu", action="store_true", help="Sweep GPU ratios from 0.0 to 1.0 sequentially.")
    parser.add_argument("--test-all-context", action="store_true", help="Forces context range step iteration sweeps.")
    parser.add_argument("--min-gpu-ratio", type=float, default=0.0, help="Starting GPU offload ratio (0.0 to 1.0).")
    parser.add_argument("--max-gpu-ratio", type=float, default=1.0, help="Ending GPU offload ratio (0.0 to 1.0).")
    parser.add_argument("--min-context", type=int, default=2048)
    parser.add_argument("--max-context", type=int, default=8192)
    parser.add_argument("--context-step", type=int, default=2048)
    return parser.parse_args()

def execute_binary_search_gpu(args, model_key, context_size, results_accumulator):
    """Executes a binary search sweep targeting the optimal GPU offload decimal ratio."""
    low = int(args.min_gpu_ratio * 10)
    high = int(args.max_gpu_ratio * 10)
    optimal_ratio_int = low
    max_observed_speed = 0.0
    evaluation_cache = {}

    while low <= high:
        mid = (low + high) // 2
        gpu_ratio_val = mid / 10.0
        
        if mid in evaluation_cache: 
            break
            
        tps = probe_configuration(model_key, gpu_ratio_val, context_size)
        evaluation_cache[mid] = tps
        
        results_accumulator.append({
            "context": context_size, "gpu_ratio": gpu_ratio_val, 
            "tps": tps, "status": "OOM/CRASH" if tps == -1.0 else "SUCCESS"
        })
        
        if tps == -1.0:
            high = mid - 1
            continue
            
        if tps > max_observed_speed:
            max_observed_speed = tps
            optimal_ratio_int = mid
            
        prev_step = mid - 1
        if prev_step >= int(args.min_gpu_ratio * 10) and prev_step not in evaluation_cache:
            prev_ratio = prev_step / 10.0
            prev_tps = probe_configuration(model_key, prev_ratio, context_size)
            evaluation_cache[prev_step] = prev_tps
            results_accumulator.append({
                "context": context_size, "gpu_ratio": prev_ratio, 
                "tps": prev_tps, "status": "OOM/CRASH" if prev_tps == -1.0 else "SUCCESS"
            })
        else:
            prev_tps = evaluation_cache.get(mid - 1, 0.0)

        if tps > prev_tps: 
            low = mid + 1
        else: 
            high = mid - 1

def generate_yaml_fallback(data_dict):
    yaml_lines = ["# Automated Lazarus AI Profile Configuration"]
    for key, val in data_dict.items():
        if isinstance(val, dict):
            yaml_lines.append(f"{key}:")
            for sub_key, sub_val in val.items():
                yaml_lines.append(f"  {sub_key}: {sub_val}")
        else:
            yaml_lines.append(f"{key}: {val}")
    return "\n".join(yaml_lines)
if __name__ == "__main__":
    args = parse_cli_arguments()
    
    print(f"\n{'='*80}")
    print("                   LOCAL LM STUDIO MODEL SELECTION")
    print(f"{'='*80}")
    
    models = get_installed_models()
    for index, model_name in enumerate(models, start=1):
        print(f"  [{index}] {model_name}")
    print("=" * 80)
    
    selected_model = None
    while True:
        try:
            choice = input(f"Select a model number to benchmark (1-{len(models)}): ").strip()
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(models):
                selected_model = models[choice_idx]
                print(f"\n[SELECTED] Target Model set to: {selected_model}")
                break
            else:
                print(f"Invalid index. Please select a number between 1 and {len(models)}.")
        except ValueError:
            print("Please enter a valid integer number.")
            
    all_runs = []
    if args.test_all_context:
        context_space = list(range(args.min_context, args.max_context + 1, args.context_step))
    else:
        context_space = [args.min_context]
    
    print(f"\n{'#'*80}")
    print(f"INITIALIZING PERFORMANCE EVALUATION MATRIX FOR: {selected_model}")
    print(f"{'#'*80}")
    
    for context in context_space:
        if args.test_all_gpu:
            print(f"\n--- GRID SWEEP CYCLE: Context Window Size = {context} Tokens ---")
            for r_int in range(int(args.min_gpu_ratio * 10), int(args.max_gpu_ratio * 10) + 1):
                ratio = r_int / 10.0
                tps = probe_configuration(selected_model, ratio, context)
                
                all_runs.append({
                    "context": context, 
                    "gpu_ratio": ratio, 
                    "tps": tps, 
                    "status": "OOM/CRASH" if tps == -1.0 else "SUCCESS"
                })
        else:
            execute_binary_search_gpu(args, selected_model, context, all_runs)

    successful_runs = [r for r in all_runs if r["tps"] > 0]
    if not successful_runs:
        print(f"\n[ERROR] All tested layouts crashed or encountered an OOM status for {selected_model}.")
        sys.exit(1)
        
    optimal_config = max(successful_runs, key=lambda x: x["tps"])

    print(f"\n{'='*80}")
    print(f"                   EXPERIMENT RUN WORKSPACE REPORT ({selected_model})")
    print(f"{'='*80}")
    print(" Context Size   |  GPU Offload Ratio  |  Status    |  Speed (TPS)")
    print("-" * 80)
    for r in all_runs:
        speed_str = "N/A" if r["tps"] == -1.0 else f"{r['tps']:.2f}"
        print(f" {r['context']:<14} | {r['gpu_ratio']:<19} | {r['status']:<10} | {speed_str}")
    print("=" * 80)
    
    print(f"\n>>> OPTIMAL SETTING SUGGESTIONS FOR LAZARUS WORKSPACE <<<")
    print(f" -> Best Performance Achieved at Context: {optimal_config['context']} Tokens")
    print(f" -> Optimal GPU Offload Ratio Variable:  {optimal_config['gpu_ratio']}")
    print(f" -> Peak Generation Throughput Capacity:  {optimal_config['tps']:.2f} tokens/sec\n")

    export_data = {
        "model_identity": selected_model,
        "target_optimized_metrics": {
            "context_length": optimal_config["context"],
            "gpu_offload_ratio": optimal_config["gpu_ratio"],
            "peak_tokens_per_second": round(optimal_config["tps"], 2)
        }
    }

    with open("lazarus_ai_profile.json", "w") as f:
        json.dump(export_data, f, indent=2)
    with open("lazarus_ai_profile.yaml", "w") as f:
        f.write(generate_yaml_fallback(export_data))
        
    report_lines = [
        "================================================================================",
        "          Benchmark LOG REPORT",
        "================================================================================",
        f"Generated Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Evaluated Target Model: {selected_model}\n",
        "SUMMARY RECOMMENDATION:",
        "Based on extensive hardware performance mapping tests, the configuration below",
        "yields the highest possible text generation throughput while preventing VRAM overflow.",
        f"  * Recommended Context Buffer Allocation:  {optimal_config['context']} Tokens",
        f"  * Recommended GPU Offload Ratio Variable: {optimal_config['gpu_ratio']}",
        f"  * Anticipated Processing Efficiency:      {optimal_config['tps']:.2f} tokens/second\n",
        "FULL METRIC LOG WINDOW MATRIX:"
    ]
    for r in all_runs:
        speed_val = "FAILED/OOM" if r["tps"] == -1.0 else f"{r['tps']:.2f} tokens/sec"
        report_lines.append(f"  - Context: {r['context']:<6} | GPU Ratio: {r['gpu_ratio']:<4} -> {speed_val}")
        
    with open("lazarus_ai_profile.txt", "w") as f:
        f.write("\n".join(report_lines))
    print("Success: Performance configurations saved to JSON, YAML, and text report logs.")
