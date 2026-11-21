# Case 03：member OOM —— 全量缓冲结果集 + 小堆

把 member 堆压到 128MB 复现的 `java.lang.OutOfMemoryError: Java heap space`。
对应章节 §15.8 实测坑「OOM / 占比被稀释」。

## 素材

| 文件 | 说明 |
|------|------|
| `member-oom.log` | member-svc 容器真实错误日志（OOM + SQL 上下文） |

## 现象

```text
Servlet.service() ... threw exception [Request processing failed:
org.springframework.jdbc.UncategorizedSQLException: StatementCallback;
uncategorized SQLException for SQL [SELECT * FROM t_biz_order WHERE status = 1 AND create_time > '2024-01-01'];
SQL state [HY001]; error code [0]; Java heap space]
Caused by: java.lang.OutOfMemoryError: Java heap space
```

客户端表现：`HTTP 500`，RT≈1.7s（OOM 抛出即返回）。

## 根因

- 表 `t_biz_order` 25 万行、payload 1300 字节 → 结果集 ~330MB
- MySQL Connector/J 默认**全量缓冲**结果集（fetchSize=0 → 一次性读入堆）
- member 堆 128MB → 一次查询直接 OOM，而且**每个请求都 OOM**（连接池内任何一条连接执行都炸）

## 复现步骤

```bash
# 1. 临时把 member-svc 的堆改小（compose 里 JAVA_OPTS）
#    -javaagent:/app/agent/skywalking-agent.jar -Xms128m -Xmx128m
# 2. 重建 member
docker compose up -d member-svc
# 3. 打请求（1 个就够触发）
curl -s "http://localhost:8082/member/test/sql/real-slow" -o /dev/null -w "HTTP %{http_code}\n"   # -> 500
# 4. 看日志
docker logs member-svc --tail 20 | grep OutOfMemory
# 5. 复原堆
#    -javaagent:/app/agent/skywalking-agent.jar -Xms512m -Xmx2048m
docker compose up -d member-svc
```

## 修复与启发

- 修复：堆给足（`-Xmx2048m`）+ 连接池收紧（`Hikari pool=2`），限制并发缓冲的结果集数量
- **流式 fetchSize 是陷阱**：`executeQuery` 用流式会提前返回，DB span 不再包含全表扫描耗时
  —— 根因分析就抓不到真实耗时。宁可给堆，不要用流式破坏 span 语义
- 真实环境应先确认驱动的 `useCursorFetch`/buffer 行为，再按"结果集大小 × 池上限"配堆
