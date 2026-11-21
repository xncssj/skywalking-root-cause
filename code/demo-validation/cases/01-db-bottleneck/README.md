# Case 01：DB 瓶颈完整走查（Ch15 §15.4）

真实产出的一份端到端结果。对应章节「实测输出」引用。

## 素材

| 文件 | 说明 |
|------|------|
| `result.json` | `analyze.py --json` 完整结果（verdict/evidence/os_scene） |
| `trace-spans.json` | 最慢 trace 的 8 个 span 原始数据（含 db.statement） |
| `llm-report.txt` | deepseek-r1:1.5b 三段式报告 + 硬约束校验通过 |

## 复现

```bash
# 1. 起栈
docker compose up -d

# 2. 低并发流量（order→cart→member→MySQL）
CONCURRENCY=2 ROUNDS=4 bash scripts/generate-traffic.sh

# 3. 双源分析（等 OAP 聚合 ~4-6min，取 6 分钟窗口）
MINUTES=6 bash scripts/run-analysis.sh "分析最近 6 分钟"

# 4. 单独导出 trace（把 traceId 换成你那次的结果）
python3 scripts/dump_trace.py <traceId> --out /tmp/trace-spans.json
```

## 本次实测数据

**判定**：`DB_BOTTLENECK` · Trace 总耗时 **1864ms** · 主导 Exit `member-svc → mysql:3306 (mysql-connector-java)` **1821ms 占 97.7%**

**8-span 分布式 trace**（order→cart→member→MySQL，三服务共享同一 traceId）：

```
 Entry order-svc  GET:/order/test/sql/real-slow          1864ms
  Exit order-svc  /cart/test/sql/real-slow   HttpClient  cart-svc:8080  1860ms
 Entry cart-svc   GET:/cart/test/sql/real-slow           1852ms
  Exit cart-svc   /member/test/sql/real-slow HttpClient  member-svc:8080 1846ms
 Entry member-svc GET:/member/test/sql/real-slow         1840ms
 Local member-svc HikariCP/Connection/getConnection      0ms
  Exit member-svc Mysql/JDBC/Statement/executeQuery       mysql:3306  1821ms
        db.statement: SELECT * FROM t_biz_order WHERE status = 1 AND create_time > '2024-01-01'
 Local member-svc HikariCP/Connection/close              0ms
```

**证据链结论**：DB span 占整条 trace 97.7%（1821/1864）→ 瓶颈在 SQL 本身（无索引全表扫描），
OS 实证层全部健康（CPU=us、内存 NONE、网络正常）→ 交叉排除宿主机资源问题。
