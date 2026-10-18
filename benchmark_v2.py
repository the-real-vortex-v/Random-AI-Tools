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
import requests  # Added for unified HTTP streaming
import lmstudio as lms

DEFAULT_INI_CONTENT = """[Benchmark Settings]
model_name = 
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
    """Reads parameters fresh from the disk across loop iterations."""
    ini_path = "llm_bench.ini"
    config = configparser.ConfigParser()
    if not os.path.exists(ini_path):
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_INI_CONTENT)
    config.read(ini_path, encoding="utf-8")
    return config["Benchmark Settings"]

def save_model_to_ini(model_key):
    """Natively writes a selected model name back to the INI file default configuration slot."""
    ini_path = "llm_bench.ini"
    config = configparser.ConfigParser()
    config.read(ini_path, encoding="utf-8")
    if "Benchmark Settings" not in config.sections():
        config.add_section("Benchmark Settings")
    config["Benchmark Settings"]["model_name"] = str(model_key).strip()
    with open(ini_path, "w", encoding="utf-8") as f:
        config.write(f)
    print(f"[INI UPDATE] Successfully saved '{model_key}' as default model name.")

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

def gather_context_filler_preloaded(target_token_count, global_client, model_key, source_path):
    """Loads text data and tokenizes it using a static model metadata hook before mounting the weights."""
    corpus_text = extract_text_from_archive(source_path)
    if not corpus_text or len(corpus_text.strip()) == 0:
        return "Standard baseline processing fallback text block. Source data not resolved."
    try:
        meta_model = global_client.llm.model(model_key)
        tokens = meta_model.tokenize(corpus_text)
        if len(tokens) > target_token_count:
            return meta_model.detokenize(tokens[:target_token_count])
    except Exception: pass
    return corpus_text

def resolve_active_bench_port():
    """
    Queries the lms engine dynamically to locate the exact active HTTP port assignments.
    Prevents port mismatch issues caused by dynamic port shifting during sweeps.
    """
    use_shell = True if os.name == 'nt' else False
    try:
        res = subprocess.run(["lms", "ps", "--json"], capture_output=True, text=True, shell=use_shell)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            for item in data:
                if item.get("identifier") == "bench-model" or "bench-model" in item.get("instanceReference", ""):
                    return item.get("port", 1234)
    except Exception:
        pass
    return 1234  # Default fallback port

def probe_configuration(global_client, model_key, gpu_ratio, context_size, fill_ratio, cli_source_override=None):
    """Evaluates prompt processing times using explicit dynamic port binding lookup arrays."""
    active_ini = load_dynamic_settings()
    source_target = cli_source_override if cli_source_override else active_ini.get("source_text_directory", "./benchmark_corpus")
    task_prompt = active_ini.get("test_prompt")

    model_key = str(model_key).strip()
    print(f"  [PROBE] Staging -> GPU Ratio: {gpu_ratio:.3f} | Window: {context_size} | Fill: {fill_ratio*100:.0f}%")
    tps_metrics = {"prefill_time": -1.0, "prefill_speed": -1.0, "generation_speed": -1.0, "ttft": -1.0}
    
    use_shell = True if os.name == 'nt' else False
    log_filename = "lsm_load_exec.log"
    
    try:
        allocated_fill_tokens = int(context_size * fill_ratio) - 300
        if allocated_fill_tokens < 100: allocated_fill_tokens = 100
        context_data = gather_context_filler_preloaded(allocated_fill_tokens, global_client, model_key, source_target)
        final_prompt = f"CONTEXT DATABASE:\n{context_data}\n\nTASK COMMAND:\n{task_prompt}"

        # Load the model via the CLI to handle the large 27B structure safely
        load_cmd = ["lms", "load", model_key, "--identifier", "bench-model", "--gpu", str(gpu_ratio), "--context-length", str(context_size)]
        with open(log_filename, "w", encoding="utf-8") as f_log:
            subprocess.run(load_cmd, stdout=f_log, stderr=f_log, shell=use_shell, check=True)
            
        time.sleep(5)

        # Dynamically resolve the active port assignment from the log file matrix
        active_port = resolve_active_bench_port()
        target_url = f"http://127.0.0.1:{active_port}/v1/chat/completions"
        
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": "bench-model",
            "messages": [{"role": "user", "content": final_prompt}],
            "temperature": 0.1,
            "stream": True
        }
        
        start_pipeline = time.perf_counter()
        time_to_first_token = None
        total_generated_characters = 0
        
        # Route streaming requests directly over HTTP to bypass SDK socket locks completely
        response = requests.post(target_url, headers=headers, json=payload, stream=True, timeout=120)
        
        if response.status_code == 200:
            for chunk in response.iter_lines():
                if chunk:
                    current_marker = time.perf_counter()
                    if time_to_first_token is None:
                        time_to_first_token = current_marker - start_pipeline
                    
                    chunk_str = chunk.decode("utf-8").strip()
                    if chunk_str.startswith("data: ") and not chunk_str.endswith("[DONE]"):
                        try:
                            chunk_json = json.loads(chunk_str[6:])
                            content = chunk_json["choices"]["delta"].get("content", "")
                            total_generated_characters += len(content)
                        except Exception: pass
                        
            total_duration = time.perf_counter() - start_pipeline
            # Estimate tokens safely based on character generation counts
            generated_tokens = max(1, total_generated_characters // 4)
            
            # Use metadata hook for stable prompt token counting without running instance errors
            meta_model = global_client.llm.model(model_key)
            prompt_tokens = len(meta_model.tokenize(final_prompt))
            
            tps_metrics["ttft"] = time_to_first_token if time_to_first_token else 0.0
            tps_metrics["prefill_time"] = tps_metrics["ttft"]
            tps_metrics["prefill_speed"] = prompt_tokens / tps_metrics["prefill_time"] if tps_metrics["prefill_time"] > 0 else 0
            gen_window = total_duration - tps_metrics["ttft"]
            tps_metrics["generation_speed"] = generated_tokens / gen_window if gen_window > 0 else 0
            
            print(f"    -> Prefill Speed: {tps_metrics['prefill_speed']:.1f} t/s | Gen Speed: {tps_metrics['generation_speed']:.1f} t/s | TTFT: {tps_metrics['ttft']:.2f}s")
        else:
            raise ConnectionError(f"Engine responded with error code: {response.status_code}")
        
    except Exception as e:
        print(f"    -> [CRASH/OOM] Execution error: {e}")
        if os.path.exists(log_filename):
            try:
                with open(log_filename, "r", encoding="utf-8", errors="ignore") as f_err:
                    log_content = f_err.read().strip()
                    if log_content:
                        print(f"    -> [DEBUG LOG TRACE]: {log_content.splitlines()[-1] if log_content.splitlines() else log_content}")
            except Exception: pass
        tps_metrics = None
        
    finally:
        if os.path.exists(log_filename):
            try: os.remove(log_filename)
            except Exception: pass
            
        try:
            print("    -> Natively purging engine weights out of host memory slots...")
            subprocess.run(["lms", "unload", "bench-model"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell)
            time.sleep(5)
        except Exception as ex:
            print(f"    -> [WARNING] Native model drop invocation hit a roadblock: {ex}")
            
    time.sleep(3)
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
    """Parses command-line arguments for hardware testing configurations."""
    parser = argparse.ArgumentParser(description="Cross-Platform Context Suite & Adaptive Search Matrix Profiler for LLMs.")
    parser.add_argument("-m", "--model", type=str, default=None, help="Explicit target model identifier key (Overrides INI settings).")
    parser.add_argument("-s", "--source", type=str, default=None, help="Path to text folder or container file to use for context filling.")
    parser.add_argument("--sweep-gpu", action="store_true", help="Force sequentially looping every offload slice step (Brute Force Mode).")
    parser.add_argument("--context-fill-override", type=float, default=None, help="Override target context window fill value.")
    parser.add_argument("--gpu-precision-override", type=int, default=None, help="Adjust max search slices (e.g. 20, 50 steps).")
    parser.add_argument("--min-gpu-ratio", type=float, default=0.0)
    parser.add_argument("--max-gpu-ratio", type=float, default=1.0)
    parser.add_argument("--min-context", type=int, default=2048)
    parser.add_argument("--max-context", type=int, default=8192)
    parser.add_argument("--context-step", type=int, default=2048)
    return parser.parse_args()

def execute_binary_search_gpu(args, global_client, model_key, context_size, fill_ratio, precision_steps, results_accumulator):
    """Executes a high-resolution binary search sweep using dynamic precision stepping scales."""
    low = int(args.min_gpu_ratio * precision_steps)
    high = int(args.max_gpu_ratio * precision_steps)
    evaluation_cache = {}

    while low <= high:
        mid = (low + high) // 2
        gpu_ratio_val = round(mid / float(precision_steps), 3)
        if mid in evaluation_cache: break
            
        metrics = probe_configuration(global_client, model_key, gpu_ratio_val, context_size, fill_ratio, cli_source_override=args.source)
        evaluation_cache[mid] = metrics
        
        if metrics is None:
            results_accumulator.append({"context": context_size, "gpu_ratio": gpu_ratio_val, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
            high = mid - 1
            continue
            
        results_accumulator.append({"context": context_size, "gpu_ratio": gpu_ratio_val, "gen_speed": metrics["generation_speed"], "ttft": metrics["ttft"], "status": "SUCCESS"})
        
        prev_step = mid - 1
        if prev_step >= int(args.min_gpu_ratio * precision_steps) and prev_step not in evaluation_cache:
            prev_ratio = round(prev_step / float(precision_steps), 3)
            prev_m = probe_configuration(global_client, model_key, prev_ratio, context_size, fill_ratio, cli_source_override=args.source)
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
    
    fresh_settings = load_dynamic_settings()
    target_fill = args.context_fill_override if args.context_fill_override else float(fresh_settings.get("context_target_fill", 0.90))
    precision = args.gpu_precision_override if args.gpu_precision_override else int(fresh_settings.get("gpu_search_precision", 20))
    
    ini_mode = fresh_settings.get("search_mode", "binary").strip().lower()
    force_sweep = True if (args.sweep_gpu or ini_mode == "sweep") else False
    
    selected_model = None
    if args.model:
        selected_model = args.model
        print(f"\n[CLI OVERRIDE] Target Model forced via parameter switch: {selected_model}")
    else:
        ini_model = fresh_settings.get("model_name", "").strip()
        if ini_model:
            selected_model = ini_model
            print(f"\n[INI AUTO-LOAD] Loaded default configured template model: {selected_model}")
        else:
            print(f"\n{'='*80}\n                   LOCAL LM STUDIO MODEL SELECTION\n{'='*80}")
            models = get_installed_models()
            for index, model_name in enumerate(models, start=1):
                print(f"  [{index}] {model_name}")
            print("=" * 80)
            
            while True:
                try:
                    choice = input(f"Select a model number to benchmark (1-{len(models)}): ").strip()
                    choice_idx = int(choice) - 1
                    if 0 <= choice_idx < len(models):
                        selected_model = models[choice_idx]
                        print(f"\n[SELECTED] Target Model set to: {selected_model}")
                        save_choice = input("Save this model as default in llm_bench.ini? (y/n): ").strip().lower()
                        if save_choice in ['y', 'yes']:
                            save_model_to_ini(selected_model)
                        break
                    else: print(f"Invalid index. Please select a number between 1 and {len(models)}.")
                except ValueError: print("Please enter a valid integer number.")
            
    all_runs = []
    context_space = list(range(args.min_context, args.max_context + 1, args.context_step))
    
    print(f"\n{'#'*80}")
    print(f"RUNNING CONTEXT TESTING LOOP (Target Model: {selected_model})")
    print(f"{'#'*80}")
    
    global_client = lms.Client()
    
    try:
        for context in context_space:
            fresh_settings = load_dynamic_settings()
            target_fill = args.context_fill_override if args.context_fill_override else float(fresh_settings.get("context_target_fill", 0.90))
            
            if force_sweep:
                print(f"\n--- GRID SWEEP CYCLE: Context Window Size = {context} Tokens ---")
                for r_int in range(int(args.min_gpu_ratio * precision), int(args.max_gpu_ratio * precision) + 1):
                    ratio = round(r_int / float(precision), 3)
                    m = probe_configuration(global_client, selected_model, ratio, context, target_fill, cli_source_override=args.source)
                    if m:
                        all_runs.append({"context": context, "gpu_ratio": ratio, "gen_speed": m["generation_speed"], "ttft": m["ttft"], "status": "SUCCESS"})
                    else:
                        all_runs.append({"context": context, "gpu_ratio": ratio, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
            else:
                execute_binary_search_gpu(args, global_client, selected_model, context, target_fill, precision, all_runs)
    finally:
        try: global_client.close()
        except Exception: pass

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
    print("Success: Configurations saved safely.")
