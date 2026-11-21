# demo-validation：SkyWalking 根因分析端到端验证

对应《SkyWalking 性能分析引擎》第 15 章（从零建 hermes-agent 根因分析链路）。
用**真实组件**（ES + OAP + UI + MySQL + Prometheus + node-exporter + SkyWalking Java Agent）
复现专栏旗舰场景：三服务级联慢查询，最终根因 = MySQL 无索引全表扫描（DB_BOTTLENECK）。

```
用户流量 ─▶ order-svc ─▶ cart-svc ─▶ member-svc ─▶ MySQL(慢查询, 全表扫描)
              │            │            │
              └── SkyWalking Java Agent 上报 ──▶ OAP:11800 ─▶ ES(存储)
                                                     │
                               UI:8383 ◀──────────────┘
              node-exporter(10.0.0.5:9100) ──▶ Prometheus:9090  ─┐
                                                                    ▼
                                           analyze.py(端点5步法) ──▶ LLM 报告(Ollama)
```

## 验证结果（2026-08-15 实测通过）

`./scripts/run-validation.sh` 一键跑通，`analyze.py`（OAP + Prometheus 双源，6 分钟窗口）输出：

```
[判定] DB_BOTTLENECK
[结论] DB 慢查询为主因（建议 EXPLAIN + 检查索引）
[Trace 总耗时] 13142ms  (Exit span 数: 3)
[主导 Exit] member-svc|demo| -> mysql:3306 (mysql-connector-java) 12808ms 占 97.5%
[组件耗时] mysql-connector-java=12808ms, local=334ms

[OS 实证层] CPU 主导=us · 内存泄漏=NONE · 网络瓶颈=否 · 宿主机超载=否
```

> 数值随负载浮动（低并发、池空闲时 DB 占比可达 98%+；有排队时降至 50%+），
> 但判定与证据链稳定为 DB_BOTTLENECK。

| 验收项 | 结果 | 说明 |
|---|---|---|
| 端点 RT 扫描（Step 1） | ✅ | order/cart/member 三个慢端点 RT 6.6-8.3s（>1s 阈值） |
| Trace 追踪（Step 2） | ✅ | 13142ms 分布式 trace，三服务共享同一 traceId，8 个 span |
| 调用链分析（Step 3） | ✅ | 嵌套容器识别正确（order→cart→member 的 Exit 均判定为外壳） |
| 根因判定（Step 4） | ✅ | **DB_BOTTLENECK**，主导 Exit = member→mysql 占 97.5% |
| 证据链（Step 5） | ✅ | L1 端点 / L2 Trace / L3 瓶颈 / L5 SQL 完整（含抽样 SQL） |
| OS 实证层交叉（Ch11） | ✅ | CPU 主导=us、内存无泄漏、网络正常 → 宿主机健康，归因 DB 层 |
| LLM 报告（Ch13/15） | ✅ | deepseek-r1:1.5b 三段式报告，硬约束校验**通过**（根因唯一/数值保真/建议匹配） |
| 单测回归 | ✅ | 23 passed |

LLM 硬约束校验输出（节选）：`【根因类型】DB_BOTTLENECK` 原样输出、主导 Exit 耗时 12808ms 与 peer `mysql:3306` 保真、
处置建议（检查索引）与根因匹配。
> 过程中还验证了硬约束的**拦截能力**：初版 prompt 下模型把 label 改写为描述句，校验器正确报"根因 label 未出现"；
> 强化输出模板后通过——这正是 Ch15 §15.5 硬约束设计要解决的问题。

## 实测中修复的真实问题（对 skill 的价值）

1. **组件名不匹配（真实 OAP 与 demo 数据不同）**：真实 OAP 的 MySQL Exit 组件名是 `mysql-connector-java`、
   HTTP Exit 是 `HttpClient`，原 `DB_COMPONENTS`/`HTTP_COMPONENTS` 集合不含 → 修复为同时收录真实 display name。
2. **时区错位**：sw_client 用宿主本地时区（UTC+8）拼窗口字符串，OAP 容器默认 UTC → 查不到 trace。
   修复：OAP 容器设 `TZ=Asia/Shanghai`。
3. **RestTemplate 默认 JDK HttpURLConnection 无 SkyWalking 插件**（9.7.0 无 jdk-http 插件）→ 无 Exit span、sw8 不传播。
   修复：demo-app 改用 Apache HttpClient 5（`apm-httpclient-5.x-plugin`），链路上下文正常传播。
4. **连接池/堆配置**：全量缓冲结果集（~140MB/查询）+ 高并发 → OOM；Hikari 排队会稀释 DB 占比。
   修复：member 堆 2GB、池 2、低并发（高占比 trace 需无排队）。
5. **OS 内存误报**：Docker VM 内存爬升（45%→86%）被内存泄漏 4 条件命中（HIGH）；内存平稳后重跑 → NONE。
   属 VM 资源约束的真实信号，非引擎缺陷（引擎按设计正确报警）。

## 链路对照（Ch15 验收清单）

| 环节 | 组件 | 端口 | 对应章节 |
|---|---|---|---|
| 存储 | Elasticsearch 8.17.0 | 9222/9322 | Ch3 |
| 分析 | SkyWalking OAP 10.2.0 | 11800/12800/1234 | Ch3/15 |
| 可视化 | SkyWalking UI 10.2.0 | 8383 | - |
| 业务库 | MySQL 8.0（t_biz_order 25 万行，无索引） | 3306 | Ch15 |
| 链路 | order-svc→cart-svc→member-svc（Spring Boot 3.2 / Java 17） | 8080/8081/8082 | Ch15 |
| OS 实证层 | node-exporter 1.7.0（静态 IP 10.0.0.5:9100） | 9100 | Ch11/15 |
| 指标源 | Prometheus 2.48.0 | 9090 | Ch11 |
| AI 报告 | Ollama deepseek-r1:1.5b | 11434 | Ch13/15 |

## 前提

- Docker Desktop（≥7GB 内存），可访问镜像加速源（本项目用 `docker.m.daocloud.io`）
- 本地 Python 3（analyze.py 仅标准库，无第三方依赖）
- Ollama 已装 `deepseek-r1:1.5b`：`ollama pull deepseek-r1:1.5b`

## 快速开始

```bash
# 1. 准备 agent（agent 二进制不入库，首次需从官方镜像提取）
mkdir -p demo-app/agent
cid=$(docker create docker.m.daocloud.io/apache/skywalking-java-agent:9.7.0-java17)
docker cp "$cid:/skywalking/agent" ./demo-app/agent/skywalking-agent
docker rm "$cid"

# 2. 起栈（镜像已缓存时约 1-2 分钟）
docker compose up -d

# 3. 等三服务就绪后造流量（order→cart→member→MySQL 全表扫描）
CONCURRENCY=2 ROUNDS=6 bash scripts/generate-traffic.sh

# 4. 真实分析（OAP + Prometheus 双源）
python3 ../skywalking-rootcause-skill/scripts/analyze.py \
  --base-url http://localhost:12800/graphql \
  --prometheus-url http://localhost:9090 \
  --os-instance 10.0.0.5:9100 --minutes 6

# 5. LLM 报告 + 硬约束校验
python3 ../skywalking-rootcause-skill/scripts/analyze.py \
  --base-url http://localhost:12800/graphql \
  --prometheus-url http://localhost:9090 \
  --os-instance 10.0.0.5:9100 --minutes 6 --json > /tmp/r.json
python3 scripts/llm_report.py /tmp/r.json

# 6. 单测回归
python3 -m pytest ../skywalking-rootcause-skill/tests/ -q
```

> 说明：`run-validation.sh` 为全流程一键脚本；分析窗口建议 ≥6 分钟（OAP 分钟级聚合有延迟），
> 且应在流量停止、内存平稳后跑，避免 OS 层把 VM 内存爬升误判为泄漏。

## 预期结果

- 端点扫描定位最慢端点 `order-svc - GET:/order/test/sql/real-slow`（RT 远高于 1s）
- Trace 分析判定 `DB_BOTTLENECK`，主导 Exit = `member-svc -> mysql:3306 (mysql-connector-java)` 占比 >60%
- 证据链含 L1 端点 / L2 Trace / L3 瓶颈 / L5 SQL（抽样 SQL 为无索引全表扫描语句）
- OS 实证层：CPU 主导、内存无泄漏、网络正常（宿主机健康 → 交叉结论归因 DB）
- LLM 报告硬约束校验通过（根因唯一、数值保真、建议匹配）

## 与 Ch15 的差异说明

1. **Java Agent 版本**：Ch15 写 "Java Agent 10.x"，但 Apache 实际只发布到 9.7.0（archive 可查）。
   本验证用 9.7.0（与 OAP 10.2.0 协议兼容）。
2. **OAP telemetry 端口**：`SW_TELEMETRY=prometheus` 实际监听 **1234**（`SW_TELEMETRY_PROMETHEUS_PORT`），
   非 12800/12300。
3. **OS 实例关联**：`analyze.py` 默认 OS 实证层 instance 为 `10.0.0.5:9100`，
   本栈给 node-exporter 分配静态 IP `10.0.0.5` 对齐；真实环境可用 `--os-instance` 覆盖。
4. **慢查询幅度**：旗舰案例 56s 依赖超大表；本 demo 用 330MB 表 + 64MB buffer pool，
   单查询 RT 约 1.5-3s（仍远超 1s 慢阈值，足以触发完整证据链）。

## 目录结构

```
demo-validation/
├── docker-compose.yml          # Ch15 全栈编排（三服务 + MySQL + node-exporter）
├── prometheus/prometheus.yml   # 抓取配置（node-exporter file_sd + OAP telemetry 1234）
├── prometheus/targets/         # file_sd 目标文件
├── demo-app/                   # 三服务级联慢查询应用（一个 jar 三种角色）
│   ├── agent/                  # SkyWalking Java Agent 9.7.0（二进制不入库，按「快速开始」提取）
│   ├── mysql-init/             # 建表 + 25 万行造数
│   ├── Dockerfile / pom.xml / src
├── cases/                      # 实测案例素材库（见 cases/README.md）
│   ├── 01-db-bottleneck/       # DB_BOTTLENECK 完整走查（result/trace-spans/llm-report）
│   ├── 02-concurrency/         # 并发对比（97.7% vs 48.4%，连接池排队稀释）
│   ├── 03-member-oom/          # member OOM 复现（Xmx128m → Java heap space）
│   ├── 04-os-memory-fp/        # OS 内存爬升≠泄漏（R² 门控）
│   ├── 05-code-hotspot/        # 代码热点：cpuHot + async-profiler 火焰图（itimer/wall）
│   ├── 06-arthas/              # Arthas 实录（thread/trace，含 JRE/JDK attach 坑）
│   ├── 07-oom-leak/            # 老年代持续上升（jstat 曲线 61%→99.9%，YGC 空转）
│   ├── 08-cache-snowball/      # 缓存雪崩（统一 TTL，命中率 1.000→0.010 + Lettuce span）
│   ├── 09-thread-blocked/      # 线程卡死（单锁持锁 sleep，Arthas BLOCKED 11 线程）
│   └── 10-gc-log/              # G1 GC 日志实录（Pause Young 7.3ms + Wall 采样锁等待）
├── scripts/
│   ├── run-validation.sh       # 一键端到端验证
│   ├── run-analysis.sh         # §15.2 真实 runner（双源分析 + LLM 报告）
│   ├── generate-traffic.sh     # 并发慢查询流量
│   ├── llm_report.py           # Ollama LLM 报告 + Ch15 §15.5 硬约束校验
│   └── dump_trace.py           # 导出 trace 的完整 span 列表（案例素材用）
└── tests/
    └── test_llm_report.py      # llm_report 硬约束校验单测（35 用例，python3 -m pytest 即跑）
```

### 代码组件归属（三段来源，谁在哪维护）

| 组件 | 位置 | 来源 | 说明 |
|------|------|------|------|
| 分析核心 `analyze.py` | `../skywalking-rootcause-skill/scripts/` | **Ch14 Skill 交付物** | runner 跨目录调用，**不复制**——Skill 可独立安装复用，复制会造成双源维护 |
| 环境编排 | `docker-compose.yml` + `prometheus/` + `demo-app/` | Ch15 新建 | 一键起全栈（`docker compose up -d`） |
| runner + LLM | `scripts/run-analysis.sh` + `scripts/llm_report.py` | Ch15 新建 | 唯一入口 `./scripts/run-analysis.sh`：采集分析 → LLM 报告 |

全链路**零第三方 Python 依赖**（纯标准库，python3 ≥ 3.8 直接跑），无需 pip install。
