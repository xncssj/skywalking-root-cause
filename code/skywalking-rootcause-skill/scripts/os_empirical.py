"""OS 实证层：node-exporter 精简四维分析（CPU/内存/网络/磁盘）。

对应专栏《SkyWalking 性能分析引擎》第 11 章 11.3（OS 实证层）的精简落地版——
只实现 ch5/6/7 判定链真正依赖的四维：
  1. CPU 主导类型（us/sy/wa/st）→ 判断"用户态热点 / 系统调用 / IO 等待 / 宿主机偷走"
  2. 内存泄漏 4 条件（trend up && R²>=0.7 && mean>=60% && slope>=0.5%/h）
  3. 网络重传 / TIME_WAIT → NETWORK_CONNECTION_LEAK 风险
  4. 磁盘 iowait / await / avgqu-sz 三阈值 → DISK 瓶颈（联动 CPU 的 wa 主导）

输出 OsSceneResult 供 analyze.py 并入证据链（L3+-OS / L4-OS）。

阈值与《NFTP 性能分析引擎技术白皮书》§4.5 / 专栏 11.3 一致：
  - CPU 主导：占比最大 mode 即主导；us 主导 → 用户态；sy 主导 → 系统调用；wa 主导 → 磁盘 IO
  - 内存泄漏：4 条件同时满足（11.3.2）；slope>=2%/h 且 mean>=80% 为高风险
  - 网络：重传>5% 判 NETWORK 瓶颈；TIME_WAIT>10000 判 NETWORK_CONNECTION_LEAK（11.3.4）
  - 磁盘：iowait>10% 或 await>20ms 或 avgqu-sz>2 命中即 DISK 瓶颈（任一即可）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class CpuScene:
    """CPU 维度结论（11.3.1 主导指标 → 排查方向）。"""

    dominant: str = "us"              # us/sy/wa/st
    us_pct: float = 0.0
    sy_pct: float = 0.0
    wa_pct: float = 0.0
    st_pct: float = 0.0
    critical: bool = False            # us>85% 视为 CPU 饱和（资源耗尽型第一优先级）

    @property
    def guide(self) -> str:
        if self.dominant == "sy":
            return "系统调用/上下文切换主导 → strace / pidstat -w（嵌套查 CS）"
        if self.dominant == "wa":
            return "IO 等待主导 → iostat -x / iotop（联动磁盘维度）"
        if self.dominant == "st":
            return "宿主超分 steal 主导 → 查宿主机超卖（VM 被偷 CPU）"
        return "用户态热点主导 → perf top -g / async-profiler / jstack"


@dataclass
class MemLeakScene:
    """内存维度结论（11.3.2 泄漏 4 条件 + 使用率）。"""

    leak_detected: bool = False
    risk: str = "NONE"                # NONE / LOW / MEDIUM / HIGH
    usage_mean_pct: float = 0.0
    slope_per_hour: float = 0.0       # %/h
    r_squared: float = 0.0
    notes: str = ""


@dataclass
class NetworkScene:
    """网络维度结论（11.3.4 精简：重传 + TIME_WAIT）。"""

    retrans_ratio_pct: float = 0.0
    timewait_count: float = 0.0
    bottleneck: bool = False
    connection_leak: bool = False

    @property
    def notes(self) -> str:
        if self.connection_leak:
            return f"TIME_WAIT {self.timewait_count:.0f} > 10000 → NETWORK_CONNECTION_LEAK（检查短连接）"
        if self.bottleneck:
            return f"重传率 {self.retrans_ratio_pct:.1f}% > 5% → 网络不稳定，重传导致延迟"
        return f"重传率 {self.retrans_ratio_pct:.1f}%、TIME_WAIT {self.timewait_count:.0f} → 网络正常"


@dataclass
class DiskScene:
    """磁盘维度结论（iowait / await / avgqu-sz 三阈值，任一命中即瓶颈）。"""

    iowait_pct: float = 0.0
    await_ms: float = 0.0
    avgqu_sz: float = 0.0
    bottleneck: bool = False

    @property
    def notes(self) -> str:
        if not self.bottleneck:
            return f"磁盘正常（iowait {self.iowait_pct:.1f}%、await {self.await_ms:.1f}ms、avgqu-sz {self.avgqu_sz:.1f}）"
        hits = []
        if self.iowait_pct > DISK_IOWAIT_PCT:
            hits.append(f"iowait {self.iowait_pct:.1f}% > {DISK_IOWAIT_PCT:.0f}%")
        if self.await_ms > DISK_AWAIT_MS:
            hits.append(f"await {self.await_ms:.1f}ms > {DISK_AWAIT_MS:.0f}ms")
        if self.avgqu_sz > DISK_AVGQU_SZ:
            hits.append(f"avgqu-sz {self.avgqu_sz:.1f} > {DISK_AVGQU_SZ:.0f}")
        return "磁盘瓶颈: " + "; ".join(hits)


@dataclass
class OsSceneResult:
    """OS 实证层综合结论（对应白皮书 OsSceneBottleneckAssembler 的精简版）。"""

    cpu: CpuScene = field(default_factory=CpuScene)
    memory: MemLeakScene = field(default_factory=MemLeakScene)
    network: NetworkScene = field(default_factory=NetworkScene)
    disk: DiskScene = field(default_factory=DiskScene)

    @property
    def host_overloaded(self) -> bool:
        """宿主机整体超载：CPU 饱和 或 内存泄漏高风险（联动第 6 章 JVM-OS 交叉）。"""
        return self.cpu.critical or self.memory.risk in ("HIGH", "MEDIUM")

    @property
    def summary(self) -> List[str]:
        lines = [f"CPU 主导={self.cpu.dominant}（{self.cpu.guide}）"]
        lines.append(f"内存使用率均值 {self.memory.usage_mean_pct:.0f}%，slope {self.memory.slope_per_hour:.2f}%/h"
                     f"（R²={self.memory.r_squared:.2f}）→ {self.memory.risk}")
        lines.append(self.network.notes)
        lines.append(self.disk.notes)
        return lines


# ----------------------------------------------------------------------
# 判定逻辑（纯函数，便于测试）
# ----------------------------------------------------------------------
RETRANS_BOTTLENECK_PCT = 5.0
TIMEWAIT_LEAK_COUNT = 10000.0
CPU_SATURATED_PCT = 85.0
MEM_LEAK_MIN_SLOPE_PH = 0.5
MEM_LEAK_MIN_R2 = 0.7
MEM_LEAK_MIN_MEAN_PCT = 60.0
MEM_HIGH_SLOPE_PH = 2.0
MEM_HIGH_MEAN_PCT = 80.0
MEM_MEDIUM_SLOPE_PH = 1.0
MEM_MEDIUM_MEAN_PCT = 85.0
DISK_IOWAIT_PCT = 10.0
DISK_AWAIT_MS = 20.0
DISK_AVGQU_SZ = 2.0


def _linear_fit(
    series: List[Tuple[float, float]],
) -> Tuple[float, float, float]:
    """最小二乘线性回归：返回 (slope, r_squared, direction)。

    slope 单位与 x 单位相关：x 为秒，故 slope_per_hour = slope * 3600。
    direction: 'up' / 'down' / 'flat'。
    """
    n = len(series)
    if n < 3:
        return 0.0, 0.0, "flat"
    xs = [p[0] for p in series]
    ys = [p[1] for p in series]
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    sxx = sum((x - x_mean) ** 2 for x in xs)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    if sxx == 0:
        return 0.0, 0.0, "flat"
    slope = sxy / sxx
    sst = sum((y - y_mean) ** 2 for y in ys)
    if sst == 0:
        r2 = 1.0
    else:
        sse = sum((y - (y_mean + slope * (x - x_mean))) ** 2 for x, y in zip(xs, ys))
        r2 = 1.0 - sse / sst
    direction = "up" if slope > 1e-9 else ("down" if slope < -1e-9 else "flat")
    return slope, max(0.0, min(1.0, r2)), direction


def analyze_cpu(modes: Dict[str, float]) -> CpuScene:
    """CPU 主导类型判定（11.3.1 精简）。"""
    scene = CpuScene()
    scene.us_pct = modes.get("user", 0.0)
    scene.sy_pct = modes.get("system", 0.0)
    scene.wa_pct = modes.get("iowait", 0.0)
    scene.st_pct = modes.get("steal", 0.0)
    ranked = sorted(
        (("us", scene.us_pct), ("sy", scene.sy_pct), ("wa", scene.wa_pct), ("st", scene.st_pct)),
        key=lambda kv: kv[1],
        reverse=True,
    )
    scene.dominant = ranked[0][0]
    scene.critical = scene.us_pct > CPU_SATURATED_PCT
    return scene


def analyze_memory(
    series: List[Tuple[float, float]],
) -> MemLeakScene:
    """内存泄漏 4 条件（11.3.2）：trend up && R²>=0.7 && mean>=60% && slope>=0.5%/h。"""
    scene = MemLeakScene()
    if not series:
        scene.notes = "无 node-exporter 内存数据（NOT_AVAILABLE）"
        return scene
    slope, r2, direction = _linear_fit(series)
    scene.slope_per_hour = slope * 3600.0
    scene.r_squared = r2
    scene.usage_mean_pct = sum(v for _, v in series) / len(series)

    cond_trend = direction == "up"
    cond_r2 = r2 >= MEM_LEAK_MIN_R2
    cond_mean = scene.usage_mean_pct >= MEM_LEAK_MIN_MEAN_PCT
    cond_slope = scene.slope_per_hour >= MEM_LEAK_MIN_SLOPE_PH
    if cond_trend and cond_r2 and cond_mean and cond_slope:
        scene.leak_detected = True
        if scene.slope_per_hour >= MEM_HIGH_SLOPE_PH and scene.usage_mean_pct >= MEM_HIGH_MEAN_PCT:
            scene.risk = "HIGH"
        elif scene.slope_per_hour >= MEM_MEDIUM_SLOPE_PH or scene.usage_mean_pct >= MEM_MEDIUM_MEAN_PCT:
            scene.risk = "MEDIUM"
        else:
            scene.risk = "LOW"
        scene.notes = f"内存泄漏 {scene.risk} 风险（4 条件命中）"
    else:
        scene.leak_detected = False
        scene.risk = "NONE"
        miss = [k for k, ok in {
            "trend up": cond_trend, "R²>=0.7": cond_r2,
            "mean>=60%": cond_mean, "slope>=0.5%/h": cond_slope,
        }.items() if not ok]
        scene.notes = f"无泄漏（未命中: {', '.join(miss) or '无'}）"
    return scene


def analyze_network(
    retrans_ratio_pct: float, timewait_count: float
) -> NetworkScene:
    """网络维度（11.3.4 精简：重传 + TIME_WAIT）。"""
    scene = NetworkScene(
        retrans_ratio_pct=retrans_ratio_pct,
        timewait_count=timewait_count,
        bottleneck=retrans_ratio_pct > RETRANS_BOTTLENECK_PCT,
        connection_leak=timewait_count > TIMEWAIT_LEAK_COUNT,
    )
    return scene


def analyze_disk(
    iowait_pct: float, await_ms: float, avgqu_sz: float
) -> DiskScene:
    """磁盘维度（iowait / await / avgqu-sz 三阈值，任一命中即瓶颈）。"""
    scene = DiskScene(
        iowait_pct=iowait_pct,
        await_ms=await_ms,
        avgqu_sz=avgqu_sz,
        bottleneck=(
            iowait_pct > DISK_IOWAIT_PCT
            or await_ms > DISK_AWAIT_MS
            or avgqu_sz > DISK_AVGQU_SZ
        ),
    )
    return scene


def analyze_os(
    cpu_modes: Dict[str, float],
    mem_series: List[Tuple[float, float]],
    retrans_ratio_pct: float,
    timewait_count: float,
    iowait_pct: float = 0.0,
    await_ms: float = 0.0,
    avgqu_sz: float = 0.0,
) -> OsSceneResult:
    """四维综合（OsSceneBottleneckAssembler 精简版）。"""
    return OsSceneResult(
        cpu=analyze_cpu(cpu_modes),
        memory=analyze_memory(mem_series),
        network=analyze_network(retrans_ratio_pct, timewait_count),
        disk=analyze_disk(iowait_pct, await_ms, avgqu_sz),
    )
