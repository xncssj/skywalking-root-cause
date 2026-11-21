# SkyWalking 性能分析引擎：公开代码与案例

《SkyWalking 性能分析引擎：从端点驱动到 AI 根因分析》专栏的配套代码与实测案例库。

本仓库只收录专栏正文实际引用的可运行代码、环境编排与实测素材；专栏正文（15 章 + 附录 A）在公众号/掘金连载，不在此仓库发布。所有代码均可独立运行，数据已完成脱敏。

## 内容一览

| 目录 | 对应章节 | 内容 |
|------|---------|------|
| [`code/skywalking-rootcause-skill/`](code/skywalking-rootcause-skill/) | 第 2、4—8、11、14 章 | hermes-agent Skill：端点驱动 5 步法 + OS 实证层多源交叉，纯标准库、`--demo` 可离线跑通 |
| [`code/demo-validation/`](code/demo-validation/) | 第 15 章 | 全栈验证环境（三服务 + MySQL + ES/OAP/UI + Prometheus + node-exporter）+ runner + LLM 报告 |
| [`code/demo-validation/cases/`](code/demo-validation/cases/) | 第 4—15 章 | 10 个真实实测案例素材：DB 瓶颈、并发稀释、OOM、缓存雪崩、火焰图、Arthas、GC 日志等 |
| [`code/ch10-pipeline/`](code/ch10-pipeline/) | 第 10 章 | 分层流水线 L1-L7 设计模式骨架（模板方法 / 责任链 / 策略 / Spec DSL），附单元测试 |

## 快速开始

### 1. 根因分析 Skill（无需任何服务）

```bash
# 离线演示：内置 OAP + node-exporter 模拟数据，双源分析直接跑通
python3 code/skywalking-rootcause-skill/scripts/analyze.py --demo

# 单元测试
python3 -m pytest code/skywalking-rootcause-skill/tests/ -q
```

连接真实环境：

```bash
python3 code/skywalking-rootcause-skill/scripts/analyze.py \
  --base-url http://oap-host:12800/graphql \
  --prometheus-url http://prometheus-host:9090 \
  --minutes 15
```

详见 [Skill README](code/skywalking-rootcause-skill/README.md)。

### 2. 端到端验证栈（第 15 章）

```bash
cd code/demo-validation
docker compose up -d
CONCURRENCY=2 ROUNDS=6 bash scripts/generate-traffic.sh
python3 ../skywalking-rootcause-skill/scripts/analyze.py \
  --base-url http://localhost:12800/graphql \
  --prometheus-url http://localhost:9090 \
  --os-instance 10.0.0.5:9100 --minutes 6
```

前提：Docker Desktop（≥7GB 内存）、Python 3、Ollama（可选，用于 LLM 报告）。完整步骤见 [demo-validation README](code/demo-validation/README.md)。

### 3. 分层流水线骨架（第 10 章）

```bash
cd code/ch10-pipeline
python3 -m pytest test_pipeline.py -q
```

## 实测案例

| 案例 | 目录 | 对应章节 | 核心结论 |
|------|------|---------|---------|
| DB 瓶颈完整走查 | [`cases/01-db-bottleneck/`](code/demo-validation/cases/01-db-bottleneck/) | §15.4 | 8-span 分布式 trace，DB_BOTTLENECK 97.7% |
| 并发对比 | [`cases/02-concurrency/`](code/demo-validation/cases/02-concurrency/) | §15.4 | 连接池排队把 DB 占比从 97.7% 稀释到 48.4% |
| member OOM | [`cases/03-member-oom/`](code/demo-validation/cases/03-member-oom/) | §15.8 | 全量缓冲结果集 + 小堆 → Java heap space |
| OS 内存假阳性防护 | [`cases/04-os-memory-fp/`](code/demo-validation/cases/04-os-memory-fp/) | §11.3.2 | R² 门控拦截负载爬升误报 |
| 代码级热点火焰图 | [`cases/05-code-hotspot/`](code/demo-validation/cases/05-code-hotspot/) | §8.6 | async-profiler：arraycopy 34.9%、StringBuilder 热点 |
| Arthas 实录 | [`cases/06-arthas/`](code/demo-validation/cases/06-arthas/) | §9.4 | dashboard / thread -n 3（96.21% 线程）/ trace cpuHot 2713ms |
| 老年代持续上升 | [`cases/07-oom-leak/`](code/demo-validation/cases/07-oom-leak/) | §6.5 | Old% 61%→99.9%，YGC 24→171 空转 |
| 缓存雪崩 | [`cases/08-cache-snowball/`](code/demo-validation/cases/08-cache-snowball/) | §5.5 | 统一 TTL：命中率 1.000→0.010 |
| 线程卡死 | [`cases/09-thread-blocked/`](code/demo-validation/cases/09-thread-blocked/) | §9.4.1 | 单锁持锁 sleep：Arthas BLOCKED 11 线程 |
| G1 GC 日志 + Wall 采样 | [`cases/10-gc-log/`](code/demo-validation/cases/10-gc-log/) | §6.6.1 / §8.6.1 | Pause Young 7.3ms + 锁等待 Wall flat |

> 数据为 2026-08-15 在 Mac + Docker Desktop + Ollama 环境真实跑出，数值会随负载浮动，但判定与证据链稳定。各案例目录内的 README 记录了复现步骤与观察要点。

## 环境要求

- Python 3.8+（Skill 与流水线骨架零第三方依赖，仅标准库）
- 第 15 章验证栈：Docker Desktop ≥7GB 内存，可访问容器镜像源
- 火焰图 / Arthas 案例：需要 JDK 17（`com.sun.tools.attach`）
- LLM 报告：Ollama + `deepseek-r1:1.5b`（可选）

## 数据与脱敏

- 仓库内服务名、表名、主机地址均为脱敏后的示例（`order-svc` / `cart-svc` / `member-svc`、`t_biz_order`、`db-host`、demo 网段 `10.0.0.0/24`）。
- 不包含任何真实生产配置、凭据、Token 或公司代码；`SKYWALKING_TOKEN` 等一律通过环境变量传入。
- 实测素材为验证环境真实产出，读者可对照正文复现同一判定结果。

## 许可

MIT，见 [LICENSE](LICENSE)。
