# SkyWalking 根因分析报告

> 模板：本技能向用户汇报的统一三段式结构。替换 `{{ }}` 占位内容。

---

## 结论

**根因类型**：{{ DB_BOTTLENECK / CACHE_BOTTLENECK / DOWNSTREAM_BOTTLENECK / CODE_LEVEL }}

{{ 一句话总结，例如：order-svc 的 GET:/order/test/sql/real-slow 慢是因为对 MySQL 的查询未走索引，DB Span 耗时占比 98.8%。 }}

---

## 证据链

| 层级 | 证据 | 引用 |
|------|------|------|
| L1-端点 | {{ order-svc - GET:/order/test/sql/real-slow RT=56057ms }} | {{ endpoint_id }} |
| L2-Trace | {{ 最慢 Trace demo.trace.001 耗时 56501ms }} | {{ traceId }} |
| L3-瓶颈 | {{ 瓶颈服务 cart-svc 占比 98.8% }} | {{ serviceName }} |
| L4-Span | {{ DB span 占比 98.8%, peer=db-host:3306 }} | {{ peer }} |
| L5-SQL | {{ SELECT * FROM t_biz_order WHERE status=? }} | {{ peer }} |

> 每条证据均可在 SkyWalking UI 中复现。

---

## 下一步建议（按根因类型）

- **DB**：`EXPLAIN` 看执行计划 → 检查 `status`/`create_time` 是否有索引 → 考虑复合索引
- **CACHE**：查命中率（`redis-cli info stats`）→ `redis-cli --latency` → 检查过期策略
- **DOWNSTREAM**：下钻最慢下游 → 计算 client/server RT 差值查网络延迟
- **CODE**：`thread -n 3`（Arthas）或 Profiling 定位热点方法 → 优化算法/并发