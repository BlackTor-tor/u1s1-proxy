"""u1s1_client 掩码助手与代理控制器接线的单测。"""
import unittest

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


if __name__ == "__main__":
    unittest.main()
