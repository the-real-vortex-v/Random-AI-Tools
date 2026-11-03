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

print("[System] Booting RipWeb...", flush=True)
warnings.filterwarnings("ignore", category=FutureWarning)

if os.name == 'nt':
    os.system('')

RED = "\033[91m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RESET = "\033[0m"

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

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"

def smart_truncate(text, max_len=70):
    if len(text) <= max_len:
        return text
    lead = max_len // 2 - 2
    tail = max_len - lead - 3
    return text[:lead] + "..." + text[-tail:]

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
        if ext.lower() in ['.woff', '.woff2']:
            return 'font/woff2'
        if ext.lower() == '.ttf':
            return 'font/ttf'
        if ext.lower() == '.eot':
            return 'application/vnd.ms-fontobject'
        if ext.lower() in ['.mp4', '.m4v']:
            return 'video/mp4'
        if ext.lower() in ['.mp3', '.m4a']:
            return 'audio/mpeg'
        if ext.lower() == '.mkv':
            return 'video/x-matroska'
        if ext.lower() == '.php' or not ext:
            if os.path.exists(path):
                try:
                    with open(path, 'rb') as f:
                        header = f.read(64)
                        if b'<!DOCTYPE html>' in header or b'<html' in header:
                            return 'text/html; charset=utf-8'
                        if header.startswith(b'{') or header.startswith(b'['):
                            return 'application/json'
                except Exception:
                    pass
            if ext.lower() == '.php':
                return 'text/html; charset=utf-8'
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
            
            toolbar_html = """
            <style>
                @keyframes rip-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                .rip-spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid rgba(255,255,255,0.3); border-radius: 50%; border-top-color: #4af; animation: rip-spin 0.8s linear infinite; }
            </style>
            <div id="ripweb-toolbar" style="position:fixed; bottom:15px; right:15px; z-index:999999; background:rgba(18,18,18,0.96); color:#fff; width:420px; border-radius:10px; font-family:sans-serif; font-size:12px; box-shadow:0 6px 25px rgba(0,0,0,0.8); border:1px solid #444; overflow:hidden;">
                <div style="background:#222; padding:10px 14px; font-weight:bold; color:#4af; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #333;">
                    <span>📥 RipWeb Download Manager</span>
                    <button onclick="toggleRipDrawer()" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px;">▲</button>
                </div>
                <div id="ripweb-drawer" style="padding:12px; display:flex; flex-direction:column; gap:10px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <label style="display:flex; align-items:center; cursor:pointer; font-weight:500; font-size:11px;">
                            <input type="checkbox" id="ripweb-toggle-intercept" style="margin-right:4px;" onchange="toggleIntercept(this)">
                            Auto-Cache Clicked
                        </label>
                        <div style="display:flex; gap:6px;">
                            <button onclick="scanAndQueueFiles(false)" style="background:#007acc; color:#fff; border:none; padding:6px 14px; border-radius:4px; cursor:pointer; font-weight:bold; font-size:11px;">Scan</button>
                            <button onclick="rescanAllFiles()" style="background:#28a745; color:#fff; border:none; padding:6px 14px; border-radius:4px; cursor:pointer; font-weight:bold; font-size:11px;">Rescan</button>
                        </div>
                    </div>
                    <div style="font-weight:600; color:#aaa; font-size:11px; text-transform:uppercase; margin-top:2px; display:flex; justify-content:space-between;">
                        <span>Discovered Files Queue</span>
                        <span id="ripweb-queue-count" style="color:#4af;">0 files</span>
                    </div>
                    <div id="ripweb-queue-list" style="max-height:340px; overflow-y:auto; background:#111; border:1px solid #333; border-radius:6px; padding:6px; display:flex; flex-direction:column; gap:5px;">
                        <div style="color:#666; text-align:center; padding:20px; font-style:italic;">Click 'Scan' to load files</div>
                    </div>
                </div>
            </div>
            <script>
                function toggleRipDrawer() {
                    const drawer = document.getElementById('ripweb-drawer');
                    const btn = event.target;
                    if (drawer.style.display === 'none') {
                        drawer.style.display = 'flex';
                        btn.innerText = '▲';
                    } else {
                        drawer.style.display = 'none';
                        btn.innerText = '▼';
                    }
                }
                function toggleIntercept(el) {
                    localStorage.setItem('ripweb_intercept', el.checked ? '1' : '0');
                }
                window.addEventListener('DOMContentLoaded', () => {
                    const isIntercept = localStorage.getItem('ripweb_intercept') === '1';
                    document.getElementById('ripweb-toggle-intercept').checked = isIntercept;
                    
                    scanAndQueueFiles(false);
                    setInterval(updateQueueUI, 1000);

                    document.addEventListener('click', function(e) {
                        const a = e.target.closest('a');
                        if (a && localStorage.getItem('ripweb_intercept') === '1') {
                            if (/\\.(mp4|mp3|mkv|zip|pdf|m4a|m4v|rar|7z)$/i.test(a.href)) {
                                e.preventDefault();
                                queueFiles([a.href]);
                            }
                        }
                    });
                });
                function queueFiles(urls) {
                    fetch('/_ripweb/bulk-download', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({urls: urls})
                    }).then(res => res.json()).then(data => {
                        updateQueueUI();
                    });
                }
                function scanAndQueueFiles(autoQueue = true) {
                    const links = [];
                    document.querySelectorAll('a').forEach(a => {
                        if (/\\.(mp4|mp3|mkv|zip|pdf|m4a|m4v|rar|7z)$/i.test(a.href)) {
                            if (!links.includes(a.href)) links.push(a.href);
                        }
                    });
                    if (links.length === 0) return;
                    
                    fetch('/_ripweb/register-links', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({urls: links, auto_download: autoQueue})
                    }).then(res => res.json()).then(data => {
                        updateQueueUI();
                    });
                }
                function rescanAllFiles() {
                    fetch('/_ripweb/rescan', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'}
                    }).then(res => res.json()).then(data => {
                        updateQueueUI();
                    });
                }
                function updateQueueUI() {
                    fetch('/_ripweb/queue-status').then(res => res.json()).then(data => {
                        const list = document.getElementById('ripweb-queue-list');
                        const countEl = document.getElementById('ripweb-queue-count');
                        if (!data.queue || data.queue.length === 0) {
                            list.innerHTML = '<div style="color:#666; text-align:center; padding:20px; font-style:italic;">No files found</div>';
                            countEl.innerText = '0 files';
                            return;
                        }
                        countEl.innerText = `${data.queue.length} files`;
                        let html = '';
                        data.queue.forEach(item => {
                            let icon = '<span style="color:#888;" title="Pending">⏳</span>';
                            if (item.status === 'downloading') {
                                icon = '<span class="rip-spinner" title="Downloading..."></span>';
                            } else if (item.status === 'completed') {
                                icon = '<span style="color:#42d4f4; font-weight:bold;" title="Downloaded">✔</span>';
                            } else if (item.status === 'failed') {
                                icon = '<span style="color:#ff4d4d; font-weight:bold;" title="Failed">✘</span>';
                            }
                            html += `<div style="display:flex; align-items:center; gap:8px; background:#1a1a1a; padding:6px 8px; border-radius:4px; font-size:11px;">
                                <div style="width:16px; text-align:center;">${icon}</div>
                                <div style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${item.url}">${item.name}</div>
                                <div style="color:#888; font-size:10px;">${item.size || item.status}</div>
                            </div>`;
                        });
                        list.innerHTML = html;
                    });
                }
            </script>
            """
            
            # Robust injection right after the <body> tag to guarantee visibility
            if re.search(r'<body[^>]*>', content_text, flags=re.IGNORECASE):
                content_text = re.sub(r'(<body[^>]*>)', r'\1' + toolbar_html, content_text, flags=re.IGNORECASE, count=1)
            else:
                content_text = toolbar_html + content_text
            
            return content_text.encode('utf-8')
        except Exception:
            return content_bytes

    def serve_local_file(self, file_path):
        try:
            f = open(file_path, 'rb')
            fs = os.fstat(f.fileno())
            file_len = fs.st_size
            ctype = self.guess_type(file_path)

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
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            to_read -= len(chunk)
                    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                        pass
                    finally:
                        f.close()
                    return
            
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(file_len))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            try:
                shutil.copyfileobj(f, self.wfile)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass
            finally:
                f.close()
        except Exception as e:
            try:
                self.send_error(404, f"File not found: {e}")
            except Exception:
                pass

    def do_POST(self):
        if not hasattr(self.server, 'base_target_url'):
            self.send_error(404)
            return

        if self.path == '/_ripweb/queue-status':
            with self.server.queue_lock:
                for u, info in self.server.download_queue.items():
                    decoded_url_path = unquote(urlparse(u).path)
                    save_dest = os.path.join(self.server.serve_dir, decoded_url_path.lstrip('/'))
                    if os.path.exists(save_dest) and os.path.getsize(save_dest) > 100: # Ensure valid file size (>100 bytes)
                        disk_size = os.path.getsize(save_dest)
                        info["status"] = "completed"
                        info["size"] = format_size(disk_size)
                    else:
                        if info["status"] == "completed":
                            info["status"] = "pending"
                            info["size"] = "Queued"
                q_data = list(self.server.download_queue.values())
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"queue": q_data}).encode())
            return

        if self.path == '/_ripweb/rescan':
            urls_to_redownload = []
            with self.server.queue_lock:
                for u, info in self.server.download_queue.items():
                    decoded_url_path = unquote(urlparse(u).path)
                    save_dest = os.path.join(self.server.serve_dir, decoded_url_path.lstrip('/'))
                    # Check if file exists and is valid (greater than 100 bytes, avoiding error HTML pages)
                    if os.path.exists(save_dest) and os.path.getsize(save_dest) > 100:
                        disk_size = os.path.getsize(save_dest)
                        info["status"] = "completed"
                        info["size"] = format_size(disk_size)
                    else:
                        info["status"] = "pending"
                        info["size"] = "Queued"
                        urls_to_redownload.append(u)

            if urls_to_redownload:
                threading.Thread(target=self.server.process_download_queue, args=(urls_to_redownload,), daemon=True).start()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "redownloaded": len(urls_to_redownload)}).encode())
            return

        if self.path == '/_ripweb/register-links':
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
                        save_dest = os.path.join(self.server.serve_dir, decoded_url_path.lstrip('/'))
                        filename = os.path.basename(decoded_url_path) or "download"
                        
                        if os.path.exists(save_dest) and os.path.getsize(save_dest) > 100:
                            disk_size = os.path.getsize(save_dest)
                            self.server.download_queue[u] = {
                                "name": filename, 
                                "url": u, 
                                "status": "completed", 
                                "size": format_size(disk_size)
                            }
                        else:
                            if u not in self.server.download_queue:
                                self.server.download_queue[u] = {
                                    "name": filename, 
                                    "url": u, 
                                    "status": "pending", 
                                    "size": "Queued"
                                }
                            if auto_download and self.server.download_queue[u]["status"] == "pending":
                                new_urls_to_download.append(u)

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
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                urls = data.get('urls', [])
                
                with self.server.queue_lock:
                    for u in urls:
                        if u not in self.server.download_queue:
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
        if not ext:
            cached_post_path += '.json'
            
        if os.path.exists(cached_post_path):
            self.serve_local_file(cached_post_path)
            return
            
        parsed_base = urlparse(self.server.base_target_url)
        path_stripped = self.path.lstrip('/')
        live_url = f"{parsed_base.scheme}://{path_stripped}" if path_stripped.startswith(parsed_base.netloc) else urljoin(self.server.base_target_url, self.path)

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            req = urllib.request.Request(live_url, data=post_data, headers=self.get_forward_headers(), method='POST')
            with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                content_type = response.headers.get('Content-Type', '').lower()
                is_text = any(t in content_type for t in ['html', 'javascript', 'js', 'css', 'json', 'text']) or cached_post_path.endswith(('.js', '.css', '.json', '.html', '.php'))
                
                os.makedirs(os.path.dirname(cached_post_path), exist_ok=True)
                
                if is_text:
                    resp_data = response.read()
                    resp_data = self.rewrite_domain_references(resp_data, parsed_base)
                    with open(cached_post_path, 'wb') as f:
                        f.write(resp_data)
                    file_size = len(resp_data)
                    
                    self.send_response(response.getcode())
                    self.patch_live_headers_for_browser(response)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(file_size))
                    self.end_headers()
                    try:
                        self.wfile.write(resp_data)
                    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                        pass
                else:
                    file_size = 0
                    with open(cached_post_path, 'wb') as f:
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            file_size += len(chunk)

                with self.server.stats_lock:
                    self.server.download_count += 1
                    self.server.total_bytes += file_size
                    self.server.last_downloaded = f"POST {live_url}"

                if getattr(self.server, 'verbose_logging', False):
                    print(f"\n{CYAN}[+] Proxied POST ({format_size(file_size)}):{RESET} {live_url}", flush=True)
                else:
                    display_url = smart_truncate(f"POST {live_url}", max_len=70)
                    size_str = format_size(self.server.total_bytes)
                    sys.stdout.write(f"\r\033[K{CYAN}[i] Files:{RESET} {self.server.download_count} | {CYAN}Size:{RESET} {size_str} | {GREEN}Last:{RESET} {display_url}")
                    sys.stdout.flush()

                self.server.archive_needs_update = True
                
                if not is_text:
                    self.serve_local_file(cached_post_path)

        except Exception as e:
            if getattr(self.server, 'verbose_logging', False):
                print(f"\n{RED}[-] Failed POST:{RESET} {live_url} - {e}", flush=True)
            try:
                self.send_error(500, str(e))
            except Exception:
                pass

    def do_GET(self):
        if self.path == '/_ripweb/queue-status':
            with self.server.queue_lock:
                for u, info in self.server.download_queue.items():
                    decoded_url_path = unquote(urlparse(u).path)
                    save_dest = os.path.join(self.server.serve_dir, decoded_url_path.lstrip('/'))
                    if os.path.exists(save_dest) and os.path.getsize(save_dest) > 100:
                        disk_size = os.path.getsize(save_dest)
                        info["status"] = "completed"
                        info["size"] = format_size(disk_size)
                    else:
                        if info["status"] == "completed":
                            info["status"] = "pending"
                            info["size"] = "Queued"
                q_data = list(self.server.download_queue.values())

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"queue": q_data}).encode())
            return

        translated = self.translate_path(self.path)
        if self.path.endswith('/'):
            translated = os.path.join(translated, 'index.html')
        
        if not os.path.exists(translated):
            parsed_base = urlparse(self.server.base_target_url)
            path_stripped = self.path.lstrip('/')
            live_url = f"{parsed_base.scheme}://{path_stripped}" if path_stripped.startswith(parsed_base.netloc) else urljoin(self.server.base_target_url, self.path)

            os.makedirs(os.path.dirname(translated), exist_ok=True)
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                
                req = urllib.request.Request(live_url, headers=self.get_forward_headers())
                with urllib.request.urlopen(req, timeout=60, context=ctx) as response:
                    content_type = response.headers.get('Content-Type', '').lower()
                    is_text = any(t in content_type for t in ['html', 'javascript', 'js', 'css', 'json', 'text']) or self.path.endswith(('.js', '.css', '.json', '.html', '.php'))
                    
                    save_path = translated
                    
                    if is_text:
                        content_data = response.read()
                        content_data = self.rewrite_domain_references(content_data, parsed_base)
                        if not os.path.splitext(save_path)[1] and (b'<!DOCTYPE html>' in content_data[:100] or b'<html' in content_data[:100]):
                            save_path += '.html'
                            translated = save_path
                        with open(save_path, 'wb') as f:
                            f.write(content_data)
                        file_size = len(content_data)
                    else:
                        file_size = 0
                        with open(save_path, 'wb') as f:
                            while True:
                                chunk = response.read(64 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                                file_size += len(chunk)
                        
                    with self.server.stats_lock:
                        self.server.download_count += 1
                        self.server.total_bytes += file_size
                        self.server.last_downloaded = live_url

                    if getattr(self.server, 'verbose_logging', False):
                        print(f"\n{CYAN}[+] Downloaded ({format_size(file_size)}):{RESET} {live_url}", flush=True)
                    else:
                        display_url = smart_truncate(live_url, max_len=70)
                        size_str = format_size(self.server.total_bytes)
                        sys.stdout.write(f"\r\033[K{CYAN}[i] Files:{RESET} {self.server.download_count} | {CYAN}Size:{RESET} {size_str} | {GREEN}Last:{RESET} {display_url}")
                        sys.stdout.flush()

                    self.server.archive_needs_update = True
                    
                    if is_text:
                        self.send_response(response.getcode())
                        self.patch_live_headers_for_browser(response)
                        self.send_header('Content-Type', content_type)
                        self.send_header('Content-Length', str(len(content_data)))
                        self.end_headers()
                        try:
                            self.wfile.write(content_data)
                        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                            pass
                        return
            except Exception as e:
                if getattr(self.server, 'verbose_logging', False):
                    print(f"\n{RED}[-] Failed to load:{RESET} {live_url} - {e}", flush=True)
                try:
                    self.send_error(404, "File Not Found")
                except Exception:
                    pass
                return
        
        self.serve_local_file(translated)

def get_target_page_url(target, port):
    parsed = urlparse(target)
    path = parsed.path if parsed.path.startswith('/') else f"/{parsed.path}"
    return f"http://localhost:{port}/{parsed.netloc}{path}"

def main():
    parser = argparse.ArgumentParser(description="RipWeb - Offline Website Archiver & Server")
    parser.add_argument("target", help="Target URL")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Port to host on")
    parser.add_argument("-a", "--archive", default=None, help="Filename for the archive")
    parser.add_argument("-t", "--temp", help="Custom path for the temporary cache directory")
    parser.add_argument("-k", "--keep", action="store_true", help="Keep temporary cache files after exit")
    parser.add_argument("-s", "--skip-compression", action="store_true", help="Skip archive compression and preserve loose temp files")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose HTTP logging")

    args = parser.parse_args()

    if args.archive:
        archive_name = os.path.abspath(args.archive)
    else:
        parsed_target = urlparse(args.target)
        domain = parsed_target.netloc or parsed_target.path.split('/')[0]
        domain = domain.replace(':', '_')
        default_filename = f"{domain}.zip" if domain else "site_clone.zip"
        archive_name = os.path.abspath(default_filename)
        print(f"{YELLOW}[i] Archive option omitted. Auto-selected name: {default_filename}{RESET}", flush=True)
    
    if args.temp:
        serve_dir = os.path.abspath(args.temp)
        os.makedirs(serve_dir, exist_ok=True)
    else:
        serve_dir = tempfile.mkdtemp(prefix="ripweb_")

    print(f"{CYAN}Cache Directory:{RESET} {serve_dir}")

    original_cwd = os.getcwd()
    try:
        if not args.skip_compression and os.path.exists(archive_name):
            print(f"{GREEN}[✓] Found existing {os.path.basename(archive_name)}.{RESET}", flush=True)
            unpack_archive_file(archive_name, serve_dir)
        elif args.skip_compression:
            print(f"{YELLOW}[i] Skip compression enabled (-s). Using direct directory cache: {serve_dir}{RESET}", flush=True)
        else:
            print(f"{YELLOW}[!] {os.path.basename(archive_name)} not found. Proxy will build cache on the fly.{RESET}", flush=True)

        os.chdir(serve_dir)
        full_page_url = get_target_page_url(args.target, args.port)

        with ThreadingHTTPServer(("", args.port), SmartHTTPRequestHandler) as httpd:
            httpd.base_target_url = args.target
            httpd.archive_needs_update = False
            httpd.verbose_logging = args.verbose
            httpd.download_count = 0
            httpd.total_bytes = 0
            httpd.last_downloaded = "None"
            httpd.stats_lock = threading.Lock()
            httpd.serve_dir = serve_dir
            httpd.download_queue = {}
            httpd.queue_lock = threading.Lock()

            def process_download_queue(target_urls):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                
                parsed_base = urlparse(httpd.base_target_url)
                download_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Encoding': 'identity',
                    'Referer': httpd.base_target_url,
                    'Origin': f"{parsed_base.scheme}://{parsed_base.netloc}"
                }
                
                for target_url in target_urls:
                    with httpd.queue_lock:
                        decoded_url_path = unquote(urlparse(target_url).path)
                        filename = os.path.basename(decoded_url_path) or "download"
                        httpd.download_queue[target_url] = {"name": filename, "url": target_url, "status": "downloading", "size": "Downloading..."}
                    
                    try:
                        decoded_url_path = unquote(urlparse(target_url).path)
                        local_subpath = decoded_url_path.lstrip('/')
                        save_dest = os.path.join(httpd.serve_dir, local_subpath)
                        os.makedirs(os.path.dirname(save_dest), exist_ok=True)
                        
                        req = urllib.request.Request(target_url, headers=download_headers)
                        with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
                            downloaded_size = 0
                            with open(save_dest, 'wb') as sf:
                                while True:
                                    chk = resp.read(64 * 1024)
                                    if not chk:
                                        break
                                    sf.write(chk)
                                    downloaded_size += len(chk)
                                    
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
                        with httpd.queue_lock:
                            httpd.download_queue[target_url]["status"] = "failed"
                            httpd.download_queue[target_url]["size"] = "Error"

            httpd.process_download_queue = process_download_queue
            
            print(f"\n{GREEN}======================================================={RESET}")
            print(f"{GREEN}[✔] Proxy server is running!{RESET}")
            print(f"{GREEN}--> Access your page directly at:{RESET}")
            print(f"{CYAN}{full_page_url}{RESET}")
            if args.verbose:
                print(f"{YELLOW}[i] Verbose logging is ON.{RESET}")
            print(f"{GREEN}======================================================={RESET}")
            print("Press Ctrl+C to stop the server and save loaded assets.\n", flush=True)
            
            httpd.serve_forever()
            
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Server shut down initiated.{RESET}", flush=True)
        if args.skip_compression:
            print(f"{GREEN}[✔] Skip compression (-s) active. Archive packaging bypassed. All files preserved at: {serve_dir}{RESET}")
        elif 'httpd' in locals() and getattr(httpd, 'archive_needs_update', False):
            pack_archive_file(archive_name, serve_dir)
        else:
            print(f"{GREEN}No new files were downloaded. Archive left unchanged.{RESET}")
    finally:
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
