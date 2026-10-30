# -*- coding: utf-8 -*-
"""
解析层测试：parse_interfaces / get_sysname / 中文对齐宽度。

这些函数不连设备、不读写文件，只吃字符串吐数据。
所以最适合写测试：输入什么东西、应该吐什么东西，完全能写死。
"""
import sys
import os
import unittest

# 测试在 tests/ 子目录里，而被测代码在上一级，所以要把上一级加进 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from device_check import (
    parse_interfaces, get_sysname, display_width, pad, DEFAULTS,
)

# 命令表搬进了 config.json，代码里只留 DEFAULTS 作兜底。
COMMAND_BY_TYPE = DEFAULTS["commands"]


class TestParseInterfaces(unittest.TestCase):
    """测「接口 -> 主 IP」的抽取。"""

    def test_两个接口(self):
        config = """#
interface Vlanif10
 ip address 192.168.10.1 255.255.255.0
#
interface LoopBack0
 ip address 1.1.1.1 255.255.255.255
#
return
"""
        self.assertEqual(parse_interfaces(config), [
            ("Vlanif10", "192.168.10.1"),
            ("LoopBack0", "1.1.1.1"),
        ])

    def test_一个接口两个IP只取第一个(self):
        """主 IP + 从 IP 时，只取紧跟在 interface 后面的第一个。"""
        config = """#
interface Vlanif10
 ip address 192.168.10.1 255.255.255.0
 ip address 192.168.10.2 255.255.255.0
#
return
"""
        result = parse_interfaces(config)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("Vlanif10", "192.168.10.1"))

    def test_接口下没有IP就不出现(self):
        config = """#
interface GigabitEthernet0/0/1
 port link-type trunk
 port trunk allow-pass vlan 10 20
#
interface Vlanif20
 ip address 192.168.20.1 255.255.255.0
#
return
"""
        self.assertEqual(parse_interfaces(config), [("Vlanif20", "192.168.20.1")])

    def test_空配置返回空列表(self):
        self.assertEqual(parse_interfaces(""), [])
        self.assertEqual(parse_interfaces("\n\n\n"), [])

    def test_没有interface行返回空列表(self):
        self.assertEqual(parse_interfaces("sysname R1\nreturn\n"), [])

    def test_行首有空格也能正确识别(self):
        """配置文件里 interface 是顶格的，但如果有缩进也必须认出来。"""
        config = "  interface Vlanif30\n   ip address 10.0.30.1 255.255.255.0\n"
        self.assertEqual(parse_interfaces(config), [("Vlanif30", "10.0.30.1")])

    def test_不把ipv6当成ipv4(self):
        """ipv6 address 那一行开头是 ipv6，不该被 startswith('ip address ') 命中。"""
        config = "interface Vlanif40\n ipv6 address 2001:db8::1/64\n"
        self.assertEqual(parse_interfaces(config), [])

    def test_window换行也能解析(self):
        config = "interface Vlanif50\r\n ip address 10.0.50.1 255.255.255.0\r\n"
        self.assertEqual(parse_interfaces(config), [("Vlanif50", "10.0.50.1")])


class TestGetSysname(unittest.TestCase):
    """测设备自述名字的抽取。"""

    def test_正常(self):
        self.assertEqual(get_sysname("#\nsysname R1\n#\n"), "R1")

    def test_没有sysname返回None(self):
        """返回 None 而不是 ''，调用方才能区分「没找到」和「找到但为空」。"""
        self.assertIsNone(get_sysname("#\nvlan batch 10\n#\n"))
        self.assertIsNone(get_sysname(""))

    def test_名字带下划线和数字(self):
        self.assertEqual(get_sysname("sysname CORE_SW_01\n"), "CORE_SW_01")

    def test_多个空格也能取到最后一段(self):
        """split(None, 1) 按任意空白切，多个空格不会切出空字符串。"""
        self.assertEqual(get_sysname("sysname    R12\n"), "R12")

    def test_行首有空格也能认(self):
        self.assertEqual(get_sysname("  sysname R7\n"), "R7")

    def test_不误匹配以sysname开头的别的词(self):
        """startswith('sysname ') 带尾随空格，所以 sysnamexxx 不会被当成 sysname。"""
        self.assertIsNone(get_sysname("sysname-fake R99\n"))

    def test_取第一个出现的(self):
        config = "sysname FIRST\nsysname SECOND\n"
        self.assertEqual(get_sysname(config), "FIRST")


class TestReportColumns(unittest.TestCase):
    """测中文对齐：这块最容易在没注意时被改坏。"""

    def test_ascii宽度(self):
        self.assertEqual(display_width("R1"), 2)
        self.assertEqual(display_width("192.168.1.1"), 11)

    def test_中文算两个宽度(self):
        self.assertEqual(display_width("成功"), 4)
        self.assertEqual(display_width("认证失败"), 8)

    def test_中英混排(self):
        self.assertEqual(display_width("R1成功"), 6)

    def test_pad补到指定宽度(self):
        self.assertEqual(pad("R1", 8), "R1      ")
        self.assertEqual(display_width(pad("成功", 8)), 8)

    def test_pad不会截断长的(self):
        self.assertEqual(pad("很长的名字", 2), "很长的名字")


class TestCommandTable(unittest.TestCase):
    """测命令表：这里出错的后果是「发错命令」，静默且难查。"""

    def test_华为和h3c都用display(self):
        self.assertEqual(COMMAND_BY_TYPE["huawei"], "display current-configuration")
        self.assertEqual(COMMAND_BY_TYPE["hp_comware"], "display current-configuration")

    def test_思科用show(self):
        self.assertEqual(COMMAND_BY_TYPE["cisco_ios"], "show running-config")

    def test_表里不许有netmiko不认识的device_type(self):
        """
        曾经踩过的坑：表里有个 'h3c' 键，但 netmiko 里没有叫 h3c 的 device_type，
        所以那一条永远匹配不到 —— 死代码。这条测试就是防止再塞进来。
        """
        legal = {
            "huawei", "huawei_vrpv8", "hp_comware", "h3c_comware",
            "cisco_ios", "cisco_xe", "ruijie_os", "generic",
        }
        self.assertTrue(set(COMMAND_BY_TYPE) <= legal,
                        f"表里有可疑的 device_type：{set(COMMAND_BY_TYPE) - legal}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
