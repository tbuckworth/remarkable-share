#!/usr/bin/env python3
"""Native messaging host for Send to reMarkable Chrome extension.

Receives JSON messages from Chrome, renders clean PDFs with headless Chrome,
uploads to reMarkable via rmapi.
Protocol: 4-byte little-endian length prefix + JSON message.
"""

import base64
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse

# Chrome launches native hosts without the user's PATH
os.environ["PATH"] = os.path.expanduser("~/bin") + ":/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
# reMarkable Cloud rejects v3 root index writes since April 2026
os.environ["RMAPI_FORCE_SCHEMA_VERSION"] = "4"

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# rmapi names the document after the file, so the PDF must sit at <dir>/<doc name>.pdf.
PDF_DIR = "/tmp"

# Why-note provenance written into every uploaded PDF's document-info dictionary,
# for the desktop's nightly reader of the reMarkable cloud copies.
MODES = ("auto", "work", "personal")
NOTE_MAX_CHARS = 500

ARXIV_ID_RE = re.compile(
    r"(?:\d{4}\.\d{4,}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?$",
    re.IGNORECASE,
)
ATOM_NS = "http://www.w3.org/2005/Atom"

CLEAN_CSS = """
@page { size: A4; margin: 0; }
body {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 14pt; line-height: 1.5; color: #000;
    max-width: 100%; margin: 0; padding: 1.5cm 1.2cm;
}
h1 { font-size: 18pt; margin: 0 0 0.5em 0; line-height: 1.2; }
h2 { font-size: 14pt; margin: 1.2em 0 0.4em 0; }
h3 { font-size: 12pt; margin: 1em 0 0.3em 0; }
p { margin: 0.5em 0; text-align: justify; }
img { max-width: 100%; height: auto; margin: 0.5em 0; }
figure { margin: 0.5em 0; }
figcaption { font-size: 9pt; color: #444; font-style: italic; }
blockquote {
    border-left: 2pt solid #666; margin: 0.5em 0; padding: 0.2em 0 0.2em 0.8em; font-style: italic;
}
pre, code { font-family: 'Courier New', monospace; font-size: 9pt; background: #f5f5f5; padding: 0.1em 0.3em; }
pre { padding: 0.5em; white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; margin: 0.5em 0; }
th, td { border: 1px solid #ccc; padding: 0.3em 0.5em; font-size: 10pt; }
th { background: #f0f0f0; font-weight: bold; }
a { color: #000; text-decoration: underline; }
ul, ol { margin: 0.5em 0; padding-left: 1.5em; }
li { margin: 0.2em 0; }
.title-block { margin-bottom: 1em; border-bottom: 1px solid #ccc; padding-bottom: 0.5em; }
.title-block .meta { font-size: 9pt; color: #666; margin-top: 0.3em; }
.title-block .why { font-size: 11pt; font-style: italic; margin-top: 0.3em; }
"""

KATEX_HEAD = (
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/katex.min.css">\n'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/katex.min.js"></script>\n'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/contrib/auto-render.min.js"'
    ' onload="renderMathInElement(document.body,{delimiters:['
    '{left:\'$$\',right:\'$$\',display:true},'
    '{left:\'$\',right:\'$\',display:false}]});"></script>'
)


def log(message):
    # stdout carries the native-messaging protocol; diagnostics must go to stderr.
    print(f"web2pdf_host: {message}", file=sys.stderr, flush=True)


def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    length = struct.unpack("<I", raw_length)[0]
    data = sys.stdin.buffer.read(length)
    return json.loads(data)


def send_message(msg):
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def get_folders():
    result = subprocess.run(["rmapi", "ls", "/"], capture_output=True, text=True, timeout=15)
    folders = []
    for line in result.stdout.strip().splitlines():
        if line.startswith("[d]"):
            name = line.split("\t", 1)[-1].strip()
            if name:
                folders.append(name)
    folders.sort()
    return folders


def build_html(html_content, title, meta, note=""):
    """The page headless Chrome prints: title block, then the extracted content."""
    # Email subjects routinely contain & and <, which would corrupt the markup.
    safe_title = html.escape(title or "")
    safe_meta = html.escape(meta or "")
    note = (note or "").strip()
    why = f'\n<div class="why">Why: {html.escape(note)}</div>' if note else ""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CLEAN_CSS}</style>
{KATEX_HEAD}
</head>
<body>
<div class="title-block">
<h1>{safe_title}</h1>
<div class="meta">{safe_meta}</div>{why}
</div>
{html_content}
</body></html>"""


def render_pdf(html_content, title, meta, output_path, note=""):
    """Render clean HTML to PDF using headless Chrome."""
    full_html = build_html(html_content, title, meta, note)

    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False, encoding="utf-8") as f:
        f.write(full_html)
        tmp_html = f.name

    try:
        subprocess.run(
            [CHROME, "--headless=new", "--force-device-scale-factor=2.35", f"--print-to-pdf={output_path}", "--print-to-pdf-no-header", tmp_html],
            capture_output=True, text=True, timeout=60,
        )
        if not Path(output_path).exists():
            raise RuntimeError("Chrome PDF rendering failed")
    finally:
        Path(tmp_html).unlink(missing_ok=True)


def clean_note(note):
    """One line, bounded: the popup's input is single-line, but don't trust it."""
    return " ".join(str(note or "").split())[:NOTE_MAX_CHARS]


def clean_mode(mode):
    mode = str(mode or "").strip().lower()
    return mode if mode in MODES else "auto"


def clean_sent_at(sent_at, now=None):
    """ISO 8601 with a numeric UTC offset (2026-10-29T11:09:05+00:00).

    Python before 3.11 can't parse a trailing Z, and the reader may run on one, so
    normalise whatever the extension sent. Missing, naive or garbled -> host's now.
    """
    if sent_at:
        try:
            parsed = datetime.fromisoformat(re.sub(r"[Zz]$", "+00:00", str(sent_at).strip()))
            if parsed.tzinfo is not None:
                return parsed.isoformat(timespec="seconds")
        except ValueError:
            pass
    return (now or datetime.now().astimezone()).isoformat(timespec="seconds")


def read_provenance(msg, fallback_url=""):
    """Note, mode, source URL and send time from an upload message, cleaned."""
    return {
        "note": clean_note(msg.get("note")),
        "mode": clean_mode(msg.get("mode")),
        "source_url": str(msg.get("source_url") or fallback_url or ""),
        "sent_at": clean_sent_at(msg.get("sent_at")),
    }


def pdf_info(prov):
    """Document-info entries for a provenance dict. /Title is deliberately absent."""
    info = {
        "/Web2pdfNote": prov["note"],
        "/Web2pdfMode": prov["mode"],
        "/Web2pdfUrl": prov["source_url"],
        "/Web2pdfSentAt": prov["sent_at"],
    }
    if prov["note"]:
        info["/Subject"] = prov["note"]
    return info


def write_pdf_metadata(pdf_path, info):
    """Merge info into the PDF's document-info dictionary, in place.

    Existing entries (/Title, /Author, ...) are kept. Returns None on success, or a
    warning string when the PDF was left untouched: metadata is a nice-to-have and
    must never block the upload.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        warning = f"pypdf not importable by {sys.executable}; uploaded without why-note metadata"
        log(warning)
        return warning

    tmp_path = f"{pdf_path}.meta-tmp"
    try:
        reader = PdfReader(pdf_path)
        if reader.is_encrypted:
            raise ValueError("PDF is encrypted")
        writer = PdfWriter(clone_from=reader)
        writer.add_metadata(info)
        with open(tmp_path, "wb") as f:
            writer.write(f)
        os.replace(tmp_path, pdf_path)
        return None
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        warning = f"could not write PDF metadata ({type(e).__name__}: {e}); uploaded without it"
        log(warning)
        return warning


def put_pdf(pdf_path, doc_name, folder, prov):
    """Stamp the why-note metadata, then hand the PDF to rmapi."""
    warning = write_pdf_metadata(pdf_path, pdf_info(prov))
    result = subprocess.run(
        ["rmapi", "put", pdf_path, folder],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return {"error": f"rmapi failed: {result.stderr.strip()}"}
    response = {"status": "ok", "title": doc_name}
    if warning:
        response["warning"] = warning
    return response


def sanitize_filename(title):
    # reMarkable Cloud rejects filenames containing reserved chars with HTTP 400.
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned[:80] or "untitled"


def arxiv_id_from_url(url):
    """Return the identifier from an arXiv abstract/PDF URL, if present."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname != "arxiv.org" and not hostname.endswith(".arxiv.org"):
            return None

        path = unquote(parsed.path).strip("/")
        kind, separator, paper_id = path.partition("/")
        if separator != "/" or kind.lower() not in {"abs", "html", "pdf"}:
            return None
        paper_id = re.sub(r"\.pdf$", "", paper_id, flags=re.IGNORECASE)
        return paper_id if ARXIV_ID_RE.fullmatch(paper_id) else None
    except (TypeError, ValueError):
        return None


def arxiv_title_from_url(url, opener=urllib.request.urlopen):
    """Resolve an arXiv URL to its article title via the documented Atom API."""
    paper_id = arxiv_id_from_url(url)
    if not paper_id:
        return None

    api_url = "https://export.arxiv.org/api/query?" + urlencode({"id_list": paper_id})
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": "remarkable-share/1.1 (personal metadata lookup)"},
    )
    try:
        with opener(request, timeout=10) as response:
            root = ET.fromstring(response.read())
    except (ET.ParseError, OSError, TimeoutError, ValueError):
        return None

    title = root.findtext(f"{{{ATOM_NS}}}entry/{{{ATOM_NS}}}title", default="")
    return " ".join(title.split()) or None


def upload_pdf(url=None, data=None, title="untitled", folder="/", prov=None):
    """Upload a PDF to reMarkable. Accepts a URL or base64-encoded data.

    The PDF itself is not altered beyond its metadata: a cover page would shift
    every page number.
    """
    prov = prov or read_provenance({}, fallback_url=url)
    if url:
        title = arxiv_title_from_url(url) or title
    clean_title = sanitize_filename(title)

    if folder != "/":
        subprocess.run(["rmapi", "mkdir", folder], capture_output=True)
    ls_result = subprocess.run(["rmapi", "ls", folder], capture_output=True, text=True, timeout=15)
    existing = {line.split("\t", 1)[-1].strip() for line in ls_result.stdout.strip().splitlines()}
    doc_name = clean_title
    n = 2
    while doc_name in existing:
        doc_name = f"{clean_title} (v{n})"
        n += 1

    pdf_path = os.path.join(PDF_DIR, f"{doc_name}.pdf")
    try:
        if data:
            Path(pdf_path).write_bytes(base64.b64decode(data))
        elif url:
            parsed = urlparse(url)
            if parsed.scheme == "file":
                local_path = unquote(parsed.path)
                shutil.copy2(local_path, pdf_path)
            else:
                urllib.request.urlretrieve(url, pdf_path)
        else:
            return {"error": "No URL or data provided"}

        if not Path(pdf_path).exists() or Path(pdf_path).stat().st_size == 0:
            return {"error": "Failed to retrieve PDF"}

        return put_pdf(pdf_path, doc_name, folder, prov)
    finally:
        Path(pdf_path).unlink(missing_ok=True)


def upload(title, html_content, meta, folder="/", prov=None):
    prov = prov or read_provenance({})
    clean_title = sanitize_filename(title)

    # Check for duplicate names
    if folder != "/":
        subprocess.run(["rmapi", "mkdir", folder], capture_output=True)
    ls_result = subprocess.run(["rmapi", "ls", folder], capture_output=True, text=True, timeout=15)
    existing = {line.split("\t", 1)[-1].strip() for line in ls_result.stdout.strip().splitlines()}
    doc_name = clean_title
    n = 2
    while doc_name in existing:
        doc_name = f"{clean_title} (v{n})"
        n += 1

    pdf_path = os.path.join(PDF_DIR, f"{doc_name}.pdf")
    try:
        render_pdf(html_content, title, meta, pdf_path, note=prov["note"])
        return put_pdf(pdf_path, doc_name, folder, prov)
    finally:
        Path(pdf_path).unlink(missing_ok=True)


def main():
    msg = read_message()
    if not msg:
        send_message({"error": "no message received"})
        return

    try:
        action = msg.get("action", "")
        if action == "folders":
            send_message({"folders": get_folders()})
        elif action == "upload_pdf":
            result = upload_pdf(
                url=msg.get("url"), data=msg.get("data"),
                title=msg.get("title", "untitled"),
                folder=msg.get("folder", "/"),
                prov=read_provenance(msg, fallback_url=msg.get("url")),
            )
            send_message(result)
        elif action == "upload":
            result = upload(
                msg["title"], msg["content"], msg.get("meta", ""),
                msg.get("folder", "/"),
                prov=read_provenance(msg),
            )
            send_message(result)
        else:
            send_message({"error": f"unknown action: {action}"})
    except Exception as e:
        send_message({"error": str(e)})


if __name__ == "__main__":
    main()
