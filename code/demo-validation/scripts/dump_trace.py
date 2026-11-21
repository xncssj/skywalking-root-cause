#!/usr/bin/env python3
"""导出指定 trace 的完整 span 列表（用于案例素材收集）。

用法:
    python3 dump_trace.py <trace_id> [--base-url http://localhost:12800/graphql] [--out trace-spans.json]

输出：按时间序的 span 表格（type/service/endpoint/component/peer/耗时），
并附 JSON 原样保存（含 tags 里的 db.statement / http.url）。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List


def _norm(base: str) -> str:
    return base.rstrip("/")


def _fetch_trace(base: str, trace_id: str) -> Dict[str, Any]:
    import urllib.request

    query = """
    query queryTrace($traceId: ID!) {
      trace: queryTrace(traceId: $traceId) {
        spans {
          traceId segmentId serviceCode endpointName startTime endTime isError
          type component peer layer
          tags { key value }
        }
      }
    }
    """
    payload = {"query": query, "variables": {"traceId": trace_id}}
    req = urllib.request.Request(
        _norm(base) + "/graphql",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        body = json.loads(resp.read().decode("utf-8"))
    if "errors" in body:
        raise SystemExit(f"GraphQL 错误: {body['errors']}")
    return body.get("data", {}).get("trace", {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_id")
    ap.add_argument("--base-url", default="http://localhost:12800")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tr = _fetch_trace(args.base_url, args.trace_id)
    spans: List[Dict[str, Any]] = tr.get("spans", [])

    print(f"traceId: {args.trace_id}   span 数: {len(spans)}")
    print(f"{'type':>7} {'service':<22} {'endpoint':<44} {'component':<24} {'peer':<24} {'ms':>8}")
    for s in spans:
        tags = {t["key"]: t["value"] for t in (s.get("tags") or [])}
        ms = (int(s.get("endTime", 0)) - int(s.get("startTime", 0))) / 1.0
        print(
            f"{s.get('type',''):>7} {s.get('serviceCode',''):<22} "
            f"{str(s.get('endpointName',''))[:44]:<44} "
            f"{str(s.get('component',''))[:24]:<24} {str(s.get('peer',''))[:24]:<24} {ms:>8.1f}"
        )
        for key in ("db.statement", "http.url"):
            if tags.get(key):
                print(f"          {key}: {tags[key]}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(tr, f, ensure_ascii=False, indent=2)
        print(f"\n已保存: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
