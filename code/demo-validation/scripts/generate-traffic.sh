#!/usr/bin/env bash
# 生成真实慢查询流量：并发打 order-svc，级联 cart -> member -> MySQL 全表扫描
# 用法: BASE=http://localhost:8080 CONCURRENCY=6 ROUNDS=4 ./generate-traffic.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8080}"
CONCURRENCY="${CONCURRENCY:-6}"
ROUNDS="${ROUNDS:-4}"
URL="${BASE}/order/test/sql/real-slow"

echo ">>> 生成流量: ${URL}  concurrency=${CONCURRENCY} rounds=${ROUNDS}"
for r in $(seq 1 "${ROUNDS}"); do
  start=$(date +%s)
  for i in $(seq 1 "${CONCURRENCY}"); do
    curl -s -o /dev/null -w "  req=${i} http=%{http_code} rt=%{time_total}s\n" "${URL}" &
  done
  wait
  end=$(date +%s)
  echo "--- round ${r} done (${end}-${start}s) ---"
done
echo ">>> 流量生成完成"
