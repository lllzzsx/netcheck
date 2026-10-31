# netcheck · 网络设备配置巡检与安全基线审计工具

[![tests](https://img.shields.io/badge/tests-91%20passed-brightgreen)](#测试)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](#环境)

批量登录华为/华三/思科设备，抓取当前配置，统计接口数量与配置体积，对**配置漂移**（设备自称的 sysname 与清单不符）告警，并可选做**安全基线审计**（Telnet/SNMP/ACL 等 10 项风险检查），最后输出终端报表和 CSV 存档。

面向的场景：网络运维里"先备份、再变更"的那一步。改配置前先确认手里这份配置确实属于这台设备，避免把 A 的配置刷到 B 上。

---

## 功能

| 能力 | 说明 |
|---|---|
| 并发巡检 | 线程池并发，默认 5 并发，实测 5 台设备 0.01 秒（mock） |
| 失败重试 | SSH 超时按 `max_retry` 次重试，间隔 `retry_wait` 秒；认证失败不重试（重试也不会成功） |
| 配置漂移检测 | 解析配置里的 `sysname`，与清单里的设备名比对，不一致即告警 |
| **安全基线审计** | `--audit` 检查 10 项安全风险（Telnet/SSHv1/SNMP 默认团体名/明文密码/弱用户名/vty 认证/vty ACL/HTTP-FTP/日志外发/NTP），按高/中/低分级评分 |
| 三层配置 | 代码内置默认值 < `config.json` < 命令行参数，优先级明确 |
| 双模式运行 | `--real` 走 SSH 连真机；不写则读本地文件（mock），无需设备即可演示和测试 |
| 可测试 | 解析、配置、模型、安全四层共 91 个单元测试，不依赖任何网络 |

---

## 目录结构

```
netcheck/
├── device_check.py          # 主程序：命令行、并发调度、报表输出
├── models.py                # 数据模型：Device（设备）/ InspectResult（单台结果）
├── security.py              # 安全基线审计：10 项检查 + 评分
├── config.json              # 运行配置：并发数、超时、命令表、路径
├── requirements.txt         # 依赖
├── devices.example.csv      # 设备清单模板（真实 devices.csv 不入库）
├── devices_demo.csv         # 演示清单，配 mock_configs/ 可直接跑
├── mock_configs/            # 演示用的设备配置样本
├── tests/
│   ├── test_models.py       # 模型层 14 条
│   ├── test_parsers.py      # 解析层 23 条
│   ├── test_config.py       # 配置层 22 条
│   └── test_security.py     # 安全层 32 条
└── reports/                 # 巡检报告输出（不入库）
```

---

## 快速开始

```bash
# 1. 依赖
python -m pip install -r requirements.txt

# 2. 免设备演示（读 mock_configs/ 里的样本文件）
python device_check.py --devices devices_demo.csv

# 3. 连真机
cp devices.example.csv devices.csv      # 然后填上真实 IP / 账号
python device_check.py --real
```

演示模式输出：

```
巡检报告  2026-10-31 22:28:05
========================================================================================
Device  IP                Sysname   Status      Bytes   Ifaces  Time
----------------------------------------------------------------------------------------
R1      192.168.106.101   R1        成功        364     2       0.0s
R2      192.168.106.102   R2        成功        488     2       0.0s
R3      192.168.106.103   R3        成功        247     1       0.0s
R4      192.168.106.104   R6        成功        316     2       0.0s
        ⚠ 配置漂移：清单写 R4，设备自称 R6
R5      192.168.106.105   R5        成功        159     2       0.0s
----------------------------------------------------------------------------------------
共 5 台，成功 5 台，失败 0 台，配置漂移 1 台，总耗时 0.01 秒
```

`R4` 那一行是刻意做的样本：清单说它是 R4，配置文件里写着 `sysname R6`。真实环境里这种情况通常是 IP 复用、清单过期，或者配置从别处拷来忘了改名——最危险的是你以为备份了 R4，其实备份的是 R6。

加上 `--audit` 之后，同一份数据会多出安全审计部分：

```bash
python device_check.py --devices devices_demo.csv --audit
```

```
Device  IP                Sysname   Status      Bytes   Ifaces  Time    Score
----------------------------------------------------------------------------------------
R1      192.168.106.101   R1        成功        364     2       0.0s    40
        ✗ [高] Telnet 已关闭
            发现：telnet server enable
            建议：undo telnet server enable
        ✗ [高] SNMP 团体名非默认
            默认团体名：snmp-agent community read public
            建议：改用 SNMPv3，或换成随机团体名 + ACL 限制
        ✗ [中] vty 使用 AAA 认证
            vty 下没有 authentication-mode aaa
            建议：authentication-mode aaa
R2      192.168.106.102   R2        成功        488     2       0.0s    100
R4      192.168.106.104   R6        成功        316     2       0.0s    37
        ✗ [高] 无明文密码
            发现：local-user test password simple Huawei@123
            建议：改用 irreversible-cipher 或 cipher
...
----------------------------------------------------------------------------------------
共 5 台，成功 5 台，失败 0 台，配置漂移 1 台，总耗时 0.01 秒

========================================================================================
安全基线汇总
========================================================================================
共审计 5 台，全部合规 1 台，平均分 69.8
不合规项合计：高危 4 个，中危 8 个，低危 9 个

问题排行（按影响设备数）：
  配置了 NTP 服务器         4 台
  vty 使用 AAA 认证         3 台
  vty 绑定管理 ACL          3 台
  配置了日志服务器          3 台
  SNMP 团体名非默认         2 台
  无弱用户名                2 台
  HTTP/FTP 已关闭           2 台
  Telnet 已关闭             1 台
  无明文密码                1 台
```

`问题排行` 是按「影响设备数」排序的 —— 它回答的是「我该先修哪一类问题」，比逐台看明细有用。

---

## 命令行参数

| 参数 | 作用 | 默认 |
|---|---|---|
| `--config JSON` | 指定配置文件 | `config.json` |
| `--real` | 连真机（不写则 mock） | mock |
| `--devices CSV` | 指定设备清单 | 配置里的 `paths.devices` |
| `--audit` | 同时做安全基线审计 | 关闭 |
| `--workers N` | 并发数 | 配置文件里的 5 |
| `--retry N` | SSH 超时重试次数 | 配置文件里的 3 |
| `--log-level LV` | DEBUG / INFO / WARNING / ERROR | 配置文件里的 INFO |
| `--show-config` | 打印最终生效的配置后退出 | — |
| `--dump-config JSON` | 把最终生效的配置导出成文件 | — |

`--show-config` 是排查"为什么这次用了 5 个并发"的入口。三层优先级合成之后的值，看这一条命令就够。

---

## 配置

`config.json` 五个节：

```json
{
  "concurrency": { "max_workers": 5, "max_retry": 3, "retry_wait": 2 },
  "connection":  { "timeout": 30, "conn_timeout": 10 },
  "commands": {
    "huawei":     "display current-configuration",
    "hp_comware": "display current-configuration",
    "cisco_ios":  "show running-config"
  },
  "paths":    { "devices": "devices.csv", "mock_configs": "mock_configs", "reports": "reports" },
  "logging":  { "level": "INFO", "file": "run.log" }
}
```

优先级固定的三层，合并规则是**逐键深合并**（`deep_merge`）：`config.json` 里只写 `max_workers`，`max_retry` 和 `retry_wait` 会自动从代码里的 `DEFAULTS` 补上，不会被整节覆盖掉。

```bash
# 命令行压过 json：显示 max_workers 变成 10，其余不变
python device_check.py --show-config --workers 10
```

---

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部设备成功 |
| 1 | 有设备失败（超时/认证失败/无数据） |
| 2 | 程序没跑起来（参数错、配置错、清单缺失） |

可以直接接在脚本后面：`python device_check.py --real || echo "有设备没巡检到"`。

---

## 测试

```bash
python -m pytest -q
# 91 passed
```

测试全部针对纯函数和纯数据，**不连任何设备**，所以能在没有 lab 环境的机器上跑。

四个文件的分工：

- `test_models.py` — 模型层的默认值、`from_row` 的必填校验、`to_dict` 的字段完整性
- `test_parsers.py` — `parse_interfaces` 对主/从 IP 的处理、`get_sysname` 的多种配置格式、中文对齐宽度
- `test_config.py` — `deep_merge` 的递归与不覆盖语义、`Config` 的属性与 `_data` 同步、配置文件各种损坏形式
- `test_security.py` — 10 项安全检查的正反两面，以及评分、统计、异常隔离

其中几条值得单说：

- `test_json写错抛ValueError并带行列号` — 配置文件写坏时，报错必须指出第几行第几列，否则用户只能靠肉眼数逗号
- `test_带BOM的文件也能读` — Windows 记事本和 PowerShell 默认会写 BOM，用 `utf-8` 读会直接失败
- `test_改属性会同步进to_dict` — 这条防的是一个真实出现过的 bug：改了 `cfg.max_workers`，但 `--show-config` 打印的还是旧值
- `test_块只取到下一个顶格行为止` — vty 段下面的 `acl 2000 inbound` 才算绑定，段外同名的 `acl 2000` 不能误判
- `test_单项检查抛异常不影响其他项` — 一项检查写错只该让那一项失败，不该让整份审计崩掉

---

## 实现要点

**并发与重试的边界。** 认证失败立即抛出，不走重试——密码错了重试一百次也是错的，白等。只有超时才重试，因为超时往往是网络抖动或设备忙。这个判断在 `fetch_config_real()` 里。

**结果顺序。** 并发执行的完成顺序不确定，但报表必须按清单顺序排。`check_all()` 用 `{设备名: 原索引}` 做了一次重排，所以输出稳定，diff 两份报告时不会因为顺序抖动而误判。

**配置对象。** `Config` 把嵌套字典包成可点号取值的对象，同时保留一份原始 `_data` 供 `json.dump` 使用。两份数据靠 `__setattr__` 同步（对比：用 `@property` 要为每个键写一对方法，13 个键就是 26 个方法，且配置文件加键要回来补）。

**安全基线检查的组织方式。** 10 项检查是 10 个独立函数，签名统一 `(config) -> (passed, detail, fix)`，全部登记在 `security.CHECKS` 表里。加一项检查只需在表里加一行，审计主流程（`audit_config`）一行都不用改。单项检查内部抛异常只影响该项，会被记成「检查执行失败」而不是让整份审计崩掉。

**「没有某行」的双向含义。** 网络设备配置有个特点：被关闭的功能通常不显示。所以 `telnet server enable` 没出现 = telnet 是关的 = 合规；而 `info-center loghost` 没出现 = 没配日志服务器 = 不合规。同样是「没有」，含义完全相反——每项检查都要单独想清楚这一点，写错就会得出相反的结论。

**编码。** 读写配置和清单统一用 `utf-8-sig`，兼容带 BOM 的文件；日志和 CSV 用 `utf-8`/`utf-8-sig` 写出，Excel 打开不乱码。

---

## 已知边界

- 漂移只比对 `sysname` 一个字段。设备名对但 ACL、路由策略变了，本工具发现不了。完整方案是存基线配置做全文 diff（Oxidized / NAPALM 做的事），这里只做最便宜的那一档。
- 只在华为 eNSP 和 mock 样本上验证过，华三/思科的命令表来自 netmiko 的 device_type 约定，未做实机验证。
- `devices.csv` 里的密码是明文。生产环境应该改成环境变量或凭证库读取，当前实现是教学简化。
- 安全基线只检查「配置里写了什么」，检查不了「配置该写但没写」的复杂策略（比如 ACL 内容是否真的生效、密码复杂度是否达标）。10 项都是能用字符串匹配判定的，更深的检查需要解析 ACL 规则或连设备验证。
- 检查项只覆盖华为 VRP 的命令语法。华三/思科的对应命令不同，需要各自的规则集。

---

## 依赖

- Python 3.10+
- [netmiko](https://github.com/ktbyers/netmiko) 4.0+（设备 SSH 交互）
- pytest（仅测试用）

---

## 演进过程

这个工具是分五天长出来的，每一步解决上一步暴露的问题：

| 阶段 | 做了什么 | 为什么 |
|---|---|---|
| 1 | 单文件串行脚本 | 先跑通"连上、发命令、存文件" |
| 2 | 拆出 `models.py`，字典改成类 | 字段清单集中定义，拼错当场报错 |
| 3 | 加 `argparse` + `logging` | 参数不能再靠改代码；日志要能事后回溯 |
| 4 | 加配置漂移检测 | 备份错设备是最贵的错误 |
| 5 | 加单元测试 | 改一个函数不确认其余有没有坏，等于没有重构 |
| 6 | 抽出 `config.json` | 换环境不该改代码 |
| 7 | 整理目录、写 README、上 Git | 别人（和面试官）要能看懂、能跑起来 |
| 8 | 加安全基线审计（`--audit`） | 配置漂移只能发现「设备是谁」，发现不了「设备配得安不安全」 |

---

## License

MIT
