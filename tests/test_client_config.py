"""u1s1_client 掩码助手、官方 Key 读取与代理控制器接线的单测。"""
import json
import tempfile
import unittest
from pathlib import Path

import u1s1_client
import u1s1_proxy


class MaskKeyTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(u1s1_client._mask_key(""), "（未填写）")

    def test_short_key(self):
        self.assertEqual(u1s1_client._mask_key("sk-abc"), "sk…")

    def test_normal_key(self):
        self.assertEqual(u1s1_client._mask_key("sk-abcdefghijklmnop"), "sk-abcd…mnop")

    def test_exact_8_chars(self):
        self.assertEqual(u1s1_client._mask_key("12345678"), "12…")


class ControllerAuthTest(unittest.TestCase):
    def test_start_injects_authorization(self):
        ctl = u1s1_client.ProxyController(log=lambda msg: None)
        try:
            msg = ctl.start("127.0.0.1", 0, "https://u1s1.io", "sk-abcdef123456")
            self.assertIn("代理已启动", msg)
            self.assertIn("sk-abcd…3456", msg)
            self.assertEqual(u1s1_proxy.U1S1ProxyHandler.authorization,
                             "Bearer sk-abcdef123456")
        finally:
            ctl.stop()
            u1s1_proxy.U1S1ProxyHandler.authorization = ""

    def test_start_without_key_keeps_blank(self):
        ctl = u1s1_client.ProxyController(log=lambda msg: None)
        try:
            ctl.start("127.0.0.1", 0, "https://u1s1.io", "")
            self.assertEqual(u1s1_proxy.U1S1ProxyHandler.authorization, "")
        finally:
            ctl.stop()
            u1s1_proxy.U1S1ProxyHandler.authorization = ""


class OfficialKeyLoaderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = u1s1_client._official_config_path
        u1s1_client._official_config_path = lambda: Path(self._tmp.name) / "config.json"

    def tearDown(self):
        u1s1_client._official_config_path = self._orig
        self._tmp.cleanup()

    def test_reads_api_key(self):
        Path(self._tmp.name, "config.json").write_text(
            json.dumps({"apiKey": "u1s1-test-key-1234"}), "utf-8")
        self.assertEqual(u1s1_client.load_official_key(), "u1s1-test-key-1234")

    def test_missing_file_returns_empty(self):
        self.assertEqual(u1s1_client.load_official_key(), "")

    def test_corrupt_file_returns_empty(self):
        Path(self._tmp.name, "config.json").write_text("{broken", "utf-8")
        self.assertEqual(u1s1_client.load_official_key(), "")


if __name__ == "__main__":
    unittest.main()
