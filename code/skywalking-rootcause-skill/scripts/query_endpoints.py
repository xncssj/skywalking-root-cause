"""单查慢端点：端点驱动 5 步法的第 1 步单独执行。

对应《SkyWalking 性能分析引擎》第 2 章 Step 1（端点 RT 扫描）。

用法:
    python query_endpoints.py --base-url http://oap-host:12800/graphql --minutes 15 --limit 20
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

from sw_client import SwClient

SLOW_ENDPOINT_THRESHOLD_MS = 1000.0


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描最近窗口内 RT 最慢的端点")
    parser.add_argument("--base-url", default="http://oap-host:12800/graphql",
                        help="OAP GraphQL 地址")
    parser.add_argument("--minutes", type=int, default=15, help="时间窗口（分钟）")
    parser.add_argument("--limit", type=int, default=20, help="返回端点数量上限")
    args = parser.parse_args()

    token = os.environ.get("SKYWALKING_TOKEN")
    client = SwClient(args.base_url, token=token)

    end = datetime.now()
    start = end - timedelta(minutes=args.minutes)
    endpoints = client.top_slow_endpoints(start, end, limit=args.limit)

    if not endpoints:
        print(f"[结果] {args.minutes} 分钟窗口内无端点数据（OAP 可能无流量或地址不对）。")
        return 1

    print(f"[最多端点] 前 {min(args.limit, len(endpoints))} 个（窗口 {args.minutes}min）:")
    for ep in endpoints:
        flag = "SLOW" if ep["resp_time_ms"] > SLOW_ENDPOINT_THRESHOLD_MS else "ok  "
        print(f"  [{flag}] {ep['service_name']} | {ep['endpoint_name']} | {ep['resp_time_ms']:.0f}ms")

    slow_count = sum(1 for ep in endpoints if ep["resp_time_ms"] > SLOW_ENDPOINT_THRESHOLD_MS)
    print(f"\n[小结] 慢端点 {slow_count}/{len(endpoints)} 个（阈值 {SLOW_ENDPOINT_THRESHOLD_MS:.0f}ms）。")
    if slow_count:
        print("下一步: 对最慢端点跑 analyze.py 做根因分析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())