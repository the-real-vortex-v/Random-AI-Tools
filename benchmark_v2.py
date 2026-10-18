import os
import sys
import json
import time
import random
import tarfile
import gzip
import bz2
import argparse
import subprocess
import configparser
import lmstudio as lms

DEFAULT_INI_CONTENT = """[Benchmark Settings]
test_prompt = Write a comprehensive, thread-safe matrix multiplication unit in standard C or C++.
context_target_fill = 0.90
source_text_directory = ./benchmark_corpus.tar.gz
gpu_search_precision = 20
search_mode = binary
min_gpu_ratio = 0.0
max_gpu_ratio = 1.0
min_context = 2048
max_context = 8192
context_step = 2048
"""

def load_dynamic_settings():
    """
    Reads parameters fresh from the disk across loop iterations.
    Allows changing prompts, file targets, and fill depths without reloading the module.
    """
    ini_path = "llm_bench.ini"
    config = configparser.ConfigParser()
    if not os.path.exists(ini_path):
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_INI_CONTENT)
    config.read(ini_path, encoding="utf-8")
    return config["Benchmark Settings"]

# Initial baseline extraction for global parsing stability
ini_settings = load_dynamic_settings()

def extract_text_from_archive(path):
    """Extracts raw text content natively from directories, tarballs, gz, bz2, and 7z containers."""
    corpus_chunks = []
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in os.listdir(path) if f.endswith('.txt')]
        random.shuffle(files)
        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    corpus_chunks.append(f.read())
            except Exception: pass
        return "\n".join(corpus_chunks)

    path_lower = path.lower()
    if tarfile.is_tarfile(path) or any(path_lower.endswith(ext) for ext in ['.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz']):
        try:
            with tarfile.open(path, "r:*") as tar:
                members = [m for m in tar.getmembers() if m.isfile() and m.name.lower().endswith('.txt')]
                random.shuffle(members)
                for member in members:
                    f = tar.extractfile(member)
                    if f: corpus_chunks.append(f.read().decode("utf-8", errors="ignore"))
        except Exception as e: print(f"[ARCHIVE ERROR] Tar extraction failure: {e}")
    elif path_lower.endswith('.gz'):
        try:
            with gzip.open(path, 'rt', encoding='utf-8', errors='ignore') as f: corpus_chunks.append(f.read())
        except Exception as e: print(f"[ARCHIVE ERROR] Gzip extraction failure: {e}")
    elif path_lower.endswith('.bz2'):
        try:
            with bz2.open(path, 'rt', encoding='utf-8', errors='ignore') as f: corpus_chunks.append(f.read())
        except Exception as e: print(f"[ARCHIVE ERROR] Bzip2 extraction failure: {e}")
    elif path_lower.endswith('.7z'):
        try:
            use_shell = True if os.name == 'nt' else False
            binary = "7z" if os.name == 'nt' else "7za"
            list_cmd = [binary, "l", path]
            res = subprocess.run(list_cmd, capture_output=True, text=True, shell=use_shell)
            if res.returncode == 0:
                extract_cmd = [binary, "x", path, "-so", "*.txt"]
                res_ext = subprocess.run(extract_cmd, capture_output=True, text=True, shell=use_shell, errors='ignore')
                if res_ext.returncode == 0: corpus_chunks.append(res_ext.stdout)
        except Exception as e: print(f"[ARCHIVE ERROR] 7-Zip process extraction failure: {e}")
            
    return "\n".join(corpus_chunks)

def gather_context_filler(target_token_count, model_instance, source_path):
    """Loads text from source container and slices it to match the precise token threshold configuration."""
    corpus_text = extract_text_from_archive(source_path)
    if not corpus_text or len(corpus_text.strip()) == 0:
        return "Standard baseline processing fallback text block. Source data not resolved."
    tokens = model_instance.tokenize(corpus_text)
    if len(tokens) > target_token_count:
        return model_instance.detokenize(tokens[:target_token_count])
    return corpus_text

def probe_configuration(model_key, gpu_ratio, context_size, fill_ratio):
    """Evaluates prompt processing times alongside standard generation throughput parameters."""
    active_ini = load_dynamic_settings()
    source_target = active_ini.get("source_text_directory", "./benchmark_corpus")
    task_prompt = active_ini.get("test_prompt")

    model_key = str(model_key).strip()
    print(f"  [PROBE] Staging -> GPU Ratio: {gpu_ratio:.3f} | Window: {context_size} | Fill: {fill_ratio*100:.0f}%")
    tps_metrics = {"prefill_time": -1.0, "prefill_speed": -1.0, "generation_speed": -1.0, "ttft": -1.0}
    
    try:
        with lms.Client() as client:
            model = client.llm.load_new_instance(model_key, config={"contextLength": context_size, "gpu": {"ratio": gpu_ratio}})
            time.sleep(2)
            allocated_fill_tokens = int(context_size * fill_ratio) - 300
            if allocated_fill_tokens < 100: allocated_fill_tokens = 100
                
            context_data = gather_context_filler(allocated_fill_tokens, model, source_target)
            final_prompt = f"CONTEXT DATABASE:\n{context_data}\n\nTASK COMMAND:\n{task_prompt}"
            
            start_pipeline = time.perf_counter()
            time_to_first_token = None
            output_buffer = ""
            stream = model.respond_stream(final_prompt, config={"temperature": 0.1})
            for fragment in stream:
                if time_to_first_token is None: time_to_first_token = time.perf_counter() - start_pipeline
                output_buffer += fragment.content
                
            total_duration = time.perf_counter() - start_pipeline
            generated_tokens = len(model.tokenize(output_buffer))
            prompt_tokens = len(model.tokenize(final_prompt))
            
            tps_metrics["ttft"] = time_to_first_token if time_to_first_token else 0.0
            tps_metrics["prefill_time"] = tps_metrics["ttft"]
            tps_metrics["prefill_speed"] = prompt_tokens / tps_metrics["prefill_time"] if tps_metrics["prefill_time"] > 0 else 0
            gen_window = total_duration - tps_metrics["ttft"]
            tps_metrics["generation_speed"] = generated_tokens / gen_window if gen_window > 0 else 0
            
            print(f"    -> Prefill Speed: {tps_metrics['prefill_speed']:.1f} t/s | Gen Speed: {tps_metrics['generation_speed']:.1f} t/s | TTFT: {tps_metrics['ttft']:.2f}s")
            model.unload()
    except Exception as e:
        print(f"    -> [CRASH/OOM] Execution error: {e}")
        return None
    time.sleep(4)
    return tps_metrics

def get_installed_models():
    """Queries the lms CLI dynamically across cross-platform environments."""
    models = []
    cmd = ["lms", "ls", "--json"]
    use_shell = True if os.name == 'nt' else False
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=use_shell, check=True)
        json_data = json.loads(result.stdout)
        if isinstance(json_data, list):
            for item in json_data:
                if "path" in item: models.append(str(item["path"]).strip())
                elif "id" in item: models.append(str(item["id"]).strip())
        elif isinstance(json_data, dict) and "models" in json_data:
            for item in json_data["models"]:
                if "path" in item: models.append(str(item["path"]).strip())
    except Exception:
        try:
            result = subprocess.run(["lms", "ls"], capture_output=True, text=True, shell=use_shell)
            for line in result.stdout.splitlines():
                clean_line = line.strip()
                if clean_line and not any(clean_line.startswith(h) for h in ["NAME", "ID", "VERSION", "TYPE"]):
                    parts = clean_line.split()
                    if parts: models.append(parts)
        except Exception: pass
    return sorted(list(set([m for m in models if m])))

def parse_cli_arguments():
    parser = argparse.ArgumentParser(
        description="Cross-Platform Context Suite & Adaptive Search Matrix Profiler for LLMs.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""SEARCH MODE EXPLANATION:
================================================================================
By default, this tool performs an intelligent BINARY SEARCH along the GPU scale
to locate VRAM allocations efficiently.

Toggling Search Modes at Runtime:
  * To run an all-inclusive Brute Force Grid Sweep: Pass --sweep-gpu 
  * To fall back to optimization profiling: Leave the flag out.

You can modify 'test_prompt' and 'source_text_directory' directly in the INI file
mid-run between context switches without ever needing to restart or reload the script.
================================================================================"""
    )
    parser.add_argument("--sweep-gpu", action="store_true", help="Force sequentially looping every offload slice step (Brute Force Mode).")
    parser.add_argument("--context-fill-override", type=float, default=None, help="Override target context window fill value.")
    parser.add_argument("--gpu-precision-override", type=int, default=None, help="Adjust max search slices (e.g. 20, 50 steps).")
    parser.add_argument("--min-gpu-ratio", type=float, default=float(ini_settings.get("min_gpu_ratio", 0.0)))
    parser.add_argument("--max-gpu-ratio", type=float, default=float(ini_settings.get("max_gpu_ratio", 1.0)))
    parser.add_argument("--min-context", type=int, default=int(ini_settings.get("min_context", 2048)))
    parser.add_argument("--max-context", type=int, default=int(ini_settings.get("max_context", 8192)))
    parser.add_argument("--context-step", type=int, default=int(ini_settings.get("context_step", 2048)))
    return parser.parse_args()
def execute_binary_search_gpu(args, model_key, context_size, fill_ratio, precision_steps, results_accumulator):
    """Executes a high-resolution binary search sweep using dynamic precision stepping scales."""
    low = int(args.min_gpu_ratio * precision_steps)
    high = int(args.max_gpu_ratio * precision_steps)
    evaluation_cache = {}

    while low <= high:
        mid = (low + high) // 2
        gpu_ratio_val = round(mid / float(precision_steps), 3)
        if mid in evaluation_cache: break
            
        metrics = probe_configuration(model_key, gpu_ratio_val, context_size, fill_ratio)
        evaluation_cache[mid] = metrics
        
        if metrics is None:
            results_accumulator.append({"context": context_size, "gpu_ratio": gpu_ratio_val, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
            high = mid - 1
            continue
            
        results_accumulator.append({"context": context_size, "gpu_ratio": gpu_ratio_val, "gen_speed": metrics["generation_speed"], "ttft": metrics["ttft"], "status": "SUCCESS"})
        
        prev_step = mid - 1
        if prev_step >= int(args.min_gpu_ratio * precision_steps) and prev_step not in evaluation_cache:
            prev_ratio = round(prev_step / float(precision_steps), 3)
            prev_m = probe_configuration(model_key, prev_ratio, context_size, fill_ratio)
            evaluation_cache[prev_step] = prev_m
            if prev_m:
                results_accumulator.append({"context": context_size, "gpu_ratio": prev_ratio, "gen_speed": prev_m["generation_speed"], "ttft": prev_m["ttft"], "status": "SUCCESS"})
            else:
                results_accumulator.append({"context": context_size, "gpu_ratio": prev_ratio, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
        
        current_tps = metrics["generation_speed"]
        prev_tps = evaluation_cache.get(mid - 1, None)
        prev_tps_val = prev_tps["generation_speed"] if prev_tps else 0.0

        if current_tps > prev_tps_val: low = mid + 1
        else: high = mid - 1

def generate_yaml_fallback(data_dict):
    yaml_lines = ["# Automated LLM Profile Configuration"]
    for key, val in data_dict.items():
        if isinstance(val, dict):
            yaml_lines.append(f"{key}:")
            for sub_key, sub_val in val.items(): yaml_lines.append(f"  {sub_key}: {sub_val}")
        else: yaml_lines.append(f"{key}: {val}")
    return "\n".join(yaml_lines)

if __name__ == "__main__":
    args = parse_cli_arguments()
    
    # Establish base tracking structures using dynamic runtime queries
    fresh_settings = load_dynamic_settings()
    target_fill = args.context_fill_override if args.context_fill_override else float(fresh_settings.get("context_target_fill", 0.90))
    precision = args.gpu_precision_override if args.gpu_precision_override else int(fresh_settings.get("gpu_search_precision", 20))
    
    # Determine search mode checking priority between CLI switch overrides and INI definitions
    ini_mode = fresh_settings.get("search_mode", "binary").strip().lower()
    force_sweep = True if (args.sweep_gpu or ini_mode == "sweep") else False
    
    print(f"\n{'='*80}\n                   LOCAL LM STUDIO MODEL SELECTION\n{'='*80}")
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
                break
            else: print(f"Invalid index. Please select a number between 1 and {len(models)}.")
        except ValueError: print("Please enter a valid integer number.")
            
    all_runs = []
    context_space = list(range(args.min_context, args.max_context + 1, args.context_step))
    
    print(f"\n{'#'*80}")
    print(f"RUNNING CONTEXT TESTING LOOP (Active Mode: {'SWEEP/BRUTE-FORCE' if force_sweep else 'OPTIMIZED BINARY SEARCH'})")
    print(f"{'#'*80}")
    
    for context in context_space:
        # Check settings at the beginning of each context loop iteration
        fresh_settings = load_dynamic_settings()
        target_fill = args.context_fill_override if args.context_fill_override else float(fresh_settings.get("context_target_fill", 0.90))
        
        if force_sweep:
            print(f"\n--- GRID SWEEP CYCLE: Context Window Size = {context} Tokens ---")
            for r_int in range(int(args.min_gpu_ratio * precision), int(args.max_gpu_ratio * precision) + 1):
                ratio = round(r_int / float(precision), 3)
                m = probe_configuration(selected_model, ratio, context, target_fill)
                if m:
                    all_runs.append({"context": context, "gpu_ratio": ratio, "gen_speed": m["generation_speed"], "ttft": m["ttft"], "status": "SUCCESS"})
                else:
                    all_runs.append({"context": context, "gpu_ratio": ratio, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
        else:
            execute_binary_search_gpu(args, selected_model, context, target_fill, precision, all_runs)

    successful_runs = [r for r in all_runs if r["gen_speed"] > 0]
    if not successful_runs:
        print("\n[ERROR] All hardware layout sweeps hit an out-of-memory or timeout boundary block.")
        sys.exit(1)
        
    optimal_config = max(successful_runs, key=lambda x: x["gen_speed"])

    print(f"\n{'='*80}\n                   EXPERIMENT RUN WORKSPACE REPORT\n{'='*80}")
    print(" Context Size   |  GPU Offload Ratio  |  Status    |  TTFT (s)  |  Gen Speed (TPS)")
    print("-" * 80)
    for r in all_runs:
        speed_str = "N/A" if r["gen_speed"] == -1.0 else f"{r['gen_speed']:.2f}"
        ttft_str = "N/A" if r["ttft"] == -1.0 else f"{r['ttft']:.2f}s"
        print(f" {r['context']:<14} | {r['gpu_ratio']:<19} | {r['status']:<10} | {ttft_str:<10} | {speed_str}")
    print("=" * 80)
    
    export_data = {
        "model_identity": selected_model,
        "target_optimized_metrics": {
            "context_length": optimal_config["context"],
            "gpu_offload_ratio": optimal_config["gpu_ratio"],
            "peak_tokens_per_second": round(optimal_config["gen_speed"], 2),
            "expected_prompt_latency_ttft": f"{optimal_config['ttft']:.2f}s"
        }
    }

    with open("llm_ai_profile.json", "w") as f: json.dump(export_data, f, indent=2)
    with open("llm_ai_profile.yaml", "w") as f: f.write(generate_yaml_fallback(export_data))
    print("Success: Configurations saved safely to your localized LLM data profiles.")
