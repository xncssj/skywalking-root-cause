# 证据链（Evidence Chain）JSON 结构

> 对应专栏第 11 章「输出编排」。本技能的每条结论都产出一条证据链，确保结论可追溯、可复核。

## 结构说明

```json
{
  "verdict": "DB_BOTTLENECK",
  "summary": "DB 慢查询为主因（建议 EXPLAIN + 检查索引）",
  "evidence": [
    {
      "层": "L1-端点",
      "证据": "order-svc - GET:/order/test/sql/real-slow RT=56057ms",
      "引用": "order.1_GET..."
    },
    {
      "层": "L2-Trace",
      "证据": "最慢 Trace demo.trace.001 耗时 56501ms",
      "引用": "demo.trace.001"
    },
    {
      "层": "L3-瓶颈",
      "证据": "瓶颈服务 cart-svc 占比 98.8% (span 总耗时 56501ms)",
      "引用": "cart-svc"
    },
    {
      "层": "L5-SQL",
      "证据": "DB span 占比 98.8%，抽样 SQL: SELECT * FROM t_biz_order WHERE status=?",
      "引用": "db-host:3306"
    }
  ],
  "slow_endpoints": [],
  "span_stats": {}
}
```

## 层标识语义（与专栏一致）

| 层 | 含义 |
|----|------|
| L1-端点 | 端点 RT 扫描结果（最慢端点） |
| L2-Trace | Trace 列表最慢条目 + traceId |
| L3-瓶颈 | 按服务聚合后的瓶颈服务与占比 |
| L4-Span | Span 组件/peer 层面的证据 |
| L5-SQL | db.statement 具体 SQL 证据 |
| L6-综合 | 多因子/最终判定 |

## 复核原则

1. 每条证据的「引用」字段（traceId / serviceName / peer）都应能在 SkyWalking UI 复现
2. `verdict` 的变化必须对应证据链条目的增加，不允许"判定改了证据没变"
3. `HEALTHY` / `INSUFFICIENT_DATA` 场景证据链允许为空或仅含 L1，但要如实说明原因