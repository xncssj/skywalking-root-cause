# 五大根因类型判定表

> 对应专栏第 4-8 章。本表是本技能 `analyze.py` 判定逻辑的理论依据（简化版）。

## 判定优先级（核心判定顺序）

`DB → CACHE → DOWNSTREAM → CODE`（先外部后内部，符合排障直觉）

## 各类型判定条件与排查方向

| 根因类型 | 判定条件（Span 组件耗时占比） | 排查方向 |
|----------|------------------------------|---------|
| **DB_BOTTLENECK** | `MySQL/JDBC` 耗时 > 60% | `EXPLAIN` 看执行计划；检查索引；`SHOW ENGINE INNODB STATUS` 看锁；连接池是否耗尽 |
| **CACHE_BOTTLENECK** | `Redis` 耗时 > 60% | 命中率（`redis-cli info stats`）；`redis-cli --latency`；是否跨机房 |
| **DOWNSTREAM_BOTTLENECK** | `Feign/HTTP`（下游调用）> 60% | 下钻最慢下游；`client_resp_time - server_resp_time` 计算网络延迟 |
| **CODE_LEVEL** | 自身处理耗时 > 70%（无显著 DB/缓存/下游） | Profiling / Arthas `thread -n 3` 定位热点方法；检查并发/锁/算法复杂度 |
| **MULTIPLE** | 无明显单因子 > 阈值 | 人工复核证据链，多指标交叉 |

## 进阶子类型（专栏第 4.4 节 / 5.3 节 / 6.3 节，需配合 OS/DB exporter 数据）

| 父类型 | 子类型 | 判定数据点 |
|--------|--------|-----------|
| DB | DB_SLOW_QUERY | 无锁/连接/IO 异常但 SQL 慢，`EXPLAIN` 确认 |
| DB | DB_CONNECTION_POOL_EXHAUSTED | 连接数/max_connections > 85% |
| DB | DB_IO_BOTTLENECK | cpuIoWait > 30% 且 diskAwait > 50ms |
| DB | DB_LOCK_CONTENTION | innodb_row_lock_current_waits > 0 |
| JVM | JVM_FULL_GC | Old GC 占比 > 10% 且老年代 > 85% |
| JVM | HOST_OVERLOAD | OS CPU user < 20% 且可用内存 < 10%（主机超载非 JVM 自身） |
| CACHE | CACHE_MISS_HIGH | 命中率 < 0.8 |

> 本技能是"定位器"（定位到根因类型 + 证据链）；子类型下钻与修复命令依赖 OS/DB exporter 数据，属专栏第 10 章进阶能力，未内置。

## 输出约定

每次判定**必须输出证据链**，宁可"无证据不下结论"，不可"有结论无证据"。