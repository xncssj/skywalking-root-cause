---
name: skywalking-rootcause
description: 端点驱动定位 SkyWalking 性能根因并输出证据链结论；可配置 Prometheus 开启 node-exporter OS 实证层多源交叉。
version: 1.1.0
author: Li Wen, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skywalking, apm, performance, root-cause, observability, prometheus, node-exporter]
    related_skills: [hermes-agent-skill-authoring]
    requires_toolsets: [terminal]
    config:
      - key: skywalking.base_url
        description: OAP GraphQL 地址
        default: "http://oap-host:12800/graphql"
        prompt: SkyWalking OAP GraphQL 地址
      - key: prometheus.base_url
        description: Prometheus 地址（node-exporter 数据源，可选；配置后开启 OS 实证层交叉验证）
        default: ""
        prompt: Prometheus 地址（可选，留空则跳过 OS 实证层）
      - key: skywalking.window_minutes
        description: 分析时间窗口（分钟）
        default: 15
        prompt: 默认分析最近多少分钟
---

# SkyWalking 根因分析技能

本技能把「人工在 SkyWalking UI 上逐页排查」变成可复用的自动化流程：从慢端点出发，
走查 Trace 调用链，识别瓶颈服务，判定根因类型（DB / 缓存 / 下游 / 代码级），
并输出带引用的证据链。基于《SkyWalking 性能分析引擎》端点驱动 5 步法。

**多源交叉（v1.1 起）**：配置 `prometheus.base_url` 后，技能同时拉取 node-exporter 指标
（Prometheus HTTP API），按第 11 章 OS 实证层做 CPU 主导 / 内存泄漏 / 网络重传三维分析，
把 OS 证据并入证据链（`L3+-OS` / `L4-OS`）——从"单源 Trace 判定"升级为"Trace + OS 双源交叉"。

本技能只做**只读分析**，不会修改任何 SkyWalking / Prometheus / exporter 配置、不会向目标服务发起流量。

## When to Use

- 用户在 SkyWalking 中发现某个/某些接口响应很慢（RT 高）
- 用户想自动定位"到底是 DB、缓存、下游服务还是代码慢"
- 用户想要一份"根因类型 + 证据链"的可追溯结论（可贴进工单/报告）
- 用户压测后想一键输出性能瓶颈分析
- 用户想确认"是应用的问题还是宿主机的问题"（需配置 Prometheus，走 OS 实证层交叉，第 11 章）

**不要用于**：修复服务本身（本技能只定位，不执行修复命令）；对无 APM 数据的服务做猜测；对无 exporter 的实例下 OS 层结论（会诚实标注 NOT_AVAILABLE）。

## Prerequisites

- SkyWalking OAP GraphQL 可达（默认 `http://oap-host:12800/graphql`，可用 `hermes config` 覆盖）
- 可选：Prometheus 可达（含 node-exporter 采集），开启 OS 实证层多源交叉；不配置则跳过该层
- 可选 token：若 OAP 开启认证，设置环境变量 `SKYWALKING_TOKEN`（不会回显到对话）
- 需要网络可达 OAP / Prometheus；全程无需额外 pip 依赖（纯标准库）

## How to Run

本技能通过 `terminal` 工具调用脚本，无需手工介入：

```
terminal(command="python3 ${HERMES_SKILL_DIR}/scripts/analyze.py --base-url {OAP_URL} --prometheus-url {PROM_URL} --minutes 15", timeout=60000)
```

离线演示（双源模拟数据：OAP + node-exporter 均内置，无真实服务也能跑通）：

```
terminal(command="python3 ${HERMES_SKILL_DIR}/scripts/analyze.py --demo", timeout=30000)
```

## Quick Reference

| 操作 | 命令 |
|------|------|
| 完整根因分析（默认窗口 15min） | `python3 ${HERMES_SKILL_DIR}/scripts/analyze.py --base-url {OAP_URL}` |
| 多源交叉分析（带 OS 实证层） | 追加 `--prometheus-url {PROM_URL}` |
| 调整窗口 | 追加 `--minutes 30` |
| 离线演示（双源模拟） | `python3 ${HERMES_SKILL_DIR}/scripts/analyze.py --demo` |
| 单查慢端点 | `python3 ${HERMES_SKILL_DIR}/scripts/query_endpoints.py --base-url {OAP_URL}` |

## Procedure

1. **确认目标与窗口**：问清用户要看哪个时间窗口（默认最近 15 分钟）。
2. **确认是否有 Prometheus**：若用户部署了 node-exporter/Prometheus，带上 `--prometheus-url` 开启多源交叉；否则如实告知"本次跳过 OS 实证层"。
3. **执行分析脚本**：用 `terminal` 跑 `${HERMES_SKILL_DIR}/scripts/analyze.py`，传入 `--base-url`、`--prometheus-url`（可选）与 `--minutes`。
4. **读取结果**：脚本输出 `[判定][结论][Trace 总耗时][主导 Exit][组件耗时][OS 实证层][证据链][慢端点]`。
5. **组织回复**：用中文把结果整理成三段式——**结论先行**（根因类型 + 一句话解释）、**证据链**（照抄脚本里的逐条证据，含 OS 实证层条目）、**下一步建议**（针对根因类型给出排查方向）。
6. **留白处理**：若判定为 `INSUFFICIENT_DATA`（端点慢但无 Trace），如实告知用户并建议扩大窗口或检查客户端超时配置；不要编造证据。
7. **OS 层留白**：未配置 Prometheus 时证据链会出现 `L3+-OS 未配置 Prometheus`——如实呈现，不假装做了交叉验证。

## Pitfalls

- **不要编造 Trace / SQL 证据**：脚本没返回就不写；证据链每一条必须能在 OAP 里复现。
- **`HEALTHY` 不等于"没问题"**：只是最近窗口无慢端点，SLA/Apdex 异常场景需另查。
- **DB_BOTTLENECK 需要 SQL 佐证**：只有组件耗时占比不够，务必引用 `db.statement` 才能下结论是慢查询而非连接池。
- **Base64 service/endpoint ID 不是给人看的**：回复用户时始终用服务名 + 端点路径（脚本已解出）。
- **压测场景的 cpm 集中属正常**：多个服务同时慢时，聚焦最长 Trace 而不是逐个服务展开。
- **OS 层不能替代应用层证据**：node-exporter 只说"宿主机是否超载"；CPU 主导 us 也不能下"代码慢"结论，应用层判定仍以 Trace 为准（第 11 章分工原则：桥接粗判 + 实证细判）。
- **实例名解析**：SkyWalking 实例 `{uuid}@{IP}` 与 node-exporter `{IP}:9100` 靠 IP 关联；K8s PodName 无 IP 时 OS 层自动降级为 NOT_AVAILABLE（第 6 章 6.4）。

## Verification

- 运行 `python3 ${HERMES_SKILL_DIR}/scripts/analyze.py --demo` 能输出完整八块报告且判定为 `DB_BOTTLENECK`，证据链含 `L3+-OS`（CPU 主导=us / 内存 NONE / 网络正常）。
- 真实 OAP 环境下，抽样验证证据链里的 `L2-Trace`（traceId）确实在 SkyWalking UI 中能找到。
- 真实 Prometheus 环境下，`OS 实证层` 输出的 CPU 主导类型与 `node_cpu_seconds_total` 实际分布一致。
- 判定结论与证据链条目数一致（有判定必有证据，无证据不下结论）。

## References

- `references/api-queries.md` — SkyWalking GraphQL 接口速查（L1-L6 完整 curl 模板）
- `references/root-cause-types.md` — 五大根因类型判定表 + 排查方向
- `references/evidence-chain.md` — 证据链 JSON 结构说明
- `scripts/prom_client.py` — Prometheus / node-exporter 只读客户端（纯标准库）
- `scripts/os_empirical.py` — OS 实证层三维分析（CPU 主导 / 内存泄漏 4 条件 / 网络重传）

## Templates

- `templates/report.md` — 报告三段式输出模板（结论 / 证据链 / 建议）