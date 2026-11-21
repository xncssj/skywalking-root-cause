# 06-arthas：Arthas attach 实录（第 9 章）

2026-08-15 实测。对 demo-app 的 member 角色（`/member/test/cpu/hot` 端点）用
`arthas-boot.jar` attach 后执行 `dashboard` / `thread -n 3` / `trace` 三条核心命令。

## 文件

| 文件 | 说明 |
|------|------|
| `arthas-batch.txt` | 完整批处理实录（dashboard / thread -n 3 / trace cpuHot） |

## 实录关键输出（arthas-batch.txt）

**① thread -n 3 —— 最忙线程直接指向 StringBuilder 热点：**

```
"http-nio-8080-exec-3" Id=66 cpuUsage=96.21% deltaTime=242ms time=22768ms RUNNABLE
    at java.base@17.0.19/java.lang.AbstractStringBuilder.<init>(AbstractStringBuilder.java:111)
    at java.base@17.0.19/java.lang.StringBuilder.<init>(StringBuilder.java:131)
```

**② trace SlowController.cpuHot —— 方法级耗时：**

```
Affect(class count: 1 , method count: 1) cost in 435 ms, listenerId: 1
`---ts=2026-08-15 10:29:11.031;thread_name=http-nio-8080-exec-5;id=68;...
    `---[2713.008699ms] com.example.demo.SlowController:cpuHot()
```

**③ dashboard** 可见 http-nio-8080-exec 线程 WAITIN/RUNNAB 交替、SkyWalking DataCarrier 消费线程等。

> 与第 8 章火焰图交叉验证：Arthas `thread -n 3` 的栈顶 `StringBuilder.<init>`
> 正是火焰图里 `AbstractStringBuilder` + `arraycopy` 热点的**另一个视图**——两个工具互相印证。

## 复现步骤

```bash
# 1. demo-app 必须用 JDK 镜像（JRE 缺 com.sun.tools.attach，Arthas attach 会
#    NoClassDefFoundError: com/sun/tools/attach/AgentLoadException）
# 2. 下载 boot（容器内可直接下载）
curl -O https://arthas.aliyun.com/arthas-boot.jar

# 3. 打流量让热点线程活跃
(for i in $(seq 1 12); do curl -s "http://localhost:<port>/member/test/cpu/hot?n=60000" >/dev/null; done) &

# 4. attach + 批量执行（PID 用目标 JVM 进程号）
java -jar arthas-boot.jar -c "dashboard -n 1; thread -n 3" <pid>
# 或写 batch 脚本
printf 'dashboard -n 1\nthread -n 3\ntrace com.example.demo.SlowController cpuHot -n 1\nquit\n' > batch.as
java -jar arthas-boot.jar -f batch.as <pid>

# 5. 用完后关闭
java -jar arthas-boot.jar -c "stop" <pid>
```

## 常见坑（真实踩到）

- **JRE 容器 attach 失败**：`NoClassDefFoundError: com/sun/tools/attach/AgentLoadException`，
  且 boot 仍打印 "Attach process success" 但 telnet 连接被拒（agent 没真正起来）→ 换 JDK 镜像。
- **attach 后 agent 常驻**：`quit` 只断终端，agent 还在目标 JVM 里 → 用 `stop` 命令彻底关闭。
- **容器内下载慢**：GitHub 慢，`https://arthas.aliyun.com/arthas-boot.jar`（阿里云 CDN）快；
  容器内网络可达时直接在容器内下载最省事。
