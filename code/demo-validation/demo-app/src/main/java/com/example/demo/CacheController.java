package com.example.demo;

import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 缓存读接口（第 5 章 §5.5 缓存命中率骤降案例用）。
 *
 * 读路径：先查 Redis(goods:detail:{id})，miss 则查 MySQL 并回填，TTL 统一由调用方指定。
 * 造"雪崩"：所有 key 用同一个 TTL 预热 → 到期后同时过期 → 流量全打 DB，命中率骤降。
 * /test/cache/stats?reset=1 暴露 hit/miss 累计，供实验前后对拍。
 */
@RestController
public class CacheController {

    private final StringRedisTemplate redis;
    private final JdbcTemplate jdbc;

    private static final AtomicLong HIT = new AtomicLong();
    private static final AtomicLong MISS = new AtomicLong();

    public CacheController(StringRedisTemplate redis, JdbcTemplate jdbc) {
        this.redis = redis;
        this.jdbc = jdbc;
    }

    @GetMapping("/test/cache/{id}")
    public Map<String, Object> read(@PathVariable String id,
                                    @RequestParam(defaultValue = "60") int ttlSeconds) {
        long t0 = System.currentTimeMillis();
        String key = "goods:detail:" + id;
        String v = redis.opsForValue().get(key);
        if (v != null) {
            HIT.incrementAndGet();
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("source", "redis");
            out.put("ttl_left_s", redis.getExpire(key));
            out.put("elapsed_ms", System.currentTimeMillis() - t0);
            return out;
        }
        MISS.incrementAndGet();
        String row = jdbc.queryForObject(
                "SELECT id FROM t_biz_order ORDER BY id LIMIT 1", String.class);
        redis.opsForValue().set(key, row, Duration.ofSeconds(ttlSeconds));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("source", "db");
        out.put("row", row);
        out.put("elapsed_ms", System.currentTimeMillis() - t0);
        return out;
    }

    @GetMapping("/test/cache/stats")
    public Map<String, Object> stats(@RequestParam(defaultValue = "0") int reset) {
        if (reset == 1) {
            HIT.set(0);
            MISS.set(0);
        }
        long h = HIT.get();
        long m = MISS.get();
        double rate = (h + m) == 0 ? 0.0 : (double) h / (h + m);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("hit", h);
        out.put("miss", m);
        out.put("total", h + m);
        out.put("hit_rate", String.format("%.3f", rate));
        return out;
    }
}
