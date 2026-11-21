"""Ch13 §13.3 硬约束校验测试：验证 validate() 能抓到 LLM 幻觉（无需真实 Ollama）。

运行:
    python -m pytest code/demo-validation/tests/ -q
    或 python3 code/demo-validation/tests/test_llm_report.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from llm_report import build_prompt, fallback_report, validate  # noqa: E402

RESULT = {
    "verdict": "DB_BOTTLENECK",
    "summary": "member-svc 调用 mysql:3306 慢查询导致 DB_BOTTLENECK",
    "evidence": [
        {"层": "L1", "证据": "端点 /api/member/query 平均 56057ms > 1000ms"},
        {"层": "L3+-OS", "证据": "CPU 主导=us（用户态热点主导）"},
    ],
    "slow_endpoints": [{"endpoint": "/api/member/query", "avg": 56057.0}],
    "span_stats": {
        "total_ms": 56501.0,
        "dominant_exit": {
            "service": "member-svc",
            "peer": "mysql:3306",
            "component": "Mysql",
            "duration_ms": 1821.0,
            "ratio": 0.977,
        },
    },
    "os_scene": {
        "cpu_dominant": "us",
        "host_overloaded": False,
        "mem_leak_risk": "NONE",
        "network_bottleneck": False,
        "disk_bottleneck": False,
    },
}


def _valid_report():
    return (
        "【根因类型】DB_BOTTLENECK\n"
        "member-svc 调用 mysql:3306 慢查询。"
        "主导 Exit 耗时 1821ms，占比 97.7%。"
    )


def test_build_prompt_injects_verdict_and_os():
    prompt = build_prompt(RESULT)
    assert "【根因类型】DB_BOTTLENECK" in prompt
    assert "磁盘瓶颈=False" in prompt


def test_validate_passes_on_faithful_report():
    ok, issues = validate(RESULT, _valid_report())
    assert ok, issues


def test_validate_catches_number_drift():
    drifted = _valid_report().replace("1821ms", "1860ms")
    ok, issues = validate(RESULT, drifted)
    assert not ok
    assert any("1821ms" in i for i in issues)


def test_validate_catches_missing_verdict():
    ok, issues = validate(RESULT, "根因是慢查询导致的延迟。")
    assert not ok
    assert any("根因 label" in i for i in issues)


def test_validate_catches_extra_verdict_label():
    injected = _valid_report() + "同时存在 CODE_LEVEL 的可能性。"
    ok, issues = validate(RESULT, injected)
    assert not ok
    assert any("额外根因 label" in i for i in issues)


def test_validate_catches_peer_swap():
    swapped = _valid_report().replace("mysql:3306", "cart:9090")
    ok, issues = validate(RESULT, swapped)
    assert not ok
    assert any("peer" in i for i in issues)


def test_fallback_report_renders_rule_engine_source():
    fallback = fallback_report(RESULT, ["根因 label 未出现"])
    assert "回退" in fallback
    assert "DB_BOTTLENECK" in fallback
    assert "member-svc 调用 mysql:3306" in fallback
    assert "磁盘瓶颈=False" in fallback


def test_fallback_report_used_when_validate_fails():
    from llm_report import _print_report  # noqa: PLC0415

    _print_report(fallback_report(RESULT, ["数字漂移"]), False, ["数字漂移"])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    sys.exit(1 if failures else 0)
