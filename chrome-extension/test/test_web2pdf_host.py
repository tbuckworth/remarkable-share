import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


HOST_PATH = Path(__file__).parents[1] / "native-host" / "web2pdf_host.py"
SPEC = importlib.util.spec_from_file_location("web2pdf_host", HOST_PATH)
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ArxivMetadataTests(unittest.TestCase):
    def test_extracts_current_arxiv_pdf_id(self):
        self.assertEqual(
            HOST.arxiv_id_from_url("https://arxiv.org/pdf/2511.18397v2.pdf?download=1"),
            "2511.18397v2",
        )

    def test_extracts_extensionless_and_legacy_ids(self):
        self.assertEqual(
            HOST.arxiv_id_from_url("https://export.arxiv.org/pdf/1804.08838"),
            "1804.08838",
        )
        self.assertEqual(
            HOST.arxiv_id_from_url("https://arxiv.org/abs/hep-th/9901001v1"),
            "hep-th/9901001v1",
        )

    def test_rejects_non_arxiv_and_lookalike_hosts(self):
        self.assertIsNone(HOST.arxiv_id_from_url("https://example.com/pdf/2511.18397"))
        self.assertIsNone(HOST.arxiv_id_from_url("https://arxiv.org.example/pdf/2511.18397"))

    def test_reads_article_title_not_feed_title(self):
        atom = b"""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>ArXiv Query: id_list=2511.18397</title>
          <entry><title>Natural Emergent\n Misalignment from Reward Hacking</title></entry>
        </feed>"""

        def opener(request, timeout):
            self.assertEqual(timeout, 10)
            self.assertIn("id_list=2511.18397", request.full_url)
            return FakeResponse(atom)

        self.assertEqual(
            HOST.arxiv_title_from_url("https://arxiv.org/pdf/2511.18397", opener),
            "Natural Emergent Misalignment from Reward Hacking",
        )

    def test_metadata_failure_preserves_filename_fallback(self):
        def opener(_request, timeout):
            self.assertEqual(timeout, 10)
            raise TimeoutError

        self.assertIsNone(HOST.arxiv_title_from_url("https://arxiv.org/pdf/2511.18397", opener))


try:
    import pypdf
except ImportError:  # the degradation tests below still run
    pypdf = None

needs_pypdf = unittest.skipIf(pypdf is None, "pypdf not installed")

OFFSET_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")
SENT_AT = "2026-10-29T11:09:05+00:00"
NOTE = 'Compare with <b>Smith</b> & "Jones" — ch. 3'
ARTICLE_MSG = {
    "action": "upload",
    "title": "An article",
    "content": "<p>Body text.</p>",
    "meta": "example.com",
    "folder": "/Articles",
    "note": NOTE,
    "mode": "work",
    "source_url": "https://example.com/post?id=1&x=2",
    "sent_at": SENT_AT,
}


def make_pdf(path, title="Original title", text_pages=1):
    writer = pypdf.PdfWriter()
    for _ in range(text_pages):
        writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": title, "/Author": "Someone"})
    with open(path, "wb") as f:
        writer.write(f)


def chrome_binary():
    """A local Chrome/Chromium to render with, if this machine has one."""
    if Path(HOST.CHROME).exists():
        return HOST.CHROME
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


class FakeRmapi:
    """Stands in for subprocess.run: an empty cloud that records each put."""

    def __init__(self):
        self.puts = []  # (doc basename, folder, metadata or None, bytes)

    def __call__(self, args, **_kwargs):
        if args[:2] == ["rmapi", "put"]:
            path, folder = args[2], args[3]
            meta = None
            if pypdf is not None:
                try:
                    meta = dict(pypdf.PdfReader(path).metadata or {})
                except Exception:
                    meta = None
            self.puts.append((Path(path).name, folder, meta, Path(path).read_bytes()))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class HostTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.object(HOST, "PDF_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_host(self, msg):
        """Drive main() with one framed native message; return the decoded reply."""
        raw = json.dumps(msg).encode("utf-8")
        stdin = io.TextIOWrapper(io.BytesIO(struct.pack("<I", len(raw)) + raw))
        stdout = io.TextIOWrapper(io.BytesIO())
        with mock.patch.object(HOST.sys, "stdin", stdin), mock.patch.object(HOST.sys, "stdout", stdout):
            HOST.main()
        out = stdout.buffer.getvalue()
        (length,) = struct.unpack("<I", out[:4])
        return json.loads(out[4 : 4 + length])


class ProvenanceTests(unittest.TestCase):
    def test_cleans_note_mode_and_url(self):
        prov = HOST.read_provenance(
            {"note": "  two\nlines\t here ", "mode": "WORK", "source_url": "https://a.example/x", "sent_at": SENT_AT}
        )
        self.assertEqual(
            prov,
            {"note": "two lines here", "mode": "work", "source_url": "https://a.example/x", "sent_at": SENT_AT},
        )

    def test_defaults_for_a_message_without_why_fields(self):
        prov = HOST.read_provenance({}, fallback_url="https://a.example/paper.pdf")
        self.assertEqual(prov["note"], "")
        self.assertEqual(prov["mode"], "auto")
        self.assertEqual(prov["source_url"], "https://a.example/paper.pdf")
        self.assertRegex(prov["sent_at"], OFFSET_ISO_RE)

    def test_unknown_mode_becomes_auto_and_note_is_bounded(self):
        prov = HOST.read_provenance({"mode": "holiday", "note": "x" * 2000})
        self.assertEqual(prov["mode"], "auto")
        self.assertEqual(len(prov["note"]), HOST.NOTE_MAX_CHARS)

    def test_sent_at_is_normalised_to_a_numeric_offset(self):
        self.assertEqual(HOST.clean_sent_at("2026-10-29T11:09:05.123Z"), "2026-10-29T11:09:05+00:00")
        self.assertEqual(HOST.clean_sent_at("2026-10-29T16:39:05+05:30"), "2026-10-29T16:39:05+05:30")
        now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-5)))
        for bad in ("", None, "yesterday", "2026-10-29T11:09:05"):  # naive is ambiguous
            self.assertEqual(HOST.clean_sent_at(bad, now=now), "2026-01-02T03:04:05-05:00")

    def test_pdf_info_sets_subject_only_for_a_note(self):
        with_note = HOST.pdf_info(HOST.read_provenance(ARTICLE_MSG))
        self.assertEqual(with_note["/Subject"], NOTE)
        self.assertNotIn("/Title", with_note)
        without = HOST.pdf_info(HOST.read_provenance({**ARTICLE_MSG, "note": "   "}))
        self.assertEqual(without["/Web2pdfNote"], "")
        self.assertNotIn("/Subject", without)


class WhyLineHtmlTests(unittest.TestCase):
    def test_note_is_escaped_under_the_title(self):
        page = HOST.build_html("<p>x</p>", "Title", "meta", '<script>alert("x")</script> & co')
        self.assertIn(
            '<div class="meta">meta</div>\n'
            '<div class="why">Why: &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; co</div>\n</div>',
            page,
        )
        self.assertNotIn('<script>alert("x")', page)

    def test_empty_note_has_no_why_line(self):
        for note in ("", "   ", None):
            page = HOST.build_html("<p>x</p>", "Title", "meta", note)
            self.assertNotIn("Why:", page)
            self.assertNotIn('class="why"', page)
            self.assertIn('<div class="meta">meta</div>\n</div>', page)


class MetadataTests(HostTestCase):
    @needs_pypdf
    def test_writes_readable_keys_and_keeps_title(self):
        pdf = self.dir / "doc.pdf"
        make_pdf(pdf)
        prov = HOST.read_provenance({**ARTICLE_MSG, "note": "naïve — 日本語 <ok>"})
        self.assertIsNone(HOST.write_pdf_metadata(str(pdf), HOST.pdf_info(prov)))

        meta = pypdf.PdfReader(str(pdf)).metadata
        self.assertEqual(meta["/Web2pdfNote"], "naïve — 日本語 <ok>")
        self.assertEqual(meta["/Web2pdfMode"], "work")
        self.assertEqual(meta["/Web2pdfUrl"], "https://example.com/post?id=1&x=2")
        self.assertEqual(meta["/Web2pdfSentAt"], SENT_AT)
        self.assertEqual(meta["/Subject"], "naïve — 日本語 <ok>")
        self.assertEqual(meta["/Title"], "Original title")
        self.assertEqual(meta["/Author"], "Someone")

    @needs_pypdf
    def test_empty_note_writes_empty_key_and_no_subject(self):
        pdf = self.dir / "doc.pdf"
        make_pdf(pdf)
        prov = HOST.read_provenance({**ARTICLE_MSG, "note": ""})
        self.assertIsNone(HOST.write_pdf_metadata(str(pdf), HOST.pdf_info(prov)))

        meta = pypdf.PdfReader(str(pdf)).metadata
        self.assertEqual(meta["/Web2pdfNote"], "")
        self.assertNotIn("/Subject", meta)
        self.assertEqual(meta["/Web2pdfMode"], "work")

    @needs_pypdf
    def test_direct_pdf_keeps_its_pages(self):
        pdf = self.dir / "doc.pdf"
        make_pdf(pdf, text_pages=3)
        HOST.write_pdf_metadata(str(pdf), HOST.pdf_info(HOST.read_provenance(ARTICLE_MSG)))
        self.assertEqual(len(pypdf.PdfReader(str(pdf)).pages), 3)

    def test_unreadable_pdf_is_left_untouched_with_a_warning(self):
        pdf = self.dir / "doc.pdf"
        pdf.write_bytes(b"<html>not a pdf</html>")
        with contextlib.redirect_stderr(io.StringIO()):
            warning = HOST.write_pdf_metadata(str(pdf), {"/Web2pdfNote": "x"})
        self.assertIn("uploaded without", warning)
        self.assertEqual(pdf.read_bytes(), b"<html>not a pdf</html>")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["doc.pdf"])


class GracefulDegradationTests(HostTestCase):
    """No pypdf for the host's interpreter: warn, upload the PDF unchanged."""

    def test_missing_pypdf_warns_and_leaves_pdf_unchanged(self):
        pdf = self.dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 original bytes")
        stderr = io.StringIO()
        with mock.patch.dict(sys.modules, {"pypdf": None}), contextlib.redirect_stderr(stderr):
            warning = HOST.write_pdf_metadata(str(pdf), {"/Web2pdfNote": "x"})
        self.assertIn("pypdf not importable", warning)
        self.assertIn("pypdf not importable", stderr.getvalue())
        self.assertEqual(pdf.read_bytes(), b"%PDF-1.4 original bytes")

    def test_upload_still_happens_without_pypdf(self):
        rmapi = FakeRmapi()
        data = base64.b64encode(b"%PDF-1.4 original bytes").decode()
        with mock.patch.dict(sys.modules, {"pypdf": None}), mock.patch.object(
            HOST.subprocess, "run", rmapi
        ), contextlib.redirect_stderr(io.StringIO()):
            reply = self.run_host(
                {"action": "upload_pdf", "data": data, "title": "Local paper", "folder": "/", "note": "why"}
            )
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(reply["title"], "Local paper")
        self.assertIn("pypdf not importable", reply["warning"])
        self.assertEqual(rmapi.puts[0][3], b"%PDF-1.4 original bytes")


class HostMessageTests(HostTestCase):
    """The extension's message fields reach the uploaded PDF."""

    def fake_render(self, calls):
        def render(html_content, title, meta, output_path, note=""):
            calls.append({"title": title, "meta": meta, "note": note})
            make_pdf(output_path, title="tmpabc.html")  # what Chrome leaves in /Title today
        return render

    @needs_pypdf
    def test_article_upload_carries_note_mode_url_and_time(self):
        rmapi, renders = FakeRmapi(), []
        with mock.patch.object(HOST.subprocess, "run", rmapi), mock.patch.object(
            HOST, "render_pdf", self.fake_render(renders)
        ):
            reply = self.run_host(ARTICLE_MSG)

        self.assertEqual(reply, {"status": "ok", "title": "An article"})
        self.assertEqual(renders, [{"title": "An article", "meta": "example.com", "note": NOTE}])
        name, folder, meta, _ = rmapi.puts[0]
        self.assertEqual((name, folder), ("An article.pdf", "/Articles"))
        self.assertEqual(meta["/Web2pdfNote"], NOTE)
        self.assertEqual(meta["/Web2pdfMode"], "work")
        self.assertEqual(meta["/Web2pdfUrl"], "https://example.com/post?id=1&x=2")
        self.assertEqual(meta["/Web2pdfSentAt"], SENT_AT)
        self.assertEqual(meta["/Subject"], NOTE)
        self.assertEqual(meta["/Title"], "tmpabc.html")

    @needs_pypdf
    def test_direct_pdf_upload_gets_metadata_and_no_cover_page(self):
        src = self.dir / "src.pdf"
        make_pdf(src, title="Paper", text_pages=2)
        rmapi = FakeRmapi()
        msg = {
            "action": "upload_pdf",
            "url": src.as_uri(),
            "title": "paper",
            "folder": "/",
            "note": "",
            "mode": "personal",
            "source_url": src.as_uri(),
            "sent_at": SENT_AT,
        }
        with mock.patch.object(HOST.subprocess, "run", rmapi):
            reply = self.run_host(msg)

        self.assertEqual(reply, {"status": "ok", "title": "paper"})
        _, _, meta, body = rmapi.puts[0]
        self.assertEqual(meta["/Web2pdfNote"], "")
        self.assertEqual(meta["/Web2pdfMode"], "personal")
        self.assertEqual(meta["/Web2pdfUrl"], src.as_uri())
        self.assertNotIn("/Subject", meta)
        self.assertEqual(meta["/Title"], "Paper")
        self.assertEqual(len(pypdf.PdfReader(io.BytesIO(body)).pages), 2)

    def test_old_extension_message_still_uploads(self):
        """A popup built before this change sends no why fields at all."""
        rmapi, renders = FakeRmapi(), []

        def render(html_content, title, meta, output_path, note=""):
            renders.append(note)
            Path(output_path).write_bytes(b"%PDF-1.4 rendered")

        legacy = {k: ARTICLE_MSG[k] for k in ("action", "title", "content", "meta", "folder")}
        with mock.patch.object(HOST.subprocess, "run", rmapi), mock.patch.object(
            HOST, "render_pdf", render
        ), contextlib.redirect_stderr(io.StringIO()):
            reply = self.run_host(legacy)
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(renders, [""])


@unittest.skipIf(chrome_binary() is None, "no Chrome to render with")
@needs_pypdf
class RenderWithChromeTests(HostTestCase):
    """Real headless-Chrome render: the note shows on page 1, as text, not markup."""

    def render_page1(self, note):
        out = self.dir / "out.pdf"
        with mock.patch.object(HOST, "CHROME", chrome_binary()):
            HOST.render_pdf("<p>Body paragraph.</p>", "Render title", "example.com", str(out), note=note)
        return pypdf.PdfReader(str(out)).pages[0].extract_text()

    def test_note_printed_escaped_on_page_one(self):
        text = self.render_page1('<b>bold?</b> & "quoted"')
        self.assertIn('Why: <b>bold?</b> & "quoted"', text)
        self.assertLess(text.index("Render title"), text.index("Why:"))
        self.assertLess(text.index("Why:"), text.index("Body paragraph."))

    def test_no_why_line_without_note(self):
        text = self.render_page1("")
        self.assertIn("Render title", text)
        self.assertNotIn("Why:", text)


if __name__ == "__main__":
    unittest.main()
