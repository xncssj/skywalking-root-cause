"""Prometheus / node-exporter 零依赖客户端。

设计来源：专栏《SkyWalking 性能分析引擎》第 11 章（OS 实证层 node-exporter 五维分析）。
仅用 Python 标准库（urllib），无需 pip install 任何依赖，与 sw_client.py 保持同一工程风格。

本客户端只做**只读查询**（Prometheus HTTP API），不改任何 exporter 配置。

用法示例:
    from prom_client import PromClient

    client = PromClient(base_url="http://prometheus-host:9090")
    series = client.query_range("node_cpu_seconds_total", start, end, step="60s")
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class PromClientError(RuntimeError):
    """Prometheus 查询失败。"""


def _ts(dt: datetime) -> float:
    """datetime -> 秒级 Unix 时间戳（Prometheus 用）。"""
    return dt.timestamp()


class PromClient:
    """Prometheus HTTP API 客户端（覆盖 OS 实证层所需的最小查询集）。"""

    def __init__(self, base_url: str, timeout: int = 10, token: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _get(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 网络/解析错误统一上抛
            raise PromClientError(f"Prometheus 请求失败: {exc}") from exc
        if body.get("status") != "success":
            raise PromClientError(f"Prometheus 返回错误: {body.get('error') or body}")
        return body.get("data", {})

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def query_range(
        self, query: str, start: datetime, end: datetime, step: str = "60s"
    ) -> List[Dict[str, Any]]:
        """range 查询：返回按时间排序的 (timestamp, value) 序列列表。"""
        data = self._get(
            "/api/v1/query_range",
            {
                "query": query,
                "start": str(int(_ts(start))),
                "end": str(int(_ts(end))),
                "step": step,
            },
        )
        return data.get("result", [])

    def instant_query(self, query: str) -> List[Dict[str, Any]]:
        """instant 查询：返回当前值列表。"""
        data = self._get("/api/v1/query", {"query": query})
        return data.get("result", [])

    # ------------------------------------------------------------------
    # OS 实证层维度查询（精简四维，见 os_empirical.py）
    # ------------------------------------------------------------------
    def cpu_modes(self, instance: str, start: datetime, end: datetime) -> Dict[str, float]:
        """node_cpu_seconds_total 各 mode 的占比（rate，占单核归一化到 0-100%）。"""
        result = self.query_range(
            f'rate(node_cpu_seconds_total{{instance="{instance}"}}[1m])',
            start, end, step="60s",
        )
        mode_rates: Dict[str, float] = {}
        for series in result:
            mode = (series.get("metric") or {}).get("mode", "")
            values = series.get("values") or []
            if not values or mode == "idle":
                continue
            # 取窗口内最后一个采样点
            mode_rates[mode] = float(values[-1][1])
        total = sum(mode_rates.values()) or 1.0
        return {mode: (v / total) * 100.0 for mode, v in mode_rates.items()}

    def mem_usage_series(
        self, instance: str, start: datetime, end: datetime
    ) -> List[Tuple[float, float]]:
        """内存使用率序列（%）：(timestamp, usage%)，用于泄漏趋势分析。"""
        result = self.query_range(
            f"100 * (1 - node_memory_MemAvailable_bytes{{instance=\"{instance}\"}}"
            f" / node_memory_MemTotal_bytes{{instance=\"{instance}\"}})",
            start, end, step="60s",
        )
        out: List[Tuple[float, float]] = []
        for series in result:
            for t, v in series.get("values") or []:
                try:
                    out.append((float(t), float(v)))
                except (TypeError, ValueError):
                    continue
        out.sort(key=lambda kv: kv[0])
        return out

    def tcp_retrans_ratio(self, instance: str, start: datetime, end: datetime) -> float:
        """TCP 重传率（%）：RetransSegs / OutSegs 或 InSegs，窗口内均值。"""
        result = self.query_range(
            f"(rate(node_netstat_Tcp_RetransSegs{{instance=\"{instance}\"}}[1m])"
            f" / rate(node_netstat_Tcp_OutSegs{{instance=\"{instance}\"}}[1m])) * 100",
            start, end, step="60s",
        )
        ratios: List[float] = []
        for series in result:
            for _, v in series.get("values") or []:
                try:
                    ratios.append(float(v))
                except (TypeError, ValueError):
                    continue
        if not ratios:
            return 0.0
        return sum(ratios) / len(ratios)

    def tcp_timewait_count(self, instance: str, start: datetime, end: datetime) -> float:
        """TCP TIME_WAIT 连接数（窗口内均值），联动 NETWORK_CONNECTION_LEAK。"""
        result = self.query_range(
            f"node_netstat_Tcp_CurrEstab{{instance=\"{instance}\",state=\"TIME_WAIT\"}}"
            f" or node_sockstat_TCP_TW{{instance=\"{instance}\"}}",
            start, end, step="60s",
        )
        vals: List[float] = []
        for series in result:
            for _, v in series.get("values") or []:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    continue
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    def disk_metrics(
        self, instance: str, start: datetime, end: datetime, iowait_pct: float
    ) -> Tuple[float, float, float]:
        """磁盘维度 (iowait_pct, await_ms, avgqu_sz)。

        iowait 复用 cpu_modes 的 mode=iowait（避免重复查询）；await 取读时延
        （node_disk_read_time / node_disk_reads_completed，未暴露时回退 0）；
        avgqu-sz 依赖 iostat 导出，node-exporter 默认不暴露 → 回退 0（视为正常）。
        """
        await_ms = 0.0
        result = self.query_range(
            f"rate(node_disk_read_time_seconds_total{{instance=\"{instance}\"}}[1m])"
            f" / rate(node_disk_reads_completed_total{{instance=\"{instance}\"}}[1m]) * 1000",
            start, end, step="60s",
        )
        vals: List[float] = []
        for series in result:
            for _, v in series.get("values") or []:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    continue
        # 过滤 NaN（如 node-exporter 无磁盘读统计时分母为 0 → NaN），避免阈值比较异常
        clean = [v for v in vals if v == v]
        if clean:
            await_ms = sum(clean) / len(clean)
        return iowait_pct, await_ms, 0.0


# ----------------------------------------------------------------------
# Demo 模式：内置脱敏模拟数据（无需真实 Prometheus）
# 场景 = 第 4 章 DB_BOTTLENECK 压测案例的宿主机侧：
#   CPU us 主导但整体不高、内存平稳无泄漏、网络无重传 → OS 实证层 NORMAL
#   （交叉结论：宿主机健康 → 瓶颈确在 DB 调用，不是主机超载）
# ----------------------------------------------------------------------
class DemoPromClient:
    """模拟 PromClient：返回与压测案例一致的健康宿主机的 node-exporter 数据。"""

    CPU_MODES = {"user": 12.0, "system": 4.0, "iowait": 1.5, "steal": 0.2, "nice": 0.1}
    MEM_USAGE_PCT = 45.0            # 内存使用率平稳 ~45%
    RETRANS_RATIO_PCT = 0.3         # 重传率 0.3%（ch7 走查的"正常"值）
    TIMEWAIT_COUNT = 2400.0         # TIME_WAIT 远低于 10000 阈值

    def cpu_modes(self, instance: str, start: datetime, end: datetime) -> Dict[str, float]:
        # 归一化补足 100%（idle 剩余），与真实 Prometheus 归一行为一致
        total = sum(self.CPU_MODES.values())
        return {mode: (v / total) * 100.0 for mode, v in self.CPU_MODES.items()}

    def mem_usage_series(
        self, instance: str, start: datetime, end: datetime
    ) -> List[Tuple[float, float]]:
        # 平稳序列：45% ± 0.5% 的对称抖动（完整周期正弦，净趋势≈0）
        # → slope≈0、R²≈0，不满足泄漏 4 条件（与真实健康宿主机的平稳内存一致）
        n = 16
        out = []
        for i in range(n):
            t = _ts(start) + i * 60.0
            usage = self.MEM_USAGE_PCT + 0.5 * math.sin(i * math.pi / 4.0)
            out.append((t, usage))
        return out

    def tcp_retrans_ratio(self, instance: str, start: datetime, end: datetime) -> float:
        return self.RETRANS_RATIO_PCT

    def tcp_timewait_count(self, instance: str, start: datetime, end: datetime) -> float:
        return self.TIMEWAIT_COUNT

    def disk_metrics(
        self, instance: str, start: datetime, end: datetime, iowait_pct: float
    ) -> Tuple[float, float, float]:
        # 健康宿主机：iowait 1.5%（低于 10%）、await 8ms（低于 20ms）、avgqu 0.5（低于 2）
        return iowait_pct, 8.0, 0.5


if __name__ == "__main__":
    print("PromClient 已就绪。示例:")
    print("  client = PromClient('http://prometheus-host:9090')")
    print("  client.cpu_modes('10.0.0.5:9100', start, end)")
    print("  client.mem_usage_series('10.0.0.5:9100', start, end)")
    sys.exit(0)
