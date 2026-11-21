"""analyze.py 单元测试（使用 DemoClient 内置模拟数据，无需真实 OAP）。

运行:
    python -m pytest tests/ -q
    或  python tests/test_analyze.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze import (  # noqa: E402
    DemoClient,
    analyze,
    build_span_stats,
    judge_root_cause,
)


def _demo_result():
    end = datetime.now()
    start = end - timedelta(minutes=15)
    return analyze(DemoClient(), start, end)


def test_demo_returns_db_bottleneck():
    result = _demo_result()
    assert result["verdict"] == "DB_BOTTLENECK", result["verdict"]


def test_evidence_chain_nonempty():
    result = _demo_result()
    assert len(result["evidence"]) >= 4, "DB 场景至少应有 L1/L2/L3/L5 四层证据"
    layers = {e["层"] for e in result["evidence"]}
    assert "L1-端点" in layers and "L2-Trace" in layers


def test_dominant_exit_is_db():
    result = _demo_result()
    dominant = result["span_stats"]["dominant_exit"]
    assert dominant["component"] == "MySQL"
    assert dominant["service"] == "cart-svc"
    assert dominant["peer"] == "db-host:3306"


def test_demo_sql_evidence_present():
    result = _demo_result()
    sql_evidence = [e for e in result["evidence"] if e["层"] == "L5-SQL"]
    assert len(sql_evidence) == 1
    assert "db.statement" or "SELECT" in sql_evidence[0]["证据"]


def test_span_stats_dominant_ratio():
    spans = DemoClient().query_trace("demo.trace.001")["spans"]
    stats = build_span_stats(spans)
    assert stats["total_ms"] == 56501
    d = stats["dominant_exit"]
    assert d["ratio"] > 0.9


def test_judge_low_ratio_falls_back():
    # 构造一个"自身代码慢"的场景：最长 Exit 是 CPU 型（无 peer 组件归属 local）
    stats = {
        "total_ms": 1000,
        "service_durations": {"order-svc": 900},
        "component_durations": {"local": 900, "MySQL": 50},
        "db_spans": [],
        "dominant_exit": {
            "component": "local",
            "service": "order-svc",
            "peer": "internal",
            "duration_ms": 900,
            "ratio": 0.9,
            "sql": None,
        },
        "exit_count": 1,
    }
    verdict, _, _ = judge_root_cause(stats, [])
    assert verdict == "CODE_LEVEL"
    assert stats["component_durations"]["MySQL"] / stats["total_ms"] < 0.6


if __name__ == "__main__":
    failures = 0
    for fn in [test_demo_returns_db_bottleneck, test_evidence_chain_nonempty,
               test_dominant_exit_is_db, test_demo_sql_evidence_present,
               test_span_stats_dominant_ratio, test_judge_low_ratio_falls_back]:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    sys.exit(1 if failures else 0)