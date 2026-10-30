# -*- coding: utf-8 -*-
"""
数据模型：Device（一台待巡检设备）与 InspectResult（单台设备的巡检结果）。

为什么用类而不是字典：
    1. 字段清单写在 __init__ 里 —— 少传就报错，不用等跑到一半才 KeyError
    2. 默认值只写一次 —— 不用每个调用方都抄一遍
    3. 拼错属性名报 AttributeError，带类名，位置精确
    4. 能在对象身上挂方法（ok / to_dict），逻辑跟着数据走

字典版本的问题在于字段集合是「运行时动态长出来的」：
    rec = {"name": "R1", ...}
    rec["note"] = "..."          # 有异常才加这个键
    ...
    rec.get("drift")             # 于是到处得用 .get() 兜底

类版本在 __init__ 里就把 9 个字段全部预置好，
所以 r.drift 永远存在（是空字符串还是告警文字的区别），不用兜底。
"""


class Device:
    """一台待巡检的网络设备。"""

    def __init__(self, name, ip, device_type="huawei",
                 username="", password="", port=22):
        self.name = name
        self.ip = ip
        self.device_type = device_type
        self.username = username
        self.password = password
        self.port = int(port)

    @classmethod
    def from_row(cls, row):
        """
        @classmethod：不用先有对象就能调用，cls 是类本身。

        专门用来把 csv.DictReader 吐出来的一行字典，转成 Device。
        row 里每个值都是字符串（CSV 天生全是文本），所以 port 要转 int。
        """
        return cls(
            name=row["name"].strip(),
            ip=row["ip"].strip(),
            device_type=(row.get("device_type") or "huawei").strip(),
            username=(row.get("username") or "").strip(),
            password=row.get("password") or "",
            port=int(row.get("port") or 22),
        )

    def connect_params(self):
        """把对象转成 netmiko 要的参数字典。"""
        return {
            "device_type": self.device_type,
            "host": self.ip,
            "username": self.username,
            "password": self.password,
            "port": self.port,
            "timeout": 30,
            "conn_timeout": 10,
        }

    def __repr__(self):
        # 给开发看的：一眼能看出是哪个对象、里面装了什么
        return f"<Device {self.name} {self.ip}:{self.port}>"

    def __str__(self):
        # 给用户看的：报告里、日志里出现的那个样子
        return f"{self.name}({self.ip})"


class InspectResult:
    """一台设备的巡检结果。字段先全填默认值，巡检过程中再覆盖。"""

    def __init__(self, name, ip):
        self.name = name
        self.ip = ip
        self.sysname = "-"
        self.status = "未知"
        self.bytes = 0
        self.ifaces = 0
        self.elapsed = 0.0
        self.note = ""
        self.drift = ""

    @property
    def ok(self):
        """property：像读属性一样读它，不用加括号。即r.ok而不是r.ok()"""
        return self.status == "成功"

    def to_dict(self):
        """转回普通字典，给 csv.DictWriter 用。"""
        return {
            "name": self.name,
            "ip": self.ip,
            "sysname": self.sysname,
            "status": self.status,
            "bytes": self.bytes,
            "ifaces": self.ifaces,
            "elapsed": self.elapsed,
            "note": self.note,
            "drift": self.drift,
        }

    def __repr__(self):
        return f"<InspectResult {self.name} {self.status} {self.bytes}B>"
