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
from urllib.parse import urlparse, urljoin
import ssl

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
        super().end_headers()
        
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type, Accept")
        self.end_headers()

    def do_GET(self):
        translated = self.translate_path(self.path)
        
        # FIX: Force root directory requests to save as index.html
        if self.path.endswith('/'):
            translated = os.path.join(translated, 'index.html')
        
        if not os.path.exists(translated):
            if hasattr(self.server, 'base_target_url'):
                parsed_base = urlparse(self.server.base_target_url)
                path_stripped = self.path.lstrip('/')
                
                if path_stripped.startswith(parsed_base.netloc):
                    live_url = f"{parsed_base.scheme}://{path_stripped}"
                else:
                    live_url = urljoin(self.server.base_target_url, self.path)

                os.makedirs(os.path.dirname(translated), exist_ok=True)
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    
                    req = urllib.request.Request(live_url, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Accept': '*/*',
                        'Accept-Encoding': 'identity',
                        'Referer': self.server.base_target_url
                    })
                    with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
                        if response.getcode() == 200:
                            content_data = response.read()
                            with open(translated, 'wb') as f:
                                f.write(content_data)
                            if getattr(self.server, 'verbose_logging', False):
                                print(f"\n{CYAN}[+] Downloaded:{RESET} {live_url}", flush=True)
                            self.server.archive_needs_update = True
                except Exception as e:
                    if getattr(self.server, 'verbose_logging', False):
                        print(f"\n{RED}[-] Failed to load:{RESET} {live_url} - {e}", flush=True)
        
        super().do_GET()

def get_target_page_url(target, port):
    parsed = urlparse(target)
    path = parsed.path if parsed.path.startswith('/') else f"/{parsed.path}"
    return f"http://localhost:{port}/{parsed.netloc}{path}"

def main():
    parser = argparse.ArgumentParser(description="RipWeb - Offline Website Archiver & Server")
    parser.add_argument("target", help="Target URL")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Port to host on")
    parser.add_argument("-a", "--archive", default="site_clone.zip", help="Filename for the archive")
    parser.add_argument("-t", "--temp", help="Custom path for the temporary cache directory")
    parser.add_argument("-k", "--keep", action="store_true", help="Keep temporary cache files after exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose HTTP logging")

    args = parser.parse_args()
    archive_name = os.path.abspath(args.archive)
    
    if args.temp:
        serve_dir = os.path.abspath(args.temp)
        os.makedirs(serve_dir, exist_ok=True)
    else:
        serve_dir = tempfile.mkdtemp(prefix="ripweb_")

    print(f"\n{CYAN}Cache Directory:{RESET} {serve_dir}")

    original_cwd = os.getcwd()
    try:
        if os.path.exists(archive_name):
            print(f"{GREEN}[✓] Found existing {os.path.basename(archive_name)}.{RESET}", flush=True)
            unpack_archive_file(archive_name, serve_dir)
        else:
            print(f"{YELLOW}[!] {os.path.basename(archive_name)} not found. Proxy will build cache on the fly.{RESET}", flush=True)

        os.chdir(serve_dir)
        full_page_url = get_target_page_url(args.target, args.port)

        with ThreadingHTTPServer(("", args.port), SmartHTTPRequestHandler) as httpd:
            httpd.base_target_url = args.target
            httpd.archive_needs_update = False
            httpd.verbose_logging = args.verbose
            
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
        if 'httpd' in locals() and getattr(httpd, 'archive_needs_update', False):
            print(f"{CYAN}New assets lazy-loaded. Updating archive...{RESET}", flush=True)
            pack_archive_file(archive_name, serve_dir)
        else:
            print(f"{GREEN}No new files were downloaded. Archive left unchanged.{RESET}")
    finally:
        os.chdir(original_cwd)
        if args.keep:
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
