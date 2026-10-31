# -*- coding: utf-8 -*-
"""
安全基线审计：从设备配置文本里检查常见的安全配置风险。

设计要点
========

1. 为什么用「检查函数列表」而不是 if-else 堆在一起
   每项检查是独立的小函数，签名统一：(config) -> (passed, detail, fix)。
   加一项检查 = 在 CHECKS 里加一行，不用改审计主流程。
   这叫「开闭原则」：对扩展开放，对修改关闭。

2. 为什么「配置里没有某一行」也是一种结论
   网络设备配置有个特点：**被关闭的功能通常不显示**。
   所以「telnet server enable 没出现」= telnet 是关的 = 合规。
   而「info-center loghost 没出现」= 没配日志服务器 = 不合规。
   同样是「没有」，含义完全相反 —— 这是写检查时最容易搞错的地方，
   每一项都要单独想清楚「缺了这行是好还是坏」。

3. 为什么分高/中/低三级
   等保 2.0 的三级要求里，不同项的风险权重不一样。
   一个开放的 telnet 和一个没配 NTP，严重程度差一个数量级。
   分级之后才能算出一个有意义的分数，也才能排优先级。

4. 检查项的来源
   参考华为《VRP 安全加固手册》和等保 2.0 三级「安全计算环境」条款。
   不是凭空想的，每一条都能对应到具体的加固建议。
"""
import re

# ---------- 风险等级 ----------
HIGH = "高"
MEDIUM = "中"
LOW = "低"

# 每个等级扣多少分
PENALTY = {HIGH: 15, MEDIUM: 8, LOW: 3}


class Finding:
    """一条审计结论。"""

    def __init__(self, check_id, name, level, passed, detail="", fix=""):
        self.check_id = check_id      # 稳定标识，如 "telnet_off"
        self.name = name              # 人看的名字，如 "Telnet 已关闭"
        self.level = level            # HIGH / MEDIUM / LOW
        self.passed = passed          # True = 合规
        self.detail = detail          # 不合规时的证据（配置原文片段）
        self.fix = fix                # 建议的修复动作

    def __repr__(self):
        flag = "OK" if self.passed else "NG"
        return f"<Finding {flag} [{self.level}] {self.name}>"

    def to_dict(self):
        return {
            "check_id": self.check_id,
            "name": self.name,
            "level": self.level,
            "passed": self.passed,
            "detail": self.detail,
            "fix": self.fix,
        }


# ---------- 工具函数 ----------

def _lines(config):
    """配置切成「去掉首尾空白、去掉空行」的行列表。

    为什么先统一处理：后面每个检查都在做「找某一行」，
    如果每处都自己 strip 一遍，代码会重复十几遍。
    """
    out = []
    for line in config.splitlines():
        line = line.strip()
        if line:
            out.append(line)
    return out


def _find(config, keyword):
    """找到第一行包含 keyword 的，返回它；找不到返回 None。"""
    for line in _lines(config):
        if keyword in line:
            return line
    return None


def _block(config, header_prefix):
    """取出以 header_prefix 开头的那个配置块（缩进比它深的行都算块内）。

    为什么需要它：vty 的认证方式和 ACL 都在
        user-interface vty 0 4
         authentication-mode aaa
         acl 2000 inbound
    这种「父行 + 缩进子行」的结构里，只看单行判断不了。

    算法：
        1. 找到 header 行
        2. 后面的行，只要「有缩进」就算块内
        3. 遇到顶格行（# 或下一个配置段）就结束
    注意：这里用「原始行」判断缩进，不能用 _lines() 的结果（它已经 strip 了）。
    """
    result = []
    inside = False
    for raw in config.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        is_indented = raw[:1] in (" ", "\t")
        if not inside:
            if stripped.startswith(header_prefix):
                inside = True
                result.append(stripped)
        else:
            if is_indented:
                result.append(stripped)
            else:
                break
    return result


# ---------- 十项检查 ----------
# 每个函数签名：(config) -> (passed, detail, fix)

def check_telnet_off(config):
    """Telnet 用明文传输，抓包就能拿到密码。生产环境必须关。"""
    line = _find(config, "telnet server enable")
    if line:
        return False, f"发现：{line}", "undo telnet server enable"
    return True, "", ""


def check_ssh_v1x_off(config):
    """SSHv1 有已知协议缺陷，只允许 v2。"""
    line = _find(config, "ssh server compatible-ssh1x enable")
    if line:
        return False, f"发现：{line}", "undo ssh server compatible-ssh1x enable"
    return True, "", ""


def check_snmp_community(config):
    """SNMP 团体名等同于密码，用 public/private 等于没设密码。"""
    bad = []
    for line in _lines(config):
        if not line.startswith("snmp-agent community"):
            continue
        # 团体名是 community 后面那个词
        parts = line.split()
        if len(parts) >= 4:
            name = parts[3]
            if name.lower() in ("public", "private", "read", "write"):
                bad.append(line)
    if bad:
        return False, f"默认团体名：{bad[0]}", "改用 SNMPv3，或换成随机团体名 + ACL 限制"
    return True, "", ""


def check_no_plaintext_password(config):
    """`password simple xxx` 是明文存储，`display current-configuration` 就能看到。"""
    for line in _lines(config):
        if "password simple " in line:
            return False, f"发现：{line}", "改用 irreversible-cipher 或 cipher"
    return True, "", ""


def check_weak_local_user(config):
    """admin / test / huawei / root 这类用户名是最先被爆破的。"""
    weak = {"admin", "test", "huawei", "root", "guest", "user", "default"}
    for line in _lines(config):
        if not line.startswith("local-user "):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].lower() in weak:
            return False, f"弱用户名：{parts[1]}", "改用不含厂商/角色的用户名"
    return True, "", ""


def check_vty_aaa(config):
    """vty 用 password 认证意味着所有人共用一个密码，无法审计到人。"""
    block = _block(config, "user-interface vty")
    if not block:
        return False, "没有 user-interface vty 配置", "配置 vty 并启用 aaa 认证"
    for line in block:
        if line.startswith("authentication-mode aaa"):
            return True, "", ""
    return False, "vty 下没有 authentication-mode aaa", "authentication-mode aaa"


def check_vty_acl(config):
    """管理面必须限制源地址，否则 SSH 端口对全网开放。"""
    block = _block(config, "user-interface vty")
    if not block:
        return False, "没有 user-interface vty 配置", "在 vty 下绑定管理 ACL"
    for line in block:
        if re.match(r"^acl\s+\d+\s+inbound", line):
            return True, "", ""
    return False, "vty 下没有 acl inbound", "acl 2000 inbound"


def check_http_ftp_off(config):
    """HTTP/FTP 都是明文协议，管理面不该开。"""
    for keyword in ("http server enable", "ftp server enable", "http secure-server enable"):
        line = _find(config, keyword)
        if line:
            return False, f"发现：{line}", f"undo {keyword}"
    return True, "", ""


def check_loghost(config):
    """没有日志服务器，设备被重启后本地日志就没了 —— 出事查不到证据。"""
    if _find(config, "info-center loghost"):
        return True, "", ""
    return False, "没有 info-center loghost", "info-center loghost <syslog服务器IP>"


def check_ntp(config):
    """时间不同步时，日志时间戳对不上，跨设备关联分析做不了。"""
    if _find(config, "ntp-service unicast-server") or _find(config, "ntp-service enable"):
        return True, "", ""
    return False, "没有配置 NTP 服务器", "ntp-service unicast-server <NTP服务器IP>"


# ---------- 检查表 ----------
# 一行一项，加检查只改这里。
CHECKS = [
    ("telnet_off",         "Telnet 已关闭",          HIGH,   check_telnet_off),
    ("ssh_v1x_off",        "SSHv1 已禁用",           HIGH,   check_ssh_v1x_off),
    ("snmp_community",     "SNMP 团体名非默认",       HIGH,   check_snmp_community),
    ("no_plaintext_pass",  "无明文密码",             HIGH,   check_no_plaintext_password),
    ("weak_local_user",    "无弱用户名",             MEDIUM, check_weak_local_user),
    ("vty_aaa",            "vty 使用 AAA 认证",       MEDIUM, check_vty_aaa),
    ("vty_acl",            "vty 绑定管理 ACL",        MEDIUM, check_vty_acl),
    ("http_ftp_off",       "HTTP/FTP 已关闭",         LOW,    check_http_ftp_off),
    ("loghost",            "配置了日志服务器",         LOW,    check_loghost),
    ("ntp",                "配置了 NTP 服务器",        LOW,    check_ntp),
]


def audit_config(config):
    """对一份配置做全量审计，返回 [Finding, ...]。

    单项检查抛异常不该让整份审计崩掉 —— 一项写错只影响那一项，
    所以这里逐个 try。这是「批量处理」的通用原则：
    不要把 N 个独立操作串成一条链，任何一个断了全断。
    """
    findings = []
    for check_id, name, level, func in CHECKS:
        try:
            passed, detail, fix = func(config)
        except Exception as e:                      # noqa: BLE001
            # 检查函数自己写错了，记成一条「无法判定」，不要静默吞掉
            passed, detail, fix = False, f"检查执行失败：{e}", ""
        findings.append(Finding(check_id, name, level, passed, detail, fix))
    return findings


def score_of(findings):
    """把结论折算成 0-100 的分数。

    只扣不合规的项。扣到 0 为止，不会出现负数。
    """
    score = 100
    for f in findings:
        if not f.passed:
            score -= PENALTY.get(f.level, 0)
    return max(0, score)


def count_by_level(findings):
    """统计不合规项按等级的数量，返回 (high, medium, low)。"""
    h = m = l = 0
    for f in findings:
        if f.passed:
            continue
        if f.level == HIGH:
            h += 1
        elif f.level == MEDIUM:
            m += 1
        else:
            l += 1
    return h, m, l
