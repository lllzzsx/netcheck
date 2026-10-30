# -*- coding: utf-8 -*-
"""
配置层测试：deep_merge / Config / load_config。

配置这东西最容易出「静默错误」——不报错，但值是错的。
比如 deep_merge 少写一层，max_retry 变成 None，线程一次都不重试。
所以必须测。
"""
import json
import os
import sys
import tempfile
import unittest

# 测试在 tests/ 子目录里，而被测代码在上一级，所以要把上一级加进 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from device_check import DEFAULTS, Config, deep_merge, load_config


class TestDeepMerge(unittest.TestCase):
    """测 deep_merge：只盖给了的键，没给的保留。"""

    def test_只盖一个键其他保留(self):
        base = {"a": 1, "b": 2}
        result = deep_merge(base, {"a": 99})
        self.assertEqual(result, {"a": 99, "b": 2})

    def test_嵌套字典也递归合并(self):
        base = {"concurrency": {"max_workers": 5, "max_retry": 3}}
        override = {"concurrency": {"max_workers": 10}}
        result = deep_merge(base, override)
        self.assertEqual(result["concurrency"]["max_workers"], 10)
        # 这条是关键：max_retry 没在 override 里给，必须保留 base 的 3
        self.assertEqual(result["concurrency"]["max_retry"], 3)

    def test_不改原字典(self):
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        deep_merge(base, override)
        self.assertEqual(base["a"]["b"], 1)     # base 不许被改

    def test_空override返回原值(self):
        self.assertEqual(deep_merge({"a": 1}, {}), {"a": 1})

    def test_override多出的键会加进来(self):
        self.assertEqual(deep_merge({"a": 1}, {"b": 2}), {"a": 1, "b": 2})

    def test_None不覆盖已有值(self):
        """override 里某个键给了 None，视为‘没给’，保留 base。"""
        self.assertEqual(deep_merge({"a": 1}, {"a": None}), {"a": 1})


class TestConfigObject(unittest.TestCase):

    def test_点号取值(self):
        cfg = Config({"a": {"b": {"c": 42}}})
        self.assertEqual(cfg.a.b.c, 42)

    def test_get点号路径(self):
        cfg = Config({"a": {"b": 1}})
        self.assertEqual(cfg.get("a.b"), 1)
        self.assertEqual(cfg.get("a.x", "兜底"), "兜底")
        self.assertEqual(cfg.get("x.y.z", None), None)

    def test_改属性会同步进to_dict(self):
        """这条防的是我自己踩过的坑：只改属性、_data 没跟上。"""
        cfg = Config({"concurrency": {"max_workers": 5}})
        cfg.concurrency.max_workers = 10
        self.assertEqual(cfg.to_dict()["concurrency"]["max_workers"], 10)

    def test_to_dict是纯字典(self):
        cfg = Config({"a": {"b": 1}})
        d = cfg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIsInstance(d["a"], dict)
        self.assertEqual(d, {"a": {"b": 1}})

    def test_to_dict能被json序列化(self):
        cfg = Config({"a": {"b": 1}})
        # 如果 to_dict 里混进了 Config 对象，这里会 TypeError
        json.dumps(cfg.to_dict())


class TestLoadConfig(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def _write(self, text, name="c.json", encoding="utf-8"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding=encoding) as f:
            f.write(text)
        return path

    def test_正常读取(self):
        path = self._write('{"concurrency": {"max_workers": 7}}')
        cfg = load_config(path)
        self.assertEqual(cfg.concurrency.max_workers, 7)

    def test_缺项用DEFAULTS补全(self):
        path = self._write('{"concurrency": {"max_workers": 7}}')
        cfg = load_config(path)
        self.assertEqual(cfg.concurrency.max_retry, DEFAULTS["concurrency"]["max_retry"])
        self.assertEqual(cfg.connection.timeout, DEFAULTS["connection"]["timeout"])

    def test_空对象也能跑(self):
        cfg = load_config(self._write("{}"))
        self.assertEqual(cfg.concurrency.max_workers, 5)

    def test_文件不存在抛FileNotFoundError(self):
        with self.assertRaises(FileNotFoundError):
            load_config(os.path.join(self.dir, "nope.json"))

    def test_json写错抛ValueError并带行列号(self):
        path = self._write('{\n  "a": 1,\n  "b": 2,,\n}')
        with self.assertRaises(ValueError) as ctx:
            load_config(path)
        msg = str(ctx.exception)
        self.assertIn("第 3 行", msg)
        self.assertIn("列", msg)

    def test_最外层是列表要报错(self):
        with self.assertRaises(ValueError):
            load_config(self._write("[1, 2, 3]"))

    def test_带BOM的文件也能读(self):
        """
        Windows 记事本/PowerShell Out-File 会在文件头写 BOM。
        用 encoding='utf-8' 读会报 Unexpected UTF-8 BOM，必须用 utf-8-sig。
        """
        path = self._write('{"concurrency": {"max_workers": 9}}',
                           name="bom.json", encoding="utf-8-sig")
        cfg = load_config(path)
        self.assertEqual(cfg.concurrency.max_workers, 9)

    def test_中文配置不乱码(self):
        path = self._write('{"commands": {"huawei": "显示当前配置"}}')
        cfg = load_config(path)
        self.assertEqual(cfg.commands.huawei, "显示当前配置")


if __name__ == "__main__":
    unittest.main(verbosity=2)
