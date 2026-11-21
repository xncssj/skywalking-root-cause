# Case 07 · 老年代持续上升（内存泄漏特征，实测）

- **日期**：2026-08-15
- **场景**：第 6 章 §6.5 "老年代持续上升 → 内存泄漏" 的 demo 实测
- **端点**：`member-svc GET:/member/test/leak?mb=2`（SlowController 静态 List 累积 byte[16KB]）
- **堆**：`-Xms128m -Xmx128m -XX:+UseG1GC`
- **采集**：`jstat -gcutil <pid>` 每 ~2s 一轮，打流量 2MB/轮 × 25 轮

## 原始数据

- `jstat-gcutil-raw.txt`：完整 25 轮 + baseline + 持续泄漏后的极端样本

## 关键样本

| round | Eden% | Old% | YGC | 说明 |
|------|-------|------|-----|------|
| 1    | 20.27 | 61.32 | 24 | baseline |
| 13   |  0.00 | 78.77 | 25 | 首轮 YGC 后老年代跳升（泄漏对象存活） |
| 16   | 20.00 | 83.25 | 26 | 老年代持续爬升 |
| 19   |  0.00 | 88.13 | 29 | |
| 20   |  0.00 | 92.02 | 30 | 逼近 90% 预警线 |
| 22   |  0.00 | 97.74 | 32 | |
| 23   |  0.00 | 99.92 | 35 | 老年代撑满，YGC 空转加速 |
| 末   |  0.00 | 99.83 | 171 | 继续泄漏：YGC 从 35→171 徒劳空转，Old≈100% |

## 教学要点

1. **泄漏特征不是 "Full GC 频繁"，而是 "Young GC 越来越频繁且回收无效 + 老年代占比持续攀升"**。
   G1 下 FGC 全程为 0，但 Old% 从 61% → 99.9% 单调上升（回不到低水位）。
2. jstat 看 `O`（Old 占用%）连续多轮单调上行、`YGC` 增速加快 = 高置信泄漏信号。
3. 泄漏对象必须是**非 humongous** 的小块（byte[16KB]），否则 G1 把大数组直接放 humongous region，
   `jstat` 的 Old 列不统计，数据会失真（本次迭代中的教训）。
4. 用 `-Xmx128m` 小堆放大效果，几分钟内就能观察完整曲线；生产堆大，需拉长观测窗口。

## 复现命令

```bash
# 1) 起 JDK 容器（128m 堆）
docker run -d --name member-leak --network demo-validation_default \
  -e SW_AGENT_NAME=member-leak -e SW_AGENT_NAMESPACE=demo \
  -e SW_AGENT_COLLECTOR_BACKEND_SERVICES=oap:11800 \
  -e CTX_PATH=/member -e DOWNSTREAM_URL= -e DB_HOST=mysql \
  -e SPRING_DATASOURCE_HIKARI_MAXIMUM-POOL-SIZE=2 \
  -e JAVA_OPTS="-javaagent:/app/agent/skywalking-agent.jar -Xms128m -Xmx128m -XX:+UseG1GC" \
  -v $PWD/../.cache/app-leak.jar:/app/app.jar -v $PWD/../.cache/agent:/app/agent \
  -p 8085:8080 --entrypoint sh docker.m.daocloud.io/maven:3.9-eclipse-temurin-17 \
  -c "java \$JAVA_OPTS -jar /app/app.jar"

# 2) 打流量 + 采样
for r in $(seq 1 25); do
  curl -s "http://localhost:8085/member/test/leak?mb=2" >/dev/null
  sleep 1; curl -s "http://localhost:8085/member/test/leak?mb=2" >/dev/null; sleep 1
  docker exec member-leak jstat -gcutil <java-pid>
done
```

## 注意事项

- 复现镜像：JDK 容器才有 `jstat`（JRE 没有）；agent 与 jar 需从构建镜像 `docker cp` 出来。
- 泄漏到 Old≈100% 后继续打会触发 OOM/进程退出，演示完即停（`docker rm -f member-leak`）。
