# -*- coding: utf-8 -*-
"""
模型层测试：Device 与 InspectResult 的字段处理。

约定：
    文件名必须 test 开头，unittest 或 pytest 才会自动发现它。
    类必须继承 unittest.TestCase。
    方法名必须 test 开头，才会被当成一条测试。

运行：
    python -m pytest -q
    python -m pytest tests/test_models.py -v            （只跑这一个文件）
    python -m pytest "tests/test_models.py::TestDevice" -v
"""
import sys
import os
import unittest

# 测试在 tests/ 子目录里，而被测代码在上一级，所以要把上一级加进 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Device, InspectResult


class TestDevice(unittest.TestCase):
    """测 Device：从 CSV 行造对象。"""

    def test_from_row_正常行(self):
        # csv.DictReader 吐出来的东西长这样：键是表头，值全是字符串
        row = {
            "name": "R1",
            "ip": "192.168.106.101",
            "device_type": "huawei",
            "username": "netdev",
            "password": "Demo-Pass-123",
            "port": "22",
        }
        dev = Device.from_row(row)

        self.assertEqual(dev.name, "R1")
        self.assertEqual(dev.ip, "192.168.106.101")
        self.assertEqual(dev.device_type, "huawei")
        self.assertEqual(dev.username, "netdev")
        # 这条最重要：CSV 里是字符串 "22"，对象里必须是整数 22
        self.assertEqual(dev.port, 22)
        self.assertIsInstance(dev.port, int)

    def test_from_row_去掉前后空格(self):
        row = {"name": " R2 ", "ip": " 1.1.1.1 ", "device_type": "huawei ",
               "username": " netdev ", "password": "x", "port": "22"}
        dev = Device.from_row(row)
        self.assertEqual(dev.name, "R2")
        self.assertEqual(dev.ip, "1.1.1.1")
        self.assertEqual(dev.device_type, "huawei")
        self.assertEqual(dev.username, "netdev")

    def test_from_row_密码里带空格不能strip(self):
        """密码原文是 'Demo Pass 123 '，两头空格是密码的一部分，必须原样保留。"""
        row = {"name": "R3", "ip": "1.1.1.3", "device_type": "huawei",
               "username": "netdev", "password": "Demo Pass 123 ", "port": "22"}
        dev = Device.from_row(row)
        self.assertEqual(dev.password, "Demo Pass 123 ")

    def test_from_row_port为空走默认22(self):
        """CSV 里那一格是空的，int('') 会 ValueError，所以要先 or 22。"""
        row = {"name": "R4", "ip": "1.1.1.4", "device_type": "huawei",
               "username": "netdev", "password": "x", "port": ""}
        self.assertEqual(Device.from_row(row).port, 22)

    def test_from_row_缺device_type走默认huawei(self):
        """注意：这里用 row.get()，所以缺列不报错。"""
        row = {"name": "R5", "ip": "1.1.1.5", "username": "netdev",
               "password": "x", "port": "22"}
        self.assertEqual(Device.from_row(row).device_type, "huawei")

    def test_from_row_缺name要报错(self):
        """name 是必填字段，用了 row['name']，缺了就 KeyError —— 这是故意的。"""
        row = {"ip": "1.1.1.6", "device_type": "huawei"}
        with self.assertRaises(KeyError):
            Device.from_row(row)

    def test_connect_params_键名齐全是netmiko要的(self):
        dev = Device("R1", "192.168.106.101", username="netdev",
                     password="Demo-Pass-123", port=22)
        params = dev.connect_params()

        self.assertEqual(set(params), {
            "device_type", "host", "username", "password",
            "port", "timeout", "conn_timeout",
        })
        # netmiko 的 host 是 IP，不是 name —— 这个映射最容易搞错
        self.assertEqual(params["host"], "192.168.106.101")
        self.assertNotIn("R1", params.values())

    def test_repr和str(self):
        dev = Device("R9", "10.0.0.9")
        self.assertEqual(repr(dev), "<Device R9 10.0.0.9:22>")
        self.assertEqual(str(dev), "R9(10.0.0.9)")

    def test_端口自动转int(self):
        """有人直接传字符串，__init__ 里的 int(port) 要兜住。"""
        self.assertEqual(Device("R1", "1.1.1.1", port="2222").port, 2222)


class TestInspectResult(unittest.TestCase):
    """测 InspectResult：巡检结果的默认值和派生属性。"""

    def test_默认值(self):
        r = InspectResult("R1", "192.168.106.101")
        self.assertEqual(r.sysname, "-")
        self.assertEqual(r.status, "未知")
        self.assertEqual(r.bytes, 0)
        self.assertEqual(r.ifaces, 0)
        self.assertEqual(r.elapsed, 0.0)
        self.assertEqual(r.note, "")
        self.assertEqual(r.drift, "")

    def test_ok只认成功(self):
        r = InspectResult("R1", "1.1.1.1")
        self.assertFalse(r.ok)

        r.status = "成功"
        self.assertTrue(r.ok)

        r.status = "超时"
        self.assertFalse(r.ok)

    def test_ok是现算的(self):
        """property 不是存下来的字段：改 status，ok 立刻跟着变。"""
        r = InspectResult("R1", "1.1.1.1")
        r.status = "成功"
        first = r.ok
        r.status = "认证失败"
        self.assertNotEqual(first, r.ok)

    def test_to_dict键齐全(self):
        """这条测试锁住 CSV 的列：少一个键，写出来的报告就少一列。"""
        r = InspectResult("R1", "1.1.1.1")
        d = r.to_dict()
        self.assertEqual(list(d), [
            "name", "ip", "sysname", "status",
            "bytes", "ifaces", "elapsed", "note", "drift",
        ])

    def test_to_dict不含内部属性(self):
        """就算以后给对象加了 _t0 这种内部属性，也不该漏进 CSV。"""
        r = InspectResult("R1", "1.1.1.1")
        r._t0 = 12345          # 模拟以后加的内部字段
        self.assertNotIn("_t0", r.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
