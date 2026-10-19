### This Version should support Linux/Mac OS/BSD/Windows.
# Part 1

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
import requests

DEFAULT_INI_CONTENT = """[Benchmark Settings]
backend_provider = lmstudio
model_name = 
test_prompt = Write a comprehensive, thread-safe matrix multiplication unit in standard C or C++.
context_target_fill = 0.90
source_text_directory = ./benchmark_corpus.tar.gz
gpu_search_precision = 20
search_mode = binary
min_gpu_layers = 0
max_gpu_layers = 64
min_cpu_threads = 4
max_cpu_threads = 12
cpu_thread_step = 4
moe_active_experts = 2
min_agent_slots = 1
max_agent_slots = 4
min_context = 2048
max_context = 8192
context_step = 2048
"""

def load_dynamic_settings():
    """Reads configuration choices fresh from the local disk architecture."""
    ini_path = "llm_bench.ini"
    config = configparser.ConfigParser()
    if not os.path.exists(ini_path):
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_INI_CONTENT)
    config.read(ini_path, encoding="utf-8")
    return config["Benchmark Settings"]

def save_model_to_ini(model_key, provider):
    """Natively records the selected model path string and backend server as defaults."""
    ini_path = "llm_bench.ini"
    config = configparser.ConfigParser()
    config.read(ini_path, encoding="utf-8")
    if "Benchmark Settings" not in config.sections():
        config.add_section("Benchmark Settings")
    config["Benchmark Settings"]["model_name"] = str(model_key).strip()
    config["Benchmark Settings"]["backend_provider"] = str(provider).strip().lower()
    with open(ini_path, "w", encoding="utf-8") as f:
        config.write(f)
    print(f"[INI UPDATE] Saved model '{model_key}' with target backend '{provider}'.")

ini_settings = load_dynamic_settings()

def extract_text_from_archive(path):
    """Extracts raw text content natively from folders, tarballs, gz, bz2, and 7z archives."""
    corpus_chunks = []
    if not os.path.exists(path):
        return ""
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

# Part 2
def generate_synthetic_context(target_token_count, source_path):
    """Generates context string data using local files, falling back to static blocks to prevent token deadlocks."""
    raw_text = extract_text_from_archive(path=source_path)
    if raw_text and len(raw_text.strip()) > 100:
        max_chars = target_token_count * 4
        return raw_text[:max_chars]
        
    base_phrase = "Cross-platform hardware benchmarking matrix suite optimized for local runtime environments. "
    repetitions = (target_token_count * 4) // len(base_phrase)
    return base_phrase * max(1, repetitions)

def resolve_active_lmstudio_port():
    """Queries the backend dynamically to track down active running LM Studio local engine ports."""
    use_shell = True if os.name == 'nt' else False
    try:
        res = subprocess.run(["lms", "ps", "--json"], capture_output=True, text=True, shell=use_shell)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            for item in data:
                if item.get("identifier") == "bench-model" or "bench-model" in item.get("instanceReference", ""):
                    return item.get("port", 1234)
    except Exception: pass
    return 1234

def probe_configuration(provider, model_key, gpu_layers, max_layers_bound, cpu_threads, moe_experts, agent_slots, context_size, fill_ratio, cli_source_override=None):
    """Orchestrates hardware layer parameters and profiles streaming throughput cleanly across server types."""
    active_ini = load_dynamic_settings()
    source_target = cli_source_override if cli_source_override else active_ini.get("source_text_directory", "./benchmark_corpus")
    task_prompt = active_ini.get("test_prompt")
    provider = str(provider).strip().lower()

    model_key = str(model_key).strip()
    layers_display = "ALL" if gpu_layers == -1 else f"{gpu_layers}/{max_layers_bound}"
    print(f"  [PROBE] Target Backend: {provider.upper()} -> Layers: {layers_display} | CPU Threads: {cpu_threads} | Agent Slots: {agent_slots} | Window: {context_size}")
    tps_metrics = {"prefill_time": -1.0, "prefill_speed": -1.0, "generation_speed": -1.0, "ttft": -1.0}
    
    use_shell = True if os.name == 'nt' else False
    
    try:
        # Pre-build context strings before triggering memory switches
        allocated_fill_tokens = int(context_size * fill_ratio) - 300
        if allocated_fill_tokens < 100: allocated_fill_tokens = 100
        context_data = generate_synthetic_context(allocated_fill_tokens, source_target)
        final_prompt = f"CONTEXT DATABASE:\n{context_data}\n\nTASK COMMAND:\n{task_prompt}"

        # Calculate offload floats
        if gpu_layers == -1 or max_layers_bound <= 0:
            gpu_ratio_arg = 1.0
        else:
            gpu_ratio_arg = round(max(0.0, min(1.0, gpu_layers / float(max_layers_bound))), 3)

        # ENGINE BRANCH 1: LM STUDIO HOOK ARCHITECTURE
        if provider == "lmstudio":
            try:
                subprocess.run(["lms", "unload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell)
                time.sleep(3)
            except Exception: pass

            load_cmd = [
                "lms", "load", model_key, "--identifier", "bench-model", 
                "--gpu", str(gpu_ratio_arg), "--thread-count", str(cpu_threads), "--context-length", str(context_size)
            ]
            subprocess.run(load_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell, check=True)
            time.sleep(5)
            
            target_url = f"http://127.0.0.1:{resolve_active_lmstudio_port()}/v1/chat/completions"
            payload = {
                "model": "bench-model", "messages": [{"role": "user", "content": final_prompt}], "temperature": 0.1, "stream": True
            }

        # ENGINE BRANCH 2: OLLAMA SERVER HOOK ARCHITECTURE
        else:
            target_url = "http://127.0.0"
            # Ollama takes native model layouts and runs parameters directly into the body layer array
            payload = {
                "model": model_key,
                "messages": [{"role": "user", "content": final_prompt}],
                "stream": True,
                "options": {
                    "num_ctx": context_size,
                    "num_predict": 512,
                    "num_thread": cpu_threads,
                    "num_gpu": gpu_layers if gpu_layers != -1 else 999,
                    "num_experts": moe_experts,
                    "temperature": 0.1
                }
            }

        # UNIFIED STREAM INFERENCE DRIVER PIPELINE
        start_pipeline = time.perf_counter()
        time_to_first_token = None
        total_generated_tokens = 0
        
        response = requests.post(target_url, json=payload, stream=True, timeout=120)
        
        if response.status_code == 200:
            for chunk in response.iter_lines():
                if chunk:
                    if time_to_first_token is None:
                        time_to_first_token = time.perf_counter() - start_pipeline
                    
                    chunk_str = chunk.decode("utf-8").strip()
                    # Unified character delta parsing
                    if "content" in chunk_str or '"message"' in chunk_str:
                        total_generated_tokens += 1
                        
            total_duration = time.perf_counter() - start_pipeline
            # Approximate baseline prompt tokens safely for output reporting metrics
            prompt_tokens = max(100, int(len(final_prompt) / 4))
            
            tps_metrics["ttft"] = time_to_first_token if time_to_first_token else 0.0
            tps_metrics["prefill_time"] = tps_metrics["ttft"]
            tps_metrics["prefill_speed"] = prompt_tokens / tps_metrics["prefill_time"] if tps_metrics["prefill_time"] > 0 else 0
            gen_window = total_duration - tps_metrics["ttft"]
            tps_metrics["generation_speed"] = (total_generated_tokens / 2) / gen_window if gen_window > 0 else 0
            
            print(f"    -> Prefill Speed: {tps_metrics['prefill_speed']:.1f} t/s | Gen Speed: {tps_metrics['generation_speed']:.1f} t/s | TTFT: {tps_metrics['ttft']:.2f}s")
        else:
            raise ConnectionError(f"Target server returned a bad status code response field: {response.status_code}")
        
    except Exception as e:
        print(f"    -> [CRASH/OOM] Backend configuration run failed: {e}")
        tps_metrics = None
        
    finally:
        if provider == "lmstudio":
            try: subprocess.run(["lms", "unload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell)
            except Exception: pass
        time.sleep(4)
            
    time.sleep(2)
    return tps_metrics

# Part 3

def parse_cli_arguments():
    """Parses command-line arguments for multi-backend hardware testing configurations."""
    parser = argparse.ArgumentParser(description="Cross-Platform Multi-Backend (LM Studio / Ollama) MoE Thread Profiler.")
    parser.add_argument("-b", "--backend", type=str, default=None, choices=["lmstudio", "ollama"], help="Target provider backend choice to run profiles against.")
    parser.add_argument("-m", "--model", type=str, default=None, help="Target model identifier string specifier name on disk.")
    parser.add_argument("-s", "--source", type=str, default=None, help="Path to text folder or container file to use for context filling.")
    parser.add_argument("-t", "--threads", type=int, default=None, help="Force override CPU thread worker constraints parameters.")
    parser.add_argument("--sweep-gpu", action="store_true", help="Force sequentially sweeping layer splits step by step.")
    parser.add_argument("--context-fill-override", type=float, default=None, help="Override target context window fill value fraction.")
    parser.add_argument("--min-layers", type=int, default=None)
    parser.add_argument("--max-layers", type=int, default=None)
    parser.add_argument("--min-context", type=int, default=None)
    parser.add_argument("--max-context", type=int, default=None)
    parser.add_argument("--context-step", type=int, default=None)
    return parser.parse_args()

def execute_binary_search_layers(args, provider, model_key, max_layers_bound, cpu_threads, moe_experts, slots, context_size, fill_ratio, min_l, max_l, results_accumulator):
    """Executes a binary search sweep targeting optimal hardware layers offload bounds mapping matrices."""
    low = min_l
    high = max_l if max_l != -1 else 64
    evaluation_cache = {}

    while low <= high:
        mid = (low + high) // 2
        if mid in evaluation_cache: break
            
        metrics = probe_configuration(provider, model_key, mid, max_layers_bound, cpu_threads, moe_experts, slots, context_size, fill_ratio, cli_source_override=args.source)
        evaluation_cache[mid] = metrics
        
        if metrics is None:
            results_accumulator.append({"context": context_size, "gpu_layers": mid, "threads": cpu_threads, "moe_experts": moe_experts, "slots": slots, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
            high = mid - 1
            continue
            
        results_accumulator.append({"context": context_size, "gpu_layers": mid, "threads": cpu_threads, "moe_experts": moe_experts, "slots": slots, "gen_speed": metrics["generation_speed"], "ttft": metrics["ttft"], "status": "SUCCESS"})
        
        current_span = high - low
        step_reduction = max(1, int(current_span * 0.25))
        slope_step = mid - step_reduction
        
        if slope_step >= min_l and slope_step not in evaluation_cache:
            prev_m = probe_configuration(provider, model_key, slope_step, max_layers_bound, cpu_threads, moe_experts, slots, context_size, fill_ratio, cli_source_override=args.source)
            evaluation_cache[slope_step] = prev_m
            if prev_m:
                results_accumulator.append({"context": context_size, "gpu_layers": slope_step, "threads": cpu_threads, "moe_experts": moe_experts, "slots": slots, "gen_speed": prev_m["generation_speed"], "ttft": prev_m["ttft"], "status": "SUCCESS"})
            else:
                results_accumulator.append({"context": context_size, "gpu_layers": slope_step, "threads": cpu_threads, "moe_experts": moe_experts, "slots": slots, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
        
        current_tps = metrics["generation_speed"]
        prev_tps_obj = evaluation_cache.get(slope_step, None)
        prev_tps_val = prev_tps_obj["generation_speed"] if prev_tps_obj else 0.0

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
    
    active_backend = args.backend if args.backend else fresh_settings.get("backend_provider", "lmstudio").strip().lower()
    target_fill = args.context_fill_override if args.context_fill_override is not None else float(fresh_settings.get("context_target_fill", 0.90))
    min_layers = args.min_layers if args.min_layers is not None else int(fresh_settings.get("min_gpu_layers", 0))
    max_layers = args.max_layers if args.max_layers is not None else int(fresh_settings.get("max_gpu_layers", 64))
    moe_active = int(fresh_settings.get("moe_active_experts", 2))
    
    min_cores = int(fresh_settings.get("min_cpu_threads", 4))
    max_cores = int(fresh_settings.get("max_cpu_threads", 8))
    core_step = int(fresh_settings.get("cpu_thread_step", 4))
    if args.threads is not None:
        min_cores = max_cores = args.threads
        core_step = 1
        
    min_agents = int(fresh_settings.get("min_agent_slots", 1))
    max_agents = int(fresh_settings.get("max_agent_slots", 2))
    
    force_sweep_gpu = True if args.sweep_gpu else False
    
    selected_model = None
    if args.model:
        selected_model = args.model
        print(f"\n[CLI OVERRIDE] Target forced via switch identifier: {selected_model}")
    else:
        ini_model = fresh_settings.get("model_name", "").strip()
        if ini_model:
            selected_model = ini_model
            print(f"\n[INI AUTO-LOAD] Loaded template configuration model: {selected_model}")
        else:
            print("\n" + "="*80)
            print("                   LOCAL MULTI-BACKEND MODEL WORKSPACE")
            print("="*80)
            print(f"Active Backend Provider Selected Target: {active_backend.upper()}")
            selected_model = input("Enter model identifier name (e.g. qwen2.5:7b or qwen/qwen3.8-27b): ").strip()
            while not selected_model:
                selected_model = input("Identifier entry cannot remain empty: ").strip()
            save_choice = input("Save this configuration default in llm_bench.ini? (y/n): ").strip().lower()
            if save_choice in ['y', 'yes']: save_model_to_ini(selected_model, active_backend)
            
    all_runs = []
    c_start = args.min_context if args.min_context is not None else int(fresh_settings.get("min_context", 2048))
    c_end = args.max_context if args.max_context is not None else int(fresh_settings.get("max_context", 8192))
    c_step = args.context_step if args.context_step is not None else int(fresh_settings.get("context_step", 2048))
    context_space = list(range(c_start, c_end + 1, c_step))
    
    print(f"\n{'#'*80}\nRUNNING HIERARCHICAL CORES AND LAYERS SERVER PROFILE ENGINE\n{'#'*80}")
    
    for context in context_space:
        for agents in range(min_agents, max_agents + 1):
            for threads in range(min_cores, max_cores + 1, core_step):
                print(f"\n--- ROUTE MATRIX: Context={context} | Threads={threads} | Server={active_backend.upper()} ---")
                
                if force_sweep_gpu and max_layers != -1:
                    for layer in range(min_layers, max_layers + 1):
                        m = probe_configuration(active_backend, selected_model, layer, max_layers, threads, moe_active, agents, context, target_fill, cli_source_override=args.source)
                        if m: all_runs.append({"context": context, "gpu_layers": layer, "threads": threads, "moe_experts": moe_active, "slots": agents, "gen_speed": m["generation_speed"], "ttft": m["ttft"], "status": "SUCCESS"})
                        else: all_runs.append({"context": context, "gpu_layers": layer, "threads": threads, "moe_experts": moe_active, "slots": agents, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
                else:
                    execute_binary_search_layers(args, active_backend, selected_model, max_layers, threads, moe_active, agents, context, target_fill, min_layers, max_layers, all_runs)

    successful_runs = [r for r in all_runs if r["gen_speed"] > 0]
    if not successful_runs:
        print("\n[ERROR] All hardware configurations hit a boundary failure.")
        sys.exit(1)
        
    optimal_config = max(successful_runs, key=lambda x: x["gen_speed"])

    print(f"\n{'='*80}\n                   EXPERIMENT RUN WORKSPACE REPORT\n{'='*80}")
    print(" Context Size | GPU Layers Loaded | CPU Cores Engaged | Agent Slots | TTFT (s) | Gen Speed (TPS)")
    print("-" * 80)
    for r in all_runs:
        speed_str = "N/A" if r["gen_speed"] == -1.0 else f"{r['gen_speed']:.2f}"
        ttft_str = "N/A" if r["ttft"] == -1.0 else f"{r['ttft']:.2f}s"
        layer_str = "ALL" if r["gpu_layers"] == -1 else f"{r['gpu_layers']}/{max_layers}"
        print(f" {r['context']:<12} | {layer_str:<18} | {r['threads']:<17} | {r['slots']:<11} | {ttft_str:<8} | {speed_str}")
    print("=" * 80)
    
    export_data = {
        "model_identity": selected_model,
        "backend_provider": active_backend,
        "target_optimized_metrics": {
            "context_length": optimal_config["context"],
            "gpu_layers_offloaded": optimal_config["gpu_layers"],
            "allocated_cpu_threads": optimal_config["threads"],
            "moe_active_experts": optimal_config["moe_experts"],
            "simulated_agent_slots": optimal_config["slots"],
            "peak_tokens_per_second": round(optimal_config["gen_speed"], 2),
            "expected_prompt_latency_ttft": f"{optimal_config['ttft']:.2f}s"
        }
    }
	with open("llm_ai_profile.json", "w") as f: json.dump(export_data, f, indent=2)
	with open("llm_ai_profile.yaml", "w") as f: f.write(generate_yaml_fallback(export_data))
	print("Success: Multi-backend optimization matrices packaged and saved safely.")
