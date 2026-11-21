-- bizdb 初始化：构造无索引全表扫描的慢查询场景（复现专栏旗舰 56s 案例）
-- 表：t_biz_order 约 25 万行、行宽 ~1.7KB、无 (status, create_time) 复合索引
-- 查询：SELECT * FROM t_biz_order WHERE status=1 AND create_time>'2024-01-01'  -> 全表扫描命中 ~1/3

SET SESSION cte_max_recursion_depth = 1000000;

CREATE DATABASE IF NOT EXISTS bizdb CHARACTER SET utf8mb4;
USE bizdb;

DROP TABLE IF EXISTS t_biz_order;
CREATE TABLE t_biz_order (
    id          BIGINT      NOT NULL PRIMARY KEY,
    order_no    VARCHAR(32) NOT NULL,
    status      TINYINT     NOT NULL,
    create_time DATETIME    NOT NULL,
    remark      VARCHAR(255) NOT NULL,
    payload     VARCHAR(1500) NOT NULL
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- 递归 CTE 批量造数：25 万行，payload 1300 字符 -> 表约 330MB，全表扫描 >1s
INSERT INTO t_biz_order (id, order_no, status, create_time, remark, payload)
WITH RECURSIVE seq(n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 250000
)
SELECT n,
       CONCAT('NO', LPAD(n, 10, '0')),
       MOD(n, 3),
       TIMESTAMPADD(MINUTE, -(MOD(n, 200000)), NOW()),
       REPEAT('r', 120),
       REPEAT('p', 1300)
FROM seq;

-- 验证
SELECT COUNT(*) AS total_rows FROM t_biz_order;
