import importlib.util
import io
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
