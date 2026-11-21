# cases/：实测案例素材库

第 15 章各小节引用的**真实实测数据**存档。每个案例目录含原始 JSON/日志与复现步骤，
供读者对照文章跑出相同结果。

| 案例 | 目录 | 对应章节 | 核心结论 |
|------|------|---------|---------|
| DB 瓶颈完整走查 | `01-db-bottleneck/` | §15.4 | 8-span 分布式 trace，DB_BOTTLENECK 97.7% |
| 并发对比 | `02-concurrency/` | §15.4 走查要点 | 连接池排队把 DB 占比从 97.7% 稀释到 48.4% |
| member OOM | `03-member-oom/` | §15.8 实测坑 | 全量缓冲结果集 + 小堆 → Java heap space |
| OS 内存假阳性防护 | `04-os-memory-fp/` | §11.3.2 | R² 门控拦截负载爬升误报，稳态读取原则 |
| 代码级热点火焰图 | `05-code-hotspot/` | §8.6 | cpuHot 端点 + async-profiler：arraycopy 34.9%、StringBuilder 热点（Wall/CPU 图） |
| Arthas 实录 | `06-arthas/` | §9.4 | attach 后 dashboard / thread -n 3（96.21% 线程）/ trace cpuHot 2713ms |
| 老年代持续上升 | `07-oom-leak/` | §6.5 | /test/leak 泄漏端点 + jstat：Old% 61%→99.9%，YGC 24→171 空转（G1 下 FGC=0） |
| 缓存雪崩 | `08-cache-snowball/` | §5.5 | /test/cache 统一 TTL：命中率 1.000→0.010，Lettuce span + DB 回源 + 连接池排队 |
| 线程卡死 | `09-thread-blocked/` | §9.4.1 | /test/stuck 单锁持锁 sleep：Arthas thread --state BLOCKED 11 线程 |
| G1 GC 日志 + Wall 采样 | `10-gc-log/` | §6.6.1 / §8.6.1 | -Xlog:gc 实录（Pause Young 7.3ms）+ 锁等待 Wall flat（libc 77.8% 无锁帧） |

> 数据均为 2026-08-15 真实跑出（Mac + Docker Desktop + Ollama deepseek-r1:1.5b）。
> 数值会随负载浮动，但判定/机制稳定。
>
> 05/06 案例复现需要 JDK（Arthas/async-profiler attach 需 `com.sun.tools.attach`）：
> 将 member-svc 换成 `eclipse-temurin:17-jdk` 运行即可，复现步骤见各目录 README。
