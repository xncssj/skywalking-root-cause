# Case 09 · 线程卡死（单锁 + 持锁慢操作，Arthas BLOCKED 实测）

- **日期**：2026-08-15
- **场景**：第 9 章 §9.4.1 "线程卡死（thread + jstack 定位）" 的 demo 实测
- **端点**：`member-svc GET:/member/test/stuck?holdMs=20000`（SlowController 单锁 + 持锁 sleep，
  锁粒度过大反模式）
- **复现**：6 个并发请求同时打 `/test/stuck` → 1 个持锁 sleep，其余 **BLOCKED** 排队等锁
- **采集**：Arthas `thread --state BLOCKED` + `thread -n 3`（JDK 容器 + arthas-boot 4.3.4）

## 实测输出

`arthas-thread-blocked.txt`（已清理 ANSI 码），关键片段：

```
[arthas@7]$ thread --state BLOCKED
Threads Total: 70, NEW: 0, RUNNABLE: 30, BLOCKED: 11, WAITING: 4, TIMED_WAITING: ...
63 http-nio-8080-exec-  main  5  BLOCKE  0.0  0:0.42  false true
64 http-nio-8080-exec-  main  5  BLOCKE  0.0  0:0.00  false true
... （共 11 个 http-nio-8080-exec-* 全部 BLOCKED）
```

`thread -n 3` 顶部（最忙线程）：
```
"DataCarrier.DEFAULT.Consumer.0.Thread" Id=24 cpuUsage=0.35% TIMED_WAITING
"DataCarrier.gRPC-log.Consumer.0.Thread" Id=32 cpuUsage=0.28% TIMED_WAITING
```

## 教学要点

1. **BLOCKED 11 个全是 http-nio-8080-exec（业务工作线程）** → 同一把锁上排队 = 锁竞争，
   不是"慢"是"等"（CPU 不高，看 `thread --state BLOCKED` 才能发现）。
2. **`thread -n 3` 最忙线程是 SW DataCarrier 后台消费者**（TIMED_WAITING）→ 业务线程全卡死时
   CPU 是"假健康"的，最忙线程往往是后台线程，必须排除后才能锁死锁。
3. 修复方向（案例给出的）：IO 移出锁外 / 读写锁 / 队列缓冲。

## 复现命令

```bash
# 1) 并发 6 个持锁请求（holdMs=20s，保证观测窗口足够）
for i in $(seq 1 6); do curl -s -m 30 "http://localhost:8086/member/test/stuck?holdMs=20000" & done

# 2) Arthas attach（batch.as: thread --state BLOCKED / thread -n 3）
docker exec <jdk-ctr> sh -c "cd /opt && java -jar arthas-boot.jar -f batch.as <java-pid>"
```

## 注意事项

- Arthas 必须 attach 到 **JDK** 容器（JRE 缺 `com.sun.tools.attach`），java PID 要取
  `java` 进程（不是 `sh -c java` 的 sh，PID 1）。
- 持锁 sleep 用 20s 保证观测窗口；锁是进程内单锁，并发越接近线程池上限，BLOCKED 越多。
