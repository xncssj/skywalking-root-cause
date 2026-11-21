# Case 08 · 缓存命中率骤降（统一 TTL 雪崩，实测）

- **日期**：2026-08-15
- **场景**：第 5 章 §5.5 "命中率骤降 → 雪崩" 的 demo 实测
- **链路**：member-svc 新增 `/member/test/cache/{id}?ttlSeconds=`（CacheController）
  - 读路径：`Redis GET goods:detail:{id}` → miss → 查 MySQL → `SETEX` 回填
  - 命中率计数：`/member/test/cache/stats?reset=1`（hit/miss 累计）
- **Redis**：compose 新增 redis:7.0（`--maxmemory 128mb`）
- **SkyWalking 插件**：apm-lettuce 插件自动识别 Redis（component=Lettuce, peer=redis:6379）

## 实验数据

`snowball-stats.txt`（三段式）：

| 阶段 | hit | miss | hit_rate | 说明 |
|------|-----|------|----------|------|
| 预热（100 key, ttl=10s） | - | - | - | 全部 miss 回填 |
| 平稳段（reset 后重打 100） | 100 | 0 | **1.000** | 全部命中 |
| 等 11s（统一 TTL=10 全部到期） | - | - | - | 雪崩窗口 |
| 雪崩段（reset 后重打 100） | 1 | 99 | **0.010** | 几乎全 miss，回源打 DB |

Redis 侧全局计数：`keyspace_hits=206, keyspace_misses=199`。

## Trace 实测（trace-span.txt）

一条雪崩段 miss 请求的 span 树（duration 4856ms）：

```
Entry Tomcat      GET:/member/test/cache/1                    4856ms
├── Exit Lettuce  redis:6379  Lettuce/GET                      11ms   # miss
├── Local HikariCP HikariCP/Connection/getConnection          1743ms  # 雪崩并发回源，连接池排队
├── Exit mysql    mysql:3306  Mysql/JDBC/Statement/executeQuery 100ms  # 回源 DB
└── Exit Lettuce  redis:6379  Lettuce/SETEX                     4ms   # 回填
```

关键点：
1. **命中（平稳段）的 trace 只有 Lettuce/GET**，无 DB span；**miss（雪崩段）才有 mysql 回源**。
2. 雪崩段 100 并发 miss 全打 DB → HikariCP `getConnection` 排队 1743ms（连接池打满），
   DB span 占比从"无"变成显性——这就是"命中率骤降后 DB 占比暴涨"的引擎判定依据。
3. 修复：`SETEX` 改为 `ttlSeconds=10+random(0..5)`（TTL 随机抖动），即可破坏"统一过期"窗口。

## 复现命令

```bash
# 预热 100 key（统一 ttl=10s）
for i in $(seq 1 100); do curl -s "http://localhost:8082/member/test/cache/$i?ttlSeconds=10" >/dev/null; done

# 平稳段（reset 后全命中）
curl -s "http://localhost:8082/member/test/cache/stats?reset=1"
for i in $(seq 1 100); do curl -s "http://localhost:8082/member/test/cache/$i?ttlSeconds=10" >/dev/null; done
curl -s "http://localhost:8082/member/test/cache/stats"

# 等 TTL 统一到期 → 雪崩段
sleep 11
curl -s "http://localhost:8082/member/test/cache/stats?reset=1"
for i in $(seq 1 100); do curl -s "http://localhost:8082/member/test/cache/$i?ttlSeconds=10" >/dev/null; done
curl -s "http://localhost:8082/member/test/cache/stats"
```

## 注意事项

- 雪崩段结束后所有 key 又被回填（新 TTL），重复实验需再等一个 TTL 周期。
- 演示用的是"统一 TTL"这一个雪崩成因；真实雪崩还叠加"内存淘汰"（`maxmemory`+LRU），
  可把 redis 的 `--maxmemory` 调小触发，判据看 `evicted_keys`（§5.4.2）。
