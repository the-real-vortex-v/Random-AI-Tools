# Some tools made with AI "Vibe" coding. It's all about the "vibe" you know? They work and they work good enough. As long as it's a useful tool then who cares if it's working AI Slop?

# RipWeb Enterprise Archiver & Live Proxy

**RipWeb Enterprise** (`RipWeb_complex.py`) is an offline website archiver, proxy server, and web download manager. It acts as an on-the-fly local caching proxy while automatically stripping Content Security Policies (CSP) and injecting an in-browser enterprise download manager to capture media, documents, and assets during regular browsing.

---

## 1. Core Capabilities

* **On-The-Fly Web Archiving:** Proxies HTTP/HTTPS traffic through a local port, serving existing files from cache while transparently downloading and caching new assets.
* **In-Browser Toolbar Injection:** Automatically injects a fixed-position DOM toolbar into all served HTML documents, providing real-time controls for link scanning, queued downloading, and state locking.
* **Auto-Queueing & Intersection Observer:** Employs dynamic viewport detection (`IntersectionObserver`) to scan and queue media files as they scroll into view.
* **Automatic CSP & Security Mitigation:** Strips restrictive HTTP CSP headers, frame locks, and cross-origin security rules to allow uninhibited inline script execution and asset fetching.
* **HTTP Range & Media Support:** Full support for byte-range HTTP standard requests (`206 Partial Content`), allowing seekable audio/video streaming directly from the local archive.
* **Portable Offline Exporters:**
  * **Mode 1 (Directory Index):** Generates a static HTML sidebar browser for reviewing local files offline.
  * **Mode 2 (2-File JS-Jar Loader):** Packages the entire website into a compressed `.zip` or `.jar` archive and pairs it with a client-side JavaScript loader for pure browser-native RAM unzipping.

---

## 2. Command Line Interface (CLI) & Examples

### Usage Syntax
```bash
python RipWeb_complex.py <TARGET_URL> [options]

Flag,Full Name,Description,Default
-p,--port,Local port number to bind the HTTP proxy server.,8000
-a,--archive,Target path for compressed output archive (.zip or .7z).,Auto domain name
-t,--temp,Directory path for the uncompressed local cache directory.,System Temp
-s,--skip-compression,Keeps cache as an uncompressed folder on exit without zipping.,Disabled
-k,--keep,Preserves local cache directory on disk after exiting.,Disabled
-v,--verbose,Output verbose HTTP request and response stream lines.,Disabled
-dm,--download-manager,Toggles injected in-browser toolbar UI (on or off).,on
-l,--lazy-cache,Toggles writing proxied web assets directly to disk (on or off).,on
-c,--create-portable,Generates portable offline package without starting server (directory or jar).,None

# Execution Examples
Example 1: Standard Mirroring with Custom Directory Cache
Start archiving a target site on port 1337 and keep the uncompressed folder on drive F:

Bash:
python RipWeb_complex.py [https://archive.org/download/the-bill-complete-series-1-to-26/](https://archive.org/download/the-bill-complete-series-1-to-26/) -p 1337 -t F:\web\Internet_Archive -s

Example 2: Target Custom .7z Archive Output
Cache files to a 7-Zip file named media_archive.7z:

python RipWeb_complex.py [https://example.com/gallery](https://example.com/gallery) -a media_archive.7z

Example 3: Offline Portable Package Generation
Build a 2-file JS-Jar client-side browser loader from an existing archive:

python RipWeb_complex.py -a media_archive.zip -c jar

## 3. Console & Injected UI Controls
Terminal Dashboard Hotkeys (Press Key + Enter)
L / l (Lock): Freezes local site cache. Rejects any live requests to remote servers.

U / u (Unlock): Unfreezes local site cache, re-enabling live proxying.

E / e (Export): Triggers the offline portable wizard.

H / h (Help): Opens full CLI documentation.

Q / q (Quit): Gracefully stops the HTTP server, compresses the cache into the specified archive, and exits.

Browser Web Toolbar Buttons
Lazy-Queue: Toggles automatic links detection via viewport scrolling.

Auto-Download: Instantly begins downloading matched media items as soon as they enter the queue.

Lock Website: Toggles site lock state from within the browser window.

Scan Page: Manual DOM parse for media files (.mp4, .zip, .pdf, .png, etc.).

DL Selected: Commences downloading checked items in the toolbar drawer.

Resync Sizes: Sends HEAD requests to compare remote vs local byte lengths for verification.

Clear Finished: Purges validated downloads from the queue drawer list.

## 4. Technical Limitations
Single-Origin Proxying: RipWeb translates URLs tied to the target host domain. Multi-domain cross-origin links (e.g., media hosted across external third-party CDNs or S3 buckets) will bypass the local domain mapping unless rewritten explicitly.

Dynamic SPA / WebSockets: Heavy Single Page Applications relying on WebSocket streams, WebRTC, or runtime WebAssembly APIs may require manual interaction to trigger HTTP asset requests.

HTTP Authentication & CAPTCHAs: RipWeb does not automatically bypass Cloudflare CAPTCHAs or complex OAuth authentication flows unless pass-through cookies are already established in the client browser session.
