# -*- coding: utf-8 -*-
"""
安全基线审计的测试。

为什么这些测试特别值得写
========================

前面测试测的是「解析对不对」（函数输入输出）。
这里测的是「安全结论对不对」—— 一旦写反，后果是：
    · 把合规判成不合规 → 运维白改一通
    · 把不合规判成合规 → 真问题被掩盖，比不检查更危险

第二种是「静默错误」，所以每项检查都要有正反两个用例：
    ① 有问题的配置 → 必须判 NG，且 detail 里要带上证据原文
    ② 没问题的配置 → 必须判 OK

只测一种的测试是假测试 —— 它证明不了「反过来也能判对」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security import (
    HIGH, LOW, MEDIUM,
    audit_config, check_http_ftp_off, check_loghost, check_no_plaintext_password,
    check_ntp, check_snmp_community, check_ssh_v1x_off, check_telnet_off,
    check_vty_aaa, check_vty_acl, check_weak_local_user,
    count_by_level, score_of,
)


class TestTelnet(unittest.TestCase):

    def test_开了telnet要报(self):
        ok, detail, fix = check_telnet_off("telnet server enable")
        self.assertFalse(ok)
        self.assertIn("telnet server enable", detail)
        self.assertIn("undo", fix)

    def test_没开telnet算合规(self):
        """设备默认不开 telnet，配置里就没这行 —— 「没有」是好消息。"""
        ok, _, _ = check_telnet_off("sysname R1\n#\nreturn")
        self.assertTrue(ok)


class TestSnmp(unittest.TestCase):

    def test_public团体名要报(self):
        ok, detail, _ = check_snmp_community("snmp-agent community read public")
        self.assertFalse(ok)
        self.assertIn("public", detail)

    def test_private团体名要报(self):
        ok, _, _ = check_snmp_community("snmp-agent community write private")
        self.assertFalse(ok)

    def test_随机团体名算合规(self):
        ok, _, _ = check_snmp_community("snmp-agent community read NetMon_2026")
        self.assertTrue(ok)

    def test_大小写不敏感(self):
        """PUBLIC 和 public 一样危险，不能因为大小写漏判。"""
        ok, _, _ = check_snmp_community("snmp-agent community read PUBLIC")
        self.assertFalse(ok)


class TestPlaintextPassword(unittest.TestCase):

    def test_password_simple要报(self):
        ok, detail, _ = check_no_plaintext_password(
            "local-user test password simple Huawei@123")
        self.assertFalse(ok)
        self.assertIn("password simple", detail)

    def test_irreversible算合规(self):
        ok, _, _ = check_no_plaintext_password(
            "local-user netdev password irreversible-cipher %^%#x^%^#")
        self.assertTrue(ok)


class TestWeakUser(unittest.TestCase):

    def test_admin要报(self):
        ok, detail, _ = check_weak_local_user(
            "local-user admin password irreversible-cipher x")
        self.assertFalse(ok)
        self.assertIn("admin", detail)

    def test_netdev算合规(self):
        ok, _, _ = check_weak_local_user(
            "local-user netdev password irreversible-cipher x")
        self.assertTrue(ok)


class TestVty(unittest.TestCase):

    GOOD = """#
sysname R2
#
user-interface vty 0 4
 authentication-mode aaa
 acl 2000 inbound
#
return
"""

    BAD_PASSWORD_MODE = """#
user-interface vty 0 4
 authentication-mode password
#
return
"""

    def test_aaa加acl算合规(self):
        ok, _, _ = check_vty_aaa(self.GOOD)
        self.assertTrue(ok)
        ok, _, _ = check_vty_acl(self.GOOD)
        self.assertTrue(ok)

    def test_password认证要报(self):
        ok, detail, _ = check_vty_aaa(self.BAD_PASSWORD_MODE)
        self.assertFalse(ok)
        self.assertIn("authentication-mode aaa", detail)

    def test_没有vty段也要报(self):
        """设备上没配 vty 意味着用的是默认配置，默认往往是最弱的。"""
        ok, detail, _ = check_vty_aaa("sysname R5\n#\nreturn")
        self.assertFalse(ok)
        self.assertIn("没有", detail)

    def test_块只取到下一个顶格行为止(self):
        """vty 块下面的 acl 不能跨段串到别的配置里去。"""
        config = """#
user-interface vty 0 4
 authentication-mode password
#
acl 2000
 rule 5 permit
#
return
"""
        ok, _, _ = check_vty_acl(config)
        self.assertFalse(ok, "acl 2000 在 vty 段外面，不该被算成 vty 绑定的 ACL")


class TestHttpFtp(unittest.TestCase):

    def test_http要报(self):
        ok, detail, _ = check_http_ftp_off("http server enable")
        self.assertFalse(ok)
        self.assertIn("http server enable", detail)

    def test_ftp要报(self):
        ok, _, _ = check_http_ftp_off("ftp server enable")
        self.assertFalse(ok)

    def test_都没开算合规(self):
        ok, _, _ = check_http_ftp_off("sysname R2\nreturn")
        self.assertTrue(ok)


class TestLoghostAndNtp(unittest.TestCase):

    def test_没有loghost要报(self):
        ok, _, _ = check_loghost("sysname R1\nreturn")
        self.assertFalse(ok)

    def test_有loghost算合规(self):
        ok, _, _ = check_loghost("info-center loghost 192.168.106.200")
        self.assertTrue(ok)

    def test_没有ntp要报(self):
        ok, _, _ = check_ntp("sysname R1\nreturn")
        self.assertFalse(ok)

    def test_有ntp算合规(self):
        ok, _, _ = check_ntp("ntp-service unicast-server 192.168.106.201")
        self.assertTrue(ok)


class TestSshV1(unittest.TestCase):

    def test_兼容v1要报(self):
        ok, _, _ = check_ssh_v1x_off("ssh server compatible-ssh1x enable")
        self.assertFalse(ok)

    def test_显式disable算合规(self):
        ok, _, _ = check_ssh_v1x_off("ssh server compatible-ssh1x disable")
        self.assertTrue(ok)


class TestAuditAggregate(unittest.TestCase):

    def test_返回十项结论(self):
        findings = audit_config("sysname R1\nreturn")
        self.assertEqual(len(findings), 10)
        for f in findings:
            self.assertIn(f.level, (HIGH, MEDIUM, LOW))
            self.assertIsInstance(f.passed, bool)

    def test_全合规是满分(self):
        """一份照着加固手册配出来的配置，应该拿 100 分。"""
        config = """#
sysname R2
#
ssh server compatible-ssh1x disable
#
user-interface vty 0 4
 authentication-mode aaa
 acl 2000 inbound
#
local-user netdev password irreversible-cipher %^%#x^%^#
#
snmp-agent community read NetMon_2026
#
info-center loghost 192.168.106.200
#
ntp-service unicast-server 192.168.106.201
#
return
"""
        findings = audit_config(config)
        bad = [f for f in findings if not f.passed]
        self.assertEqual(bad, [], f"不该有不合规项，实际：{[f.name for f in bad]}")
        self.assertEqual(score_of(findings), 100)

    def test_最差的配置扣到0为止(self):
        """一份什么都没配的配置，扣分不能变成负数。"""
        findings = audit_config("sysname R5\nreturn")
        self.assertGreaterEqual(score_of(findings), 0)

    def test_单项检查抛异常不影响其他项(self):
        """一项写错只该让那一项失败，不该让整份审计崩掉。"""
        import security
        original = security.CHECKS
        try:
            def boom(config):
                raise RuntimeError("故意炸")
            security.CHECKS = original + [("boom", "会炸的检查", HIGH, boom)]
            findings = audit_config("sysname R1\nreturn")
            self.assertEqual(len(findings), 11)
            crashed = [f for f in findings if f.check_id == "boom"][0]
            self.assertFalse(crashed.passed)
            self.assertIn("检查执行失败", crashed.detail)
        finally:
            security.CHECKS = original

    def test_按等级统计(self):
        findings = audit_config("telnet server enable\nsnmp-agent community read public\n")
        high, medium, low = count_by_level(findings)
        self.assertEqual(high, 2)
        self.assertGreater(medium + low, 0)

    def test_高危扣15分(self):
        findings = audit_config("telnet server enable")
        # telnet 一项高危 = 扣 15，加上其他低危项，分数应低于 100
        self.assertLess(score_of(findings), 100)
        self.assertGreaterEqual(score_of(findings), 0)


class TestRealSamples(unittest.TestCase):
    """用 mock_configs 里的真实样本跑一遍。

    这些样本是刻意造的：
        R2 = 全合规（100 分）
        R4 = 最差（telnet 之外什么坏毛病都有）
        R5 = 啥都没配
    它们同时被 --devices devices_demo.csv 用，所以这份测试也顺带验证了
    「演示数据本身没被改坏」。
    """

    def _config(self, name):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "mock_configs", f"{name}.txt")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_R2是满分(self):
        findings = audit_config(self._config("R2"))
        self.assertEqual(score_of(findings), 100)

    def test_R1有telnet问题(self):
        findings = audit_config(self._config("R1"))
        telnet = [f for f in findings if f.check_id == "telnet_off"][0]
        self.assertFalse(telnet.passed)

    def test_R4有明文密码(self):
        findings = audit_config(self._config("R4"))
        plain = [f for f in findings if f.check_id == "no_plaintext_pass"][0]
        self.assertFalse(plain.passed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
