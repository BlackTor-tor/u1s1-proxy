"""u1s1_proxy 转发头组装与反代鉴权注入的单测。"""
import unittest

import u1s1_proxy


def make_handler(headers: dict, authorization: str) -> u1s1_proxy.U1S1ProxyHandler:
    """绕开网络，直接构造一个可测的 handler 实例。"""
    h = u1s1_proxy.U1S1ProxyHandler.__new__(u1s1_proxy.U1S1ProxyHandler)
    h.headers = headers
    h.authorization = authorization
    h.upstream_host = "u1s1.io"
    return h


class BuildUpstreamHeadersTest(unittest.TestCase):
    def test_host_rewritten(self):
        out = make_handler({}, "")._build_upstream_headers()
        self.assertEqual(out["Host"], "u1s1.io")

    def test_version_header_added(self):
        out = make_handler({}, "")._build_upstream_headers()
        self.assertEqual(out["x-u1s1-version"], u1s1_proxy.CLIENT_VERSION)

    def test_no_key_no_injection(self):
        out = make_handler({"Cookie": "a=1"}, "")._build_upstream_headers()
        self.assertNotIn("Authorization", out)

    def test_key_injected(self):
        out = make_handler({}, "Bearer sk-abc")._build_upstream_headers()
        self.assertEqual(out["Authorization"], "Bearer sk-abc")

    def test_key_overrides_client_auth(self):
        out = make_handler({"Authorization": "Bearer fake"},
                           "Bearer sk-real")._build_upstream_headers()
        self.assertEqual(out["Authorization"], "Bearer sk-real")
        self.assertNotIn("authorization", out)

    def test_hop_by_hop_stripped(self):
        out = make_handler({"Connection": "keep-alive", "Cookie": "a=1"},
                           "")._build_upstream_headers()
        self.assertNotIn("connection", out)
        self.assertNotIn("Connection", out)


if __name__ == "__main__":
    unittest.main()
