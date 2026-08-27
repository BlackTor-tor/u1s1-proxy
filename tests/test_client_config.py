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


if __name__ == "__main__":
    unittest.main()
