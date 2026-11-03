import os
import sys
import shutil
import argparse
import tempfile
import zipfile
import http.server
import socketserver
import subprocess
import types
import warnings
import urllib.request
from urllib.parse import urlparse, urljoin, unquote
import ssl
import threading
import hashlib
import re
import json
import time

print("[System] Booting RipWeb Enterprise...", flush=True)
warnings.filterwarnings("ignore", category=FutureWarning)

if os.name == 'nt':
    os.system('')

RED = "\033[91m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"
CLEAR_SCREEN = "\033[2J\033[H"

if 'cgi' not in sys.modules:
    import email.message
    cgi_mod = types.ModuleType("cgi")
    def parse_header(line):
        msg = email.message.Message()
        msg['content-type'] = line
        params = msg.get_params()
        if not params: return '', {}
        return params[0][0], dict(params[1:])
    cgi_mod.parse_header = parse_header
    sys.modules["cgi"] = cgi_mod

def ensure_module(module_name, pip_name=None):
    pip_name = pip_name or module_name
    try:
        __import__(module_name)
    except ImportError:
        print(f"\n{RED}[!] Missing required module: '{module_name}'{RESET}", flush=True)
        choice = input(f"{GREEN}Install '{pip_name}' now using pip? (Y/n): {RESET}").strip().lower()
        if choice in ['', 'y', 'yes']:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
        else:
            sys.exit(1)

ensure_module('py7zr')
import py7zr

def get_single_keypress():
    """Reads a single keypress instantly without requiring Enter across Windows, Linux, and macOS."""
    if os.name == 'nt':
        import msvcrt
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                try:
                    return ch.decode('utf-8', errors='ignore')
                except Exception:
                    return ''
            time.sleep(0.05)
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            return ch
        except Exception:
            return ''
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def format_size(size_bytes):
    if size_bytes is None or size_bytes < 0:
        return "Unknown"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"

def format_speed(bytes_per_sec):
    if bytes_per_sec <= 0:
        return "0 B/s"
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.2f} MB/s"

def smart_truncate(text, max_len=75):
    if len(text) <= max_len:
        return text
    lead = max_len // 2 - 2
    tail = max_len - lead - 3
    return text[:lead] + "..." + text[-tail:]

def safe_join(base_dir, req_path):
    safe_path = os.path.abspath(os.path.join(base_dir, req_path.lstrip('/\\')))
    if not safe_path.startswith(os.path.abspath(base_dir)):
        raise ValueError("Directory traversal attempt detected")
    return safe_path

def atomic_save(tmp_path, final_path):
    try:
        os.replace(tmp_path, final_path)
    except OSError:
        try:
            if os.path.exists(final_path):
                try: os.remove(final_path)
                except OSError: pass
            os.rename(tmp_path, final_path)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except OSError: pass

def get_directory_stats(directory):
    total_files = 0
    total_bytes = 0
    for root, dirs, files in os.walk(directory):
        for file in files:
            full_path = os.path.join(root, file)
            if os.path.isfile(full_path):
                total_files += 1
                total_bytes += os.path.getsize(full_path)
    return total_files, total_bytes

def unpack_archive_file(archive_path, extract_dir):
    if not os.path.exists(archive_path):
        return
    print(f"{CYAN}[i] Unpacking {os.path.basename(archive_path)}...{RESET}", flush=True)
    if archive_path.lower().endswith('.7z'):
        with py7zr.SevenZipFile(archive_path, mode='r') as archive:
            archive.extractall(path=extract_dir)
    else:
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

def pack_archive_file(archive_path, source_dir):
    file_count, byte_size = get_directory_stats(source_dir)
    print(f"\n{CYAN}[i] Preparing to compress cache into archive...{RESET}")
    print(f"{CYAN}    - Files to archive : {GREEN}{file_count}{RESET}")
    print(f"{CYAN}    - Total uncompressed: {GREEN}{format_size(byte_size)}{RESET}")
    print(f"{CYAN}[i] Packing cache directory into staging archive...{RESET}", flush=True)
    
    staging_path = archive_path + ".tmp"
    
    if archive_path.lower().endswith('.7z'):
        with py7zr.SevenZipFile(staging_path, mode='w') as archive:
            for root, dirs, files in os.walk(source_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, source_dir)
                    arcname = rel_path.replace(os.sep, '/')
                    archive.write(full_path, arcname)
    else:
        with zipfile.ZipFile(staging_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for root, dirs, files in os.walk(source_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, source_dir)
                    arcname = rel_path.replace(os.sep, '/')
                    zip_ref.write(full_path, arcname=arcname)
    
    if os.path.exists(archive_path):
        os.remove(archive_path)
    os.rename(staging_path, archive_path)
    print(f"{GREEN}[✔] Archive successfully updated!{RESET}", flush=True)

def generate_directory_index_html(serve_dir):
    file_links = []
    for root, dirs, files in os.walk(serve_dir):
        for f in sorted(files):
            if f in ['index.html', 'site_loader.html']: continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, serve_dir).replace('\\', '/')
            file_links.append(rel)
            
    links_html = "\n".join([f'<li><a href="{l}" target="content_frame">{l}</a></li>' for l in file_links])
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>RipWeb Portable Browser Index</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; display: flex; height: 100vh; background: #121212; color: #eee; }}
        #sidebar {{ width: 320px; background: #1e1e1e; border-right: 1px solid #333; display: flex; flex-direction: column; }}
        #header {{ padding: 15px; background: #252526; border-bottom: 1px solid #333; }}
        #header h2 {{ font-size: 15px; color: #60a5fa; }}
        #search {{ width: 100%; padding: 8px 10px; margin-top: 10px; background: #2d2d2d; border: 1px solid #444; color: #fff; border-radius: 4px; }}
        #file-list {{ flex: 1; overflow-y: auto; padding: 10px; list-style: none; }}
        #file-list li {{ margin-bottom: 4px; }}
        #file-list a {{ color: #93c5fd; text-decoration: none; font-size: 12px; word-break: break-all; display: block; padding: 4px 6px; border-radius: 3px; }}
        #file-list a:hover {{ background: #2a2d3e; color: #fff; }}
        #main {{ flex: 1; height: 100vh; background: #fff; }}
        iframe {{ width: 100%; height: 100%; border: none; }}
    </style>
</head>
<body>
    <div id="sidebar">
        <div id="header">
            <h2>🌐 RipWeb Portable Archive</h2>
            <input type="text" id="search" placeholder="Filter files..." oninput="filterFiles()">
        </div>
        <ul id="file-list">
            {links_html}
        </ul>
    </div>
    <div id="main">
        <iframe name="content_frame" id="content_frame" src="about:blank"></iframe>
    </div>
    <script>
        function filterFiles() {{
            const query = document.getElementById('search').value.toLowerCase();
            document.querySelectorAll('#file-list li').forEach(li => {{
                li.style.display = li.textContent.toLowerCase().includes(query) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>
"""

def generate_browser_jar_loader_html(archive_basename):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>RipWeb Portable Browser-Native Loader</title>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #111827; color: #f3f4f6; margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; }}
        .card {{ background: #1f2937; padding: 30px; border-radius: 12px; border: 1px solid #374151; max-width: 500px; width: 100%; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        h1 {{ font-size: 20px; color: #38bdf8; margin-bottom: 10px; }}
        p {{ font-size: 13px; color: #9ca3af; margin-bottom: 20px; line-height: 1.5; }}
        .btn {{ display: inline-block; background: #0284c7; color: white; padding: 10px 20px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 13px; cursor: pointer; border: none; transition: background 0.2s; }}
        .btn:hover {{ background: #0369a1; }}
        #file-input {{ display: none; }}
        #status {{ margin-top: 15px; font-size: 12px; color: #fbbf24; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🌐 RipWeb Client-Side Loader</h1>
        <p>This web viewer unzips and renders the site directly inside your browser using client-side JavaScript memory management. Works in Firefox, Chrome, Pale Moon, Safari, and Edge.</p>
        <button class="btn" onclick="document.getElementById('file-input').click()">Load Archive File ({archive_basename})</button>
        <input type="file" id="file-input" accept=".zip,.jar" onchange="handleFileSelect(event)">
        <div id="status">Select your downloaded archive to begin viewing.</div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
    <script>
        async function handleFileSelect(evt) {{
            const file = evt.target.files[0];
            if (!file) return;
            const status = document.getElementById('status');
            status.innerText = "Extracting archive into browser RAM...";
            
            try {{
                const zip = await JSZip.loadAsync(file);
                status.innerText = "Building virtual site map...";
                
                let entryHtml = null;
                zip.forEach((relPath, fileEntry) => {{
                    if (!entryHtml && relPath.endsWith('index.html')) {{
                        entryHtml = fileEntry;
                    }}
                }});

                if (!entryHtml) {{
                    zip.forEach((relPath, fileEntry) => {{
                        if (!entryHtml && relPath.endsWith('.html')) {{
                            entryHtml = fileEntry;
                        }}
                    }});
                }}

                if (!entryHtml) {{
                    status.innerText = "Error: No HTML starting page found in archive.";
                    return;
                }}

                const content = await entryHtml.async("blob");
                const blobUrl = URL.createObjectURL(new Blob([content], {{type: 'text/html'}}));
                status.innerText = "Launching website...";
                window.location.href = blobUrl;
            }} catch (err) {{
                status.innerText = "Error loading archive: " + err.message;
            }}
        }}
    </script>
</body>
</html>
"""

def create_portable_package(serve_dir, output_filename, mode="directory"):
    print(f"\n{CYAN}[i] Creating portable browser package (Mode: {mode})...{RESET}", flush=True)
    
    if mode == "jar":
        jar_path = output_filename if output_filename.endswith('.jar') or output_filename.endswith('.zip') else output_filename + '.jar'
        
        pack_archive_file(jar_path, serve_dir)
        
        loader_path = os.path.join(os.path.dirname(os.path.abspath(jar_path)), "site_loader.html")
        with open(loader_path, 'w', encoding='utf-8') as f:
            f.write(generate_browser_jar_loader_html(os.path.basename(jar_path)))
            
        print(f"{GREEN}[✔] Portable 2-file bundle created:{RESET}")
        print(f"{CYAN}    - Archive Data : {jar_path}{RESET}")
        print(f"{CYAN}    - Browser Loader: {loader_path}{RESET}")
    else:
        index_path = os.path.join(serve_dir, "index.html")
        if not os.path.exists(index_path):
            with open(index_path, 'w', encoding='utf-8') as f:
                f.write(generate_directory_index_html(serve_dir))
                
        zip_path = output_filename if output_filename.endswith('.zip') else output_filename + '.zip'
        pack_archive_file(zip_path, serve_dir)
        print(f"{GREEN}[✔] Portable directory zip package created successfully: {zip_path}{RESET}", flush=True)

class TopConsoleDisplay:
    def __init__(self, httpd):
        self.httpd = httpd
        self.history = []
        self.active_transfers = {}
        self.lock = threading.Lock()
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def record_completed(self, url, size, duration_sec):
        speed = size / duration_sec if duration_sec > 0 else 0
        with self.lock:
            self.history.insert(0, {
                "time": time.strftime("%H:%M:%S"),
                "name": os.path.basename(unquote(urlparse(url).path)) or "index.html",
                "size": size,
                "speed": speed,
                "url": url
            })
            if len(self.history) > 50:
                self.history.pop()

    def update_active(self, thread_id, url, downloaded, total_size, start_time):
        elapsed = time.time() - start_time
        speed = downloaded / elapsed if elapsed > 0 else 0
        with self.lock:
            self.active_transfers[thread_id] = {
                "name": os.path.basename(unquote(urlparse(url).path)) or "download",
                "downloaded": downloaded,
                "total_size": total_size,
                "speed": speed,
                "url": url
            }

    def remove_active(self, thread_id):
        with self.lock:
            if thread_id in self.active_transfers:
                del self.active_transfers[thread_id]

    def _loop(self):
        while self.running:
            if not getattr(self.httpd, 'verbose_logging', False):
                self.render()
            time.sleep(0.5)

    def render(self):
        with self.httpd.stats_lock:
            files_count = self.httpd.download_count
            total_bytes = self.httpd.total_bytes
            dm_status = "ON" if self.httpd.enable_dm else "OFF"
            lc_status = "ON" if self.httpd.enable_lazy_cache else "OFF"
            is_locked = getattr(self.httpd, 'website_locked', False)

        try:
            term_rows, term_cols = os.get_terminal_size()
        except OSError:
            term_rows, term_cols = 40, 100

        max_history_lines = max(5, term_rows - 20)

        buf = [CLEAR_SCREEN]
        lock_status_str = f"{RED}LOCKED{RESET}" if is_locked else f"{GREEN}UNLOCKED{RESET}"
        buf.append(f"{BOLD}{GREEN}═══ RipWeb Enterprise Top Dashboard ═══{RESET}  Target: {CYAN}{self.httpd.base_target_url}{RESET}")
        buf.append(f"Status: {lock_status_str} | DM UI: {GREEN if dm_status=='ON' else RED}{dm_status}{RESET} | Lazy-Cache: {GREEN if lc_status=='ON' else RED}{lc_status}{RESET} | Total Files: {GREEN}{files_count}{RESET} | Size: {GREEN}{format_size(total_bytes)}{RESET}\n")

        with self.lock:
            buf.append(f"{BOLD}{CYAN}── ACTIVE TRANSFERS ──{RESET}")
            if not self.active_transfers:
                buf.append("  (No active transfers)")
            else:
                for tid, info in list(self.active_transfers.items()):
                    sz_str = format_size(info['downloaded'])
                    if info['total_size']:
                        sz_str += f" / {format_size(info['total_size'])}"
                    sp_str = format_speed(info['speed'])
                    buf.append(f"  ► {info['name']:<45} | Size: {sz_str:<18} | Speed: {YELLOW}{sp_str:<12}{RESET}")

            buf.append(f"\n{BOLD}{CYAN}── RECENT DOWNLOAD HISTORY ──{RESET}")
            buf.append(f"  {'Time':<10} {'Filename':<46} {'File Size':<14} {'Avg Speed':<12}")
            buf.append("  " + "─" * 86)
            if not self.history:
                buf.append("  (No downloads recorded yet)")
            else:
                for item in self.history[:max_history_lines]:
                    buf.append(f"  {item['time']:<10} {smart_truncate(item['name'], 45):<46} {format_size(item['size']):<14} {format_speed(item['speed']):<12}")

        buf.append(f"\n{BOLD}{YELLOW}── CONSOLE COMMANDS (Instant Single-Keypress) ──{RESET}")
        buf.append(f"  {GREEN}[L]ock{RESET} / {GREEN}[U]nlock{RESET}  - Toggle site caching lock state")
        buf.append(f"  {GREEN}[E]xport{RESET}        - Export portable ZIP or 2-file Jar archive package")
        buf.append(f"  {GREEN}[H]elp{RESET}         - Open comprehensive CLI & web bar documentation")
        buf.append(f"  {GREEN}[Q]uit{RESET}         - Stop server and save site archive")
        buf.append(f"\n{BOLD}{CYAN}RipWeb Console (Press L / U / E / H / Q) > {RESET}")

        sys.stdout.write("\n".join(buf))
        sys.stdout.flush()

def show_help_screen():
    print(CLEAR_SCREEN, end='')
    help_text = f"""
{BOLD}{GREEN}================================================================================{RESET}
{BOLD}{CYAN}                     RIPWEB ENTERPRISE - HELP & MANUAL                        {RESET}
{BOLD}{GREEN}================================================================================{RESET}

{BOLD}{YELLOW}1. CONSOLE CLI KEYBOARD COMMANDS (Instant Single-Keypress){RESET}
  {GREEN}L{RESET} / {GREEN}l{RESET} : {BOLD}Lock Website{RESET}   - Freezes local cache. Proxy rejects downloading new files.
  {GREEN}U{RESET} / {GREEN}u{RESET} : {BOLD}Unlock Website{RESET} - Re-enables live web proxying and automatic lazy-caching.
  {GREEN}E{RESET} / {GREEN}e{RESET} : {BOLD}Export Package{RESET} - Launches wizard to generate portable ZIP or JS-Jar offline site.
  {GREEN}H{RESET} / {GREEN}h{RESET} : {BOLD}Help Screen{RESET}    - Displays this complete documentation view.
  {GREEN}Q{RESET} / {GREEN}q{RESET} : {BOLD}Quit RipWeb{RESET}    - Stops HTTP server, compresses/saves archive, and exits.

{BOLD}{YELLOW}2. INJECTED WEB TOOLBAR CONTROLS (Browser Bottom-Right){RESET}
  • {BOLD}Lazy-Queue{RESET}     : IntersectionObserver dynamically queues links as you scroll.
  • {BOLD}Auto-Download{RESET}  : Automatically begins downloading media files as soon as queued.
  • {BOLD}Lock Website{RESET}   : Toggles proxy lock state directly from the browser window.
  • {BOLD}Scan Page{RESET}      : Scans current document DOM for media links (.mp4, .zip, .pdf, etc.).
  • {BOLD}DL Selected{RESET}    : Starts downloading all items selected in the toolbar queue list.
  • {BOLD}Resync Sizes{RESET}   : Issues HEAD requests to verify remote vs local file byte sizes.
  • {BOLD}Clear Finished{RESET} : Removes completed transfers from queue UI after size verification.

{BOLD}{YELLOW}3. COMMAND LINE STARTUP FLAGS{RESET}
  python RipWeb_complex.py <TARGET_URL> [options]
  -p, --port <int>           Port to host local proxy server on (default: 8000).
  -a, --archive <file>       Specify target output .zip or .7z archive file path.
  -t, --temp <path>          Custom folder directory path for uncompressed cache.
  -s, --skip-compression     Preserve uncompressed cache folder without zipping on exit.
  -k, --keep                 Keep cache files on disk when shutting down.
  -v, --verbose              Print full HTTP request/response stream to terminal.
  -dm, --download-manager    Enable or disable injected browser web bar (on|off).
  -l, --lazy-cache           Enable or disable writing proxied web assets to disk (on|off).
  -c, --create-portable      Build portable offline bundle without running server (directory|jar).

{BOLD}{GREEN}================================================================================{RESET}
"""
    print(help_text)
    print(f"{BOLD}{GREEN}Press any key to return to live dashboard...{RESET}", end='', flush=True)
    get_single_keypress()

def inject_toolbar(html_bytes):
    try:
        text = html_bytes.decode('utf-8', errors='ignore')
        
        text = re.sub(r'<!-- ripweb-toolbar -->.*?<!-- /ripweb-toolbar -->', '', text, flags=re.IGNORECASE | re.DOTALL)
        
        toolbar_js = r"""<!-- ripweb-toolbar -->
        <style>
            @keyframes rip-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            .rip-spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid rgba(255,255,255,0.3); border-radius: 50%; border-top-color: #4af; animation: rip-spin 0.8s linear infinite; }
            #ripweb-toolbar { position: fixed !important; bottom: 15px !important; right: 15px !important; z-index: 2147483647 !important; background: rgba(18,18,18,0.96) !important; color: #fff !important; width: 500px !important; border-radius: 10px !important; font-family: sans-serif !important; font-size: 12px !important; box-shadow: 0 6px 25px rgba(0,0,0,0.8) !important; border: 1px solid #444 !important; overflow: hidden !important; display: block !important; }
        </style>
        <script>
            (function() {
                try {
                    let lazyObserver = null;

                    function ensureRipToolbar() {
                        if (document.getElementById('ripweb-toolbar')) return;
                        if (!document.body) return;
                        
                        const div = document.createElement('div');
                        div.id = 'ripweb-toolbar';
                        div.innerHTML = `
                            <div style="background:#222; padding:10px 14px; font-weight:bold; color:#4af; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #333;">
                                <span>📥 RipWeb Enterprise Download Manager</span>
                                <button onclick="toggleRipDrawer()" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px;">▲</button>
                            </div>
                            <div id="ripweb-drawer" style="padding:12px; display:flex; flex-direction:column; gap:10px;">
                                <div style="display:flex; justify-content:space-between; align-items:center; background:#181818; padding:6px 8px; border-radius:4px;">
                                    <label style="display:flex; align-items:center; cursor:pointer; font-weight:500; font-size:11px;">
                                        <input type="checkbox" id="ripweb-toggle-lazyqueue" style="margin-right:5px;" onchange="toggleLazyQueue(this)">
                                        Lazy-Queue
                                    </label>
                                    <label style="display:flex; align-items:center; cursor:pointer; font-weight:500; font-size:11px;">
                                        <input type="checkbox" id="ripweb-toggle-intercept" style="margin-right:5px;" onchange="toggleIntercept(this)">
                                        Auto-Download
                                    </label>
                                    <button id="ripweb-lock-btn" onclick="toggleLockWebsite()" style="background:#d97706; color:#fff; border:none; padding:3px 8px; border-radius:4px; cursor:pointer; font-weight:bold; font-size:10px;">Lock Website</button>
                                </div>
                                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                                    <button onclick="scanAndQueueFiles()" style="background:#007acc; color:#fff; border:none; padding:5px 10px; border-radius:4px; cursor:pointer; font-weight:bold; font-size:11px;">Scan Page</button>
                                    <button onclick="downloadSelected()" style="background:#0284c7; color:#fff; border:none; padding:5px 10px; border-radius:4px; cursor:pointer; font-weight:bold; font-size:11px;">DL Selected</button>
                                    <button onclick="rescanAndCheckSizes()" style="background:#28a745; color:#fff; border:none; padding:5px 10px; border-radius:4px; cursor:pointer; font-weight:bold; font-size:11px;">Resync Sizes</button>
                                    <button onclick="clearFinishedFiles()" style="background:#6b7280; color:#fff; border:none; padding:5px 10px; border-radius:4px; cursor:pointer; font-weight:bold; font-size:11px;">Clear Finished</button>
                                </div>
                                <div style="font-weight:600; color:#aaa; font-size:11px; text-transform:uppercase; margin-top:2px; display:flex; justify-content:space-between; align-items:center;">
                                    <div style="display:flex; align-items:center; gap:8px;">
                                        <input type="checkbox" id="ripweb-select-all" onchange="toggleSelectAll(this)" title="Select / Unselect All">
                                        <span>Queue Items</span>
                                        <button onclick="invertSelection()" style="background:#333; color:#ccc; border:1px solid #555; padding:2px 6px; border-radius:3px; cursor:pointer; font-size:10px;">Invert</button>
                                    </div>
                                    <span id="ripweb-queue-count" style="color:#4af;">0 files</span>
                                </div>
                                <div id="ripweb-queue-list" style="max-height:260px; overflow-y:auto; background:#111; border:1px solid #333; border-radius:6px; padding:6px; display:flex; flex-direction:column; gap:5px;">
                                    <div style="color:#666; text-align:center; padding:20px; font-style:italic;">Queue empty</div>
                                </div>
                            </div>
                        `;
                        document.body.appendChild(div);
                        
                        const isIntercept = localStorage.getItem('ripweb_intercept') === '1';
                        const cbInt = document.getElementById('ripweb-toggle-intercept');
                        if (cbInt) cbInt.checked = isIntercept;

                        const isLazyQ = localStorage.getItem('ripweb_lazyqueue') !== '0';
                        const cbLazy = document.getElementById('ripweb-toggle-lazyqueue');
                        if (cbLazy) cbLazy.checked = isLazyQ;

                        updateQueueUI();
                        setInterval(updateQueueUI, 3000);

                        scanAndQueueFiles(isIntercept);
                        initLazyObserver();
                    }

                    window.initLazyObserver = function() {
                        const isLazyQ = localStorage.getItem('ripweb_lazyqueue') !== '0';
                        if (!isLazyQ) {
                            if (lazyObserver) { lazyObserver.disconnect(); lazyObserver = null; }
                            return;
                        }

                        if ('IntersectionObserver' in window && !lazyObserver) {
                            lazyObserver = new IntersectionObserver((entries) => {
                                let hasNew = false;
                                entries.forEach(entry => {
                                    if (entry.isIntersecting) {
                                        hasNew = true;
                                        lazyObserver.unobserve(entry.target);
                                    }
                                });
                                if (hasNew) {
                                    const isIntercept = localStorage.getItem('ripweb_intercept') === '1';
                                    scanAndQueueFiles(isIntercept);
                                }
                            }, { rootMargin: '300px' });

                            document.querySelectorAll('a, source, video, audio, img').forEach(el => lazyObserver.observe(el));
                        }
                    };

                    window.toggleRipDrawer = function() {
                        const drawer = document.getElementById('ripweb-drawer');
                        if (!drawer) return;
                        drawer.style.display = (drawer.style.display === 'none') ? 'flex' : 'none';
                    };

                    window.toggleIntercept = function(el) {
                        localStorage.setItem('ripweb_intercept', el.checked ? '1' : '0');
                        if (el.checked) scanAndQueueFiles(true);
                    };
                    
                    window.toggleLazyQueue = function(el) {
                        localStorage.setItem('ripweb_lazyqueue', el.checked ? '1' : '0');
                        initLazyObserver();
                    };

                    window.toggleSelectAll = function(master) { document.querySelectorAll('.ripweb-item-chk').forEach(chk => { chk.checked = master.checked; }); };
                    window.invertSelection = function() { document.querySelectorAll('.ripweb-item-chk').forEach(chk => { chk.checked = !chk.checked; }); };
                    window.getSelectedUrls = function() {
                        const selected = [];
                        document.querySelectorAll('.ripweb-item-chk:checked').forEach(chk => { selected.push(chk.getAttribute('data-url')); });
                        return selected;
                    };

                    window.toggleLockWebsite = function() {
                        fetch('/_ripweb/toggle-lock', {method: 'POST'}).then(res => res.json()).then(data => {
                            const btn = document.getElementById('ripweb-lock-btn');
                            if (btn) {
                                btn.style.background = data.locked ? '#dc2626' : '#d97706';
                                btn.innerText = data.locked ? 'Unlock Website' : 'Lock Website';
                            }
                        });
                    };

                    window.clearFinishedFiles = function() {
                        fetch('/_ripweb/clear-finished', {method: 'POST'}).then(res => res.json()).then(data => {
                            if (data.warnings && data.warnings.length > 0) {
                                alert('Warnings during verification:\n' + data.warnings.join('\n'));
                            }
                            updateQueueUI();
                        });
                    };

                    window.scanAndQueueFiles = function(autoQueue) {
                        const cbInt = document.getElementById('ripweb-toggle-intercept');
                        const isAuto = (typeof autoQueue === 'boolean') ? autoQueue : (cbInt && cbInt.checked);
                        
                        const links = [];
                        const mediaRegex = /\.(mp4|m4v|mp4v|mkv|webm|avi|mov|qt|wmv|flv|ogv|ogx|mpg|mpeg|m1v|m2v|vob|ts|mts|m2ts|3gp|3g2|divx|asf|rm|rmvb|mp3|mp2|mp1|mpa|m4a|aac|ogg|oga|opus|spx|flac|wav|wma|aiff|aif|mid|midi|mod|xm|s3m|it|weba|caf|jpg|jpeg|jpe|png|gif|webp|svg|bmp|ico|tif|tiff|avif|heic|heif|jxl|psd|zip|rar|7z|tar|gz|bz2|xz|iso|exe|dmg|apk|msi|deb|rpm|pdf|doc|docx|xls|xlsx|ppt|pptx)$/i;

                        document.querySelectorAll('a, source, video, audio, img').forEach(el => {
                            const href = el.href || el.src;
                            if (href) {
                                const cleanUrl = href.split('?')[0].split('#')[0];
                                if (mediaRegex.test(cleanUrl)) {
                                    if (!links.includes(href)) links.push(href);
                                }
                            }
                        });
                        if (links.length === 0) { updateQueueUI(); return; }
                        
                        fetch('/_ripweb/register-links', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({urls: links, auto_download: isAuto})
                        }).then(res => res.json()).then(data => { updateQueueUI(); });
                    };

                    window.downloadSelected = function() {
                        const urls = getSelectedUrls();
                        if (urls.length === 0) { alert('No files selected!'); return; }
                        fetch('/_ripweb/bulk-download', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({urls: urls})
                        }).then(res => res.json()).then(data => { updateQueueUI(); });
                    };

                    window.rescanAndCheckSizes = function() {
                        const selected = getSelectedUrls();
                        fetch('/_ripweb/rescan', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({urls: selected})
                        }).then(res => res.json()).then(data => { updateQueueUI(); });
                    };

                    window.updateQueueUI = function() {
                        fetch('/_ripweb/queue-status').then(res => res.json()).then(data => {
                            const list = document.getElementById('ripweb-queue-list');
                            const countEl = document.getElementById('ripweb-queue-count');
                            const lockBtn = document.getElementById('ripweb-lock-btn');
                            if (!list || !countEl) return;

                            if (lockBtn) {
                                lockBtn.style.background = data.locked ? '#dc2626' : '#d97706';
                                lockBtn.innerText = data.locked ? 'Unlock Website' : 'Lock Website';
                            }

                            if (!data.queue || data.queue.length === 0) {
                                list.innerHTML = '<div style="color:#666; text-align:center; padding:20px; font-style:italic;">Queue empty</div>';
                                countEl.innerText = '0 files';
                                return;
                            }
                            
                            const checkedState = {};
                            document.querySelectorAll('.ripweb-item-chk').forEach(chk => {
                                checkedState[chk.getAttribute('data-url')] = chk.checked;
                            });

                            countEl.innerText = `${data.queue.length} files`;
                            let html = '';
                            data.queue.forEach(item => {
                                let icon = '<span style="color:#888;">⏳</span>';
                                if (item.status === 'downloading') {
                                    icon = '<span class="rip-spinner"></span>';
                                } else if (item.status === 'completed') {
                                    icon = '<span style="color:#42d4f4; font-weight:bold;">✔</span>';
                                } else if (item.status === 'failed') {
                                    icon = '<span style="color:#ff4d4d; font-weight:bold;">✘</span>';
                                } else if (item.status === 'checking') {
                                    icon = '<span style="color:#eab308; font-weight:bold;">🔍</span>';
                                }

                                const isChecked = checkedState[item.url] !== undefined ? checkedState[item.url] : true;
                                
                                html += `<div style="display:flex; align-items:center; gap:8px; background:#1a1a1a; padding:6px 8px; border-radius:4px; font-size:11px;">
                                    <input type="checkbox" class="ripweb-item-chk" data-url="${item.url}" ${isChecked ? 'checked' : ''}>
                                    <div style="width:16px; text-align:center;">${icon}</div>
                                    <div style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${item.url}">
                                        <a href="${item.url}" target="_blank" style="color:#60a5fa; text-decoration:none;">${item.name}</a>
                                    </div>
                                    <div style="color:#aaa; font-size:10px; font-weight:bold; white-space:nowrap;">${item.size || item.status}</div>
                                </div>`;
                            });
                            list.innerHTML = html;
                        });
                    };

                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', ensureRipToolbar);
                    } else {
                        ensureRipToolbar();
                    }
                    window.addEventListener('load', ensureRipToolbar);
                    setTimeout(ensureRipToolbar, 500);
                } catch(err) {
                    console.error("RipWeb Toolbar Exception:", err);
                }
            })();
        </script>
        <!-- /ripweb-toolbar -->"""

        if re.search(r'</body>', text, flags=re.IGNORECASE):
            text = re.sub(r'(</body>)', lambda m: toolbar_js + '\n' + m.group(1), text, flags=re.IGNORECASE, count=1)
        elif re.search(r'</html>', text, flags=re.IGNORECASE):
            text = re.sub(r'(</html>)', lambda m: toolbar_js + '\n' + m.group(1), text, flags=re.IGNORECASE, count=1)
        else:
            text = text + '\n' + toolbar_js
            
        return text.encode('utf-8')
    except Exception:
        return html_bytes

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

class SmartHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        if getattr(self.server, 'verbose_logging', False):
            super().log_message(format, *args)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type, Accept')
        self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        super().end_headers()
        
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.end_headers()

    def guess_type(self, path):
        base, ext = os.path.splitext(path)
        ext_lower = ext.lower()
        
        mime_map = {
            # Core Web Formats
            '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8',
            '.css': 'text/css', '.js': 'application/javascript', '.json': 'application/json',
            
            # Audio Formats
            '.mp3': 'audio/mpeg', '.mp2': 'audio/mpeg', '.mp1': 'audio/mpeg', '.mpa': 'audio/mpeg',
            '.m4a': 'audio/mp4', '.aac': 'audio/aac',
            '.ogg': 'audio/ogg', '.oga': 'audio/ogg', '.opus': 'audio/opus', '.spx': 'audio/ogg',
            '.flac': 'audio/flac', '.wav': 'audio/wav', '.wma': 'audio/x-ms-wma',
            '.aiff': 'audio/x-aiff', '.aif': 'audio/x-aiff', '.aifc': 'audio/x-aiff',
            '.mid': 'audio/midi', '.midi': 'audio/midi', '.kar': 'audio/midi',
            '.mod': 'audio/x-mod', '.xm': 'audio/x-xm', '.s3m': 'audio/s3m', '.it': 'audio/it',
            '.ra': 'audio/x-pn-realaudio', '.ram': 'audio/x-pn-realaudio',
            '.caf': 'audio/x-caf', '.weba': 'audio/webm',
            
            # Video Formats
            '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mp4v': 'video/mp4',
            '.mkv': 'video/x-matroska', '.webm': 'video/webm',
            '.avi': 'video/x-msvideo', '.mov': 'video/quicktime', '.qt': 'video/quicktime',
            '.wmv': 'video/x-ms-wmv', '.flv': 'video/x-flv',
            '.ogv': 'video/ogg', '.ogx': 'application/ogg',
            '.mpg': 'video/mpeg', '.mpeg': 'video/mpeg', '.m1v': 'video/mpeg', '.m2v': 'video/mpeg',
            '.vob': 'video/x-ms-vob', '.ts': 'video/mp2t', '.mts': 'video/mp2t', '.m2ts': 'video/mp2t',
            '.3gp': 'video/3gpp', '.3g2': 'video/3gpp2', '.divx': 'video/divx',
            '.asf': 'video/x-ms-asf', '.rm': 'application/vnd.rn-realmedia', '.rmvb': 'application/vnd.rn-realmedia',

            # Image Formats
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.jpe': 'image/jpeg', '.jfif': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp',
            '.svg': 'image/svg+xml', '.svgz': 'image/svg+xml',
            '.bmp': 'image/bmp', '.dib': 'image/bmp', '.ico': 'image/x-icon',
            '.tif': 'image/tiff', '.tiff': 'image/tiff',
            '.avif': 'image/avif', '.heic': 'image/heic', '.heif': 'image/heif',
            '.jxl': 'image/jxl', '.psd': 'image/vnd.adobe.photoshop',
            '.tga': 'image/x-tga', '.dng': 'image/x-adobe-dng',
            
            # Fonts & Web
            '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
            '.otf': 'font/otf', '.eot': 'application/vnd.ms-fontobject'
        }

        if ext_lower in mime_map:
            return mime_map[ext_lower]

        if ext_lower == '.php' or not ext_lower:
            if os.path.exists(path):
                try:
                    with open(path, 'rb') as f:
                        header = f.read(1024).lower()
                        if b'<!doctype html' in header or b'<html' in header or b'<body' in header:
                            return 'text/html; charset=utf-8'
                except Exception: pass
            if ext_lower == '.php': return 'text/html; charset=utf-8'
            
        return super().guess_type(path)

    def translate_path(self, path):
        decoded_path = unquote(path)
        parsed_path = urlparse(decoded_path)
        clean_path = parsed_path.path
        query = parsed_path.query
        
        translated = super().translate_path(clean_path)
        
        if query:
            query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
            base, ext = os.path.splitext(translated)
            translated = f"{base}_q_{query_hash}{ext}"
            
        if not os.path.exists(translated):
            base, ext = os.path.splitext(translated)
            if not ext and os.path.exists(base + '.html'):
                return base + '.html'
        return translated

    def get_forward_headers(self):
        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ['host', 'referer', 'origin', 'accept-encoding', 'content-length', 'connection', 'range']:
                headers[k] = v
        parsed_base = urlparse(self.server.base_target_url)
        headers['Origin'] = f"{parsed_base.scheme}://{parsed_base.netloc}"
        headers['Referer'] = self.server.base_target_url
        headers['Accept-Encoding'] = 'identity' 
        return headers

    def patch_live_headers_for_browser(self, response):
        blocked_headers = [
            'transfer-encoding', 'content-length', 'content-encoding', 
            'connection', 'content-type', 'content-security-policy', 
            'content-security-policy-report-only', 'strict-transport-security', 
            'x-frame-options', 'x-xss-protection'
        ]
        
        for k, v in response.getheaders():
            k_lower = k.lower()
            if k_lower == 'set-cookie':
                v = re.sub(r'Domain=[^;]+;?\s*', '', v, flags=re.IGNORECASE)
                v = re.sub(r'Secure;?\s*', '', v, flags=re.IGNORECASE)
                v = re.sub(r'SameSite=None;?\s*', 'SameSite=Lax;', v, flags=re.IGNORECASE)
                self.send_header(k, v)
            elif k_lower not in blocked_headers:
                self.send_header(k, v)

    def rewrite_domain_references(self, content_bytes, parsed_base):
        try:
            content_text = content_bytes.decode('utf-8', errors='ignore')
            target_netloc = parsed_base.netloc
            proxy_url = f"http://localhost:{self.server.server_port}/{target_netloc}"
            proxy_url_escaped = proxy_url.replace('/', r'\/')
            
            content_text = re.sub(r'<meta[^>]+http-equiv=["\']?Content-Security-Policy["\']?[^>]*>', '', content_text, flags=re.IGNORECASE)

            schemes = [f"https://{target_netloc}", f"http://{target_netloc}", f"//{target_netloc}"]
            for s in schemes:
                content_text = content_text.replace(s, proxy_url)
                content_text = content_text.replace(s.replace('/', r'\/'), proxy_url_escaped)
            
            return content_text.encode('utf-8')
        except Exception:
            return content_bytes

    def serve_local_file(self, file_path):
        try:
            ctype = self.guess_type(file_path)
            is_html = 'text/html' in ctype
            
            if is_html:
                with open(file_path, 'rb') as f: data = f.read()
                if getattr(self.server, 'enable_dm', True): data = inject_toolbar(data)
                    
                file_len = len(data)
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(file_len))
                self.end_headers()
                self.wfile.write(data)
                return

            f = open(file_path, 'rb')
            fs = os.fstat(f.fileno())
            file_len = fs.st_size

            if 'Range' in self.headers:
                match = re.search(r'bytes=(\d+)-(\d*)', self.headers['Range'])
                if match:
                    start = int(match.group(1))
                    end_str = match.group(2)
                    end = int(end_str) if end_str else file_len - 1
                    end = min(end, file_len - 1)
                    length = end - start + 1
                    
                    self.send_response(206)
                    self.send_header('Content-Type', ctype)
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Content-Range', f'bytes {start}-{end}/{file_len}')
                    self.send_header('Content-Length', str(length))
                    self.end_headers()
                    
                    f.seek(start)
                    to_read = length
                    try:
                        while to_read > 0:
                            chunk = f.read(min(64 * 1024, to_read))
                            if not chunk: break
                            self.wfile.write(chunk)
                            to_read -= len(chunk)
                    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError): pass
                    finally: f.close()
                    return
            
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(file_len))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            try: shutil.copyfileobj(f, self.wfile)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError): pass
            finally: f.close()
        except Exception as e:
            try: self.send_error(404, f"File not found: {e}")
            except Exception: pass

    def do_POST(self):
        if not hasattr(self.server, 'base_target_url'):
            self.send_error(404)
            return

        if self.path == '/_ripweb/toggle-lock':
            with self.server.queue_lock:
                self.server.website_locked = not getattr(self.server, 'website_locked', False)
                is_locked = self.server.website_locked
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"locked": is_locked}).encode())
            return

        if self.path == '/_ripweb/clear-finished':
            warnings_list = []
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with self.server.queue_lock:
                completed_items = [(u, info['name']) for u, info in self.server.download_queue.items() if info["status"] == "completed"]

            to_remove = []
            for u, name in completed_items:
                decoded_url_path = unquote(urlparse(u).path)
                try: save_dest = safe_join(self.server.serve_dir, decoded_url_path)
                except ValueError: continue
                
                if not os.path.exists(save_dest):
                    warnings_list.append(f"Missing file for {name}")
                    continue
                
                local_size = os.path.getsize(save_dest)
                remote_size = None
                try:
                    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}, method='HEAD')
                    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                        clen = resp.headers.get('Content-Length')
                        if clen and clen.isdigit(): remote_size = int(clen)
                except Exception: pass

                if remote_size is None:
                    try:
                        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Range': 'bytes=0-0'})
                        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                            crange = resp.headers.get('Content-Range')
                            if crange and '/' in crange and crange.split('/')[-1].isdigit():
                                remote_size = int(crange.split('/')[-1])
                    except Exception: pass

                if remote_size is not None and local_size != remote_size:
                    warnings_list.append(f"Size mismatch for {name}: Local ({format_size(local_size)}) != Remote ({format_size(remote_size)})")
                else:
                    to_remove.append(u)

            with self.server.queue_lock:
                for u in to_remove:
                    self.server.download_queue.pop(u, None)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "warnings": warnings_list}).encode())
            return

        if self.path == '/_ripweb/queue-status':
            with self.server.queue_lock:
                for u, info in self.server.download_queue.items():
                    if info["status"] not in ["downloading", "failed", "checking"]:
                        decoded_url_path = unquote(urlparse(u).path)
                        try:
                            save_dest = safe_join(self.server.serve_dir, decoded_url_path)
                            if os.path.exists(save_dest) and os.path.getsize(save_dest) > 0:
                                info["status"] = "completed"
                                info["size"] = format_size(os.path.getsize(save_dest))
                            else:
                                info["status"] = "pending"
                                info["size"] = "Queued"
                        except ValueError:
                            info["status"] = "failed"
                q_data = list(self.server.download_queue.values())
                is_locked = getattr(self.server, 'website_locked', False)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"queue": q_data, "locked": is_locked}).encode())
            return

        if self.path == '/_ripweb/rescan':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
            try: target_urls = json.loads(post_data.decode('utf-8')).get('urls', [])
            except Exception: target_urls = []

            threading.Thread(target=self.server.resync_and_check_files, args=(target_urls,), daemon=True).start()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode())
            return

        if self.path == '/_ripweb/register-links':
            if getattr(self.server, 'website_locked', False):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "locked", "message": "Website locked. New links ignored."}).encode())
                return

            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                urls = data.get('urls', [])
                auto_download = data.get('auto_download', False)
                
                new_urls_to_download = []
                with self.server.queue_lock:
                    for u in urls:
                        decoded_url_path = unquote(urlparse(u).path)
                        filename = os.path.basename(decoded_url_path) or "download"
                        try:
                            save_dest = safe_join(self.server.serve_dir, decoded_url_path)
                            if os.path.exists(save_dest) and os.path.getsize(save_dest) > 0:
                                self.server.download_queue[u] = {"name": filename, "url": u, "status": "completed", "size": format_size(os.path.getsize(save_dest))}
                            else:
                                if u not in self.server.download_queue:
                                    self.server.download_queue[u] = {"name": filename, "url": u, "status": "pending", "size": "Queued"}
                                if auto_download and self.server.download_queue[u]["status"] == "pending":
                                    new_urls_to_download.append(u)
                        except ValueError: pass

                if auto_download and new_urls_to_download:
                    threading.Thread(target=self.server.process_download_queue, args=(new_urls_to_download,), daemon=True).start()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
                return
            except Exception as ex:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(ex)}).encode())
                return

        if self.path == '/_ripweb/bulk-download':
            if getattr(self.server, 'website_locked', False):
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Website locked. Downloads disabled."}).encode())
                return

            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                urls = json.loads(post_data.decode('utf-8')).get('urls', [])
                with self.server.queue_lock:
                    for u in urls:
                        filename = os.path.basename(unquote(urlparse(u).path)) or "download"
                        self.server.download_queue[u] = {"name": filename, "url": u, "status": "pending", "size": "Queued"}

                threading.Thread(target=self.server.process_download_queue, args=(urls,), daemon=True).start()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
                return
            except Exception as ex:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(ex)}).encode())
                return

        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else None
        
        payload_hash = hashlib.md5(post_data).hexdigest()[:8] if post_data else "nopayload"
        translated = self.translate_path(self.path)
        base, ext = os.path.splitext(translated)
        cached_post_path = f"{base}_POST_{payload_hash}{ext}"
        if not ext: cached_post_path += '.json'
            
        if os.path.exists(cached_post_path):
            self.serve_local_file(cached_post_path)
            return
            
        parsed_base = urlparse(self.server.base_target_url)
        path_stripped = self.path.lstrip('/')
        live_url = f"{parsed_base.scheme}://{path_stripped}" if path_stripped.startswith(parsed_base.netloc) else urljoin(self.server.base_target_url, self.path)

        tid = threading.get_ident()
        start_t = time.time()

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            req = urllib.request.Request(live_url, data=post_data, headers=self.get_forward_headers(), method='POST')
            with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                content_type = response.headers.get('Content-Type', '').lower()
                is_text = any(t in content_type for t in ['html', 'javascript', 'js', 'css', 'json', 'text']) or cached_post_path.endswith(('.js', '.css', '.json', '.html', '.php'))
                total_len = int(response.headers.get('Content-Length', 0)) or None
                
                os.makedirs(os.path.dirname(cached_post_path), exist_ok=True)
                
                if is_text:
                    resp_data = response.read()
                    resp_data = self.rewrite_domain_references(resp_data, parsed_base)
                    
                    if getattr(self.server, 'enable_lazy_cache', True):
                        tmp_path = cached_post_path + f".{tid}.tmp"
                        with open(tmp_path, 'wb') as f: f.write(resp_data)
                        atomic_save(tmp_path, cached_post_path)
                    
                    served_data = resp_data
                    is_html_doc = ('text/html' in content_type) or cached_post_path.endswith('.html')
                    if is_html_doc and getattr(self.server, 'enable_dm', True):
                        served_data = inject_toolbar(resp_data)
                    
                    self.send_response(response.getcode())
                    self.patch_live_headers_for_browser(response)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(served_data)))
                    self.end_headers()
                    try: self.wfile.write(served_data)
                    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError): pass
                    file_size = len(resp_data)
                else:
                    if getattr(self.server, 'enable_lazy_cache', True):
                        file_size = 0
                        tmp_path = cached_post_path + f".{tid}.tmp"
                        with open(tmp_path, 'wb') as f:
                            while True:
                                chunk = response.read(64 * 1024)
                                if not chunk: break
                                f.write(chunk)
                                file_size += len(chunk)
                                self.server.top_display.update_active(tid, live_url, file_size, total_len, start_t)
                        atomic_save(tmp_path, cached_post_path)
                    else:
                        self.send_response(response.getcode())
                        self.patch_live_headers_for_browser(response)
                        self.send_header('Content-Type', content_type)
                        content_len = response.headers.get('Content-Length')
                        if content_len: self.send_header('Content-Length', content_len)
                        self.end_headers()
                        file_size = 0
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk: break
                            try: self.wfile.write(chunk)
                            except: pass
                            file_size += len(chunk)
                            self.server.top_display.update_active(tid, live_url, file_size, total_len, start_t)

                duration = time.time() - start_t
                self.server.top_display.remove_active(tid)
                self.server.top_display.record_completed(live_url, file_size, duration)

                with self.server.stats_lock:
                    self.server.download_count += 1
                    self.server.total_bytes += file_size
                    self.server.last_downloaded = f"POST {live_url}"

                if getattr(self.server, 'enable_lazy_cache', True):
                    self.server.archive_needs_update = True
                
                if not getattr(self.server, 'enable_lazy_cache', True) and not is_text: return
                if not is_text: self.serve_local_file(cached_post_path)

        except Exception as e:
            self.server.top_display.remove_active(tid)
            try: self.send_error(500, str(e))
            except Exception: pass

    def do_GET(self):
        if self.path == '/_ripweb/queue-status':
            with self.server.queue_lock:
                for u, info in self.server.download_queue.items():
                    if info["status"] not in ["downloading", "failed", "checking"]:
                        decoded_url_path = unquote(urlparse(u).path)
                        try:
                            save_dest = safe_join(self.server.serve_dir, decoded_url_path)
                            if os.path.exists(save_dest) and os.path.getsize(save_dest) > 0:
                                info["status"] = "completed"
                                info["size"] = format_size(os.path.getsize(save_dest))
                            else:
                                info["status"] = "pending"
                                info["size"] = "Queued"
                        except ValueError:
                            info["status"] = "failed"
                q_data = list(self.server.download_queue.values())
                is_locked = getattr(self.server, 'website_locked', False)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"queue": q_data, "locked": is_locked}).encode())
            return

        translated = self.translate_path(self.path)
        if self.path.endswith('/'): translated = os.path.join(translated, 'index.html')
        
        if not os.path.exists(translated):
            parsed_base = urlparse(self.server.base_target_url)
            path_stripped = self.path.lstrip('/')
            live_url = f"{parsed_base.scheme}://{path_stripped}" if path_stripped.startswith(parsed_base.netloc) else urljoin(self.server.base_target_url, self.path)

            tid = threading.get_ident()
            start_t = time.time()

            os.makedirs(os.path.dirname(translated), exist_ok=True)
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                
                req = urllib.request.Request(live_url, headers=self.get_forward_headers())
                with urllib.request.urlopen(req, timeout=60, context=ctx) as response:
                    content_type = response.headers.get('Content-Type', '').lower()
                    is_text = any(t in content_type for t in ['html', 'javascript', 'js', 'css', 'json', 'text']) or self.path.endswith(('.js', '.css', '.json', '.html', '.php'))
                    total_len = int(response.headers.get('Content-Length', 0)) or None
                    
                    save_path = translated
                    
                    if is_text:
                        content_data = response.read()
                        content_data = self.rewrite_domain_references(content_data, parsed_base)
                        if not os.path.splitext(save_path)[1] and (b'<!DOCTYPE html>' in content_data[:100] or b'<html' in content_data[:100]):
                            save_path += '.html'
                            translated = save_path
                            
                        if getattr(self.server, 'enable_lazy_cache', True):
                            tmp_path = save_path + f".{tid}.tmp"
                            with open(tmp_path, 'wb') as f: f.write(content_data)
                            atomic_save(tmp_path, save_path)
                        
                        file_size = len(content_data)
                        served_data = content_data
                        
                        is_html_doc = ('text/html' in content_type) or translated.endswith('.html')
                        if is_html_doc and getattr(self.server, 'enable_dm', True):
                            served_data = inject_toolbar(content_data)
                    else:
                        if getattr(self.server, 'enable_lazy_cache', True):
                            file_size = 0
                            tmp_path = save_path + f".{tid}.tmp"
                            with open(tmp_path, 'wb') as f:
                                while True:
                                    chunk = response.read(64 * 1024)
                                    if not chunk: break
                                    f.write(chunk)
                                    file_size += len(chunk)
                                    self.server.top_display.update_active(tid, live_url, file_size, total_len, start_t)
                            atomic_save(tmp_path, save_path)
                        else:
                            self.send_response(response.getcode())
                            self.patch_live_headers_for_browser(response)
                            self.send_header('Content-Type', content_type)
                            content_len = response.headers.get('Content-Length')
                            if content_len: self.send_header('Content-Length', content_len)
                            self.end_headers()
                            
                            file_size = 0
                            while True:
                                chunk = response.read(64 * 1024)
                                if not chunk: break
                                try: self.wfile.write(chunk)
                                except: pass
                                file_size += len(chunk)
                                self.server.top_display.update_active(tid, live_url, file_size, total_len, start_t)
                        
                    duration = time.time() - start_t
                    self.server.top_display.remove_active(tid)
                    self.server.top_display.record_completed(live_url, file_size, duration)

                    with self.server.stats_lock:
                        self.server.download_count += 1
                        self.server.total_bytes += file_size
                        self.server.last_downloaded = live_url

                    if getattr(self.server, 'enable_lazy_cache', True):
                        self.server.archive_needs_update = True
                        
                    if not getattr(self.server, 'enable_lazy_cache', True) and not is_text: return
                    
                    if is_text:
                        self.send_response(response.getcode())
                        self.patch_live_headers_for_browser(response)
                        self.send_header('Content-Type', content_type)
                        self.send_header('Content-Length', str(len(served_data)))
                        self.end_headers()
                        try: self.wfile.write(served_data)
                        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError): pass
                        return
            except Exception as e:
                self.server.top_display.remove_active(tid)
                try: self.send_error(404, "File Not Found")
                except Exception: pass
                return
        
        self.serve_local_file(translated)

def main():
    parser = argparse.ArgumentParser(description="RipWeb - Offline Website Archiver & Server")
    parser.add_argument("target", nargs="?", default=None, help="Target URL")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Port to host on")
    parser.add_argument("-a", "--archive", default=None, help="Filename for the archive")
    parser.add_argument("-t", "--temp", help="Custom path for temporary cache directory")
    parser.add_argument("-k", "--keep", action="store_true", help="Keep temporary cache files after exit")
    parser.add_argument("-s", "--skip-compression", action="store_true", help="Skip archive compression")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose HTTP logging")
    parser.add_argument("-DM", "-dm", "--download-manager", nargs="?", const="on", default="on", choices=["on", "off"], help="Enable/disable Download Manager UI")
    parser.add_argument("-L", "-l", "--lazy-cache", nargs="?", const="on", default="on", choices=["on", "off"], help="Enable/disable lazy caching to disk")
    parser.add_argument("-c", "--create-portable", nargs="?", const="directory", default=None, choices=["directory", "jar"], help="Create a portable self-contained archive package (modes: directory, jar)")

    option_map = {opt.lower(): opt for opt in parser._option_string_actions}
    raw_args = sys.argv[1:]
    processed_args = []
    for arg in raw_args:
        if arg.startswith('-'):
            if '=' in arg:
                opt, val = arg.split('=', 1)
                opt_norm = option_map.get(opt.lower(), opt)
                val_norm = val.lower() if val.lower() in ['on', 'off', 'directory', 'jar'] else val
                processed_args.append(f"{opt_norm}={val_norm}")
            else:
                processed_args.append(option_map.get(arg.lower(), arg))
        else:
            processed_args.append(arg.lower() if arg.lower() in ['on', 'off', 'directory', 'jar'] else arg)

    args = parser.parse_args(processed_args)

    if args.create_portable is not None:
        if not args.archive:
            print(f"{RED}[!] Specify archive name via -a/--archive to create portable package.{RESET}")
            sys.exit(1)
        serve_dir = os.path.abspath(args.temp) if args.temp else tempfile.mkdtemp(prefix="ripweb_")
        if os.path.exists(os.path.abspath(args.archive)) and not os.listdir(serve_dir):
            unpack_archive_file(os.path.abspath(args.archive), serve_dir)
        create_portable_package(serve_dir, args.archive, mode=args.create_portable)
        sys.exit(0)

    if not args.target:
        parser.print_help()
        sys.exit(1)

    if args.archive:
        archive_name = os.path.abspath(args.archive)
    else:
        parsed_target = urlparse(args.target)
        domain = parsed_target.netloc or parsed_target.path.split('/')[0]
        domain = domain.replace(':', '_')
        default_filename = f"{domain}.zip" if domain else "site_clone.zip"
        archive_name = os.path.abspath(default_filename)
        print(f"{YELLOW}[i] Archive option omitted. Auto-selected name: {default_filename}{RESET}", flush=True)
    
    serve_dir = os.path.abspath(args.temp) if args.temp else tempfile.mkdtemp(prefix="ripweb_")

    original_cwd = os.getcwd()
    try:
        if not args.skip_compression and os.path.exists(archive_name):
            print(f"{GREEN}[✓] Found existing {os.path.basename(archive_name)}.{RESET}", flush=True)
            unpack_archive_file(archive_name, serve_dir)
        elif args.skip_compression:
            print(f"{YELLOW}[i] Skip compression enabled (-s). Directory cache: {serve_dir}{RESET}", flush=True)
        else:
            print(f"{YELLOW}[!] {os.path.basename(archive_name)} not found. Proxy will build cache on fly.{RESET}", flush=True)

        os.chdir(serve_dir)

        with ThreadingHTTPServer(("", args.port), SmartHTTPRequestHandler) as httpd:
            httpd.base_target_url = args.target
            httpd.archive_needs_update = False
            httpd.verbose_logging = args.verbose
            httpd.enable_dm = (args.download_manager.lower() == "on")
            httpd.enable_lazy_cache = (args.lazy_cache.lower() == "on")
            httpd.website_locked = False
            httpd.download_count = 0
            httpd.total_bytes = 0
            httpd.last_downloaded = "None"
            httpd.stats_lock = threading.Lock()
            httpd.serve_dir = serve_dir
            httpd.download_queue = {}
            httpd.queue_lock = threading.Lock()

            top_display = TopConsoleDisplay(httpd)
            httpd.top_display = top_display
            top_display.start()

            def resync_and_check_files(target_urls=None):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                parsed_base = urlparse(httpd.base_target_url)
                download_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Referer': httpd.base_target_url,
                    'Origin': f"{parsed_base.scheme}://{parsed_base.netloc}"
                }

                with httpd.queue_lock:
                    items_to_check = target_urls if target_urls else list(httpd.download_queue.keys())
                    for u in items_to_check:
                        if u in httpd.download_queue:
                            httpd.download_queue[u]["status"] = "checking"

                urls_needing_redownload = []

                for target_url in items_to_check:
                    parsed_u = urlparse(target_url)
                    path_stripped = unquote(parsed_u.path).lstrip('/')
                    if parsed_u.netloc.startswith("localhost") or parsed_u.netloc.startswith("127.0.0.1"):
                        real_url = f"{parsed_base.scheme}://{path_stripped}" if path_stripped.startswith(parsed_base.netloc) else urljoin(httpd.base_target_url, parsed_u.path)
                    else:
                        real_url = target_url

                    try: save_dest = safe_join(httpd.serve_dir, path_stripped)
                    except ValueError: continue

                    local_exists = os.path.exists(save_dest)
                    local_size = os.path.getsize(save_dest) if local_exists else 0

                    remote_size = None
                    try:
                        req = urllib.request.Request(real_url, headers=download_headers, method='HEAD')
                        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                            clen = resp.headers.get('Content-Length')
                            if clen and clen.isdigit(): remote_size = int(clen)
                    except Exception: pass

                    if remote_size is None:
                        try:
                            req = urllib.request.Request(real_url, headers={**download_headers, 'Range': 'bytes=0-0'})
                            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                                crange = resp.headers.get('Content-Range')
                                if crange and '/' in crange and crange.split('/')[-1].isdigit():
                                    remote_size = int(crange.split('/')[-1])
                        except Exception: pass

                    needs_dl = False
                    if not local_exists or local_size == 0: needs_dl = True
                    elif remote_size is not None and local_size != remote_size: needs_dl = True

                    with httpd.queue_lock:
                        if target_url in httpd.download_queue:
                            if needs_dl:
                                httpd.download_queue[target_url]["status"] = "pending"
                                httpd.download_queue[target_url]["size"] = f"Mismatch (Local {format_size(local_size)} != Remote {format_size(remote_size)})" if remote_size else "Mismatch / Queued"
                                urls_needing_redownload.append(target_url)
                            else:
                                httpd.download_queue[target_url]["status"] = "completed"
                                httpd.download_queue[target_url]["size"] = format_size(local_size)

                if urls_needing_redownload:
                    process_download_queue(urls_needing_redownload)

            def process_download_queue(target_urls):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                
                parsed_base = urlparse(httpd.base_target_url)
                download_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Encoding': 'identity',
                    'Referer': httpd.base_target_url,
                    'Origin': f"{parsed_base.scheme}://{parsed_base.netloc}"
                }
                
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPRedirectHandler())

                for target_url in target_urls:
                    # Prevent duplicate concurrent downloads for the same URL
                    with httpd.queue_lock:
                        if target_url in httpd.download_queue and httpd.download_queue[target_url]["status"] == "downloading":
                            continue
                        decoded_url_path = unquote(urlparse(target_url).path)
                        filename = os.path.basename(decoded_url_path) or "download"
                        httpd.download_queue[target_url] = {"name": filename, "url": target_url, "status": "downloading", "size": "Initializing..."}

                    tid = threading.get_ident()
                    start_t = time.time()
                    
                    try:
                        # ... (rest of the download logic remains unchanged)
                        parsed_u = urlparse(target_url)
                        path_stripped = unquote(parsed_u.path).lstrip('/')
                        
                        if parsed_u.netloc.startswith("localhost") or parsed_u.netloc.startswith("127.0.0.1"):
                            if path_stripped.startswith(parsed_base.netloc): real_url = f"{parsed_base.scheme}://{path_stripped}"
                            else: real_url = urljoin(httpd.base_target_url, parsed_u.path)
                        else: real_url = target_url

                        save_dest = safe_join(httpd.serve_dir, path_stripped)
                        os.makedirs(os.path.dirname(save_dest), exist_ok=True)
                        
                        req = urllib.request.Request(real_url, headers=download_headers)
                        with opener.open(req, timeout=30) as resp:
                            downloaded_size = 0
                            total_len = int(resp.headers.get('Content-Length', 0)) or None
                            tmp_dest = save_dest + f".{tid}.tmp"
                            
                            chunks = 0
                            with open(tmp_dest, 'wb') as sf:
                                while True:
                                    chk = resp.read(64 * 1024)
                                    if not chk: break
                                    sf.write(chk)
                                    downloaded_size += len(chk)
                                    chunks += 1
                                    
                                    top_display.update_active(tid, target_url, downloaded_size, total_len, start_t)

                                    if chunks % 15 == 0:
                                        with httpd.queue_lock:
                                            httpd.download_queue[target_url]["size"] = format_size(downloaded_size)
                                            
                            atomic_save(tmp_dest, save_dest)
                            duration = time.time() - start_t
                            top_display.remove_active(tid)
                            top_display.record_completed(target_url, downloaded_size, duration)

                            size_str = format_size(downloaded_size)
                            with httpd.queue_lock:
                                httpd.download_queue[target_url]["status"] = "completed"
                                httpd.download_queue[target_url]["size"] = size_str

                            with httpd.stats_lock:
                                httpd.download_count += 1
                                httpd.total_bytes += downloaded_size
                                httpd.last_downloaded = target_url
                            httpd.archive_needs_update = True

                    except Exception as ex:
                        top_display.remove_active(tid)
                        with httpd.queue_lock:
                            httpd.download_queue[target_url]["status"] = "failed"
                            httpd.download_queue[target_url]["size"] = "Error"

            httpd.process_download_queue = process_download_queue
            httpd.resync_and_check_files = resync_and_check_files
            
            server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            server_thread.start()

            while True:
                try:
                    char = get_single_keypress().lower()
                    if not char:
                        continue
                    
                    if char == 'l':
                        httpd.website_locked = True
                    elif char == 'u':
                        httpd.website_locked = False
                    elif char == 'e':
                        top_display.running = False
                        time.sleep(0.6)
                        
                        print(f"\n{CYAN}═══ Portable Export Wizard ═══{RESET}")
                        mode_choice = input("Select portable mode (1: Directory index, 2: 2-File Jar/Browser Loader) [1/2]: ").strip()
                        p_mode = "jar" if mode_choice == "2" else "directory"
                        out_name = input(f"Output archive filename (default: {os.path.basename(archive_name)}): ").strip() or archive_name
                        create_portable_package(httpd.serve_dir, out_name, mode=p_mode)
                        
                        input(f"\n{GREEN}Press Enter to return to dashboard...{RESET}")
                        top_display.running = True
                        top_display.start()
                    elif char == 'h':
                        top_display.running = False
                        time.sleep(0.6)
                        show_help_screen()
                        top_display.running = True
                        top_display.start()
                    elif char == 'q':
                        break
                except (KeyboardInterrupt, EOFError):
                    break
            
    except KeyboardInterrupt:
        pass
    finally:
        if 'top_display' in locals(): top_display.stop()
        print(f"\n{YELLOW}Server shutdown complete.{RESET}", flush=True)
        os.chdir(original_cwd)
        if args.skip_compression or args.keep:
            print(f"{YELLOW}Cache preserved at: {serve_dir}{RESET}", flush=True)
        else:
            shutil.rmtree(serve_dir, ignore_errors=True)
            print(f"{YELLOW}Temporary cache cleaned up.{RESET}", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\n{RED}[FATAL ERROR EXCEPTION]{RESET}")
        traceback.print_exc()
        sys.exit(1)
