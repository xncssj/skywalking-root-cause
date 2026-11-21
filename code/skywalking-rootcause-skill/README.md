# skywalking-rootcause

SkyWalking 性能根因分析的 **hermes-agent Skill** 实现。

把「人工在 SkyWalking UI 上逐页排查慢接口」变成可复用的自动化流程：
从慢端点出发 → 走查 Trace 调用链 → 定位瓶颈服务 → 判定根因类型（DB / 缓存 / 下游 / 代码级）
→ 输出带引用的证据链。对应《SkyWalking 性能分析引擎》端点驱动 5 步法（第 2 / 4-8 章）。

**多源交叉（v1.1）**：配置 Prometheus 后，同时拉取 node-exporter 指标，按第 11 章 OS 实证层
做 CPU 主导 / 内存泄漏 / 网络重传三维分析，OS 证据并入证据链（`L3+-OS` / `L4-OS`）。
从"单源 Trace 判定"升级为"Trace + OS 双源交叉"——回答"是应用的问题还是宿主机的问题"。

> 本目录是专栏 `code/` 的可交付物之一，只包含核心分析逻辑与脱敏模拟数据，
> 不依赖任何公司内部代码；按 MIT 许可开源，可自由安装与二次开发。

## 目录结构

```
skywalking-rootcause-skill/
├── SKILL.md                  # hermes-agent Skill 定义（When to Use / Procedure / Pitfalls）
├── scripts/
│   ├── analyze.py            # 端点驱动根因分析（5 步法 + OS 实证层，含 --demo 双源离线演示）
│   ├── query_endpoints.py    # 单查慢端点（5 步法 Step 1）
│   ├── sw_client.py          # SkyWalking OAP GraphQL 零依赖客户端
│   ├── prom_client.py        # Prometheus / node-exporter 只读客户端（第 11 章 OS 实证层数据源）
│   └── os_empirical.py       # OS 实证层三维分析（CPU 主导 / 内存泄漏 4 条件 / 网络重传）
├── references/               # 接口手册 / 根因类型表 / 证据链说明
├── templates/report.md       # 报告三段式输出模板
└── tests/
    ├── test_analyze.py       # 单源判定单元测试（内置模拟数据，无需真实 OAP）
    └── test_multisource.py   # 多源交叉测试（prom_client / os_empirical / analyze 集成）
```

## 安装到 hermes-agent

```bash
# 将本目录放入 hermes-agent 的 skills 目录（视你的安装而定）
ln -s "$(pwd)" ~/.hermes/skills/skywalking-rootcause

# 配置 OAP 地址（可选，默认 http://oap-host:12800/graphql）
hermes config set skywalking.base_url "http://your-oap:12800/graphql"

# 配置 Prometheus 地址（可选，开启 OS 实证层多源交叉）
hermes config set prometheus.base_url "http://your-prometheus:9090"

# 若 OAP 开启认证，设置环境变量（不写入仓库/配置）
export SKYWALKING_TOKEN="<token>"
```

## 命令行用法（不依赖 hermes-agent 可直接跑）

```bash
# 1. 完整根因分析（连接真实 OAP，最近 15 分钟）
python3 scripts/analyze.py --base-url http://oap-host:12800/graphql --minutes 15

# 2. 多源交叉分析（带 Prometheus/node-exporter OS 实证层）
python3 scripts/analyze.py --base-url http://oap-host:12800/graphql \
    --prometheus-url http://prometheus-host:9090 --minutes 15

# 3. 离线演示（双源模拟：OAP + node-exporter 均内置，无服务也能跑通）
python3 scripts/analyze.py --demo

# 4. 单查慢端点
python3 scripts/query_endpoints.py --base-url http://oap-host:12800/graphql --minutes 15
```

运行测试：

```bash
python3 -m pytest tests/ -q        # 或 python3 tests/test_analyze.py
```

零第三方依赖（仅 Python 标准库），Python 3.8+。

## 分析模型

**端点驱动 5 步法**（`analyze.py`）：

1. **端点 RT 扫描**：`top_n(endpoint_resp_time)` 一次请求定位最慢端点
2. **Trace 追踪**：`queryBasicTraces(BY_DURATION)` → `queryTrace` 拉完整 Span 树
3. **调用链分析**：聚合 Span → 定位**最内层主导 Exit span**（根因所在）
4. **根因判定**：DB / CACHE / DOWNSTREAM / CODE_LEVEL 四类 + 失败兜底
5. **证据链输出**：L1-端点 / L2-Trace / L3-瓶颈 / L4-Span / L5-SQL 逐层证据

**OS 实证层三维**（`os_empirical.py`，对应第 11 章 11.3 精简版）：

- **CPU 主导**：us/sy/wa/st 占比最大者 → 排查方向（perf / strace / iostat / steal 查宿主机超卖）
- **内存泄漏**：trend up && R²>=0.7 && mean>=60% && slope>=0.5%/h 四条件同时满足才判泄漏
- **网络**：重传率>5% 判瓶颈；TIME_WAIT>10000 判 NETWORK_CONNECTION_LEAK

**多源交叉（第 11 章）**：SkyWalking 实例 `{uuid}@{IP}` 与 node-exporter `{IP}:9100` 靠 IP 关联；
OS 实证层产出并入证据链（`L3+-OS` / `L4-OS`）。宿主超载（CPU 饱和/内存泄漏）会提示先排除主机
再归因应用（联动第 6 章 JVM-OS 交叉决策树）。未配置 Prometheus 时该层诚实标注 NOT_AVAILABLE。

**Trace 归因（经典坑）**：级联 Trace 中 Entry/Exit span 嵌套计时，简单累加会把耗时算成
数倍并掩盖真实瓶颈。本实现取「**最内层主导 Exit span**」——去掉包着下游 Entry 的容器 Exit
（如 `order→cart` 的 Feign 外壳），对叶子 Exit 按时长排序，最长者即根因。
演示报告（`--demo`）可复现专栏 3.5.8.3 的判定口径：
`cart→db-host:3306 MySQL 55797ms 占 98.8%` → `DB_BOTTLENECK`，且 OS 实证层显示宿主机健康
（CPU us 主导但不高、内存平稳、网络正常）→ 交叉结论：瓶颈确在 DB 调用，非主机超载。

## 安全与隐私

- **只读**：全部查询均为只读 GraphQL / Prometheus HTTP API，不修改 OAP/export 配置、不向目标服务发流量。
- **脱敏**：内置模拟数据已脱敏（`order-svc/cart-svc/member-svc`、`db-host`、`t_biz_order`）；
  真实环境分析时注意输出中的 SQL/服务名可能含业务信息，对外传播前按 [`references/脱敏清单.md`](references/脱敏清单.md) 处理。