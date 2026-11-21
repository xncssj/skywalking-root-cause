# Case 02：并发对比 —— 连接池排队如何稀释 DB 占比

同一套代码、同一张表，**只有并发不同**，DB 占比从 97.7% 掉到 48.4%。
对应章节 §15.4 走查要点「数值会随负载浮动」的实证。

## 素材

| 文件 | 说明 |
|------|------|
| `low-cc-result.json` | CONCURRENCY=2（≈case-01，97.7%） |
| `high-cc-result.json` | CONCURRENCY=6（48.4%） |
| `high-cc-trace.json` | 高并发最慢 trace 的 8 个 span |

## 机制

member 的 Hikari 连接池 = 2，order-svc 的 HttpClient 连接池默认 maxPerRoute = 2。
并发 6 时，4 个请求在连接池排队等待连接（**连接等待时间记入 trace 总耗时，但不属于 DB span**）：
- 慢的那个请求总耗时被撑到 3574ms
- 其中 DB executeQuery 仍只花 1729ms
- 占比 = 1729/3574 = **48.4%**（低并发时 1821/1864 = 97.7%）

## 高并发最慢 trace 对比

```
低并发 (97.7%)                        高并发 (48.4%)
 Entry order 1864ms                   Entry order 3574ms
  Exit  order→cart  1860ms             Exit  order→cart  3567ms   ← 含连接池等待
 Entry cart  1852ms                   Entry cart   1762ms
  Exit  cart→member 1846ms             Exit  cart→member 1757ms
 Entry member 1840ms                  Entry member  1752ms
 Local Hikari getConnection 0ms       Local Hikari getConnection 0ms
  Exit  mysql 1821ms (97.7%)           Exit  mysql  1729ms (48.4%)
```

## 复现

```bash
# 低并发
CONCURRENCY=2 ROUNDS=4 bash scripts/generate-traffic.sh
# 高并发（看第 6 个请求 RT 翻倍）
CONCURRENCY=6 ROUNDS=3 bash scripts/generate-traffic.sh
# 各等 ~5min 聚合后分别分析，比较 DB 占比
MINUTES=8 bash scripts/run-analysis.sh
```

## 结论

- **占比稀释 ≠ 根因变化**：两种并发下判定都是 DB_BOTTLENECK（主导 Exit 仍是 mysql）
- 排队的特征是 RT 翻倍（1.7s→3.5s）+ 占比下降，原因是连接池等待被计入 trace 总耗时
- 经验：**根因判定看「主导 Exit + 绝对值」，不看占比高低**；占比用来排除/佐证，不单独下结论
