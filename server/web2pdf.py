#!/usr/bin/env python3
"""Convert a web article to a clean, tight-margin PDF for reMarkable."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from readability import Document

import platform
if platform.system() == "Darwin":
    CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else:
    CHROME = "google-chrome"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# Clean CSS optimized for reMarkable (tight margins, full-width, readable)
CLEAN_CSS = """
@page {
    size: A4;
    margin: 0;  /* zero page margin = no room for Chrome header/footer */
}
body {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 14pt;
    line-height: 1.5;
    color: #000;
    max-width: 100%;
    margin: 0;
    padding: 1.5cm 1.2cm;  /* visual margins via padding instead */
}
h1 { font-size: 18pt; margin: 0 0 0.5em 0; line-height: 1.2; }
h2 { font-size: 14pt; margin: 1.2em 0 0.4em 0; }
h3 { font-size: 12pt; margin: 1em 0 0.3em 0; }
p { margin: 0.5em 0; text-align: justify; }
img { max-width: 100%; height: auto; margin: 0.5em 0; }
figure { margin: 0.5em 0; }
figcaption { font-size: 9pt; color: #444; font-style: italic; }
blockquote {
    border-left: 2pt solid #666;
    margin: 0.5em 0 0.5em 0;
    padding: 0.2em 0 0.2em 0.8em;
    font-style: italic;
}
pre, code {
    font-family: 'Courier New', monospace;
    font-size: 9pt;
    background: #f5f5f5;
    padding: 0.1em 0.3em;
}
pre { padding: 0.5em; overflow-x: auto; white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; margin: 0.5em 0; }
th, td { border: 1px solid #ccc; padding: 0.3em 0.5em; font-size: 10pt; }
th { background: #f0f0f0; font-weight: bold; }
a { color: #000; text-decoration: underline; }
ul, ol { margin: 0.5em 0; padding-left: 1.5em; }
li { margin: 0.2em 0; }
.title-block { margin-bottom: 1em; border-bottom: 1px solid #ccc; padding-bottom: 0.5em; }
.title-block .meta { font-size: 9pt; color: #666; margin-top: 0.3em; }
"""

KATEX_HEAD = (
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/katex.min.css">\n'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/katex.min.js"></script>\n'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.27/dist/contrib/auto-render.min.js"'
    ' onload="renderMathInElement(document.body,{delimiters:['
    '{left:\'$$\',right:\'$$\',display:true},'
    '{left:\'$\',right:\'$\',display:false}]});"></script>'
)


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


# --- Google Docs support ------------------------------------------------------
# A plain GET of a docs.google.com/.../edit URL returns only a JavaScript loader
# shell, so Readability salvages nothing but "JavaScript isn't enabled...". Google
# Docs instead expose a native, fully-structured HTML export that we use directly.
GDOC_RE = re.compile(
    r"docs\.google\.com/document/(?:u/\d+/)?d/(?!e/)([A-Za-z0-9_-]+)"
)


def google_doc_id(url: str) -> str | None:
    """Return the document id if this is a standard Google Docs URL, else None."""
    m = GDOC_RE.search(url or "")
    return m.group(1) if m else None


def fetch_google_doc(doc_id: str) -> str:
    """Fetch a Google Doc's native HTML export (requires link-share or auth)."""
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=html"
    resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    if "accounts.google.com" in resp.url or "ServiceLogin" in resp.text[:5000]:
        raise PermissionError(
            "Google Doc isn't accessible without signing in. Enable "
            "'Anyone with the link can view' on the doc, then re-share it."
        )
    resp.raise_for_status()
    return resp.text


def extract_google_doc(export_html: str) -> tuple[str, str]:
    """Extract (title, body HTML) from a Google Docs HTML export.

    Google encodes bold/italic/underline as CSS classes in a <style> block, so we
    resolve those classes into semantic inline styles before stripping Google's
    classes — otherwise stripping would drop all character formatting. Typography
    (font, size, margins) is then governed by our reMarkable CLEAN_CSS.
    """
    soup = BeautifulSoup(export_html, "html.parser")

    # Map Google's formatting classes (.c3{font-weight:700}, etc.) to semantics.
    css = "".join(s.string or "" for s in soup.find_all("style"))
    bold, italic, underline = set(), set(), set()
    for cls, decls in re.findall(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}", css):
        if re.search(r"font-weight\s*:\s*(?:bold|[6-9]00)", decls, re.I):
            bold.add(cls)
        if re.search(r"font-style\s*:\s*italic", decls, re.I):
            italic.add(cls)
        if re.search(r"text-decoration\s*:\s*underline", decls, re.I):
            underline.add(cls)

    body = soup.body or soup

    # Title: the Google "Title" paragraph style, else first heading, else <title>.
    title_el = body.find(class_="title") or body.find(["h1", "h2"])
    title = (
        title_el.get_text(strip=True) if title_el and title_el.get_text(strip=True)
        else (soup.title.string.strip() if soup.title and soup.title.string else None)
    ) or "Google Doc"
    # Drop it from the body so clean_html's title block doesn't duplicate it.
    if title_el and title_el.get_text(strip=True) == title:
        title_el.decompose()

    # Resolve formatting classes -> inline styles, then strip Google's attributes.
    for el in body.find_all(True):
        classes = el.get("class") or []
        styles = []
        if any(c in bold for c in classes):
            styles.append("font-weight:bold")
        if any(c in italic for c in classes):
            styles.append("font-style:italic")
        if any(c in underline for c in classes):
            styles.append("text-decoration:underline")
        if styles:
            el["style"] = ";".join(styles)
        else:
            el.attrs.pop("style", None)
        el.attrs.pop("class", None)
        el.attrs.pop("id", None)

    # Unwrap Google's redirect links: google.com/url?q=<real>&... -> <real>.
    for a in body.find_all("a"):
        href = a.get("href", "")
        if "google.com/url" in href:
            q = parse_qs(urlparse(href).query).get("q")
            if q:
                a["href"] = q[0]

    return title, body.decode_contents()


def preserve_math(html: str) -> str:
    """Replace CKEditor/KaTeX math elements with $-delimited LaTeX before Readability."""
    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for el in soup.find_all(class_="ck-math-tex"):
        tex = el.get("data-math-tex")
        if not tex:
            continue
        display = "ck-math-tex-display" in (el.get("class") or [])
        delim = "$$" if display else "$"
        el.replace_with(delim + tex + delim)
        changed = True
    return str(soup) if changed else html


def extract_article(html: str, url: str) -> tuple[str, str]:
    """Extract article content and title using readability."""
    html = preserve_math(html)
    doc = Document(html, url=url)
    title = doc.title()
    # Strip common " - Site Name" or " | Site Name" suffixes
    title = re.split(r"\s*[|\-–—]\s*(?=[^|\-–—]*$)", title)[0].strip()
    content = doc.summary()
    return title, content


def clean_html(content: str, title: str, url: str, source_html: str, font_size: str = "11pt") -> str:
    """Clean extracted HTML: fix relative URLs, add title block."""
    soup = BeautifulSoup(content, "html.parser")

    # Fix relative image URLs
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src and not src.startswith(("http://", "https://", "data:")):
            img["src"] = urljoin(url, src)
        # Remove srcset to avoid issues
        img.attrs.pop("srcset", None)

    # Fix relative link URLs
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if href and not href.startswith(("http://", "https://", "mailto:", "#")):
            a["href"] = urljoin(url, href)

    # Try to extract date/author from source
    meta_parts = []
    source_soup = BeautifulSoup(source_html, "html.parser")

    for attr in ["article:published_time", "date", "publishedDate"]:
        tag = source_soup.find("meta", {"property": attr}) or source_soup.find("meta", {"name": attr})
        if tag and tag.get("content"):
            meta_parts.append(tag["content"][:10])
            break

    for attr in ["author", "article:author"]:
        tag = source_soup.find("meta", {"property": attr}) or source_soup.find("meta", {"name": attr})
        if tag and tag.get("content"):
            meta_parts.append(tag["content"])
            break

    domain = urlparse(url).netloc.replace("www.", "")
    meta_parts.append(domain)
    meta_str = " · ".join(meta_parts)

    css = CLEAN_CSS.replace("font-size: 14pt", f"font-size: {font_size}")
    final_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{css}</style>
{KATEX_HEAD}
</head>
<body>
<div class="title-block">
<h1>{title}</h1>
<div class="meta">{meta_str}</div>
</div>
{soup}
</body>
</html>"""
    return final_html


def to_pdf(html_str: str, output_path: str) -> None:
    """Render HTML to PDF using headless Chrome."""
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False, encoding="utf-8") as f:
        f.write(html_str)
        tmp_html = f.name
    try:
        output_abs = str(Path(output_path).resolve())
        result = subprocess.run(
            [
                CHROME,
                "--headless=new",
                f"--print-to-pdf={output_abs}",
                "--print-to-pdf-no-header",
                tmp_html,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if not Path(output_abs).exists():
            print(f"Chrome PDF failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
    finally:
        Path(tmp_html).unlink(missing_ok=True)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def sanitize_filename(title: str) -> str:
    # reMarkable Cloud rejects filenames containing reserved chars with HTTP 400.
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned[:80] or "untitled"


def send_to_remarkable(pdf_path: str, folder: str = "/") -> bool:
    """Upload PDF to reMarkable via rmapi."""
    import os
    os.environ.setdefault("RMAPI_FORCE_SCHEMA_VERSION", "4")
    try:
        if folder != "/":
            subprocess.run(["rmapi", "mkdir", folder], capture_output=True)
        result = subprocess.run(
            ["rmapi", "put", pdf_path, folder],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print(f"Sent to reMarkable: {folder}")
            return True
        else:
            print(f"rmapi error: {result.stderr.strip()}", file=sys.stderr)
            return False
    except FileNotFoundError:
        print("rmapi not found", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Convert web article to clean PDF")
    parser.add_argument("url", help="URL of the article")
    parser.add_argument("-o", "--output", help="Output PDF path (default: auto-named)")
    parser.add_argument("--rm", action="store_true", help="Send to reMarkable via rmapi")
    parser.add_argument("--rm-folder", default="/", help="reMarkable folder (default: /)")
    parser.add_argument("--no-images", action="store_true", help="Strip images")
    parser.add_argument("--font-size", default="14pt", help="Body font size (default: 14pt)")
    args = parser.parse_args()

    doc_id = google_doc_id(args.url)
    if doc_id:
        print(f"Fetching Google Doc export: {doc_id}")
        raw_html = fetch_google_doc(doc_id)
        print("Extracting Google Doc content...")
        title, content = extract_google_doc(raw_html)
    else:
        print(f"Fetching: {args.url}")
        raw_html = fetch_page(args.url)
        print("Extracting article content...")
        title, content = extract_article(raw_html, args.url)
    print(f"Title: {title}")

    final_html = clean_html(content, title, args.url, raw_html, font_size=args.font_size)

    if args.no_images:
        soup = BeautifulSoup(final_html, "html.parser")
        for img in soup.find_all("img"):
            img.decompose()
        final_html = str(soup)

    if args.output:
        output_path = args.output
    else:
        output_path = f"{slugify(title)}.pdf"

    print(f"Rendering PDF: {output_path}")
    to_pdf(final_html, output_path)
    print(f"Done: {output_path}")

    if args.rm:
        send_to_remarkable(output_path, args.rm_folder)


if __name__ == "__main__":
    main()
