# 05-code-hotspot：代码级热点火焰图（第 8 章）

2026-08-15 实测。为 demo-app 新增 CPU 热点端点 `GET /member/test/cpu/hot?n=80000`：

```java
// 低效字符串拼接：循环里反复 new StringBuilder + toString
for (int i = 0; i < n; i++) {
    StringBuilder sb = new StringBuilder(s);
    sb.append(i);
    s = sb.toString();
}
```

不打下游、不打 DB → trace 里只有 Entry + Local span，自身逻辑占比接近 100%（CODE_BOTTLENECK）。

## 文件

| 文件 | 说明 |
|------|------|
| `cpu-itimer-flame.html` | itimer 事件火焰图（可交互，浏览器打开）：CPU 热点，顶部 arraycopy |
| `wall-flame.html` | wall 事件火焰图：全貌（空闲线程等待占底部，业务热点塔在上层） |
| `cpu-itimer-flat.txt` | itimer flat 聚合：`jlong_disjoint_arraycopy 34.91%`、`AbstractStringBuilder.<init>`、`cpuHot` |
| `wall-flat.txt` | wall flat 聚合：空闲线程（libc 等待）为主 + cpuHot 调用链 |

## 实测关键数字（cpu-itimer-flat.txt）

```
Total samples: 11398
jlong_disjoint_arraycopy  34.91%   ← StringBuilder 底层 char[] 拷贝
jbyte_disjoint_arraycopy  26.91%
libc.so.6                 23.20%   ← 少量空闲/系统调用
java.lang.AbstractStringBuilder.<init>  0.55%
com.example.demo.SlowController.cpuHot  0.40%
```

> 注意：`arraycopy` 才是真实 CPU 热点——低效拼接的本质是"每次拷贝上一次的结果串"，O(n²) 拷贝。
> 修复：`StringBuilder` 复用一个实例（或直接 `s = Integer.toString(i)`），热点消失。

## 复现步骤

```bash
# 1. demo-app 需用 JDK 镜像跑（async-profiler attach 需要 com.sun.tools.attach）
#    临时：docker run ... -e JAVA_OPTS="...-agentpath:/ap/lib/libasyncProfiler.so=start,event=itimer,file=/ap/flames/itimer.html" \
#         -v <async-profiler目录>:/ap eclipse-temurin:17-jdk ...

# 2. 打流量（让热点线程持续活跃）
for i in $(seq 1 8); do
  curl -s "http://localhost:<port>/member/test/cpu/hot?n=80000" >/dev/null
done

# 3. dump（容器内）
cd <async-profiler目录>
./bin/asprof dump -f /ap/flames/itimer.html <pid>          # 火焰图
./bin/asprof dump -o flat -f /ap/flames/itimer-flat.txt <pid>

# 4. stop
./bin/asprof stop <pid>
```

## 常见坑（真实踩到）

- **容器内 CPU（perf）事件无数据**：`event=cpu` 需要 `perf_event_open` 权限，
  Docker Desktop 容器默认无 → 火焰图只有 JVM 启动期样本、`com.example=0`。
  解法：用 `event=itimer`（SIGPROF，CPU 近似，任何环境可用）或 `event=wall`。
- **JRE 无法 attach**：asprof/Arthas attach 需要 `com.sun.tools.attach`，JRE 缺它 → 用 JDK 镜像。
- **`start` + `duration` 到点只 stop 不 dump**：火焰图是空的 0 字节文件。
  解法：`start` 后打流量，再 `asprof dump -f xxx.html <pid>` 主动 dump。
