"""多源交叉测试：prom_client / os_empirical / analyze 集成（无需真实 Prometheus）。

运行:
    python -m pytest tests/ -q
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze import DemoClient, collect_os_evidence, analyze  # noqa: E402
from os_empirical import (  # noqa: E402
    analyze_cpu,
    analyze_memory,
    analyze_network,
    analyze_disk,
    analyze_os,
    MEM_LEAK_MIN_SLOPE_PH,
)
from prom_client import DemoPromClient, PromClientError  # noqa: E402


def _window():
    end = datetime.now()
    start = end - timedelta(minutes=15)
    return start, end


# ----------------------------------------------------------------------
# os_empirical 纯函数
# ----------------------------------------------------------------------
def test_cpu_dominant_us():
    scene = analyze_cpu({"user": 40.0, "system": 8.0, "iowait": 3.0, "steal": 0.5})
    assert scene.dominant == "us"
    assert "perf top" in scene.guide or "async-profiler" in scene.guide


def test_cpu_dominant_wa():
    scene = analyze_cpu({"user": 10.0, "system": 5.0, "iowait": 60.0, "steal": 0.0})
    assert scene.dominant == "wa"
    assert "iostat" in scene.guide


def test_cpu_saturated_flag():
    scene = analyze_cpu({"user": 92.0, "system": 5.0, "iowait": 1.0, "steal": 0.0})
    assert scene.critical is True


def test_memory_no_leak_flat_series():
    series = [(i * 60, 45.0 + 0.5) for i in range(16)]
    scene = analyze_memory(series)
    assert scene.leak_detected is False
    assert scene.risk == "NONE"


def test_memory_leak_detected_rising_series():
    # 使用率从 65% 匀速涨到 85%（16 点 * 60s），满足 slope/R²/mean/trend 四条件
    series = [(i * 60, 65.0 + i * (20.0 / 15)) for i in range(16)]
    scene = analyze_memory(series)
    assert scene.leak_detected is True
    assert scene.risk in ("LOW", "MEDIUM", "HIGH")
    assert scene.slope_per_hour >= MEM_LEAK_MIN_SLOPE_PH
    assert scene.r_squared >= 0.7


def _rising(mean0: float, slope_per_hour: float) -> list:
    # 完美直线序列：16 点 * 60s 跨 900s；每点增量 = slope_per_hour/60
    delta = slope_per_hour / 60.0
    return [(i * 60, mean0 + i * delta) for i in range(16)]


def test_memory_high_risk():
    scene = analyze_memory(_rising(82.0, 2.5))
    assert scene.leak_detected is True
    assert scene.risk == "HIGH"


def test_memory_medium_by_slope_boundary():
    # slope 1.5%/h >=1%（但 <2%），mean~70% <80% → 中风险（11.3.2：slope>=1%/h 即 MEDIUM）
    scene = analyze_memory(_rising(70.0, 1.5))
    assert scene.leak_detected is True
    assert scene.risk == "MEDIUM"


def test_memory_medium_by_mean_boundary():
    # mean~88% >=85%（但 slope 0.6%/h <1%）→ 中风险（11.3.2：mean>=85% 即 MEDIUM）
    scene = analyze_memory(_rising(88.0, 0.6))
    assert scene.leak_detected is True
    assert scene.risk == "MEDIUM"


def test_memory_low_risk():
    # 满足 4 条件但 slope<1% 且 mean<85% → 低风险
    scene = analyze_memory(_rising(65.0, 0.6))
    assert scene.leak_detected is True
    assert scene.risk == "LOW"


def test_network_bottleneck_over_5pct():
    scene = analyze_network(6.5, 2000)
    assert scene.bottleneck is True
    assert scene.connection_leak is False


def test_network_connection_leak_over_10000():
    scene = analyze_network(0.3, 15000)
    assert scene.bottleneck is False
    assert scene.connection_leak is True


def test_disk_normal_below_thresholds():
    scene = analyze_disk(1.5, 8.0, 0.5)
    assert scene.bottleneck is False
    assert "磁盘正常" in scene.notes


def test_disk_bottleneck_on_any_threshold():
    scene = analyze_disk(12.0, 5.0, 0.5)
    assert scene.bottleneck is True
    assert "iowait" in scene.notes
    scene = analyze_disk(1.0, 35.0, 0.5)
    assert scene.bottleneck is True
    assert "await" in scene.notes
    scene = analyze_disk(1.0, 5.0, 3.5)
    assert scene.bottleneck is True
    assert "avgqu-sz" in scene.notes


def test_analyze_demo_os_disk_healthy():
    start, end = _window()
    result = analyze(DemoClient(), start, end, prom=DemoPromClient())
    assert result["os_scene"]["disk_bottleneck"] is False


def test_analyze_os_host_overloaded_on_cpu_sat():
    scene = analyze_os({"user": 95.0, "system": 2.0, "iowait": 1.0, "steal": 0.0},
                       [(0, 50.0), (60, 50.0)], 0.3, 100)
    assert scene.host_overloaded is True


# ----------------------------------------------------------------------
# DemoPromClient + analyze 多源集成
# ----------------------------------------------------------------------
def test_demo_prom_client_flat_memory():
    prom = DemoPromClient()
    start, end = _window()
    series = prom.mem_usage_series("10.0.0.5:9100", start, end)
    assert len(series) == 16
    assert all(44.0 < v < 46.0 for _, v in series)


def test_collect_os_evidence_demo():
    prom = DemoPromClient()
    start, end = _window()
    scene, evidence = collect_os_evidence(prom, start, end)
    assert scene.cpu.dominant == "us"
    assert scene.memory.risk == "NONE"
    assert scene.network.bottleneck is False
    assert scene.host_overloaded is False
    layers = {e.layer for e in evidence}
    assert "L3+-OS" in layers and "L4-OS" in layers


def test_collect_os_evidence_none_prom():
    scene, evidence = collect_os_evidence(None, *_window())
    assert "L3+-OS" in {e.layer for e in evidence}
    assert "未配置 Prometheus" in evidence[0].desc


class _BrokenProm:
    """OAP 正常但 Prometheus 不可达 → OS 实证层降级 NOT_AVAILABLE（Ch15 验收项 #7）。"""

    def cpu_modes(self, *_a, **_k):
        raise PromClientError("Connection refused: prometheus:9090")

    def mem_usage_series(self, *_a, **_k):
        raise PromClientError("Connection refused: prometheus:9090")


def test_collect_os_evidence_prom_unavailable_degrades():
    scene, evidence = collect_os_evidence(_BrokenProm(), *_window())
    assert "NOT_AVAILABLE" in evidence[0].desc
    assert "Prometheus 不可用" in evidence[0].desc
    assert scene.host_overloaded is False


def test_analyze_demo_includes_os():
    start, end = _window()
    result = analyze(DemoClient(), start, end, prom=DemoPromClient())
    assert result["verdict"] == "DB_BOTTLENECK"
    assert result["os_scene"]["cpu_dominant"] == "us"
    assert result["os_scene"]["host_overloaded"] is False
    layers = {e["层"] for e in result["evidence"]}
    assert "L3+-OS" in layers


def test_analyze_demo_without_prom_marks_unavailable():
    start, end = _window()
    result = analyze(DemoClient(), start, end, prom=None)
    layers = {e["层"] for e in result["evidence"]}
    assert "L3+-OS" in layers  # NOT_AVAILABLE 标注而非缺失


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
