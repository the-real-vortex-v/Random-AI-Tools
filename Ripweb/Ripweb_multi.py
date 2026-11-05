#!/usr/bin/env python3
"""
RipWeb Enterprise - Multi-Site Namespace Gateway & Session Scraper Proxy
Complete, self-contained single-file Python script.

Three Main User Interfaces:
 1. Text Console   - The real-time interactive terminal output in the shell.
 2. Web Downloader - The floating download management widget injected into archived web pages.
 3. Web Console    - The browser-based management dashboard at http://localhost:<port>/

Session Architecture & Directory Structure:
  {workingdir}/{session_id}.rip/{primary_domain}/
  {workingdir}/{session_id}.rip/{other_domain}/  (if recorded under the current session)
"""

import os
import sys
import re
import time
import json
import gzip
import zlib
import ssl
import shutil
import zipfile
import mimetypes
import webbrowser
import urllib.parse
import threading
import http.server
import http.cookiejar
import socketserver
import urllib.request
import urllib.error
from html import escape as html_escape
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin, unquote

# ---------------------------------------------------------------------------
# Cross-platform single keypress & terminal initialization
# ---------------------------------------------------------------------------

if os.name == 'nt':
    import msvcrt

    # Ensure UTF-8 output with fallback replacement on Windows
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    # Enable ANSI Virtual Terminal Processing on Windows 10/11
    os.system('')
    try:
        import ctypes
        _kernel32 = ctypes.windll.kernel32
        _stdout_handle = _kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        _console_mode = ctypes.c_ulong()
        if _kernel32.GetConsoleMode(_stdout_handle, ctypes.byref(_console_mode)):
            _kernel32.SetConsoleMode(_stdout_handle, _console_mode.value | 0x0004)
    except Exception:
        pass

    def get_single_keypress():
        """Non-blocking single keypress for Windows console."""
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
        # Extended key prefix
        if ch in (b'\x00', b'\xe0'):
            if msvcrt.kbhit():
                msvcrt.getch()
            return None
        if ch == b'\x03':  # Ctrl+C
            raise KeyboardInterrupt
        if ch == b'\x1b':  # Escape
            return 'esc'
        if ch in (b'\r', b'\n'):
            return 'enter'
        if ch == b' ':
            return 'space'
        try:
            decoded = ch.decode('utf-8', errors='ignore')
            return decoded.lower() if decoded else None
        except Exception:
            return None

else:
    import termios
    import tty
    import select

    def get_single_keypress():
        """Non-blocking single keypress for POSIX terminal."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            rlist, _, _ = select.select([sys.stdin], [], [], 0)
            if rlist:
                ch = sys.stdin.read(1)
                if ch == '\x03':  # Ctrl+C
                    raise KeyboardInterrupt
                if ch == '\x1b':
                    r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if r2:
                        sys.stdin.read(2)
                        return None
                    return 'esc'
                if ch in ('\r', '\n'):
                    return 'enter'
                if ch == ' ':
                    return 'space'
                return ch.lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return None

# ---------------------------------------------------------------------------
# ANSI Colour Codes & Constants
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"
DIM = "\033[2m"
CLEAR_EOL = "\033[K" # Clears to end of line to prevent terminal ghosting

MAX_FINISHED_QUEUE_ITEMS = 200
MAX_HISTORY_ITEMS = 50
CONCURRENT_DOWNLOADS = 4
CONSOLE_REFRESH_INTERVAL = 1.0

# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def format_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.2f} {units[i]}"

def format_speed(bps):
    return f"{format_size(bps)}/s"

def smart_truncate(text, max_len=30):
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."

def js_string_escape(value):
    return json.dumps(str(value))

def is_valid_domain(domain):
    if not domain or len(domain) > 253:
        return False
    host = domain.split(':')[0]
    if '.' not in host and host != 'localhost':
        return False
    if any(c in domain for c in ' /\\?#'):
        return False
    return True

def make_session_id(domain_or_url):
    raw = domain_or_url.strip()
    if raw.startswith('http://') or raw.startswith('https://'):
        domain = urlparse(raw).netloc
    else:
        domain = raw.split('/')[0]
    domain = domain.split(':')[0].strip().lower()
    if not domain.endswith('.rip'):
        return f"{domain}.rip"
    return domain

def extract_session_and_domain(path):
    if not path.startswith('/'):
        return None, None, path
    parts = path.split('/', 3)
    if len(parts) >= 3 and parts[1].endswith('.rip'):
        session_id = parts[1]
        domain = parts[2]
        remainder = '/' + parts[3] if len(parts) > 3 else '/'
        return session_id, domain, remainder
    if len(parts) >= 2:
        domain = parts[1]
        remainder = '/' + parts[2] if len(parts) > 2 else '/'
        return None, domain, remainder
    return None, None, path

def extract_domain_from_path(path):
    _, domain, remainder = extract_session_and_domain(path)
    return domain, remainder

def create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.set_ciphers('DEFAULT@SECLEVEL=1')
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def sanitize_cookie_for_browser(cookie_header):
    cleaned = re.sub(r'(?i)\bdomain=[^;]+;?\s*', '', cookie_header)
    cleaned = re.sub(r'(?i)\bsecure;?\s*', '', cleaned)
    return cleaned.strip().rstrip(';')

def clean_headers_for_remote(headers, target_domain):
    out = {}
    skip = {'host', 'content-length', 'connection', 'accept-encoding', 'if-none-match', 'if-modified-since'}
    for key, value in headers.items():
        if key.lower() in skip:
            continue
        if key.lower() in ('referer', 'origin') and ('localhost' in value or '127.0.0.1' in value):
            out[key] = f"https://{target_domain}/"
        else:
            out[key] = value
    out['Host'] = target_domain
    out['Accept-Encoding'] = 'gzip, deflate'
    return out

def translate_workspace_path(workspace_dir, session_id, domain, path_rest):
    workspace_dir = os.path.abspath(workspace_dir)
    safe_session = os.path.basename(os.path.normpath(session_id))
    safe_domain = os.path.basename(os.path.normpath(domain))

    if not safe_session or safe_session in ('.', '..'):
        raise ValueError(f"Invalid session ID: {session_id}")
    if not safe_domain or safe_domain in ('.', '..'):
        raise ValueError(f"Invalid domain: {domain}")

    parsed_p = urlparse(path_rest)
    clean_path = unquote(parsed_p.path.lstrip('/'))
    
    # Windows Path Sanitization (Fixes [WinError 3] for invalid chars and trailing spaces)
    sanitized_parts = []
    for part in clean_path.split('/'):
        safe_part = re.sub(r'[<>:"|?*\x00-\x1f]', '_', part)
        safe_part = safe_part.rstrip('. ')
        if safe_part:
            sanitized_parts.append(safe_part)
            
    clean_path = '/'.join(sanitized_parts)
    
    # Map directory roots and extensionless routes to index.html
    _, ext = os.path.splitext(clean_path)
    if not ext or parsed_p.path.endswith('/'):
        if clean_path and not clean_path.endswith('index.html'):
            clean_path = os.path.join(clean_path, 'index.html')
        elif not clean_path:
            clean_path = 'index.html'

    full_dest = os.path.abspath(os.path.join(workspace_dir, safe_session, safe_domain, clean_path))
    
    try:
        if os.path.commonpath([workspace_dir, full_dest]) != workspace_dir:
            raise ValueError(f"Path traversal blocked: {path_rest}")
    except Exception:
        raise ValueError(f"Path traversal blocked: {path_rest}")

    if os.name == 'nt' and not full_dest.startswith('\\\\?\\'):
        full_dest = '\\\\?\\' + full_dest

    return full_dest

def ensure_parent_dir(filepath):
    """Creates directory structure safely, resolving file vs directory collisions (WinError 183)."""
    clean_path = filepath[4:] if filepath.startswith('\\\\?\\') else filepath
    dir_path = os.path.dirname(clean_path)
    if not dir_path:
        return

    current = dir_path
    stack = []
    while current and current != os.path.dirname(current):
        if os.path.isfile(current):
            try:
                temp_file = current + ".tmp_bak"
                os.rename(current, temp_file)
                os.makedirs(current, exist_ok=True)
                shutil.move(temp_file, os.path.join(current, "index.html"))
            except Exception:
                try:
                    os.remove(current)
                except Exception:
                    pass
            break
        stack.append(current)
        current = os.path.dirname(current)

    os.makedirs(dir_path, exist_ok=True)

def guess_content_type(filepath):
    content_type, _ = mimetypes.guess_type(filepath)
    return content_type or 'application/octet-stream'


# ---------------------------------------------------------------------------
# Session & Workspace Management
# ---------------------------------------------------------------------------

class SessionManager:
    def __init__(self, workspace_dir):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.config_path = os.path.join(self.workspace_dir, 'ripweb_config.json')
        self.lock = threading.RLock()
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.data = self._load()
        if "sessions" not in self.data:
            self.data["sessions"] = {}
        self.active_session_id = next(iter(self.data.get("sessions", {})), None)
        self.cookie_jars = {}

    def get_cookie_jar(self, session_id):
        with self.lock:
            if session_id not in self.cookie_jars:
                self.cookie_jars[session_id] = http.cookiejar.CookieJar()
            return self.cookie_jars[session_id]

    @property
    def serve_dir(self):
        return self.workspace_dir

    def _load(self):
        with self.lock:
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                        if "sessions" not in data:
                            data["sessions"] = data.get("projects", {})
                        return data
                except Exception:
                    pass
            return {"sessions": {}}

    def save(self):
        with self.lock:
            try:
                with open(self.config_path, 'w', encoding='utf-8') as fh:
                    json.dump(self.data, fh, indent=2)
            except Exception as exc:
                print(f"{RED}[!] Error saving workspace config: {exc}{RESET}")

    def get_or_create_session(self, primary_url_or_domain):
        with self.lock:
            raw = primary_url_or_domain.strip()
            if raw.startswith('http://') or raw.startswith('https://'):
                domain = urlparse(raw).netloc
            else:
                domain = raw.split('/')[0]
            domain = domain.split(':')[0].strip().lower()
            session_id = make_session_id(domain)

            if session_id in self.data["sessions"]:
                session = self.data["sessions"][session_id]
                self.active_session_id = session_id
                return session_id, session

            session = {
                "session_id": session_id,
                "primary_domain": domain,
                "recorded_domains": [domain],
                "locked": False,
                "exported": False,
                "created_at": datetime.now().isoformat(),
            }
            self.data["sessions"][session_id] = session
            self.active_session_id = session_id
            self.save()

            primary_folder = os.path.join(self.workspace_dir, session_id, domain)
            os.makedirs(primary_folder, exist_ok=True)
            return session_id, session

    def get_session(self, session_id):
        with self.lock:
            return self.data["sessions"].get(session_id)

    def find_session_for_domain(self, domain):
        with self.lock:
            domain = domain.lower()
            for sid, s in self.data["sessions"].items():
                if s["primary_domain"] == domain or domain in s.get("recorded_domains", []):
                    return sid, s
            if self.active_session_id and self.active_session_id in self.data["sessions"]:
                return self.active_session_id, self.data["sessions"][self.active_session_id]
            return None, None

    def add_domain_to_session(self, session_id, domain):
        with self.lock:
            domain = domain.lower()
            if session_id in self.data["sessions"]:
                session = self.data["sessions"][session_id]
                if domain not in session["recorded_domains"]:
                    session["recorded_domains"].append(domain)
                    self.save()
                dom_dir = os.path.join(self.workspace_dir, session_id, domain)
                os.makedirs(dom_dir, exist_ok=True)

    def is_domain_in_session(self, session_id, domain):
        with self.lock:
            session = self.data["sessions"].get(session_id)
            if not session:
                return False
            domain = domain.lower()
            return domain == session["primary_domain"] or domain in session.get("recorded_domains", [])

    def delete_session(self, session_id):
        with self.lock:
            if session_id in self.data["sessions"]:
                session_path = os.path.join(self.workspace_dir, session_id)
                if os.path.exists(session_path):
                    shutil.rmtree(session_path, ignore_errors=True)
                
                export_dir = os.path.join(self.workspace_dir, f"{session_id}.export")
                if os.path.exists(export_dir):
                    shutil.rmtree(export_dir, ignore_errors=True)

                del self.data["sessions"][session_id]
                if self.active_session_id == session_id:
                    self.active_session_id = next(iter(self.data["sessions"]), None)
                self.save()

    def toggle_lock(self, session_id):
        with self.lock:
            if session_id in self.data["sessions"]:
                session = self.data["sessions"][session_id]
                session["locked"] = not session.get("locked", False)
                self.save()
                return session["locked"]
        return None

    def export_session(self, session_id, compression='deflate'):
        with self.lock:
            if session_id not in self.data["sessions"]:
                return None
            session = self.data["sessions"][session_id]
            primary_domain = session["primary_domain"]
            
        session_dir = os.path.join(self.workspace_dir, session_id)
        if not os.path.exists(session_dir):
            return None
            
        export_dir = os.path.join(self.workspace_dir, f"{session_id}.export")
        os.makedirs(export_dir, exist_ok=True)
            
        zip_path = os.path.join(export_dir, f"{session_id}.zip")
        loader_path = os.path.join(export_dir, "_Loader.html")

        comp_method = zipfile.ZIP_DEFLATED if compression == 'deflate' else zipfile.ZIP_STORED
        
        shim_js = """// RipWeb Offline Protocol Shim
(function() {
    if (window.location.protocol !== 'file:') return;
    const _origFetch = window.fetch;
    window.fetch = function(resource, init) {
        if (typeof resource === 'string' && resource.startsWith('/')) {
            console.log("RipWeb Offline Fetch Intercepted:", resource);
        }
        return _origFetch.call(this, resource, init);
    };
})();
"""
        with zipfile.ZipFile(zip_path, 'w', comp_method) as zipf:
            zipf.writestr("ripweb_offline_shim.js", shim_js)
            for root, _, files in os.walk(session_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    rel_to_session = os.path.relpath(full_path, session_dir).replace(os.sep, '/')
                    
                    if fname.endswith('.html'):
                        try:
                            with open(full_path, 'rb') as fh:
                                content = fh.read()
                            
                            # Auto-repair for files previously cached as raw gzip
                            if content.startswith(b'\x1f\x8b'):
                                try: content = gzip.decompress(content)
                                except Exception: pass
                                
                            html_str = content.decode('utf-8', errors='ignore')
                            
                            file_domain = rel_to_session.split('/')[0]
                            rewriter = HTMLRewriter(
                                current_session=session_id,
                                current_domain=file_domain,
                                is_domain_in_session_fn=self.is_domain_in_session,
                                export_mode=True
                            )
                            rewriter.feed(html_str)
                            html_str = rewriter.get_output()
                            
                            depth = rel_to_session.count('/')
                            rel_shim = ('../' * depth) + 'ripweb_offline_shim.js' if depth > 0 else 'ripweb_offline_shim.js'
                            shim_tag = f'<script src="{rel_shim}"></script>'
                            if '<head>' in html_str:
                                html_str = html_str.replace('<head>', f'<head>\n    {shim_tag}', 1)
                                
                            html_str = re.sub(r'<div id="ripweb-toolbar".*?</div>\s*<script>.*?</script>', '', html_str, flags=re.DOTALL)
                            zipf.writestr(rel_to_session, html_str.encode('utf-8'))
                        except Exception:
                            zipf.write(full_path, rel_to_session)
                    else:
                        zipf.write(full_path, rel_to_session)

        loader_html = self._generate_offline_loader_html(session_id, primary_domain)
        with open(loader_path, 'w', encoding='utf-8') as f:
            f.write(loader_html)

        with self.lock:
            session["exported"] = True
            self.save()

        return zip_path

    def _generate_offline_loader_html(self, session_id, primary_domain):
        zip_file_name = f"{session_id}.zip"
        return f"""<!DOCTYPE html>
<html>
<head>
    <title>Offline Archive: {session_id}</title>
    <style>
        body {{ margin:0; font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#0f172a; color:#f8fafc; overflow:hidden; }}
        #overlay {{ position:absolute; top:0; left:0; width:100%; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; background:#0f172a; z-index:10; }}
        #drop-box {{ border: 2px dashed #38bdf8; padding: 40px; border-radius: 12px; text-align:center; background:#1e293b; cursor:pointer; max-width: 450px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); transition: background 0.2s; }}
        #drop-box:hover {{ background:#334155; }}
        #drop-box h2 {{ color: #38bdf8; margin-top:0; }}
        #drop-box p {{ color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }}
        iframe {{ width:100%; height:100vh; border:none; display:none; background:#fff; }}
    </style>
</head>
<body>
    <div id="overlay">
        <div id="drop-box">
            <h2>Load Offline Archive</h2>
            <p>Select or drag-and-drop the archive file to begin offline browsing:<br><br><b style="color:#e2e8f0; font-size:1.1rem;">{zip_file_name}</b></p>
            <input type="file" id="file-input" accept=".zip" style="display:none;">
            <p id="status-text" style="display:none; color:#38bdf8; font-weight:bold; margin-top:15px;"></p>
        </div>
    </div>
    <iframe id="content-frame"></iframe>

    <script>
        const primaryDomain = "{primary_domain}";
        const expectedZip = "{zip_file_name}";
        const fileInput = document.getElementById('file-input');
        const dropBox = document.getElementById('drop-box');
        const overlay = document.getElementById('overlay');
        const iframe = document.getElementById('content-frame');
        const statusText = document.getElementById('status-text');

        dropBox.addEventListener('click', () => fileInput.click());
        dropBox.addEventListener('dragover', (e) => {{ e.preventDefault(); dropBox.style.borderColor = '#bae6fd'; }});
        dropBox.addEventListener('dragleave', () => {{ dropBox.style.borderColor = '#38bdf8'; }});
        dropBox.addEventListener('drop', (e) => {{
            e.preventDefault();
            if(e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        }});
        fileInput.addEventListener('change', (e) => {{
            if(e.target.files.length) handleFile(e.target.files[0]);
        }});

        let zipArchive = null;
        let urlCache = {{}};

        async function attemptAutoLoad() {{
            try {{
                statusText.style.display = 'block';
                statusText.innerText = "Attempting auto-load...";
                const res = await fetch(expectedZip);
                if (res.ok) {{
                    const blob = await res.blob();
                    handleFile(new File([blob], expectedZip));
                }} else {{
                    statusText.style.display = 'none';
                }}
            }} catch (e) {{
                statusText.style.display = 'none';
                console.log("Auto-load prevented by browser CORS. User must select file manually.");
            }}
        }}
        
        attemptAutoLoad();

        async function handleFile(file) {{
            dropBox.style.pointerEvents = 'none';
            dropBox.innerHTML = "<h2 style='color:#38bdf8;'>Parsing Archive...</h2>";
            try {{
                zipArchive = await loadZip(file);
                overlay.style.display = 'none';
                iframe.style.display = 'block';
                await navigate(primaryDomain + '/index.html');
            }} catch (err) {{
                dropBox.style.pointerEvents = 'auto';
                dropBox.innerHTML = `<h2 style="color:#ef4444;">Error</h2><p style="color:#f8fafc; text-align:center; max-width:80%; margin:0 auto;">${{err.message || err}}</p><button onclick="location.reload()" style="margin-top:15px;padding:8px 16px;background:#38bdf8;border:none;border-radius:6px;cursor:pointer;font-weight:bold;">Try Again</button>`;
            }}
        }}

        async function loadZip(file) {{
            const buf = await file.arrayBuffer();
            const dv = new DataView(buf);
            let eocd = buf.byteLength - 22;
            while(eocd >= 0 && dv.getUint32(eocd, true) !== 0x06054b50) eocd--;
            if (eocd < 0) throw new Error("Not a valid ZIP file. Please ensure you selected the exported .zip archive.");
            
            let cdOff = dv.getUint32(eocd + 16, true);
            let cdRecs = dv.getUint16(eocd + 10, true);
            const entries = {{}};
            
            for(let i=0; i<cdRecs; i++) {{
                const comp = dv.getUint16(cdOff + 10, true);
                const compSize = dv.getUint32(cdOff + 20, true);
                const nLen = dv.getUint16(cdOff + 28, true);
                const xLen = dv.getUint16(cdOff + 30, true);
                const cLen = dv.getUint16(cdOff + 32, true);
                const lhOff = dv.getUint32(cdOff + 42, true);
                
                const nameBytes = new Uint8Array(buf, cdOff + 46, nLen);
                const name = new TextDecoder().decode(nameBytes);
                entries[name] = {{ comp, compSize, lhOff }};
                
                cdOff += 46 + nLen + xLen + cLen;
            }}
            return {{ buf, dv, entries }};
        }}

        async function getFileData(zip, name) {{
            const ent = zip.entries[name];
            if(!ent) return null;
            const nLen = zip.dv.getUint16(ent.lhOff + 26, true);
            const xLen = zip.dv.getUint16(ent.lhOff + 28, true);
            const dataOff = ent.lhOff + 30 + nLen + xLen;
            const raw = new Uint8Array(zip.buf, dataOff, ent.compSize);
            
            if(ent.comp === 0) return raw;
            if(ent.comp === 8) {{
                const ds = new DecompressionStream('deflate-raw');
                const writer = ds.writable.getWriter();
                writer.write(raw).catch(()=>{{}});
                writer.close().catch(()=>{{}});
                const res = new Response(ds.readable);
                const ab = await res.arrayBuffer();
                return new Uint8Array(ab);
            }}
            throw new Error("Unsupported compression method inside zip: " + ent.comp);
        }}

        async function getBlobUrl(zip, path, mime) {{
            if(urlCache[path]) return urlCache[path];
            const data = await getFileData(zip, path);
            if(!data) return '';
            const blob = new Blob([data], {{type: mime}});
            const url = URL.createObjectURL(blob);
            urlCache[path] = url;
            return url;
        }}

        function normalizePath(p) {{
            const parts = p.split('/');
            const res = [];
            for(let part of parts) {{
                if(part === '' && res.length > 0) continue;
                if(part === '.') continue;
                if(part === '..') res.pop();
                else res.push(part);
            }}
            return res.join('/');
        }}

        window.addEventListener('message', async (e) => {{
            if(e.data && e.data.type === 'navigate') {{
                let target = e.data.url;
                let current = e.data.current;
                let basePath = current.substring(0, current.lastIndexOf('/') + 1);
                
                let finalPath = target.startsWith('/') ? target.substring(1) : basePath + target;
                finalPath = normalizePath(finalPath.split('#')[0].split('?')[0]);
                
                if(finalPath === '' || finalPath.endsWith('/')) finalPath += 'index.html';
                if(!finalPath.endsWith('.html') && !zipArchive.entries[finalPath]) finalPath += '/index.html';
                
                await navigate(finalPath);
            }}
        }});

        async function navigate(path) {{
            const data = await getFileData(zipArchive, path);
            if(!data) {{
                iframe.srcdoc = `<div style="font-family:sans-serif;padding:40px;"><h2>404 - Not Found in Archive</h2><p>${{path}}</p><button style="padding:10px; cursor:pointer;" onclick="window.parent.postMessage({{type:'navigate', url:'/', current:'${{path}}'}}, '*')">Go Home</button></div>`;
                return;
            }}
            let html = new TextDecoder().decode(data);
            
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            const basePath = path.substring(0, path.lastIndexOf('/') + 1);
            
            function resolve(href) {{
                if(!href || href.startsWith('http') || href.startsWith('data:') || href.startsWith('blob:') || href.startsWith('javascript:')) return null;
                let p = href.startsWith('/') ? href.substring(1) : basePath + href;
                return normalizePath(p.split('#')[0].split('?')[0]);
            }}

            const els = [
                ...doc.querySelectorAll('link[rel="stylesheet"]'),
                ...doc.querySelectorAll('img'),
                ...doc.querySelectorAll('script[src]')
            ];
            
            for(let el of els) {{
                const attr = el.tagName === 'LINK' ? 'href' : 'src';
                const val = el.getAttribute(attr);
                const p = resolve(val);
                if(p && zipArchive.entries[p]) {{
                    let mime = 'application/octet-stream';
                    if(p.endsWith('.css')) mime = 'text/css';
                    else if(p.endsWith('.js')) mime = 'application/javascript';
                    else if(p.endsWith('.svg')) mime = 'image/svg+xml';
                    else if(p.endsWith('.png')) mime = 'image/png';
                    else if(p.match(/\\.jpe?g$/i)) mime = 'image/jpeg';
                    
                    const blobUrl = await getBlobUrl(zipArchive, p, mime);
                    if(blobUrl) el.setAttribute(attr, blobUrl);
                }}
            }}

            const script = doc.createElement('script');
            script.textContent = `
                document.addEventListener('click', e => {{
                    const a = e.target.closest('a');
                    if(a) {{
                        const href = a.getAttribute('href');
                        if(href && !href.startsWith('http') && !href.startsWith('javascript:') && !href.startsWith('mailto:')) {{
                            e.preventDefault();
                            window.parent.postMessage({{ type: 'navigate', url: href, current: '${{path}}' }}, '*');
                        }}
                    }}
                }});
            `;
            doc.body.appendChild(script);

            iframe.srcdoc = doc.documentElement.outerHTML;
        }}
    </script>
</body>
</html>"""

    def get_stats(self):
        with self.lock:
            stats = {}
            for sid, s in self.data["sessions"].items():
                s_path = os.path.join(self.workspace_dir, sid)
                total_files = 0
                total_size = 0
                if os.path.exists(s_path):
                    for root, _, files in os.walk(s_path):
                        for fname in files:
                            total_files += 1
                            try:
                                total_size += os.path.getsize(os.path.join(root, fname))
                            except Exception:
                                pass
                stats[sid] = {
                    "files": total_files,
                    "size": total_size,
                    "size_str": format_size(total_size),
                }
            return stats


# ---------------------------------------------------------------------------
# HTMLRewriter with Interstitial Navigation
# ---------------------------------------------------------------------------

class HTMLRewriter(HTMLParser):
    def __init__(self, current_session, current_domain, is_domain_in_session_fn, export_mode=False):
        super().__init__(convert_charrefs=False)
        self.current_session = current_session
        self.current_domain = current_domain
        self.is_domain_in_session_fn = is_domain_in_session_fn
        self.export_mode = export_mode
        self.output = []

    def handle_decl(self, decl):
        self.output.append(f'<!{decl}>')

    def handle_starttag(self, tag, attrs):
        self._emit_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._emit_tag(tag, attrs)

    def handle_endtag(self, tag):
        self.output.append(f'</{tag}>')

    def handle_data(self, data):
        self.output.append(data)

    def handle_comment(self, data):
        self.output.append(f'<!--{data}-->')

    def handle_entityref(self, name):
        self.output.append(f'&{name};')

    def handle_charref(self, name):
        self.output.append(f'&#{name};')

    def handle_pi(self, data):
        self.output.append(f'<?{data}>')

    def _emit_tag(self, tag, attrs):
        new_attrs = []
        is_anchor = (tag.lower() == 'a')
        for attr, val in attrs:
            if val is not None and attr in ('href', 'src', 'action', 'data-src'):
                if is_anchor and attr == 'href':
                    val = self._rewrite_anchor_link(val)
                else:
                    val = self._rewrite_asset_url(val)
            elif val is not None and attr == 'srcset':
                val = self._rewrite_srcset(val)
            new_attrs.append((attr, val))

        parts = []
        for attr, val in new_attrs:
            if val is None:
                parts.append(f' {attr}')
            else:
                parts.append(f' {attr}="{html_escape(val, quote=True)}"')

        self.output.append(f'<{tag}{"".join(parts)}>')

    def _rewrite_anchor_link(self, url):
        url = url.strip()
        if not url or url.startswith('#') or url.startswith('data:') or url.startswith('javascript:') or url.startswith('mailto:'):
            return url

        parsed = urlparse(url)
        if parsed.netloc:
            host = parsed.netloc.split(':')[0].lower()
            if host in ('localhost', '127.0.0.1'):
                return url

            target_domain = parsed.netloc.lower()
            if self.is_domain_in_session_fn(self.current_session, target_domain):
                full_path = parsed.path + ('?' + parsed.query if parsed.query else '')
                if not full_path.startswith('/'):
                    full_path = '/' + full_path
                return f"/{target_domain}{full_path}" if self.export_mode else f"/{self.current_session}/{target_domain}{full_path}"
            else:
                escaped_target = urllib.parse.quote(url, safe='')
                return url if self.export_mode else f"/_ripweb/interstitial?session={self.current_session}&target={escaped_target}"

        if url.startswith('/'):
            if url.startswith(f"/{self.current_session}/"):
                return url.replace(f"/{self.current_session}/", "/", 1) if self.export_mode else url
            if url.startswith('/_ripweb/'):
                return url
            return f"/{self.current_domain}{url}" if self.export_mode else f"/{self.current_session}/{self.current_domain}{url}"
        return url

    def _rewrite_asset_url(self, url):
        url = url.strip()
        if not url or url.startswith('#') or url.startswith('data:') or url.startswith('javascript:'):
            return url

        parsed = urlparse(url)
        if parsed.netloc:
            host = parsed.netloc.split(':')[0].lower()
            if host in ('localhost', '127.0.0.1'):
                return url

            target_domain = parsed.netloc.lower()
            full_path = parsed.path + ('?' + parsed.query if parsed.query else '')
            if not full_path.startswith('/'):
                full_path = '/' + full_path
            return f"/{target_domain}{full_path}" if self.export_mode else f"/{self.current_session}/{target_domain}{full_path}"

        if url.startswith('/'):
            if url.startswith(f"/{self.current_session}/"):
                return url.replace(f"/{self.current_session}/", "/", 1) if self.export_mode else url
            if url.startswith('/_ripweb/'):
                return url
            return f"/{self.current_domain}{url}" if self.export_mode else f"/{self.current_session}/{self.current_domain}{url}"
        return url

    def _rewrite_srcset(self, srcset):
        entries = srcset.split(',')
        rewritten = []
        for entry in entries:
            parts = entry.strip().split(None, 1)
            if parts:
                parts[0] = self._rewrite_asset_url(parts[0])
                rewritten.append(' '.join(parts))
        return ', '.join(rewritten)

    def get_output(self):
        return ''.join(self.output)


# ---------------------------------------------------------------------------
# HTTP Request Handler (Gateway & Session Router)
# ---------------------------------------------------------------------------

class RipWebHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if getattr(self.server, 'verbose', False):
            super().log_message(format, *args)

    def _resolve_target(self, path):
        """Intelligently resolves target session and domain, recovering broken relative links silently."""
        session_id, domain, path_rest = extract_session_and_domain(path)

        if domain in ('localhost', '127.0.0.1') or (domain and domain.startswith('localhost:')):
            return self._resolve_target(path_rest)

        if session_id and domain and '.' in domain and is_valid_domain(domain):
            return session_id, domain, path_rest

        referer = self.headers.get('Referer', '')
        ref_sid, ref_dom = None, None
        if referer:
            ref_parsed = urlparse(referer)
            if ref_parsed.netloc and ('localhost' in ref_parsed.netloc or '127.0.0.1' in ref_parsed.netloc):
                ref_sid, ref_dom, _ = extract_session_and_domain(ref_parsed.path)
                if not ref_sid and ref_dom:
                    ref_sid, _ = self.server.workspace.find_session_for_domain(ref_dom)

        if not session_id:
            if domain and is_valid_domain(domain) and '.' in domain:
                s_id, _ = self.server.workspace.find_session_for_domain(domain)
                if s_id:
                    return s_id, domain, path_rest
            
            target_sid = ref_sid or self.server.workspace.active_session_id
            if target_sid:
                session = self.server.workspace.get_session(target_sid)
                if session:
                    target_dom = ref_dom or session['primary_domain']
                    return target_sid, target_dom, path
        else:
            target_dom = ref_dom
            if not target_dom:
                session = self.server.workspace.get_session(session_id)
                if session:
                    target_dom = session['primary_domain']
            
            if target_dom:
                reconstructed = f"/{domain}{path_rest}" if domain else path_rest
                reconstructed = reconstructed.replace('//', '/')
                return session_id, target_dom, reconstructed

        return session_id, domain, path_rest

    def handle_one_request(self):
        try:
            self.server.set_browser_state(True)
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            self.server.set_browser_state(False, f"Client socket reset: {exc}")
            self.close_connection = True
        except Exception as exc:
            self.server.set_browser_state(False, f"Client error: {exc}")
            self.close_connection = True

    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        query = parsed_path.query

        if path in ('/', ''):
            if getattr(self.server, 'no_dashboard', False):
                self.send_error(403, "Web Console disabled (--no-dashboard).")
                return
            self._serve_web_console()
            return

        if path == '/_ripweb/queue-status':
            self._serve_queue_status()
            return

        if path == '/_ripweb/interstitial':
            self._serve_interstitial(query)
            return

        if '.export/' in path:
            clean_path = unquote(path.lstrip('/'))
            local_file = os.path.join(self.server.workspace.workspace_dir, os.path.normpath(clean_path))
            if os.path.exists(local_file) and os.path.isfile(local_file):
                self._serve_local_file(local_file, None, None)
                return

        session_id, domain, path_rest = self._resolve_target(path)

        if not session_id or not domain or not is_valid_domain(domain):
            self.send_error(400, f"RipWeb: Invalid Gateway Route '{path}'.")
            return

        self.server.workspace.add_domain_to_session(session_id, domain)
        session = self.server.workspace.get_session(session_id)

        if session and session.get('locked', False):
            try:
                local_file = translate_workspace_path(self.server.workspace.workspace_dir, session_id, domain, path_rest)
            except ValueError:
                self.send_error(400, "Bad path.")
                return

            if os.path.exists(local_file) and os.path.isfile(local_file):
                self._serve_local_file(local_file, session_id, domain)
                return

            target_url = f"https://{domain}{path_rest}"
            is_downloading = False
            with self.server.queue_lock:
                for item in self.server.download_queue.values():
                    if item.get("download_url") == target_url and item.get("status") == "downloading":
                        is_downloading = True
                        break

            if is_downloading:
                for _ in range(30):
                    time.sleep(0.1)
                    if os.path.exists(local_file) and os.path.isfile(local_file):
                        self._serve_local_file(local_file, session_id, domain)
                        return

            self.send_error(404, "Session is locked in offline test mode. Resource not captured.")
            return

        accept = self.headers.get('Accept', '')
        fetch_mode = self.headers.get('Sec-Fetch-Mode', '')
        if 'text/html' in accept or fetch_mode == 'navigate':
            captured = {}
            skip = {'host', 'content-length', 'connection', 'accept-encoding'}
            for key, value in self.headers.items():
                if key.lower() not in skip:
                    captured[key] = value
            self.server.last_browser_headers = captured

        target_url = f"https://{domain}{path_rest}"
        if query:
            target_url += f"?{query}"
        self._proxy_fetch(target_url, session_id, domain, path_rest)

    def do_POST(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path in ('/_ripweb/add-session', '/_ripweb/add-project'):
            self._handle_add_session()
            return
        if path in ('/_ripweb/delete-session', '/_ripweb/delete-project'):
            self._handle_delete_session()
            return
        if path in ('/_ripweb/export-session', '/_ripweb/export-project'):
            self._handle_export_session()
            return
        if path == '/_ripweb/toggle-lock':
            self._handle_toggle_lock()
            return
        if path == '/_ripweb/interstitial-action':
            self._handle_interstitial_action()
            return
        if path == '/_ripweb/clear-finished':
            self._handle_clear_finished()
            return
        if path in ('/_ripweb/register-links', '/_ripweb/bulk-download'):
            self._handle_register_or_download(path)
            return

        session_id, domain, path_rest = self._resolve_target(path)
        
        if session_id and domain and is_valid_domain(domain):
            length = int(self.headers.get('Content-Length', 0))
            post_body = self.rfile.read(length) if length > 0 else b''
            query = parsed_path.query
            target_url = f"https://{domain}{path_rest}"
            if query:
                target_url += f"?{query}"
            self._proxy_fetch(target_url, session_id, domain, path_rest, method='POST', post_body=post_body)
            return

        self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _read_form_body(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length).decode('utf-8')
        return urllib.parse.parse_qs(raw)

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_add_session(self):
        params = self._read_form_body()
        url = params.get('url', [''])[0].strip()
        if url:
            if not url.startswith('http'):
                url = 'https://' + url
            dom = urlparse(url).netloc
            if dom and is_valid_domain(dom):
                sid, session = self.server.workspace.get_or_create_session(dom)
                self.send_response(303)
                self.send_header('Location', f"/{sid}/{session['primary_domain']}/")
                self.end_headers()
                return
        self.send_response(303)
        self.send_header('Location', '/')
        self.end_headers()

    def _handle_delete_session(self):
        params = self._read_form_body()
        sid = params.get('sid', [''])[0] or params.get('pid', [''])[0]
        if sid:
            self.server.workspace.delete_session(sid)
        self._send_json({"status": "ok"})

    def _handle_export_session(self):
        params = self._read_form_body()
        sid = params.get('sid', [''])[0] or params.get('pid', [''])[0]
        comp = params.get('compression', ['deflate'])[0]
        zip_path = self.server.workspace.export_session(sid, compression=comp)
        if zip_path:
            self._send_json({"status": "exported", "path": zip_path})
        else:
            self._send_json({"status": "error", "message": "Unknown session"}, status=404)

    def _handle_toggle_lock(self):
        params = self._read_form_body()
        sid = params.get('sid', [''])[0] or params.get('pid', [''])[0]
        if sid:
            new_state = self.server.workspace.toggle_lock(sid)
            if new_state is not None:
                self._send_json({"status": "ok", "locked": new_state})
                return
        self.send_error(400, "Missing or invalid session ID")

    def _handle_interstitial_action(self):
        params = self._read_form_body()
        action = params.get('action', [''])[0]
        session_id = params.get('session', [''])[0]
        target_url = params.get('target', [''])[0]

        parsed = urlparse(target_url)
        target_domain = parsed.netloc.lower()
        target_path = parsed.path + ('?' + parsed.query if parsed.query else '')
        if not target_path.startswith('/'):
            target_path = '/' + target_path

        if action == 'record_current' and session_id:
            self.server.workspace.add_domain_to_session(session_id, target_domain)
            redirect_to = f"/{session_id}/{target_domain}{target_path}"
            self.send_response(303)
            self.send_header('Location', redirect_to)
            self.end_headers()
            return

        if action == 'new_session':
            new_sid, new_session = self.server.workspace.get_or_create_session(target_domain)
            redirect_to = f"/{new_sid}/{target_domain}{target_path}"
            self.send_response(303)
            self.send_header('Location', redirect_to)
            self.end_headers()
            return

        self.send_response(303)
        self.send_header('Location', '/')
        self.end_headers()

    def _handle_clear_finished(self):
        with self.server.queue_lock:
            to_remove = [u for u, item in self.server.download_queue.items()
                         if item["status"] in ("finished", "failed")]
            for u in to_remove:
                del self.server.download_queue[u]
        self._send_json({"status": "ok", "removed": len(to_remove)})

    def _handle_register_or_download(self, path):
        data = self._read_json_body()
        urls = data.get('urls', [])
        session_id = data.get('session_id') or data.get('proj_id')

        session = self.server.workspace.get_session(session_id)
        if session and session.get('locked', False):
            self._send_json({"status": "locked"})
            return

        fallback_domain = session['primary_domain'] if session else None

        client_headers = {}
        if getattr(self.server, 'last_browser_headers', None):
            client_headers = self.server.last_browser_headers.copy()
        else:
            skip = {'host', 'content-length', 'content-type', 'connection', 'accept-encoding'}
            for key, value in self.headers.items():
                if key.lower() not in skip:
                    client_headers[key] = value
        client_headers['Accept-Encoding'] = 'gzip, deflate'

        new_count = 0
        with self.server.queue_lock:
            if 'bulk-download' in path:
                for clean_u in urls:
                    if clean_u not in self.server.download_queue:
                        continue
                    item = self.server.download_queue[clean_u]
                    item['headers'] = client_headers
                    real_u = item.get("download_url", clean_u)
                    parsed_real = urlparse(real_u)
                    
                    if parsed_real.netloc.startswith('localhost') or parsed_real.netloc.startswith('127.0.0.1'):
                        _, domain, p_rest = extract_session_and_domain(parsed_real.path)
                        if not domain or not is_valid_domain(domain):
                            domain = item.get('domain') or fallback_domain
                            p_rest = parsed_real.path
                    else:
                        domain = parsed_real.netloc
                        p_rest = parsed_real.path
                        
                    if domain:
                        item['domain'] = domain
                        item['path_rest'] = p_rest + ('?' + parsed_real.query if parsed_real.query else '')
                    if item["status"] != "downloading":
                        item["status"] = "pending"
                        new_count += 1
            else:
                for raw_url in urls:
                    parsed_u = urlparse(raw_url)
                    
                    if parsed_u.path.startswith('/_ripweb/') or parsed_u.scheme not in ('http', 'https'):
                        continue
                        
                    if parsed_u.netloc.startswith('localhost') or parsed_u.netloc.startswith('127.0.0.1'):
                        _, domain, p_rest = extract_session_and_domain(parsed_u.path)
                        if not domain or not is_valid_domain(domain):
                            domain = fallback_domain
                            p_rest = parsed_u.path
                    else:
                        domain = parsed_u.netloc
                        p_rest = parsed_u.path

                    if parsed_u.query:
                        p_rest += '?' + parsed_u.query
                        
                    if not domain:
                        domain = fallback_domain
                        
                    if not domain:
                        continue
                    
                    clean_u = f"https://{domain}{p_rest}"
                    
                    clean_path = unquote(parsed_u.path)
                    filename = os.path.basename(clean_path)
                    if not filename or filename == domain:
                        if p_rest in ('', '/'):
                            filename = "index.html"
                        else:
                            parent_dir = os.path.basename(p_rest.split('?')[0].rstrip('/'))
                            filename = f"{parent_dir}/index.html" if parent_dir else "index.html"

                    if clean_u not in self.server.download_queue:
                        self.server.download_queue[clean_u] = {
                            "name": filename,
                            "session_id": session_id,
                            "domain": domain,
                            "path_rest": p_rest,
                            "download_url": clean_u,
                            "size": 0,
                            "speed": 0,
                            "status": "pending",
                            "error": None,
                            "headers": client_headers,
                        }
                        new_count += 1

        self._send_json({"status": "queued", "added": new_count})

    def _proxy_fetch(self, target_url, session_id, domain, path_rest, method='GET', post_body=None):
        try:
            local_file = translate_workspace_path(self.server.workspace.workspace_dir, session_id, domain, path_rest)
        except ValueError:
            self.send_error(400, "Bad path.")
            return

        session = self.server.workspace.get_session(session_id)
        headers = clean_headers_for_remote(self.headers, domain)
        if method == 'POST' and post_body is not None:
            req = urllib.request.Request(target_url, data=post_body, headers=headers, method='POST')
        else:
            req = urllib.request.Request(target_url, headers=headers)
        ctx = create_ssl_context()
        cookie_jar = self.server.workspace.get_cookie_jar(session_id)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar),
            urllib.request.HTTPSHandler(context=ctx)
        )

        max_retries = 3
        resp_status = 200
        resp_headers = {}
        raw_data = b''
        content_type = ''

        for attempt in range(max_retries):
            try:
                with opener.open(req, timeout=15) as resp:
                    self.server.set_remote_state(True)
                    content_type = resp.headers.get('Content-Type', '')
                    raw_data = resp.read()

                    enc = resp.headers.get('Content-Encoding', '').lower()
                    if 'gzip' in enc:
                        try: raw_data = gzip.decompress(raw_data)
                        except Exception: pass
                    elif 'deflate' in enc:
                        try: raw_data = zlib.decompress(raw_data)
                        except Exception: pass

                    resp_status = resp.status
                    resp_headers = resp.headers
                    break

            except urllib.error.HTTPError as exc:
                if exc.code in (408, 429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    time.sleep(0.3 + (attempt * 0.4))
                    continue

                if exc.code == 304:
                    self.send_response(304)
                    for key, value in exc.headers.items():
                        if key.lower() not in ('content-encoding', 'transfer-encoding', 'content-length', 'connection'):
                            self.send_header(key, value)
                    self.end_headers()
                    return

                if exc.code >= 500:
                    self.server.set_remote_state(False, f"Remote {domain} returned HTTP {exc.code}")
                else:
                    self.server.set_remote_state(True)

                challenge_data = b''
                try: challenge_data = exc.read()
                except Exception: pass

                content_type = exc.headers.get('Content-Type', '') if hasattr(exc, 'headers') else ''
                if challenge_data and 'text/html' in content_type:
                    enc = exc.headers.get('Content-Encoding', '').lower()
                    if 'gzip' in enc:
                        try: challenge_data = gzip.decompress(challenge_data)
                        except Exception: pass
                    elif 'deflate' in enc:
                        try: challenge_data = zlib.decompress(challenge_data)
                        except Exception: pass

                    try:
                        html_text = challenge_data.decode('utf-8', errors='ignore')
                        rewriter = HTMLRewriter(
                            current_session=session_id,
                            current_domain=domain,
                            is_domain_in_session_fn=self.server.workspace.is_domain_in_session,
                        )
                        rewriter.feed(html_text)
                        rewritten = rewriter.get_output()
                        toolbar = self._build_web_downloader(session_id, domain)
                        rewritten = re.sub(r'</body>', f'{toolbar}</body>', rewritten, count=1, flags=re.IGNORECASE)
                        if toolbar not in rewritten:
                            rewritten += toolbar
                        challenge_data = rewritten.encode('utf-8')
                    except Exception:
                        pass

                    self.send_response(exc.code)
                    for key, value in exc.headers.items():
                        if key.lower() not in ('content-encoding', 'transfer-encoding', 'content-length', 'connection'):
                            if key.lower() == 'set-cookie':
                                value = sanitize_cookie_for_browser(value)
                            self.send_header(key, value)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Content-Length', str(len(challenge_data)))
                    self.end_headers()
                    try:
                        self.wfile.write(challenge_data)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
                        self.server.set_browser_state(False, f"Browser socket reset: {exc}")
                        self.close_connection = True
                    return

                if os.path.exists(local_file):
                    self._serve_local_file(local_file, session_id, domain)
                else:
                    self.send_error(exc.code, f"Gateway Proxy HTTP Error: {exc.reason}")
                return

            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(0.3 + (attempt * 0.4))
                    continue

                self.server.set_remote_state(False, f"Remote {domain} failed: {exc}")
                if os.path.exists(local_file):
                    self._serve_local_file(local_file, session_id, domain)
                else:
                    self.send_error(502, f"Gateway Connection Failed: {exc}")
                return

        # --- SUCCESS PATH: Write raw/rewritten data back to client ---
        if not session or not session.get('locked', False):
            ensure_parent_dir(local_file)
            try:
                with open(local_file, 'wb') as fh:
                    fh.write(raw_data)
            except Exception:
                pass

        if 'text/html' in content_type:
            try:
                html_text = raw_data.decode('utf-8', errors='ignore')
                rewriter = HTMLRewriter(
                    current_session=session_id,
                    current_domain=domain,
                    is_domain_in_session_fn=self.server.workspace.is_domain_in_session,
                )
                rewriter.feed(html_text)
                rewritten = rewriter.get_output()

                toolbar = self._build_web_downloader(session_id, domain)
                rewritten = re.sub(
                    r'</body>',
                    f'{toolbar}</body>',
                    rewritten,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if toolbar not in rewritten:
                    rewritten += toolbar

                raw_data = rewritten.encode('utf-8')
            except Exception:
                pass

        self.send_response(resp_status)
        for key, value in resp_headers.items():
            if key.lower() not in ('content-encoding', 'transfer-encoding', 'content-length', 'connection', 'x-frame-options', 'content-security-policy'):
                if key.lower() == 'set-cookie':
                    value = sanitize_cookie_for_browser(value)
                self.send_header(key, value)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(raw_data)))
        self.end_headers()
        try:
            self.wfile.write(raw_data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            self.server.set_browser_state(False, f"Browser socket reset: {exc}")
            self.close_connection = True

    def _serve_local_file(self, local_file, session_id=None, domain=None):
        try:
            real_file = os.path.abspath(local_file)
            real_ws = os.path.abspath(self.server.workspace.workspace_dir)

            clean_rf = real_file[4:] if real_file.startswith('\\\\?\\') else real_file
            clean_ws = real_ws[4:] if real_ws.startswith('\\\\?\\') else real_ws

            if os.path.commonpath([clean_ws, clean_rf]) != clean_ws:
                self.send_error(403, "Access denied: file path is outside workspace directory.")
                return
            with open(local_file, 'rb') as fh:
                data = fh.read()
            
            # Auto-repair for files previously cached as raw gzip
            if data.startswith(b'\x1f\x8b'):
                try: data = gzip.decompress(data)
                except Exception: pass
            
            content_type = guess_content_type(clean_rf)
            
            if 'text/html' in content_type and session_id and domain:
                try:
                    html_text = data.decode('utf-8', errors='ignore')
                    toolbar = self._build_web_downloader(session_id, domain)
                    if '</body>' in html_text.lower():
                        html_text = re.sub(r'</body>', f'{toolbar}</body>', html_text, count=1, flags=re.IGNORECASE)
                    else:
                        html_text += toolbar
                    data = html_text.encode('utf-8')
                except Exception:
                    pass

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
                self.server.set_browser_state(False, f"Browser socket reset: {exc}")
                self.close_connection = True
        except Exception:
            self.send_error(404, "File not found locally.")

    def _serve_web_console(self):
        stats = self.server.workspace.get_stats()
        sessions = self.server.workspace.data.get("sessions", {})
        port = self.server.server_port

        rows_html = ""
        if not sessions:
            rows_html = '<tr><td colspan="5" style="text-align:center; color:#64748b;">No active sessions in workspace. Start one below!</td></tr>'
        else:
            for sid, s in sessions.items():
                st = stats.get(sid, {"files": 0, "size_str": "0 B"})
                recorded_list = s.get("recorded_domains", [s["primary_domain"]])
                rec_str = ", ".join(recorded_list)
                esc_sid = html_escape(sid, quote=True)
                esc_dom = html_escape(s['primary_domain'], quote=True)
                esc_rec = html_escape(rec_str)
                lock_label = "Unlock" if s.get("locked") else "Lock"
                lock_color = "#eab308" if s.get("locked") else "#6366f1"
                
                is_exported = s.get('exported', False)
                exp_badge = '<br><span style="color:#bbf7d0;background:#166534;padding:2px 4px;border-radius:3px;font-size:0.7rem;font-weight:bold;display:inline-block;margin-top:4px;">[EXPORTED]</span>' if is_exported else ''
                status_badge = '<span style="color:#eab308;font-size:0.75rem;font-weight:bold;">[LOCKED]</span>' if s.get("locked") else ''
                loader_btn = f'<a href="/{esc_sid}.export/_Loader.html" target="_blank"><button style="padding: 6px 12px; font-size: 0.8rem; background:#8b5cf6; margin-left:4px;">Try Loader</button></a>' if is_exported else ''
                
                session_browse_url = f"http://localhost:{port}/{esc_sid}/{esc_dom}/"

                rows_html += f"""
                    <tr>
                        <td><b>{esc_sid}</b> {status_badge}{exp_badge}</td>
                        <td><a href="{session_browse_url}" target="_blank" style="color: #38bdf8; text-decoration: none;">{esc_dom}</a></td>
                        <td style="color: #94a3b8; font-size: 0.85rem; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{esc_rec}</td>
                        <td><span class="badge">{st['files']} files ({html_escape(st['size_str'])})</span></td>
                        <td>
                            <a href="{session_browse_url}" target="_blank"><button style="padding: 6px 12px; font-size: 0.8rem;">Browse</button></a>
                            <button onclick="toggleLock('{esc_sid}')" style="padding: 6px 12px; font-size: 0.8rem; background: {lock_color};">{lock_label}</button>
                            <button id="exp-{esc_sid}" onclick="exportSession('{esc_sid}')" style="padding: 6px 12px; font-size: 0.8rem; background: #16a34a;">Export</button>
                            <button id="del-{esc_sid}" onclick="deleteSession('{esc_sid}', {str(is_exported).lower()})" class="danger" style="padding: 6px 12px; font-size: 0.8rem;">Cleanup / Delete</button>
                            {loader_btn}
                        </td>
                    </tr>"""

        page = f"""<!DOCTYPE html>
<html>
<head>
    <title>RipWeb Enterprise - Web Console</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 40px; }}
        .container {{ max-width: 1000px; margin: 0 auto; }}
        h1 {{ color: #38bdf8; font-size: 2.2rem; margin-bottom: 5px; }}
        p.subtitle {{ color: #94a3b8; margin-top: 0; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
        .form-group {{ display: flex; gap: 12px; margin-top: 15px; }}
        input[type="text"] {{ flex: 1; background: #0f172a; border: 1px solid #475569; color: #fff; padding: 12px 16px; border-radius: 8px; font-size: 1rem; }}
        button {{ background: #0284c7; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; cursor: pointer; transition: background 0.2s; }}
        button:hover {{ background: #0369a1; }}
        button.danger {{ background: #dc2626; }}
        button.danger:hover {{ background: #b91c1c; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid #334155; }}
        th {{ color: #94a3b8; font-size: 0.85rem; text-transform: uppercase; }}
        .badge {{ background: #0369a1; color: #bae6fd; padding: 4px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>RipWeb Enterprise</h1>
        <p class="subtitle">Multi-Site Namespace Gateway &amp; Web Console</p>

        <div class="card">
            <h2>Start New Archiving Session</h2>
            <form action="/_ripweb/add-session" method="POST">
                <div class="form-group">
                    <input type="text" name="url" placeholder="Enter target website URL (e.g. https://www.wikipedia.org)" required autocomplete="off">
                    <button type="submit">Start Session &amp; Browse</button>
                </div>
            </form>
        </div>

        <div class="card">
            <h2>Active Workspace Sessions</h2>
            <table>
                <thead>
                    <tr>
                        <th>Session ID</th>
                        <th>Primary Site</th>
                        <th>Recorded Domains</th>
                        <th>Files / Size</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>{rows_html}
                </tbody>
            </table>
        </div>
    </div>
    <script>
        function exportSession(sid) {{
            var comp = confirm("Apply Deflate compression?\\n\\n- [OK] Standard compressed ZIP.\\n- [Cancel] Uncompressed ZIP (Store) - Faster export, larger size.\\n\\nNote: Advanced formats like 7z are not natively supported by browsers for offline loading.") ? "deflate" : "store";
            var btn = document.getElementById('exp-' + sid);
            btn.innerText = "Exporting...";
            btn.style.pointerEvents = "none";
            fetch('/_ripweb/export-session', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                body: 'sid=' + encodeURIComponent(sid) + '&compression=' + encodeURIComponent(comp)
            }}).then(function(r) {{ return r.json(); }}).then(function(res) {{
                btn.innerText = "Done!";
                setTimeout(function() {{ location.reload(); }}, 1000);
            }});
        }}
        
        var deleteConfirm = {{}};
        function deleteSession(sid, isExported) {{
            var btn = document.getElementById('del-' + sid);
            if (!isExported && !deleteConfirm[sid]) {{
                btn.innerText = "Not Exported! Click Again";
                btn.style.background = "#991b1b";
                deleteConfirm[sid] = true;
                setTimeout(function() {{ deleteConfirm[sid] = false; btn.innerText = "Cleanup / Delete"; btn.style.background = "#dc2626"; }}, 3000);
                return;
            }}
            if (isExported && !deleteConfirm[sid]) {{
                btn.innerText = "Confirm Delete?";
                deleteConfirm[sid] = true;
                setTimeout(function() {{ deleteConfirm[sid] = false; btn.innerText = "Cleanup / Delete"; }}, 3000);
                return;
            }}
            
            btn.innerText = "Deleting...";
            fetch('/_ripweb/delete-session', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                body: 'sid=' + encodeURIComponent(sid)
            }}).then(function(r) {{ return r.json(); }}).then(function() {{
                location.reload();
            }});
        }}
        
        function toggleLock(sid) {{
            fetch('/_ripweb/toggle-lock', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                body: 'sid=' + encodeURIComponent(sid)
            }}).then(function(r) {{ return r.json(); }}).then(function() {{
                location.reload();
            }});
        }}
    </script>
</body>
</html>"""

        body = page.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_interstitial(self, query):
        params = urllib.parse.parse_qs(query)
        session_id = html_escape(params.get('session', [''])[0], quote=True)
        target_url = params.get('target', [''])[0]
        esc_target = html_escape(target_url, quote=True)

        parsed = urlparse(target_url)
        target_domain = html_escape(parsed.netloc, quote=True)
        new_session_id = make_session_id(parsed.netloc)
        ws_dir = html_escape(self.server.workspace.workspace_dir)

        page = f"""<!DOCTYPE html>
<html>
<head>
    <title>Cross-Domain Navigation Intercepted - RipWeb</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 40px; display: flex; justify-content: center; align-items: center; min-height: 80vh; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 32px; max-width: 620px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }}
        h1 {{ color: #38bdf8; font-size: 1.8rem; margin-top: 0; }}
        p {{ color: #cbd5e1; line-height: 1.5; }}
        .target-box {{ background: #0f172a; padding: 12px 16px; border-radius: 8px; border: 1px solid #334155; word-break: break-all; color: #38bdf8; font-family: monospace; font-size: 0.95rem; margin: 16px 0; }}
        .btn {{ display: block; width: 100%; padding: 14px 18px; border-radius: 8px; border: none; font-size: 0.95rem; font-weight: bold; cursor: pointer; text-align: left; margin-bottom: 12px; transition: background 0.2s; }}
        .btn-primary {{ background: #0284c7; color: white; }}
        .btn-primary:hover {{ background: #0369a1; }}
        .btn-secondary {{ background: #16a34a; color: white; }}
        .btn-secondary:hover {{ background: #15803d; }}
        .btn-cancel {{ background: #475569; color: white; text-align: center; }}
        .btn-cancel:hover {{ background: #334155; }}
        .subtext {{ display: block; font-size: 0.8rem; font-weight: normal; color: #e2e8f0; margin-top: 4px; }}
        .badge {{ background: #0369a1; color: #bae6fd; padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Cross-Domain Navigation Intercepted</h1>
        <p>You clicked a link pointing outside current session <span class="badge">{session_id}</span>:</p>
        <div class="target-box">{esc_target}</div>
        <p>Would you like to record this website under your current session, or start a new archiving session?</p>
        <form action="/_ripweb/interstitial-action" method="POST">
            <input type="hidden" name="action" value="record_current">
            <input type="hidden" name="session" value="{session_id}">
            <input type="hidden" name="target" value="{esc_target}">
            <button type="submit" class="btn btn-primary">
                1. Record to Current Session ({session_id})
                <span class="subtext">Saves to: {ws_dir}\\{session_id}\\{target_domain}\\</span>
            </button>
        </form>
        <form action="/_ripweb/interstitial-action" method="POST">
            <input type="hidden" name="action" value="new_session">
            <input type="hidden" name="target" value="{esc_target}">
            <button type="submit" class="btn btn-secondary">
                2. Start New Session ({new_session_id})
                <span class="subtext">Saves to: {ws_dir}\\{new_session_id}\\{target_domain}\\</span>
            </button>
        </form>
        <button onclick="window.history.back()" class="btn btn-cancel">3. Cancel / Stay on Current Page</button>
    </div>
</body>
</html>"""
        body = page.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_queue_status(self):
        query = urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        filter_sid = params.get('session_id', [None])[0] or params.get('proj_id', [None])[0]
        raw_url = params.get('url', [''])[0]

        if filter_sid and raw_url:
            parsed = urlparse(raw_url)
            _, dom, p_rest = extract_session_and_domain(parsed.path)
            real_url = f"https://{dom}{p_rest}" if dom else raw_url
            if parsed.query: real_url += f"?{parsed.query}"
            self.server.active_browser_views.setdefault(filter_sid, {})
            self.server.active_browser_views[filter_sid][real_url] = time.time()

        with self.server.queue_lock:
            queue_list = []
            for url, item in self.server.download_queue.items():
                if filter_sid and item.get("session_id") != filter_sid:
                    continue
                queue_list.append({
                    "url": url,
                    "name": item["name"],
                    "size": item["size"],
                    "speed": item["speed"],
                    "status": item["status"],
                    "error": item["error"],
                })
        self._send_json({"queue": queue_list})

    def _build_web_downloader(self, current_session, current_domain):
        """Constructs the Web Downloader floating widget with checkboxes, filtering, and click-to-open logic."""
        safe_domain = html_escape(current_domain, quote=True)
        safe_session = html_escape(current_session, quote=True)
        js_session = js_string_escape(current_session)
        js_domain = js_string_escape(current_domain)

        session = self.server.workspace.get_session(current_session)
        is_locked = session.get('locked', False) if session else False
        lock_status_text = "LOCKED (Offline Test Mode)" if is_locked else "ACTIVE (Live Capture Mode)"
        lock_status_color = "#eab308" if is_locked else "#22c55e"
        lock_btn_text = "Unlock (Ingest)" if is_locked else "Lock (Offline)"
        lock_btn_color = "#16a34a" if is_locked else "#d97706"

        return f"""
<div id="ripweb-toolbar" style="position:fixed;bottom:10px;right:10px;z-index:999999;background:#1e293b;color:#f8fafc;padding:12px;border-radius:10px;box-shadow:0 10px 25px -5px rgba(0,0,0,0.5);font-family:sans-serif;font-size:13px;width:340px;border:1px solid #334155; max-height: 400px; display: flex; flex-direction: column;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-weight:bold;color:#38bdf8;">RipWeb Downloader</span>
        <span style="font-size:11px;color:#94a3b8;background:#0f172a;padding:2px 6px;border-radius:4px;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{safe_session}</span>
    </div>
    
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;padding:3px 6px;background:#0f172a;border-radius:4px;font-size:11px;">
        <span style="color:{lock_status_color};font-weight:bold;">● {lock_status_text}</span>
        <button id="rw-lock-btn" style="background:{lock_btn_color};color:#fff;border:none;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:bold;">{lock_btn_text}</button>
    </div>
    
    <div style="display:flex;gap:6px;margin-bottom:8px;">
        <button id="rw-scan" style="flex:1;background:#0284c7;color:#fff;border:none;padding:6px;border-radius:4px;cursor:pointer;font-weight:bold;">Scan Page</button>
        <button id="rw-dl" style="flex:1;background:#16a34a;color:#fff;border:none;padding:6px;border-radius:4px;cursor:pointer;font-weight:bold;">DL Selected</button>
    </div>
    <div style="display:flex;gap:6px;margin-bottom:8px;">
        <button id="rw-invert" style="flex:1;background:#475569;color:#fff;border:none;padding:6px;border-radius:4px;cursor:pointer;font-size:11px;">Invert Selection</button>
        <button id="rw-clear" style="flex:1;background:#64748b;color:#fff;border:none;padding:6px;border-radius:4px;cursor:pointer;font-size:11px;">Clear Finished</button>
    </div>
    <div style="margin-bottom:8px;">
        <input type="text" id="rw-filter" placeholder="Filter files by name..." style="width:100%; box-sizing:border-box; padding:6px; border-radius:4px; border:1px solid #475569; background:#0f172a; color:#fff; font-size:11px;">
    </div>
    
    <div id="rw-queue-box" style="flex:1;overflow-y:auto;background:#0f172a;border-radius:6px;padding:6px;font-size:11px;border:1px solid #334155; min-height: 120px;">
        <div style="color:#64748b;text-align:center;padding:10px;">Click 'Scan Page' to detect assets.</div>
    </div>
</div>
<script>
(function() {{
    var session = {js_session};
    var domain = {js_domain};
    var prefix = '/' + session + '/' + domain;

    if (window.fetch) {{
        var _origFetch = window.fetch;
        window.fetch = function(resource, init) {{
            if (typeof resource === 'string') {{
                if (resource.startsWith('/') && !resource.startsWith('/' + session + '/') && !resource.startsWith('/_ripweb/')) {{
                    resource = prefix + resource;
                }}
            }}
            return _origFetch.call(this, resource, init);
        }};
    }}
    if (window.XMLHttpRequest) {{
        var _origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {{
            if (typeof url === 'string') {{
                if (url.startsWith('/') && !url.startsWith('/' + session + '/') && !url.startsWith('/_ripweb/')) {{
                    url = prefix + url;
                }}
            }}
            return _origOpen.apply(this, arguments);
        }};
    }}
}})();

(function() {{
    var sessionId = {js_session};
    var currentDomain = {js_domain};
    var queueData = [];
    var checkedUrls = new Set();

    function renderQueue() {{
        var box = document.getElementById('rw-queue-box');
        if (!box) return;
        if (queueData.length === 0) {{
            box.innerHTML = '<div style="color:#64748b;text-align:center;padding:10px;">No items in download queue.</div>';
            return;
        }}
        var filterText = document.getElementById('rw-filter').value.toLowerCase();
        var html = '';
        
        queueData.forEach(function(item) {{
            if (filterText && item.name.toLowerCase().indexOf(filterText) === -1) return;
            
            var color = '#38bdf8';
            var icon = '⏳';
            if (item.status === 'finished') {{ color = '#22c55e'; icon = '✅'; }}
            else if (item.status === 'failed') {{ color = '#ef4444'; icon = '❌'; }}
            else if (item.status === 'downloading') {{ color = '#eab308'; icon = '🔄'; }}
            
            var isChecked = checkedUrls.has(item.url) ? 'checked' : '';
            var safeUrl = item.url.replace(/"/g, '&quot;');
            var safeName = item.name.replace(/</g, '&lt;');
            
            var linkTarget = item.url;
            try {{
                var u = new URL(item.url);
                if (u.hostname === 'localhost' || u.hostname === '127.0.0.1') {{
                    linkTarget = u.pathname + u.search;
                }} else {{
                    linkTarget = '/' + sessionId + '/' + u.hostname + u.pathname + u.search;
                }}
            }} catch(e) {{}}
            
            html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;border-bottom:1px solid #1e293b;">'
                + '<div style="display:flex;align-items:center;gap:4px;overflow:hidden;">'
                + '<input type="checkbox" class="rw-chk" value="' + safeUrl + '" ' + isChecked + '>'
                + '<span title="' + item.status + '" style="font-size:12px;cursor:help;">' + icon + '</span>'
                + '<a href="' + linkTarget + '" target="_blank" style="color:' + color + ';text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:140px;" title="' + safeName + '">' + safeName + '</a>'
                + '</div>'
                + '<span style="color:#94a3b8;font-size:10px;text-transform:capitalize;">' + item.status + '</span>'
                + '</div>';
        }});
        box.innerHTML = html;
        
        document.querySelectorAll('.rw-chk').forEach(function(cb) {{
            cb.addEventListener('change', function(e) {{
                if (e.target.checked) checkedUrls.add(e.target.value);
                else checkedUrls.delete(e.target.value);
            }});
        }});
    }}

    function updateQueueUI() {{
        var currentUrl = encodeURIComponent(window.location.href);
        fetch('/_ripweb/queue-status?session_id=' + encodeURIComponent(sessionId) + '&url=' + currentUrl)
            .then(function(r) {{ return r.json(); }})
            .then(function(data) {{
                queueData = data.queue;
                queueData.forEach(function(item) {{
                    if (item.status === 'pending' && !checkedUrls.has(item.url) && !item._seen) {{
                        checkedUrls.add(item.url);
                        item._seen = true;
                    }}
                }});
                renderQueue();
            }}).catch(function() {{}});
    }}

    document.getElementById('rw-scan').addEventListener('click', function() {{
        var urls = [];
        document.querySelectorAll('img, script, link[rel="stylesheet"], video, audio, a').forEach(function(el) {{
            var href = el.href || el.src;
            if (href) urls.push(href);
        }});
        fetch('/_ripweb/register-links', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ session_id: sessionId, urls: urls }})
        }}).then(function(r) {{ return r.json(); }}).then(function() {{
            updateQueueUI();
        }});
    }});

    document.getElementById('rw-dl').addEventListener('click', function() {{
        var selectedUrls = Array.from(checkedUrls);
        fetch('/_ripweb/bulk-download', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ session_id: sessionId, urls: selectedUrls }})
        }}).then(function() {{
            checkedUrls.clear();
            updateQueueUI();
        }});
    }});

    document.getElementById('rw-invert').addEventListener('click', function() {{
        document.querySelectorAll('.rw-chk').forEach(function(cb) {{
            cb.checked = !cb.checked;
            if (cb.checked) checkedUrls.add(cb.value);
            else checkedUrls.delete(cb.value);
        }});
    }});

    document.getElementById('rw-clear').addEventListener('click', function() {{
        fetch('/_ripweb/clear-finished', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ session_id: sessionId }})
        }}).then(function(r) {{ return r.json(); }}).then(function() {{
            updateQueueUI();
        }});
    }});

    document.getElementById('rw-filter').addEventListener('keyup', renderQueue);

    var lockBtn = document.getElementById('rw-lock-btn');
    if (lockBtn) {{
        lockBtn.addEventListener('click', function() {{
            fetch('/_ripweb/toggle-lock', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                body: 'sid=' + encodeURIComponent(sessionId)
            }}).then(function(r) {{ return r.json(); }}).then(function(res) {{
                location.reload();
            }});
        }});
    }}

    setInterval(updateQueueUI, 2000);
}})();
</script>
"""

# ---------------------------------------------------------------------------
# Text Console (Interactive Terminal Interface)
# ---------------------------------------------------------------------------

class TextConsoleDisplay:
    def __init__(self, server, update_interval=CONSOLE_REFRESH_INTERVAL):
        self.server = server
        self.update_interval = update_interval
        self.running = True
        self.history = []
        self.view_mode = "dashboard"
        self.status_message = "Ready. Use 1-key commands."
        self.render_lock = threading.Lock()
        self.need_full_clear = True
        
        if getattr(self.server, 'verbose', False):
            print(f"{GREEN}[*] Verbose mode enabled. Terminal dashboard disabled to preserve logs.{RESET}")
            print(f"{YELLOW}[*] Press 'Q' to quit. Press 'C' to clear finished queue items.{RESET}\n")
            self.thread = None
        else:
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False

    def set_status(self, message):
        self.status_message = message

    def _run_loop(self):
        while self.running:
            self.render()
            time.sleep(self.update_interval)

    def render(self):
        if getattr(self.server, 'verbose', False):
            return
            
        with self.render_lock:
            buf = []
            if self.need_full_clear:
                buf.append("\033[2J\033[H")
                self.need_full_clear = False
            else:
                buf.append("\033[H")

            if self.view_mode == "help":
                lines = self._render_help_view()
            elif self.view_mode == "export":
                lines = self._render_export_view()
            elif self.view_mode == "lock":
                lines = self._render_lock_view()
            else:
                lines = self._render_dashboard_view()

            flat_lines = []
            for line in lines:
                flat_lines.extend(line.split('\n'))

            for i, line in enumerate(flat_lines):
                if i < len(flat_lines) - 1:
                    buf.append(f"{line}{CLEAR_EOL}\n")
                else:
                    buf.append(f"{line}{CLEAR_EOL}\033[0J")

            sys.stdout.write("".join(buf))
            sys.stdout.flush()

    def _render_dashboard_view(self):
        lines = []
        port = self.server.server_port
        ws_dir = self.server.workspace.workspace_dir
        paused = getattr(self.server, 'downloader_paused', False)
        dl_status = f"{RED}PAUSED{RESET}" if paused else f"{GREEN}ACTIVE{RESET}"

        now = time.time()
        
        if self.server.browser_recovering and now - self.server.browser_recovery_start > 4:
            self.server.browser_recovering = False
        if self.server.remote_recovering and now - self.server.remote_recovery_start > 4:
            self.server.remote_recovering = False

        remote_stalled = False
        with self.server.queue_lock:
            for item in self.server.download_queue.values():
                if item["status"] == "downloading":
                    if now - item.get("last_progress_time", now) > 5.0:
                        remote_stalled = True
                        break

        def get_state(ok, rec, stalled):
            if not ok: return "error"
            if rec: return "recovering"
            if stalled: return "stalled"
            return "ok"
            
        b_state = get_state(self.server.browser_conn_ok, self.server.browser_recovering, False)
        r_state = get_state(self.server.remote_conn_ok, self.server.remote_recovering, remote_stalled)
        
        colors = {"ok": GREEN, "error": RED, "recovering": YELLOW, "stalled": CYAN}
        arrows = {"ok": "<==>", "error": "<-X->", "recovering": "<-~->", "stalled": "<-?->"}
        severity = {"error": 3, "stalled": 2, "recovering": 1, "ok": 0}
        
        p_state = b_state if severity[b_state] > severity[r_state] else r_state
        
        b_col, r_col, p_col = colors[b_state], colors[r_state], colors[p_state]
        b_arr, r_arr = arrows[b_state], arrows[r_state]

        health_str = f"[{b_col}Browser{RESET}] {b_col}{b_arr}{RESET} [{p_col}HTTP Proxy{RESET}] {r_col}{r_arr}{RESET} [{r_col}Remote Session{RESET}]"

        lines.append(f"{BOLD}{GREEN}=== RipWeb Enterprise - Text Console ==={RESET}  Workspace: {CYAN}{ws_dir}{RESET}")
        lines.append(f"Status: {BOLD}{GREEN}ONLINE{RESET} | Console: {CYAN}http://localhost:{port}/{RESET} | DL: {dl_status} | {health_str}\n")

        active_sid = self.server.workspace.active_session_id
        session = self.server.workspace.get_session(active_sid) if active_sid else None
        if session:
            primary_dom = session['primary_domain']
            session_url = f"http://localhost:{port}/{active_sid}/{primary_dom}/"
            storage_path = os.path.join(ws_dir, active_sid)
            lines.append(f"{BOLD}{CYAN}-- ACTIVE SESSION: {YELLOW}{active_sid}{CYAN} --{RESET}")
            lines.append(f"  Session URL:  {BOLD}{GREEN}{session_url}{RESET}")
            lines.append(f"  Target Site:  https://{primary_dom}/")
            lines.append(f"  Disk Storage: {storage_path}\\")
            rec_doms = session.get("recorded_domains", [])
            lines.append(f"  Recorded ({len(rec_doms)}): {', '.join(rec_doms)}\n")
        else:
            lines.append(f"{BOLD}{CYAN}-- NO ACTIVE SESSION --{RESET}")
            lines.append(f"  Start a session via Web Console or pass a URL on startup.\n")

        now = time.time()
        for sid, views in list(self.server.active_browser_views.items()):
            for url, timestamp in list(views.items()):
                if now - timestamp > 6:
                    del views[url]
            if not views:
                del self.server.active_browser_views[sid]

        lines.append(f"{BOLD}{CYAN}-- ACTIVE CONNECTIONS --{RESET}")
        by_session = {}
        active_sids = set(self.server.active_browser_views.keys())
        
        with self.server.queue_lock:
            for _, item in self.server.download_queue.items():
                if item["status"] == "downloading":
                    sid = item.get("session_id") or "Unknown"
                    by_session.setdefault(sid, []).append(item)
                    active_sids.add(sid)

        if not active_sids:
            lines.append(f"  {DIM}(No active connections){RESET}")
        else:
            for sid in sorted(active_sids):
                lines.append(f"  {BOLD}{CYAN}[Session: {sid}]{RESET}")
                
                views = self.server.active_browser_views.get(sid, {})
                for v_url in views.keys():
                    lines.append(f"    [{b_col}Browser Tab{RESET}] Navigated: {YELLOW}{v_url}{RESET}")
                
                items = by_session.get(sid, [])
                if items:
                    lines.append(f"    [{YELLOW}HTTP Proxy{RESET}] Downloading {len(items)} assets...")
                    by_dom = {}
                    for item in items:
                        by_dom.setdefault(item['domain'], []).append(item)
                    for dom, dom_items in by_dom.items():
                        lines.append(f"      [{r_col}Remote: {dom}{RESET}]")
                        for item in dom_items:
                            lines.append(f"        -> {item['name']:<24} | Size: {format_size(item['size']):<10} | Speed: {format_speed(item['speed'])}")

        lines.append(f"\n{BOLD}{CYAN}-- RECENT DOWNLOAD HISTORY --{RESET}")
        lines.append(f"  {'Time':<10} {'Filename':<32} {'File Size/Status':<18} {'Speed/Error Info'}")
        
        term_width = shutil.get_terminal_size((100, 20)).columns
        sep_len = max(50, term_width - 4)
        lines.append("  " + "-" * sep_len)

        with self.server.queue_lock:
            hist_items = list(self.history[:6])
        if not hist_items:
            lines.append(f"  {DIM}(No downloads recorded yet){RESET}")
        else:
            for item in hist_items:
                if item.get('error'):
                    lines.append(f"  {item['time']:<10} {RED}{smart_truncate(item['name'], 30):<32}{RESET} {RED}FAILED{RESET}           {smart_truncate(item['error'], 50)}")
                else:
                    lines.append(f"  {item['time']:<10} {smart_truncate(item['name'], 32):<32} {format_size(item['size']):<18} {format_speed(item['speed'])}")

        if self.status_message:
            lines.append(f"\n{BOLD}{MAGENTA}[STATUS]{RESET} {self.status_message}")

        lines.append(f"\n{BOLD}{YELLOW}COMMANDS:{RESET} [{GREEN}B{RESET}]rowse | [{GREEN}O{RESET}]pen Console | [{GREEN}E{RESET}]xport | [{GREEN}L{RESET}]ock | [{GREEN}C{RESET}]lear | [{GREEN}P{RESET}]ause | [{GREEN}H{RESET}]elp | [{GREEN}R{RESET}]edraw | [{GREEN}V{RESET}]erbose | [{GREEN}Q{RESET}]uit")
        lines.append(f"RipWeb Console > ")
        return lines

    def _render_export_view(self):
        lines = []
        lines.append(f"{BOLD}{CYAN}=== RipWeb Enterprise - Export Session Package ==={RESET}\n")
        sessions = list(self.server.workspace.data.get("sessions", {}).items())

        if not sessions:
            lines.append(f"  {DIM}(No active sessions in workspace){RESET}\n")
            lines.append("Press any key to return to Text Console dashboard > ")
            return lines

        stats = self.server.workspace.get_stats()
        lines.append("Select a session to export (Instant Single-Keypress):\n")
        for idx, (sid, s) in enumerate(sessions, 1):
            st = stats.get(sid, {"files": 0, "size_str": "0 B"})
            key_tag = f"[{GREEN}{idx}{RESET}]" if idx <= 9 else f"[{idx}]"
            lines.append(f"  {key_tag} {sid:<30} ({st['files']} files, {st['size_str']})")

        lines.append(f"\n  [{GREEN}A{RESET}] Export All Sessions")
        lines.append(f"  [{GREEN}Esc{RESET}] / [{GREEN}C{RESET}] Cancel and return to dashboard\n")
        lines.append("Select option (1-9, A, Esc) > ")
        return lines

    def _render_lock_view(self):
        lines = []
        lines.append(f"{BOLD}{CYAN}=== RipWeb Enterprise - Session Lock / Cache Mode ==={RESET}\n")
        sessions = list(self.server.workspace.data.get("sessions", {}).items())

        if not sessions:
            lines.append(f"  {DIM}(No active sessions in workspace){RESET}\n")
            lines.append("Press any key to return to Text Console dashboard > ")
            return lines

        lines.append("Select a session to toggle lock mode (Instant Single-Keypress):\n")
        for idx, (sid, s) in enumerate(sessions, 1):
            is_locked = s.get('locked', False)
            status_text = f"{RED}LOCKED (Offline only){RESET}" if is_locked else f"{GREEN}UNLOCKED (Live proxying){RESET}"
            key_tag = f"[{GREEN}{idx}{RESET}]" if idx <= 9 else f"[{idx}]"
            lines.append(f"  {key_tag} {sid:<30} - Current: {status_text}")

        lines.append(f"\n  [{GREEN}Esc{RESET}] / [{GREEN}C{RESET}] Cancel and return to dashboard\n")
        lines.append("Select session to toggle (1-9, Esc) > ")
        return lines

    def _render_help_view(self):
        port = self.server.server_port
        lines = []
        lines.append(f"{BOLD}{CYAN}=== RipWeb Enterprise Documentation & User Guide ==={RESET}\n")
        lines.append(f"{BOLD}THREE USER INTERFACES:{RESET}")
        lines.append(f" 1. {BOLD}Text Console{RESET}   - The interactive terminal dashboard currently running.")
        lines.append(f" 2. {BOLD}Web Console{RESET}    - Web management dashboard at {CYAN}http://localhost:{port}/{RESET}")
        lines.append(f" 3. {BOLD}Web Downloader{RESET} - Floating toolbar injected into browsed sites for scanning/downloads.\n")
        lines.append(f"{BOLD}SESSIONS & DIRECTORY STRUCTURE:{RESET}")
        lines.append("  Every archiving session creates a directory named after the primary site:")
        lines.append(f"    {CYAN}{{workspace}}/www.website.com.rip/www.website.com/{{assets...}}{RESET}")
        lines.append("  If you visit an external domain and choose to record it to the current session:")
        lines.append(f"    {CYAN}{{workspace}}/www.website.com.rip/www.otherwebsite.com/{{assets...}}{RESET}\n")
        lines.append(f"{BOLD}TEXT CONSOLE SHORTCUTS (Instant Single-Keypress):{RESET}")
        lines.append(f"  {GREEN}B{RESET} - Browse current active session URL in your default browser.")
        lines.append(f"  {GREEN}O{RESET} - Open Web Console in your default browser.")
        lines.append(f"  {GREEN}E{RESET} - Export session archive to zip.")
        lines.append(f"  {GREEN}L{RESET} - Lock/Unlock session cache (offline mode).")
        lines.append(f"  {GREEN}C{RESET} - Clear finished and failed items from download queue.")
        lines.append(f"  {GREEN}P{RESET} - Pause or resume the background asset downloader.")
        lines.append(f"  {GREEN}H{RESET} - Toggle this help manual.")
        lines.append(f"  {GREEN}R{RESET} - Redraw the screen to clear artifacts or fix resize layout.")
        lines.append(f"  {GREEN}V{RESET} - Toggle verbose HTTP logging mode.")
        lines.append(f"  {GREEN}Q{RESET} - Stop the server and exit cleanly.\n")
        lines.append(f"{BOLD}Press any key (or [H], [Esc], Space) to return to Text Console dashboard >{RESET} ")
        return lines

    def handle_keypress(self, cmd):
        if not cmd:
            return None

        if getattr(self.server, 'verbose', False):
            if cmd == 'q': 
                return 'quit'
            if cmd == 'v':
                self.server.verbose = False
                self.thread = threading.Thread(target=self._run_loop, daemon=True)
                self.thread.start()
                return None
            if cmd == 'c':
                with self.server.queue_lock:
                    to_remove = [u for u, item in self.server.download_queue.items()
                                 if item["status"] in ("finished", "failed")]
                    for u in to_remove:
                        del self.server.download_queue[u]
                print(f"{GREEN}[*] Cleared {len(to_remove)} finished items from queue.{RESET}")
            return None

        if self.view_mode == "help":
            self.view_mode = "dashboard"
            self.need_full_clear = True
            self.set_status("Returned to Text Console dashboard.")
            self.render()
            return None

        if self.view_mode == "export":
            sessions = list(self.server.workspace.data.get("sessions", {}).keys())
            if cmd in ('esc', 'c', 'q', 'enter', 'space'):
                self.view_mode = "dashboard"
                self.need_full_clear = True
                self.set_status("Export cancelled.")
            elif cmd == 'a':
                count = 0
                for sid in sessions:
                    if self.server.workspace.export_session(sid):
                        count += 1
                self.view_mode = "dashboard"
                self.need_full_clear = True
                self.set_status(f"Exported all {count} sessions to workspace directory.")
            elif cmd.isdigit() and 1 <= int(cmd) <= len(sessions):
                idx = int(cmd) - 1
                sid = sessions[idx]
                zip_path = self.server.workspace.export_session(sid)
                self.view_mode = "dashboard"
                self.need_full_clear = True
                if zip_path:
                    self.set_status(f"Exported '{sid}' -> {zip_path}")
                else:
                    self.set_status(f"Failed to export session '{sid}'.")
            self.render()
            return None

        if self.view_mode == "lock":
            sessions = list(self.server.workspace.data.get("sessions", {}).keys())
            if cmd in ('esc', 'c', 'q', 'enter', 'space'):
                self.view_mode = "dashboard"
                self.need_full_clear = True
                self.set_status("Lock toggle cancelled.")
            elif cmd.isdigit() and 1 <= int(cmd) <= len(sessions):
                idx = int(cmd) - 1
                sid = sessions[idx]
                new_state = self.server.workspace.toggle_lock(sid)
                self.view_mode = "dashboard"
                self.need_full_clear = True
                lbl = "LOCKED (offline mode)" if new_state else "UNLOCKED (live proxying)"
                self.set_status(f"Session '{sid}' is now {lbl}.")
            self.render()
            return None

        if cmd == 'q':
            return 'quit'

        if cmd == 'b':
            active_sid = self.server.workspace.active_session_id
            session = self.server.workspace.get_session(active_sid) if active_sid else None
            if session:
                url = f"http://localhost:{self.server.server_port}/{active_sid}/{session['primary_domain']}/"
                try:
                    webbrowser.open(url)
                    self.set_status(f"Browsing active session ({url}).")
                except Exception as e:
                    self.set_status(f"Could not open browser: {e}")
            else:
                self.set_status("No active session to browse. Press [O] to start one in Web Console.")
            self.render()
            return None

        if cmd == 'o':
            url = f"http://localhost:{self.server.server_port}/"
            try:
                webbrowser.open(url)
                self.set_status(f"Opened Web Console ({url}) in browser.")
            except Exception as e:
                self.set_status(f"Could not open browser: {e}")
            self.render()
            return None

        if cmd == 'h':
            self.view_mode = "help"
            self.need_full_clear = True
            self.set_status("Displaying documentation manual.")
            self.render()
            return None

        if cmd == 'c':
            with self.server.queue_lock:
                to_remove = [u for u, item in self.server.download_queue.items()
                             if item["status"] in ("finished", "failed")]
                for u in to_remove:
                    del self.server.download_queue[u]
            self.set_status(f"Cleared {len(to_remove)} finished/failed downloads from queue.")
            self.render()
            return None

        if cmd == 'p':
            is_paused = not getattr(self.server, 'downloader_paused', False)
            self.server.downloader_paused = is_paused
            state = f"{RED}PAUSED{RESET}" if is_paused else f"{GREEN}RESUMED{RESET}"
            self.set_status(f"Background downloader {state}.")
            self.render()
            return None

        if cmd == 'e':
            sessions = list(self.server.workspace.data.get("sessions", {}).keys())
            if not sessions:
                self.set_status("No sessions found in workspace to export.")
            elif len(sessions) == 1:
                sid = sessions[0]
                zip_path = self.server.workspace.export_session(sid)
                if zip_path:
                    self.set_status(f"Exported '{sid}' -> {zip_path}")
                else:
                    self.set_status(f"Export error for session '{sid}'.")
            else:
                self.view_mode = "export"
                self.need_full_clear = True
                self.set_status("Select a session number to export.")
            self.render()
            return None

        if cmd == 'l':
            sessions = list(self.server.workspace.data.get("sessions", {}).keys())
            if not sessions:
                self.set_status("No sessions found in workspace.")
            elif len(sessions) == 1:
                sid = sessions[0]
                new_state = self.server.workspace.toggle_lock(sid)
                lbl = "LOCKED (offline mode)" if new_state else "UNLOCKED (live proxying)"
                self.set_status(f"Session '{sid}' is now {lbl}.")
            else:
                self.view_mode = "lock"
                self.need_full_clear = True
                self.set_status("Select a session number to toggle lock.")
            self.render()
            return None

        if cmd == 'r':
            self.need_full_clear = True
            self.set_status("Screen refreshed.")
            self.render()
            return None
            
        if cmd == 'v':
            self.server.verbose = True
            self.need_full_clear = True
            print(f"\n{GREEN}[*] Verbose HTTP logging enabled. Terminal UI paused.{RESET}")
            self.stop()
            return None

        return None

    def add_history(self, name, size, speed, error=None):
        with self.server.queue_lock:
            self.history.insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "name": name,
                "size": size,
                "speed": speed,
                "error": error,
            })
            if len(self.history) > MAX_HISTORY_ITEMS:
                self.history = self.history[:MAX_HISTORY_ITEMS]


# ---------------------------------------------------------------------------
# Gateway Server
# ---------------------------------------------------------------------------

class GatewayServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, workspace, verbose, no_dashboard):
        super().__init__(server_address, handler_class)
        self.workspace = workspace
        self.verbose = verbose
        self.no_dashboard = no_dashboard
        self.download_queue = {}
        self.queue_lock = threading.Lock()
        self.last_browser_headers = {}
        self.downloader_active = True
        self.downloader_paused = False
        
        self.browser_conn_ok = True
        self.browser_recovering = False
        self.browser_recovery_start = 0
        self.browser_last_error = None
        
        self.remote_conn_ok = True
        self.remote_recovering = False
        self.remote_recovery_start = 0
        self.remote_last_error = None
        
        self.active_browser_views = {}
        
        self._download_workers = []
        for _ in range(CONCURRENT_DOWNLOADS):
            t = threading.Thread(target=self._background_downloader, daemon=True)
            t.start()
            self._download_workers.append(t)

    def set_browser_state(self, ok, err=None):
        if ok:
            if not self.browser_conn_ok:
                self.browser_conn_ok = True
                self.browser_recovering = True
                self.browser_recovery_start = time.time()
        else:
            self.browser_conn_ok = False
            self.browser_last_error = err
            self.browser_recovering = False

    def set_remote_state(self, ok, err=None):
        if ok:
            if not self.remote_conn_ok:
                self.remote_conn_ok = True
                self.remote_recovering = True
                self.remote_recovery_start = time.time()
        else:
            self.remote_conn_ok = False
            self.remote_last_error = err
            self.remote_recovering = False

    def _background_downloader(self):
        while self.downloader_active:
            if self.downloader_paused:
                time.sleep(0.5)
                continue

            target_item = None
            with self.queue_lock:
                for url, item in self.download_queue.items():
                    if item["status"] == "pending":
                        session_id = item.get('session_id') or self.workspace.active_session_id or make_session_id(item['domain'])
                        session = self.workspace.get_session(session_id)
                        if session and session.get('locked', False):
                            continue
                        item["status"] = "downloading"
                        target_item = (url, item)
                        break

            if not target_item:
                time.sleep(0.3)
                continue

            url, item = target_item
            session_id = item.get('session_id') or self.workspace.active_session_id or make_session_id(item['domain'])
            domain = item['domain']
            path_rest = item['path_rest']
            
            real_url = f"https://{domain}{path_rest}"
            
            headers = item.get("headers", {}).copy()

            try:
                local_file = translate_workspace_path(self.workspace.workspace_dir, session_id, domain, path_rest)
            except ValueError:
                item["status"] = "failed"
                item["error"] = "Blocked: path traversal"
                continue

            headers = clean_headers_for_remote(headers, domain)
            req = urllib.request.Request(real_url, headers=headers)
            ctx = create_ssl_context()
            cookie_jar = self.workspace.get_cookie_jar(session_id)
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cookie_jar),
                urllib.request.HTTPSHandler(context=ctx)
            )

            start_time = time.time()
            downloaded_bytes = 0
            try:
                with opener.open(req, timeout=20) as resp:
                    self.set_remote_state(True)
                    
                    raw_data = resp.read()
                    enc = resp.headers.get('Content-Encoding', '').lower()
                    if 'gzip' in enc:
                        try: raw_data = gzip.decompress(raw_data)
                        except Exception: pass
                    elif 'deflate' in enc:
                        try: raw_data = zlib.decompress(raw_data)
                        except Exception: pass

                    ensure_parent_dir(local_file)
                    with open(local_file, 'wb') as fh:
                        fh.write(raw_data)
                        
                    downloaded_bytes = len(raw_data)
                    item["last_progress_time"] = time.time()
                    elapsed = time.time() - start_time
                    if elapsed > 0:
                        item["speed"] = downloaded_bytes / elapsed
                    item["size"] = downloaded_bytes

                    item["status"] = "finished"
                    if hasattr(self, 'console_display'):
                        self.console_display.add_history(item["name"], downloaded_bytes, item["speed"])
            except urllib.error.HTTPError as exc:
                self.set_remote_state(False, f"HTTP {exc.code}")
                item["status"] = "failed"
                item["error"] = f"HTTP {exc.code}: {exc.reason}"
                if hasattr(self, 'console_display'):
                    self.console_display.add_history(item["name"], 0, 0, error=item["error"])
            except Exception as exc:
                self.set_remote_state(False, str(exc))
                item["status"] = "failed"
                item["error"] = str(exc)
                if hasattr(self, 'console_display'):
                    self.console_display.add_history(item["name"], 0, 0, error=str(exc))

            self._prune_queue()

    def _prune_queue(self):
        with self.queue_lock:
            finished = [u for u, item in self.download_queue.items()
                        if item["status"] in ("finished", "failed")]
            if len(finished) > MAX_FINISHED_QUEUE_ITEMS:
                for u in finished[MAX_FINISHED_QUEUE_ITEMS:]:
                    del self.download_queue[u]


# ---------------------------------------------------------------------------
# CLI Help
# ---------------------------------------------------------------------------

def print_cli_help():
    print(f"\n{BOLD}{CYAN}=== RipWeb Enterprise Help Manual ==={RESET}")
    print("RipWeb Enterprise is a multi-site namespace gateway & session scraper proxy.\n")
    print(f"{BOLD}THREE USER INTERFACES:{RESET}")
    print("  1. Text Console   - Interactive terminal UI in your shell.")
    print("  2. Web Console    - Browser management dashboard at http://localhost:<port>/")
    print("  3. Web Downloader - Injected toolbar in web pages for scanning/downloads.\n")
    print(f"{BOLD}SESSION STORAGE ARCHITECTURE:{RESET}")
    print("  {workingdir}/{session_id}.rip/{primary_domain}/")
    print("  {workingdir}/{session_id}.rip/{other_domain}/  (if recorded to current session)\n")
    print(f"{BOLD}COMMAND LINE OPTIONS:{RESET}")
    print("  url                      Optional target website URL to start archiving session")
    print("  -u, --url URL            Specify target website URL to start session")
    print("  -p, --port PORT          Specify proxy server port (default: 8000)")
    print("  -w, --workspace DIR      Specify workspace directory for session archives")
    print("  -t, --temp DIR           Alias for --workspace")
    print("  --no-dashboard           Disable Web Console dashboard")
    print("  -v, --verbose            Enable verbose network logging")
    print("  -h, -?, --help           Display this help manual\n")
    print(f"{BOLD}EXAMPLE COMMANDS:{RESET}")
    print("    python ripweb_enterprise_active_connections.py https://www.wikipedia.org")
    print("    python ripweb_enterprise_active_connections.py -p 8000 -w F:\\web\\workingdir\n")


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('url_pos', nargs='?', default=None, help='Target website URL to archive')
    parser.add_argument('-u', '--url', type=str, default=None, help='Target website URL')
    parser.add_argument('-p', '--port', type=int, default=8000)
    parser.add_argument('-w', '-t', '--workspace', type=str, default='./ripweb_workspace')
    parser.add_argument('--no-dashboard', action='store_true', default=False)
    parser.add_argument('-v', '--verbose', action='store_true', default=False)
    parser.add_argument('-h', '-?', '--help', action='store_true')

    args = parser.parse_args()
    if args.help:
        print_cli_help()
        sys.exit(0)

    workspace = SessionManager(args.workspace)

    initial_url = args.url or args.url_pos
    if initial_url:
        sid, session = workspace.get_or_create_session(initial_url)
        print(f"{GREEN}[+] Started session: {sid} for {session['primary_domain']}{RESET}")

    server = GatewayServer(('127.0.0.1', args.port), RipWebHandler, workspace, args.verbose, args.no_dashboard)
    console = TextConsoleDisplay(server)
    server.console_display = console

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        while True:
            cmd = get_single_keypress()
            if cmd:
                action = console.handle_keypress(cmd)
                if action == 'quit':
                    sys.stdout.write(f"\n\n{GREEN}[*] Shutting down RipWeb Enterprise Gateway...{RESET}\n")
                    sys.stdout.flush()
                    server.downloader_active = False
                    server.shutdown()
                    console.stop()
                    break
            time.sleep(0.04)
    except KeyboardInterrupt:
        sys.stdout.write(f"\n\n{GREEN}[*] Interrupted. Shutting down server...{RESET}\n")
        sys.stdout.flush()
        server.downloader_active = False
        server.shutdown()
        console.stop()


WorkspaceManager = SessionManager

if __name__ == '__main__':
    main()
