# The most complete project here so far is Ripweb. I will discuss others as they are finished or nearly finished.

[ripweb_enterprise_documentation.md](https://github.com/user-attachments/files/34450556/ripweb_enterprise_documentation.md)
# RipWeb Enterprise 🌐📦

**RipWeb Enterprise** is an advanced, self-contained, single-file multi-site namespace gateway, live scraper proxy, and offline packaging suite written in pure Python (utilizing only standard library modules with zero external dependencies). 

Whether you are archiving complex websites for offline research, preserving digital artifacts, or testing web application assets in a sandboxed local environment, RipWeb provides a seamless bridge between live web traffic and fully portable, standalone offline archives.

---

## 🚀 What Does RipWeb Enterprise Do?

Standard web scrapers often break because modern websites rely heavily on dynamic JavaScript, absolute CDN links, cross-domain cookie policies, and asynchronous asset loading. RipWeb operates as a **transparent man-in-the-middle proxy and namespace gateway**. 

When you browse the web through RipWeb:
1. **It intercepts and normalizes traffic** dynamically rewriting all asset links (`href`, `src`, `srcset`, `action`) so they route cleanly through local proxy namespaces (`/domain.com/path`).
2. **It builds a clean local cache directory structure** that mirrors exact domain hierarchies on your disk.
3. **It features a background asset download manager** that automatically crawls and queues linked resources (images, stylesheets, scripts, media) so your local mirror is complete.
4. **It bundles everything into a zero-setup standalone offline package** complete with a native WebAssembly/JavaScript ZIP parser (`_Loader.html`) that can render complex websites entirely out of a single `.zip` file without needing an active server or internet connection.

---

## 💎 Key Features & Capabilities

* **Multi-Site Namespace Gateway:** Seamlessly switch between multiple domains and subdomains under a single proxy port (`http://localhost:1337/domain1.com/` and `http://localhost:1337/domain2.org/`).
* **Standalone Offline VFS Loader:** Generates a lightweight `_Loader.html` sidecar that unpacks your `.zip` archive entirely in your browser's memory using native `DecompressionStream` APIs and constructs secure virtual `blob:` URLs.
* **Three Unified User Interfaces:**
  1. *The Terminal Text Console* (Real-time ANSI dashboard with single-keypress hotkeys).
  2. *The Web Management Console* (Browser-based control center for project management and exports).
  3. *The Web Downloader Toolbar* (Floating widget injected into browsed pages for real-time asset scanning and bulk downloading).
* **Cross-Domain Interstitial Guard:** Intercepts external link clicks and presents an intuitive decision page allowing you to record the new domain to your current project or spin up an isolated session.
* **Isolated `.export` Folders:** Neatly packages each export into a dedicated `{session_id}.export` directory containing both the `.zip` archive and the offline loader.
* **Flexible Compression Options:** Choose between standard Deflate compression (smaller file size) or Store compression (uncompressed, instant export) when packaging archives.
* **Safety Lock & Cleanup Protection:** Lock projects into offline read-only mode to prevent accidental overwrites, and protect unexported sessions with interactive warning guards before deletion.

---

## 📦 Installation & System Requirements

RipWeb Enterprise has **zero external Python dependencies**. It requires:
* **Python 3.8 or higher** (Python 3.10+ recommended).
* A modern web browser (Chrome, Firefox, Edge, Waterfox, Safari) for viewing and running the offline loader.

Simply download `ripweb_enterprise_active_connections.py` (or your customized script filename) and place it in your working directory.

---

## 🕹️ Command Line Options & Quick Start

You can launch RipWeb directly from your terminal. If you provide a target URL on startup, it will initialize the archiving session immediately.

### Basic Command
```bash
python ripweb_enterprise_active_connections.py https://www.wikipedia.org
```

### Advanced Command Example
```bash
python ripweb_enterprise_active_connections.py -p 1337 -w F:\web\downloads
```

### Complete CLI Options Reference
| Flag / Option | Description | Default |
| :--- | :--- | :--- |
| `url` (positional) | Optional target website URL to start archiving immediately | `None` |
| `-u`, `--url URL` | Target website URL to start archiving session | `None` |
| `-p`, `--port PORT` | Specify the local gateway proxy port | `8000` |
| `-w`, `-t`, `--workspace DIR` | Directory where session caches and `.export` folders are stored | `./ripweb_workspace` |
| `--no-dashboard` | Disables the root Web Management Console dashboard | `False` |
| `-v`, `--verbose` | Enables verbose HTTP network debugging logs in the terminal | `False` |
| `-h`, `-?`, `--help` | Displays the rich terminal help manual | `False` |

---

## 🖥️ The Three User Interfaces Explained

### 1. The Web Management Console (`http://localhost:<port>/`)
When you open your browser and navigate to `http://localhost:1337/`, you are greeted by the Enterprise Control Center. 

* **Start New Project:** Enter any URL to instantly spin up a new namespace session.
* **Active Workspace Projects Table:** Displays all active sessions, primary domains, recorded linked CDNs, total cached file counts, and storage sizes.
* **Action Buttons:**
  * **Browse:** Opens the live proxy view of the primary site.
  * **Lock / Unlock:** Toggles offline read-only mode (freezes cache so no new live requests are made).
  * **Export:** Opens compression choices and builds the standalone `.export` package.
  * **Cleanup / Delete:** Purges the disk cache with built-in safety guards preventing accidental deletion of unexported sessions.
  * **Try Loader (Purple Button):** Unlocks once exported, allowing you to test the offline HTML loader directly in a new tab.

---

### 2. The Injected Web Downloader Toolbar
When browsing any website through the RipWeb proxy, a floating toolbar (`RipWeb Downloader`) appears in the bottom-right corner of the page.

* **Live Status Indicator:** Shows whether the current session is `ACTIVE` (live capturing) or `LOCKED` (offline testing). Clicking the lock button toggles the state instantly.
* **Scan Page:** Automatically traverses the current DOM, harvesting all images, scripts, stylesheets, audio, video, and anchor links, registering them into the background download queue.
* **Interactive File Queue Box:** Displays captured assets with color-coded status icons (`⏳ Pending`, `🔄 Downloading`, `✅ Finished`, `❌ Failed`).
* **Checkboxes & Filters:** Check/uncheck specific assets or use the real-time name filter input to narrow down files before downloading.
* **DL Selected / Invert / Clear:** Queue selected assets for background downloading, invert your selection, or wipe completed items from the list.

---

### 3. The Terminal Text Console
If verbose mode is disabled, RipWeb runs a sleek, zero-flicker ANSI terminal dashboard that updates in real-time.

* **Health Telemetry:** Monitors the connection status between your browser, the local proxy, and remote target servers.
* **Active Connections & Transfer Speeds:** Live progress breakdown showing files currently downloading, transfer speeds, and file sizes.
* **Download History:** Scrollable log of the last 10 completed or failed file transfers.
* **Instant Single-Keypress Hotkeys (No Enter key required!):**
  * `[B]`rowse active session in default browser.
  * `[O]`pen Web Management Console.
  * `[E]`xport session package wizard.
  * `[L]`ock / Unlock session cache mode.
  * `[C]`lear finished queue items.
  * `[P]`ause or resume the background downloader.
  * `[H]`elp documentation manual.
  * `[R]`edraw/clear screen to fix terminal resize artifacts.
  * `[V]`erbose mode toggle.
  * `[Q]`uit server cleanly.

---

## 📦 Exporting and Offline Viewing Workflow

1. **Capture:** Browse your target website using RipWeb, click **Scan Page**, and use **DL Selected** to cache all necessary assets.
2. **Export:** Go to the Web Management Console (`http://localhost:1337/`), select your compression preference (`ZIP Compressed` or `ZIP Store`), and click **Export**.
3. **Storage:** RipWeb creates a folder named `{session_id}.export/` in your workspace containing:
   * `{session_id}.zip` (The complete compressed archive of all captured domains and assets).
   * `_Loader.html` (The standalone offline virtual filesystem viewer).
4. **Test:** Click the purple **Try Loader** button on the dashboard.
5. **Offline Share:** You can zip or copy the `{session_id}.export` folder anywhere, hand it to anyone, and they can double-click `_Loader.html` to browse the website completely offline!

---

## 🛡️ License & Architecture Notes

RipWeb Enterprise is designed for local archiving, educational research, and offline accessibility preservation. Always ensure you have permission or adhere to robots/terms of service when archiving public web domains.
