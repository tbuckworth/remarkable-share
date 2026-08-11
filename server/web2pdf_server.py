#!/usr/bin/env python3
"""Minimal server for Android share-to-reMarkable.

Accepts a URL, renders a clean PDF with headless Chrome, uploads via rmapi.
Runs on the always-on desktop, reachable from Android via Tailscale.
"""

import json
import logging
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn

# Import web2pdf functions
import sys
sys.path.insert(0, str(Path(__file__).parent))
from web2pdf import (
    fetch_page, extract_article, clean_html, to_pdf, send_to_remarkable,
    slugify, sanitize_filename,
    google_doc_id, fetch_google_doc, extract_google_doc,
)

# --- Config ---

CONFIG_DIR = Path.home() / ".config" / "web2pdf"
CONFIG_FILE = CONFIG_DIR / "config.json"
PORT = 8417


def load_or_create_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    token = secrets.token_hex(16)
    config = {"token": token}
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    print(f"Generated auth token: {token}")
    return config


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

config = load_or_create_config()
AUTH_TOKEN = config["token"]

# --- App ---

app = FastAPI(title="web2pdf", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- Folder cache ---

_folder_cache: dict = {"data": None, "expires": 0}


def get_folders_list() -> list[str]:
    now = time.time()
    if _folder_cache["data"] is not None and now < _folder_cache["expires"]:
        return _folder_cache["data"]
    result = subprocess.run(["rmapi", "ls", "/"], capture_output=True, text=True, timeout=15)
    folders = []
    for line in result.stdout.strip().splitlines():
        if line.startswith("[d]"):
            name = line.split("\t", 1)[-1].strip()
            if name:
                folders.append(name)
    folders.sort()
    _folder_cache["data"] = folders
    _folder_cache["expires"] = now + 60
    return folders


# --- Auth ---

def verify_token(request: Request):
    auth = request.headers.get("Authorization", "")
    token_param = request.query_params.get("token", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else token_param
    if token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")



# --- Endpoints ---

WEB_UI = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send to reMarkable</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #fafafa; color: #111; padding: 20px; max-width: 480px; margin: 0 auto; }
  h1 { font-size: 20px; margin-bottom: 16px; }
  label { display: block; font-weight: 600; font-size: 14px; margin-bottom: 4px; margin-top: 12px; }
  input, select { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; background: #fff; }
  button { width: 100%; padding: 14px; background: #111; color: #fff; border: none; border-radius: 6px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 16px; }
  button:disabled { background: #999; }
  .status { margin-top: 12px; padding: 10px; border-radius: 6px; font-size: 14px; display: none; }
  .success { display: block; background: #dcfce7; color: #166534; }
  .error { display: block; background: #fef2f2; color: #991b1b; }
  .loading { display: block; background: #f0f4ff; color: #1e40af; }
</style>
</head><body>
<h1>Send to reMarkable</h1>
<label for="url">URL</label>
<input type="url" id="url" placeholder="Paste article URL">
<label for="folder">Folder</label>
<select id="folder"><option value="/">/</option></select>
<button id="send">Send to reMarkable</button>
<div class="status" id="status"></div>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || localStorage.getItem('token') || '';
if (TOKEN) localStorage.setItem('token', TOKEN);

const $ = id => document.getElementById(id);
const BASE = location.origin;

// Try to read clipboard on focus
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible' && !$('url').value) {
    try {
      const text = await navigator.clipboard.readText();
      if (text.match(/^https?:\\/\\//)) $('url').value = text;
    } catch(e) {}
  }
});

// Load folders
fetch(BASE + '/folders?token=' + TOKEN).then(r => r.json()).then(d => {
  const sel = $('folder');
  (d.folders || []).forEach(f => {
    const opt = document.createElement('option');
    opt.value = '/' + f;
    opt.textContent = f;
    sel.appendChild(opt);
  });
}).catch(() => {});

$('send').addEventListener('click', async () => {
  const url = $('url').value.trim();
  if (!url) { show('Enter a URL', 'error'); return; }
  $('send').disabled = true;
  $('send').textContent = 'Sending...';
  show('Converting and uploading...', 'loading');
  try {
    const resp = await fetch(BASE + '/convert', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + TOKEN },
      body: JSON.stringify({ url, folder: $('folder').value })
    });
    const data = await resp.json();
    if (resp.ok) { show('Sent: ' + data.title, 'success'); $('url').value = ''; }
    else show(data.detail || 'Error', 'error');
  } catch(e) { show('Network error: ' + e.message, 'error'); }
  $('send').disabled = false;
  $('send').textContent = 'Send to reMarkable';
});

function show(msg, cls) { const el = $('status'); el.textContent = msg; el.className = 'status ' + cls; }
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def web_ui():
    return WEB_UI


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/folders", dependencies=[Depends(verify_token)])
def list_folders():
    return {"folders": get_folders_list()}


@app.get("/convert", dependencies=[Depends(verify_token)])
def convert_get(request: Request, url: str = "", folder: str = "/"):
    """GET: /convert?token=...&url=...&folder=..."""
    # Also check if url was passed without the param name (raw query string)
    if not url:
        raise HTTPException(status_code=400, detail="'url' query param required")
    return do_convert(url, folder)


@app.post("/convert", dependencies=[Depends(verify_token)])
async def convert_post(request: Request):
    """POST: accepts JSON, form data, or query params."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    elif "form" in content_type:
        form = await request.form()
        data = dict(form)
    else:
        data = dict(request.query_params)

    url = data.get("url", "").strip()
    folder = data.get("folder", "/").strip()

    if not url:
        raise HTTPException(status_code=400, detail="'url' is required")
    return do_convert(url, folder)


def do_convert(url: str, folder: str = "/"):
    import requests as req

    log = logging.getLogger("web2pdf")
    log.info(f"Converting: {url} -> folder={folder}")

    try:
        # Check if URL points to a PDF — pass through directly
        parsed = urlparse(url)
        is_pdf = parsed.path.lower().endswith(".pdf")
        if not is_pdf:
            # Check Content-Type with a HEAD request
            try:
                head = req.head(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, allow_redirects=True)
                if "application/pdf" in head.headers.get("Content-Type", ""):
                    is_pdf = True
            except Exception:
                pass

        if is_pdf:
            log.info(f"PDF detected, downloading directly: {url}")
            resp = req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            resp.raise_for_status()
            # Use filename from Content-Disposition, or build from URL path
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                filename = cd.split("filename=")[-1].strip('" ')
                filename = Path(filename).stem
            else:
                # Use full path minus leading slash, e.g. "pdf/2312.06942" -> "2312.06942"
                filename = parsed.path.strip("/").replace("/", "-")
                if filename.lower().endswith(".pdf"):
                    filename = filename[:-4]
            filename = (filename or "document")[:80]
            pdf_path = f"/tmp/{filename}.pdf"
            Path(pdf_path).write_bytes(resp.content)
            try:
                ok = send_to_remarkable(pdf_path, folder)
                if ok:
                    return {"status": "ok", "title": filename}
                else:
                    raise HTTPException(status_code=502, detail="rmapi upload failed")
            finally:
                Path(pdf_path).unlink(missing_ok=True)
            return  # unreachable but clear

        doc_id = google_doc_id(url)
        if doc_id:
            log.info(f"Google Doc detected ({doc_id}), using HTML export")
            raw_html = fetch_google_doc(doc_id)
            title, content = extract_google_doc(raw_html)
        else:
            raw_html = fetch_page(url)
            title, content = extract_article(raw_html, url)
        log.info(f"Extracted: {title} ({len(content)} chars)")
        final_html = clean_html(content, title, url, raw_html, font_size="14pt")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir="/tmp") as f:
            pdf_path = f.name

        to_pdf(final_html, pdf_path)

        # Rename to title for rmapi
        clean_title = sanitize_filename(title)

        # Check for duplicates
        if folder != "/":
            subprocess.run(["rmapi", "mkdir", folder], capture_output=True)
        ls_result = subprocess.run(["rmapi", "ls", folder], capture_output=True, text=True, timeout=15)
        existing = {line.split("\t", 1)[-1].strip() for line in ls_result.stdout.strip().splitlines()}
        doc_name = clean_title
        n = 2
        while doc_name in existing:
            doc_name = f"{clean_title} (v{n})"
            n += 1

        named_path = f"/tmp/{doc_name}.pdf"
        Path(pdf_path).rename(named_path)

        try:
            ok = send_to_remarkable(named_path, folder)
            if ok:
                return {"status": "ok", "title": doc_name}
            else:
                raise HTTPException(status_code=502, detail="rmapi upload failed")
        finally:
            Path(named_path).unlink(missing_ok=True)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print(f"Auth token: {AUTH_TOKEN}")
    print(f"Config: {CONFIG_FILE}")
    print(f"Tailscale: http://100.96.201.75:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
