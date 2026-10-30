# -*- coding: utf-8 -*-
"""
netcheck 主程序：设备配置巡检。

三层配置，优先级从低到高：
    1. 代码里的 DEFAULTS（兜底，保证永远有值）
    2. config.json（环境相关的，改环境不改代码）
    3. 命令行参数（本次运行临时覆盖）

用法：
    python device_check.py
    python device_check.py --config my.json
    python device_check.py --workers 10               # 命令行压过 json
            "  python device_check.py --devices devices_demo.csv\n"
    python device_check.py --show-config              # 只看最终生效的配置
    python device_check.py --dump-config out.json     # 导出当前配置
    python device_check.py --real                     # 连真机
"""
import argparse
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Windows 的默认控制台编码是 GBK，打印 ⚠ 这类符号会直接
# UnicodeEncodeError 崩掉。reconfigure 把标准输出改成 UTF-8。
# 放在所有 print 之前执行；Linux/macOS 本来就 UTF-8，这段是空操作。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException

from models import Device, InspectResult


# ---------- 路径 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(BASE_DIR, "config.json")

# ---------- 代码里的兜底默认值 ----------
# config.json 缺项、或者压根没这个文件时，用这些。
# 注意：这里是“永远能跑起来”的保证，所以每一项都必须有值。
DEFAULTS = {
    "concurrency": {"max_workers": 5, "max_retry": 3, "retry_wait": 2},
    "connection": {"timeout": 30, "conn_timeout": 10},
    "commands": {
        "huawei": "display current-configuration",
        "hp_comware": "display current-configuration",
        "cisco_ios": "show running-config",
    },
    "paths": {
        "devices": "devices.csv",
        "mock_configs": "mock_configs",
        "reports": "reports",
    },
    "logging": {"level": "INFO", "file": "run.log"},
}

logger = logging.getLogger("inspect")


# ---------- 配置对象 ----------
class Config:
    """把嵌套 dict 包成能点号取值的对象。"""

    def __init__(self, data):
        # 先塞 _data，再逐个变成属性
        object.__setattr__(self, "_data", data)
        for key, value in data.items():
            object.__setattr__(self, key,
                               Config(value) if isinstance(value, dict) else value)

    def __setattr__(self, key, value):
        """
        改属性时同时同步进 _data。

        为什么需要？因为 to_dict() 是从 _data 里读的。
        不加这个方法，cfg.concurrency.max_workers = 10 只会改属性，
        _data 里还是 5，--show-config 打印出来的就是旧的 5。
        """
        object.__setattr__(self, key, value)
        data = self.__dict__.get("_data")
        if isinstance(data, dict) and key != "_data":
            data[key] = value.to_dict() if isinstance(value, Config) else value

    def __repr__(self):
        return f"<Config {sorted(self._data)}>"

    def get(self, dotted, default=None):
        node = self
        for part in dotted.split("."):
            if isinstance(node, Config) and hasattr(node, part):
                node = getattr(node, part)
            elif isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def to_dict(self):
        out = {}
        for key, value in self._data.items():
            if isinstance(value, Config):
                out[key] = value.to_dict()
            elif isinstance(value, dict):
                out[key] = Config(value).to_dict()
            else:
                out[key] = value
        return out


def deep_merge(base, override):
    """
    把 override 盖到 base 上，返回新字典。

    关键：只盖“给了的那几个键”，没给的保持 base 的值。
    所以 config.json 里只写 max_workers 也能跑，max_retry 会自动从 DEFAULTS 来。
    """
    result = {}
    for key in set(base) | set(override):
        b = base.get(key)
        o = override.get(key)
        if isinstance(b, dict) and isinstance(o, dict):
            result[key] = deep_merge(b, o)          # 两边都是字典 -> 递归
        elif o is not None:
            result[key] = o                          # 只给了 override 的值 -> 用它
        else:
            result[key] = b                          # 只给了 base 的值 -> 用它
    return result


def load_config(path):
    """
    读配置文件，缺项用 DEFAULTS 补全。

    三种失败都要给人话：
        文件不存在 / json 写错 / 最外层不是对象
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在：{path}")

    # 用 utf-8-sig 读：Windows 的记事本、PowerShell 的 Out-File 默认会在
    # 文件开头写 3 个不可见字节（BOM，EF BB BF），json.load 遇到它会直接报
    # "Unexpected UTF-8 BOM (decode using utf-8-sig)"。
    # utf-8-sig 会**自动去掉** BOM；没有 BOM 的文件它也能读。
    with open(path, encoding="utf-8-sig") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"配置文件格式错误（{os.path.basename(path)} 第 {e.lineno} 行"
                f"第 {e.colno} 列）：{e.msg}。"
                f"常见原因：多了一个逗号、用了单引号、注释没删。"
            ) from e

    if not isinstance(data, dict):
        raise ValueError(f"配置文件最外层必须是对象 {{ }}，现在是 {type(data).__name__}")

    return Config(deep_merge(DEFAULTS, data))


# ---------- 日志 ----------
def setup_logging(level_name, log_file):
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = logging.Formatter(fmt="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")

    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)

    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.propagate = False


# ---------- 命令行参数 ----------
def build_parser():
    parser = argparse.ArgumentParser(
        description="网络设备配置巡检工具（配置来自 config.json，命令行可覆盖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
        epilog=(
            "示例：\n"
            "  python device_check.py\n"
            "  python device_check.py --workers 10\n"
            "  python device_check.py --devices devices_demo.csv\n"
            "  python device_check.py --show-config\n"
            "  python device_check.py --config prod.json --real\n"
        ),
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, metavar="JSON",
                        help="配置文件路径（默认 config.json；不存在就用代码里的默认值）")
    parser.add_argument("--real", action="store_true",
                        help="真机模式；不写就是 mock 模式")
    parser.add_argument("--devices", default=None, metavar="CSV",
                        help="设备清单路径（默认取配置里的 paths.devices）")
    parser.add_argument("--workers", type=int, default=None, metavar="N",
                        help="并发数（覆盖配置文件里的值）")
    parser.add_argument("--retry", type=int, default=None, metavar="N",
                        help="重试次数（覆盖配置文件里的值）")
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别（覆盖配置文件里的值）")
    parser.add_argument("--show-config", action="store_true",
                        help="打印最终生效的配置后退出（排查“为什么用了这个值”）")
    parser.add_argument("--dump-config", metavar="JSON",
                        help="把最终生效的配置写成 json 文件后退出")
    return parser


# ---------- 参数覆盖配置 ----------
def apply_cli_overrides(cfg, args):
    """
    把命令行给了的参数盖到配置上。

    args.workers 的 default 是 None —— 这样能区分两种状态：
        None      = 用户没敲 --workers，保持配置里的值
        3         = 用户敲了 --workers 3，用 3 覆盖
    如果 default 写成 5，就永远分不清“没敲”和“敲了 5”。
    """
    if args.devices is not None:
        cfg.paths.devices = args.devices
    if args.workers is not None:
        cfg.concurrency.max_workers = args.workers
    if args.retry is not None:
        cfg.concurrency.max_retry = args.retry
    if args.log_level is not None:
        cfg.logging.level = args.log_level
    return cfg


# ---------- 读设备清单 ----------
def load_devices(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"设备清单不存在：{path}")

    devices = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            devices.append(Device.from_row(row))
    return devices


# ---------- 解析配置 ----------
def parse_interfaces(config):
    result = []
    current = None
    for line in config.splitlines():
        line = line.strip()
        if line.startswith("interface "):
            current = line.split(" ", 1)[1]
        elif current and line.startswith("ip address "):
            parts = line.split()
            result.append((current, parts[2]))
            current = None
    return result


def get_sysname(config):
    for line in config.splitlines():
        line = line.strip()
        if line.startswith("sysname "):
            return line.split(None, 1)[1]
    return None


# ---------- 取配置 ----------
def fetch_config_mock(dev, mock_dir):
    path = os.path.join(mock_dir, f"{dev.name}.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def fetch_config_real(dev, cfg):
    params = dev.connect_params()
    # 连接超时也来自配置，不再写死在 models.py 里
    params["timeout"] = cfg.connection.timeout
    params["conn_timeout"] = cfg.connection.conn_timeout

    command = cfg.commands.get(dev.device_type, "display current-configuration")
    max_retry = cfg.concurrency.max_retry
    retry_wait = cfg.concurrency.retry_wait

    for attempt in range(1, max_retry + 1):
        try:
            with ConnectHandler(**params) as conn:
                return conn.send_command(command)
        except NetmikoAuthenticationException:
            raise
        except NetmikoTimeoutException:
            if attempt == max_retry:
                raise
            logger.warning("%s 第 %d/%d 次超时，%d 秒后重试",
                           dev.name, attempt, max_retry, retry_wait)
            time.sleep(retry_wait)


def fetch_config(dev, cfg, mock):
    if mock:
        return fetch_config_mock(dev, cfg.paths.mock_configs)
    return fetch_config_real(dev, cfg)


# ---------- 巡检单台 ----------
def inspect_one(dev, cfg, mock):
    rec = InspectResult(dev.name, dev.ip)
    t0 = time.time()
    try:
        config = fetch_config(dev, cfg, mock)
    except NetmikoAuthenticationException:
        rec.status = "认证失败"
        logger.error("%s 认证失败（用户名/密码/服务类型）", dev.name)
    except NetmikoTimeoutException:
        rec.status = "超时"
        logger.error("%s 连接超时，重试 %d 次后放弃", dev.name, cfg.concurrency.max_retry)
    except FileNotFoundError:
        rec.status = "无数据"
        logger.warning("%s 找不到 mock 配置文件", dev.name)
    except Exception as e:
        rec.status = "异常"
        rec.note = str(e)[:50]
        logger.exception("%s 出现未预期的异常：%s", dev.name, e)
    else:
        rec.status = "成功"
        rec.bytes = len(config.encode("utf-8"))
        rec.ifaces = len(parse_interfaces(config))

        actual = get_sysname(config)
        rec.sysname = actual or "-"
        if actual and actual != dev.name:
            rec.drift = f"清单写 {dev.name}，设备自称 {actual}"
            logger.warning("%s 配置漂移：设备 sysname 是 %s", dev.name, actual)
        else:
            logger.info("%s 成功，%d 字节，%d 个接口", dev.name, rec.bytes, rec.ifaces)

    rec.elapsed = round(time.time() - t0, 2)
    if not rec.ok:
        logger.debug("%s 耗时 %.2f 秒，状态 %s", dev.name, rec.elapsed, rec.status)
    return rec


# ---------- 并发巡检 ----------
def check_all(devices, cfg, mock):
    results = []
    max_workers = cfg.concurrency.max_workers
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(inspect_one, d, cfg, mock) for d in devices]
        for fut in as_completed(futures):
            results.append(fut.result())

    order = {d.name: i for i, d in enumerate(devices)}
    results.sort(key=lambda r: order.get(r.name, 999))
    return results


# ---------- 输出 ----------
def display_width(s):
    return sum(2 if ord(ch) > 127 else 1 for ch in s)


def pad(s, width):
    return s + " " * max(0, width - display_width(s))


def print_report(results, start, cost):
    line = "-" * 88
    print("\n" + "=" * 88)
    print(f"巡检报告  {start}")
    print("=" * 88)
    print(f"{pad('Device', 8)}{pad('IP', 18)}{pad('Sysname', 10)}"
          f"{pad('Status', 12)}{pad('Bytes', 8)}{pad('Ifaces', 8)}Time")
    print(line)
    for r in results:
        print(f"{pad(r.name, 8)}{pad(r.ip, 18)}{pad(r.sysname, 10)}"
              f"{pad(r.status, 12)}{pad(str(r.bytes), 8)}"
              f"{pad(str(r.ifaces), 8)}{r.elapsed}s")
        if r.drift:
            print(f"        ⚠ 配置漂移：{r.drift}")
    print(line)

    ok = sum(1 for r in results if r.ok)
    drift = sum(1 for r in results if r.drift)
    print(f"共 {len(results)} 台，成功 {ok} 台，失败 {len(results) - ok} 台"
          f"，配置漂移 {drift} 台，总耗时 {cost:.2f} 秒")


def save_report(results, cfg):
    os.makedirs(cfg.paths.reports, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(cfg.paths.reports, f"{stamp}.csv")

    fieldnames = ["name", "ip", "sysname", "status",
                  "bytes", "ifaces", "elapsed", "note", "drift"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(r.to_dict() for r in results)

    logger.info("报告已保存：%s", path)
    return path


# ---------- 入口 ----------
def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # 1. 读配置（文件不存在就用 DEFAULTS，不报错 —— 让工具“开箱能跑”）
    if os.path.exists(args.config):
        try:
            cfg = load_config(args.config)
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            return 2
    else:
        if args.config != DEFAULT_CONFIG:
            print(f"❌ 配置文件不存在：{args.config}", file=sys.stderr)
            return 2
        print(f"⚠ 没找到 config.json，使用代码里的默认配置")
        cfg = Config(deep_merge(DEFAULTS, {}))

    # 2. 命令行覆盖
    cfg = apply_cli_overrides(cfg, args)

    # 3. 只看配置 / 导出配置
    if args.show_config:
        print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.dump_config:
        with open(args.dump_config, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"✅ 配置已导出：{args.dump_config}")
        return 0

    # 4. 正常巡检
    setup_logging(cfg.logging.level, cfg.logging.file)
    logger.info("配置来源：%s", os.path.basename(args.config) if os.path.exists(args.config) else "内置默认值")
    logger.info("巡检开始：模式=%s，并发=%d，重试=%d，命令超时=%d",
                "真机SSH" if args.real else "mock",
                cfg.concurrency.max_workers, cfg.concurrency.max_retry,
                cfg.connection.timeout)

    try:
        devices = load_devices(cfg.paths.devices)
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 2

    if not devices:
        logger.error("设备清单是空的：%s", cfg.paths.devices)
        return 2

    start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    results = check_all(devices, cfg, mock=not args.real)
    cost = time.time() - t0

    print_report(results, start, cost)
    save_report(results, cfg)

    ok = sum(1 for r in results if r.ok)
    failed = len(results) - ok
    logger.info("巡检结束：成功 %d，失败 %d，耗时 %.2f 秒", ok, failed, cost)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
