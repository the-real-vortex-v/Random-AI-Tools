### Part 1

import os
import sys
import json
import time
import random
import tarfile
import gzip
import bz2
import argparse
import configparser
import subprocess
import lmstudio as lms

DEFAULT_INI_CONTENT = """[Benchmark Settings]
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

def save_model_to_ini(model_key):
    """Natively records the selected model path string as the default INI target choice."""
    ini_path = "llm_bench.ini"
    config = configparser.ConfigParser()
    config.read(ini_path, encoding="utf-8")
    if "Benchmark Settings" not in config.sections():
        config.add_section("Benchmark Settings")
    config["Benchmark Settings"]["model_name"] = str(model_key).strip()
    with open(ini_path, "w", encoding="utf-8") as f:
        config.write(f)
    print(f"[INI UPDATE] Successfully saved '{model_key}' as default model path.")

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

### Part 2

def generate_synthetic_context(target_token_count, source_path):
    """Generates context string data using local files, falling back to static blocks to prevent token deadlocks."""
    raw_text = extract_text_from_archive(source_path)
    if raw_text and len(raw_text.strip()) > 100:
        max_chars = target_token_count * 4
        return raw_text[:max_chars]
        
    base_phrase = "LM Studio provides optimized local inference acceleration matrix tools. "
    repetitions = (target_token_count * 4) // len(base_phrase)
    return base_phrase * max(1, repetitions)

def probe_configuration(model_key, gpu_layers, max_layers_bound, cpu_threads, moe_experts, context_size, fill_ratio, cli_source_override=None):
    """Evaluates text processing speed by launching model parameters directly inside native lms.llm constructor structures."""
    active_ini = load_dynamic_settings()
    source_target = cli_source_override if cli_source_override else active_ini.get("source_text_directory", "./benchmark_corpus")
    task_prompt = active_ini.get("test_prompt")

    model_key = str(model_key).strip()
    layers_display = "ALL" if gpu_layers == -1 else f"{gpu_layers}/{max_layers_bound}"
    print(f"  [PROBE] Staging via SDK -> Layers: {layers_display} | CPU Threads: {cpu_threads} | MoE Experts: {moe_experts} | Window: {context_size}")
    tps_metrics = {"prefill_time": -1.0, "prefill_speed": -1.0, "generation_speed": -1.0, "ttft": -1.0}
    model = None
    
    use_shell = True if os.name == 'nt' else False
    
    try:
        # FIX: Proactively sweep out any existing model footprints by calling raw "lms unload" before initializing the loop segment
        try:
            print("    -> Issuing global 'lms unload' to clear out all memory registers...")
            subprocess.run(["lms", "unload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell)
            time.sleep(4)  # Short stabilization settle window
        except Exception:
            pass

        if gpu_layers == -1 or max_layers_bound <= 0:
            gpu_ratio_arg = 1.0
        else:
            gpu_ratio_arg = round(max(0.0, min(1.0, gpu_layers / float(max_layers_bound))), 3)

        allocated_fill_tokens = int(context_size * fill_ratio) - 300
        if allocated_fill_tokens < 100: allocated_fill_tokens = 100
        context_data = generate_synthetic_context(allocated_fill_tokens, source_target)
        final_prompt = f"CONTEXT DATABASE:\n{context_data}\n\nTASK COMMAND:\n{task_prompt}"

        # Initialize the model directly inside lms.llm mapping core threads, experts, and offload ratios natively
        model = lms.llm(
            model_key,
            config={
                "contextLength": context_size,
                "flashAttention": True,
                "evalBatchSize": 512,
                "threads": cpu_threads,
                "numExperts": moe_experts,
                "gpu": {
                    "ratio": gpu_ratio_arg
                }
            }
        )
        time.sleep(5)
        
        start_pipeline = time.perf_counter()
        time_to_first_token = None
        output_buffer = ""
        
        stream = model.respond_stream(final_prompt, config={"temperature": 0.1})
        for fragment in stream:
            if time_to_first_token is None:
                time_to_first_token = time.perf_counter() - start_pipeline
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
        
    except Exception as e:
        print(f"    -> [CRASH/OOM] Execution error: {e}")
        tps_metrics = None
        
    finally:
        # Object resource deletion forces immediate memory clearance cleanly
        if model is not None:
            try:
                del model
            except Exception: pass
            
        # FIX: Ensure a final clean memory sweep at the end of the test block pass
        try:
            print("    -> Finalizing cleanup via global 'lms unload' command...")
            subprocess.run(["lms", "unload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell)
            time.sleep(5)  # Cooldown window to let massive layers completely sweep out of RAM/VRAM
        except Exception: pass
            
    time.sleep(3)
    return tps_metrics

def parse_cli_arguments():
    """Parses command-line arguments for hardware testing configurations."""
    parser = argparse.ArgumentParser(description="Cross-Platform Layer-Stepping MoE Thread Profiler for LLMs.")
    parser.add_argument("-m", "--model", type=str, default=None, help="Explicit target model identifier path string.")
    parser.add_argument("-s", "--source", type=str, default=None, help="Path to text folder or container file.")
    parser.add_argument("-t", "--threads", type=int, default=None, help="Force override active CPU core worker limits parameter mapping.")
    parser.add_argument("--sweep-gpu", action="store_true", help="Force sequentially sweeping layer splits step by step.")
    parser.add_argument("--context-fill-override", type=float, default=None, help="Override target context fill percentage.")
    parser.add_argument("--min-layers", type=int, default=None, help="Starting offloaded GPU layer search boundary.")
    parser.add_argument("--max-layers", type=int, default=None, help="Maximum total layer constraint of model architecture.")
    parser.add_argument("--min-context", type=int, default=None)
    parser.add_argument("--max-context", type=int, default=None)
    parser.add_argument("--context-step", type=int, default=None)
    return parser.parse_args()

### Part 3

def execute_binary_search_layers(args, model_key, max_layers_bound, cpu_threads, moe_experts, context_size, fill_ratio, min_l, max_l, results_accumulator):
    """Executes a binary search sweep using a 20%-33% macro-reduction step to robustly evaluate performance."""
    low = min_l
    high = max_l if max_l != -1 else 64
    evaluation_cache = {}

    while low <= high:
        mid = (low + high) // 2
        if mid in evaluation_cache: break
            
        metrics = probe_configuration(model_key, mid, max_layers_bound, cpu_threads, moe_experts, context_size, fill_ratio, cli_source_override=args.source)
        evaluation_cache[mid] = metrics
        
        if metrics is None:
            results_accumulator.append({"context": context_size, "gpu_layers": mid, "threads": cpu_threads, "moe_experts": moe_experts, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
            high = mid - 1
            continue
            
        results_accumulator.append({"context": context_size, "gpu_layers": mid, "threads": cpu_threads, "moe_experts": moe_experts, "gen_speed": metrics["generation_speed"], "ttft": metrics["ttft"], "status": "SUCCESS"})
        
        current_span = high - low
        step_reduction = max(1, int(current_span * 0.25))
        slope_step = mid - step_reduction
        
        if slope_step >= min_l and slope_step not in evaluation_cache:
            print(f"  [SLOPE EVAL] Testing comparative macro-reduction drop target: {slope_step}/{max_layers_bound} layers")
            prev_m = probe_configuration(model_key, slope_step, max_layers_bound, cpu_threads, moe_experts, context_size, fill_ratio, cli_source_override=args.source)
            evaluation_cache[slope_step] = prev_m
            if prev_m:
                results_accumulator.append({"context": context_size, "gpu_layers": slope_step, "threads": cpu_threads, "moe_experts": moe_experts, "gen_speed": prev_m["generation_speed"], "ttft": prev_m["ttft"], "status": "SUCCESS"})
            else:
                results_accumulator.append({"context": context_size, "gpu_layers": slope_step, "threads": cpu_threads, "moe_experts": moe_experts, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
        
        current_tps = metrics["generation_speed"]
        prev_tps_obj = evaluation_cache.get(slope_step, None)
        prev_tps_val = prev_tps_obj["generation_speed"] if prev_tps_obj else 0.0

        if current_tps > prev_tps_val: 
            low = mid + 1
        else: 
            high = mid - 1

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
        
    ini_mode = fresh_settings.get("search_mode", "binary").strip().lower()
    force_sweep = True if (args.sweep_gpu or ini_mode == "sweep") else False
    
    selected_model = None
    if args.model:
        selected_model = args.model
        print(f"\n[CLI OVERRIDE] Model target path identifier locked to: {selected_model}")
    else:
        ini_model = fresh_settings.get("model_name", "").strip()
        if ini_model:
            selected_model = ini_model
            print(f"\n[INI AUTO-LOAD] Loaded template configuration model: {selected_model}")
        else:
            print("\n" + "="*80)
            print("                   LOCAL LLM STUDIO MODEL ENTRY WORKSPACE")
            print("="*80)
            selected_model = input("Enter your target model identifier string (e.g. qwen2.5-7b-instruct): ").strip()
            while not selected_model:
                selected_model = input("Path cannot be empty. Please type the model key: ").strip()
                
            save_choice = input("Save this model path string as default in llm_bench.ini? (y/n): ").strip().lower()
            if save_choice in ['y', 'yes']:
                save_model_to_ini(selected_model)
            
    all_runs = []
    c_start = args.min_context if args.min_context is not None else int(fresh_settings.get("min_context", 2048))
    c_end = args.max_context if args.max_context is not None else int(fresh_settings.get("max_context", 8192))
    c_step = args.context_step if args.context_step is not None else int(fresh_settings.get("context_step", 2048))
    context_space = list(range(c_start, c_end + 1, c_step))
    
    print(f"\n{'#'*80}\nRUNNING HIERARCHICAL CORE LAYER PROFILING MATRIX\n{'#'*80}")
    
    for context in context_space:
        for threads in range(min_cores, max_cores + 1, core_step):
            print(f"\n--- MATRIX ROUTE: Context Window Size={context} | Compute Threads={threads} | MoE Active Experts={moe_active} ---")
            
            if force_sweep and max_layers != -1:
                for layer in range(min_layers, max_layers + 1):
                    m = probe_configuration(selected_model, layer, max_layers, threads, moe_active, context, target_fill, cli_source_override=args.source)
                    if m: all_runs.append({"context": context, "gpu_layers": layer, "threads": threads, "moe_experts": moe_active, "gen_speed": m["generation_speed"], "ttft": m["ttft"], "status": "SUCCESS"})
                    else: all_runs.append({"context": context, "gpu_layers": layer, "threads": threads, "moe_experts": moe_active, "gen_speed": -1.0, "ttft": -1.0, "status": "OOM/CRASH"})
            else:
                if max_layers == -1 and force_sweep:
                    m = probe_configuration(selected_model, -1, max_layers, threads, moe_active, context, target_fill, cli_source_override=args.source)
                    if m: all_runs.append({"context": context, "gpu_layers": -1, "threads": threads, "moe_experts": moe_active, "gen_speed": m["generation_speed"], "ttft": m["ttft"], "status": "SUCCESS"})
                else:
                    execute_binary_search_layers(args, selected_model, max_layers, threads, moe_active, context, target_fill, min_layers, max_layers, all_runs)

    successful_runs = [r for r in all_runs if r["gen_speed"] > 0]
    if not successful_runs:
        print("\n[ERROR] All hardware configurations hit a boundary failure.")
        sys.exit(1)
        
    optimal_config = max(successful_runs, key=lambda x: x["gen_speed"])

    print(f"\n{'='*80}\n                   EXPERIMENT RUN WORKSPACE REPORT\n{'='*80}")
    print(" Context Size | GPU Layers Offloaded | CPU Cores Engaged | Active Experts | TTFT (s) | Gen Speed (TPS)")
    print("-" * 80)
    for r in all_runs:
        speed_str = "N/A" if r["gen_speed"] == -1.0 else f"{r['gen_speed']:.2f}"
        ttft_str = "N/A" if r["ttft"] == -1.0 else f"{r['ttft']:.2f}s"
        layer_str = "ALL" if r["gpu_layers"] == -1 else f"{r['gpu_layers']}/{max_layers}"
        print(f" {r['context']:<12} | {layer_str:<20} | {r['threads']:<17} | {r['moe_experts']:<14} | {ttft_str:<8} | {speed_str}")
    print("=" * 80)
    
    export_data = {
        "model_identity": selected_model,
        "target_optimized_metrics": {
            "context_length": optimal_config["context"],
            "gpu_layers_offloaded": optimal_config["gpu_layers"],
            "allocated_cpu_threads": optimal_config["threads"],
            "moe_active_experts": optimal_config["moe_experts"],
            "peak_tokens_per_second": round(optimal_config["gen_speed"], 2),
            "expected_prompt_latency_ttft": f"{optimal_config['ttft']:.2f}s"
        }
    }
    with open("llm_ai_profile.json", "w") as f: json.dump(export_data, f, indent=2)
    with open("llm_ai_profile.yaml", "w") as f: f.write(generate_yaml_fallback(export_data))
    print("Success: Performance data captured and written safely.")
