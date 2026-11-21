# Case 10 · GC 日志 + 锁等待 Wall 采样（JVM 章节补充实测）

- **日期**：2026-08-15
- **场景**：第 6 章 §6.6.1 GC 日志阅读 + 第 8 章 §8.6.1 锁竞争 Wall 模式判据的 demo 实测
- **JDK 容器**：`maven:3.9-eclipse-temurin-17`，256MB 堆 G1，同一容器跑两个实验
- **JVM 参数**：`-XX:+UseG1GC -agentpath:/ap/lib/libasyncProfiler.so=start,event=wall,file=/ap/lock.html -Xlog:gc:file=/tmp/gc.log`

## 1. GC 日志（g1-gc.log，33 行）

```
[1.394s][info][gc] GC(0) Pause Young (Normal) (G1 Evacuation Pause) 25M->6M(256M) 7.289ms
[2.432s][info][gc] GC(1) Pause Young (Normal) (G1 Evacuation Pause) 45M->8M(256M) 5.954ms
[3.637s][info][gc] GC(2) Pause Young (Normal) (G1 Evacuation Pause) 156M->13M(256M) 21.530ms
[4.486s][info][gc] GC(3) Pause Young (Concurrent Start) (Metadata GC Threshold) 125M->15M(256M) 12.602ms
[5.604s][info][gc] GC(5) Pause Young (Normal) (G1 Evacuation Pause) 155M->18M(256M) 22.446ms
```

读法：`Pause Young (G1 Evacuation Pause)` = G1 STW 停顿；`25M->6M(256M)` = 堆占用 25M 回收后 6M（总量 256M）；末尾 `7.289ms` = 停顿时长。`Concurrent Start` = 并发标记开始（不 STW，后续触发 mixed GC）。

## 2. 锁等待 Wall 采样（lock-wall-flat.txt）

8 个并发请求打 `/test/stuck?holdMs=30000`（单锁持锁 sleep，第 9 章 §9.4.1 同源），
`event=wall` 采样（Total 9689）：

```
libc.so.6                 77.82%   ← 空闲/等待（持锁 sleep 所在）
epoll_wait                18.43%   ← Tomcat 网络等待
（无锁帧、无业务热点帧）
```

教学点：
- **Wall 模式在纯锁等待时看不到锁帧**——flat 只有 libc/epoll 等待主导（合计 96%+），
  CPU 低、无热点，正是"在等"的特征。
- 定位锁要用 **Arthas `thread --state BLOCKED`**（§9.4.1 实测 11 线程 BLOCKED），
  Wall 负责证"CPU 低=等"，Arthas 负责定位"等在哪把锁"。

## 复现命令

```bash
docker run -d --name member-lock --network demo-validation_default \
  -e SW_AGENT_NAME=member-lock -e SW_AGENT_NAMESPACE=demo \
  -e SW_AGENT_COLLECTOR_BACKEND_SERVICES=oap:11800 \
  -e CTX_PATH=/member -e DOWNSTREAM_URL= -e DB_HOST=mysql -e REDIS_HOST=redis \
  -e JAVA_OPTS="-javaagent:/app/agent/skywalking-agent.jar -Xms256m -Xmx256m -XX:+UseG1GC -agentpath:/ap/lib/libasyncProfiler.so=start,event=wall,file=/ap/lock.html -Xlog:gc:file=/tmp/gc.log" \
  -v <app-stuck.jar>:/app/app.jar -v <agent>:/app/agent -v <async-profiler>:/ap \
  -p 8087:8080 --entrypoint sh docker.m.daocloud.io/maven:3.9-eclipse-temurin-17 \
  -c "java \$JAVA_OPTS -jar /app/app.jar"

for i in $(seq 1 8); do curl -s -m 40 "http://localhost:8087/member/test/stuck?holdMs=30000" >/dev/null & done
docker exec member-lock sh -c "cd /ap && ./bin/asprof dump -o flat -f /ap/lock-flat.txt 7"
```
