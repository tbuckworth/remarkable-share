"""Tests for the SSRF guard that stops /convert proxying into the local network.

DNS is mocked throughout so these run offline and cannot be perturbed by whatever
the real world currently resolves to.
"""

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


WEB2PDF_PATH = Path(__file__).parents[1] / "web2pdf.py"
SPEC = importlib.util.spec_from_file_location("web2pdf", WEB2PDF_PATH)
W = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W)


def resolving_to(*ips):
    """Patch getaddrinfo so a hostname resolves to exactly these addresses."""
    infos = [(2, 1, 6, "", (ip, 0)) for ip in ips]
    return mock.patch.object(W.socket, "getaddrinfo", return_value=infos)


class SchemeTests(unittest.TestCase):
    def test_rejects_non_http_schemes(self):
        for url in (
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/x",
            "data:text/html,hi",
        ):
            with self.subTest(url=url), self.assertRaises(W.BlockedURLError):
                W.assert_fetchable(url)

    def test_rejects_url_with_no_host(self):
        with self.assertRaises(W.BlockedURLError):
            W.assert_fetchable("http:///nohost")


class AddressTests(unittest.TestCase):
    def test_allows_ordinary_public_address(self):
        with resolving_to("93.184.216.34"):
            W.assert_fetchable("https://example.com/article")  # must not raise

    def test_rejects_loopback(self):
        with resolving_to("127.0.0.1"), self.assertRaises(W.BlockedURLError):
            W.assert_fetchable("http://localhost:8417/convert")

    def test_rejects_private_ranges(self):
        for ip in ("10.0.0.5", "192.168.1.1", "172.16.4.4"):
            with self.subTest(ip=ip), resolving_to(ip):
                with self.assertRaises(W.BlockedURLError):
                    W.assert_fetchable(f"http://{ip}/admin")

    def test_rejects_cloud_metadata_link_local(self):
        with resolving_to("169.254.169.254"), self.assertRaises(W.BlockedURLError):
            W.assert_fetchable("http://169.254.169.254/latest/meta-data/")

    def test_rejects_ipv6_loopback(self):
        with resolving_to("::1"), self.assertRaises(W.BlockedURLError):
            W.assert_fetchable("http://[::1]/")

    def test_rejects_host_resolving_to_both_public_and_private(self):
        """A split-horizon name must not pass on the strength of one good record."""
        with resolving_to("93.184.216.34", "10.0.0.5"):
            with self.assertRaises(W.BlockedURLError):
                W.assert_fetchable("http://sneaky.example.com/")

    def test_rejects_unresolvable_host(self):
        with mock.patch.object(W.socket, "getaddrinfo", side_effect=W.socket.gaierror):
            with self.assertRaises(W.BlockedURLError):
                W.assert_fetchable("http://nope.invalid/")


class FakeResponse:
    def __init__(self, status=200, location=None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None
        self.is_permanent_redirect = False


class RedirectTests(unittest.TestCase):
    """The interesting case: a public URL that redirects into private space."""

    def test_blocks_redirect_into_private_space(self):
        responses = [
            FakeResponse(302, "http://192.168.1.1/admin"),
            FakeResponse(200),
        ]
        addrs = {"good.example.com": "93.184.216.34", "192.168.1.1": "192.168.1.1"}

        def fake_getaddrinfo(host, *_a, **_k):
            return [(2, 1, 6, "", (addrs[host], 0))]

        with mock.patch.object(W.socket, "getaddrinfo", side_effect=fake_getaddrinfo), \
             mock.patch.object(W.requests, "request", side_effect=responses):
            with self.assertRaises(W.BlockedURLError):
                W.guarded_request("GET", "http://good.example.com/")

    def test_follows_public_redirect(self):
        responses = [
            FakeResponse(302, "https://elsewhere.example.com/final"),
            FakeResponse(200),
        ]
        with resolving_to("93.184.216.34"), \
             mock.patch.object(W.requests, "request", side_effect=responses):
            resp = W.guarded_request("GET", "http://good.example.com/")
        self.assertEqual(resp.status_code, 200)

    def test_caps_redirect_chain(self):
        forever = FakeResponse(302, "https://example.com/loop")
        with resolving_to("93.184.216.34"), \
             mock.patch.object(W.requests, "request", return_value=forever):
            with self.assertRaises(W.BlockedURLError):
                W.guarded_request("GET", "https://example.com/", max_redirects=3)

    def test_never_delegates_redirects_to_requests(self):
        """allow_redirects must be forced off, or hops go unvalidated."""
        captured = {}

        def capture(method, url, **kwargs):
            captured.update(kwargs)
            return FakeResponse(200)

        with resolving_to("93.184.216.34"), \
             mock.patch.object(W.requests, "request", side_effect=capture):
            W.guarded_request("GET", "https://example.com/")
        self.assertFalse(captured["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
