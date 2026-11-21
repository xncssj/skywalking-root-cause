package com.example.demo;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;

/**
 * 慢查询链路控制器。
 *
 * 一个 jar 三种角色（由 DOWNSTREAM_URL 区分）：
 *   - order-svc  : /order/test/sql/real-slow  -> 调 cart-svc
 *   - cart-svc   : /cart/test/sql/real-slow   -> 调 member-svc
 *   - member-svc : /member/test/sql/real-slow -> 直接打 MySQL 慢查询（全表扫描）
 *
 * 复现专栏旗舰场景：链路整体耗时全部沉淀在 member-svc 对 MySQL 的无索引全表扫描上，
 * SkyWalking Trace 里表现为最内层 MySQL Exit span 主导。
 */
@RestController
public class SlowController {

    private final JdbcTemplate jdbc;
    private final RestTemplate rest;

    @Value("${downstream-url:}")
    private String downstreamUrl;

    @Value("${spring.datasource.url:}")
    private String dsUrl;

    public SlowController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
        // 用 Apache HttpClient 5 作为 RestTemplate 底层（SkyWalking httpclient-5 插件
        // 才能生成 Exit span 并传播 sw8 链路上下文；默认 JDK HttpURLConnection 无插件支持）
        HttpComponentsClientHttpRequestFactory factory =
                new HttpComponentsClientHttpRequestFactory(HttpClients.createDefault());
        factory.setConnectTimeout(5000);
        factory.setConnectionRequestTimeout(5000);
        this.rest = new RestTemplate(factory);
    }

    @GetMapping("/test/sql/real-slow")
    public Map<String, Object> realSlow() {
        long t0 = System.currentTimeMillis();
        Map<String, Object> out = new LinkedHashMap<>();

        if (downstreamUrl == null || downstreamUrl.isEmpty()) {
            // member 角色：慢 SQL（无索引全表扫描，命中 ~1/3 行，行宽 ~1.7KB）
            String sql = "SELECT * FROM t_biz_order WHERE status = 1 AND create_time > '2024-01-01'";
            AtomicLong rows = new AtomicLong();
            jdbc.query(sql, rs -> { rows.incrementAndGet(); });
            out.put("role", "member");
            out.put("sql", sql);
            out.put("rows_scanned", rows.get());
            out.put("datasource", dsUrl);
        } else {
            // order/cart 角色：级联下游
            @SuppressWarnings("unchecked")
            Map<String, Object> downstream = rest.getForObject(downstreamUrl, Map.class);
            out.put("role", "gateway");
            out.put("downstream_url", downstreamUrl);
            out.put("downstream_result", downstream);
        }

        out.put("elapsed_ms", System.currentTimeMillis() - t0);
        return out;
    }

    /**
     * CPU 热点端点（第 8 章代码级根因案例用）。
     *
     * 自身逻辑热点：循环里反复 new StringBuilder + toString（低效字符串拼接）。
     * 不打下游、不打 DB，trace 里只有 Entry + Local span → 自身逻辑占比接近 100%，
     * 复现 CODE_BOTTLENECK，配 async-profiler 火焰图定位 StringBuilder 热点。
     */
    @GetMapping("/test/cpu/hot")
    public Map<String, Object> cpuHot(@RequestParam(defaultValue = "300000") int n) {
        long t0 = System.currentTimeMillis();
        String s = "";
        for (int i = 0; i < n; i++) {
            StringBuilder sb = new StringBuilder(s);
            sb.append(i);
            s = sb.toString();
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("role", "cpu-hot");
        out.put("iterations", n);
        out.put("result_len", s.length());
        out.put("elapsed_ms", System.currentTimeMillis() - t0);
        return out;
    }

    /**
     * 内存泄漏端点（第 6 章 §6.5 老年代上升案例用）。
     *
     * 静态 List 累积 byte[]：每次调用向堆永久持有 +mb MB。
     * 配合 jstat -gcutil 观察 oldgen 持续上升（Full GC 后回不到低水位 = 泄漏特征）。
     * 仅用于教学演示，切勿照搬到生产。
     */
    private static final java.util.List<byte[]> LEAK = new java.util.ArrayList<>();
    private static final java.util.concurrent.atomic.AtomicLong LEAKED_MB = new java.util.concurrent.atomic.AtomicLong();

    @GetMapping("/test/leak")
    public Map<String, Object> leak(@RequestParam(defaultValue = "2") int mb) {
        long t0 = System.currentTimeMillis();
        for (int i = 0; i < mb * 64; i++) {
            LEAK.add(new byte[16 * 1024]);
        }
        long total = LEAKED_MB.addAndGet(mb);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("role", "leak");
        out.put("leaked_mb", total);
        out.put("elapsed_ms", System.currentTimeMillis() - t0);
        return out;
    }

    /**
     * 持锁慢操作端点（第 9 章 §9.4.1 线程卡死案例用）。
     *
     * 单锁 + 持锁 sleep：并发调用时后续线程全部 BLOCKED 在同一个锁上（锁粒度过大反模式），
     * 配合 Arthas `thread --state BLOCKED` 实测定位。
     */
    private static final Object STUCK_LOCK = new Object();

    @GetMapping("/test/stuck")
    public Map<String, Object> stuck(@RequestParam(defaultValue = "10000") int holdMs) {
        long t0 = System.currentTimeMillis();
        synchronized (STUCK_LOCK) {
            try {
                Thread.sleep(holdMs);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("role", "stuck");
        out.put("held_ms", System.currentTimeMillis() - t0);
        return out;
    }
}
