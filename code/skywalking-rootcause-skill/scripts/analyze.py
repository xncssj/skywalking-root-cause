"""端点驱动根因分析：SkyWalking 性能根因定位的可执行实现（多源交叉版）。

对应《SkyWalking 性能分析引擎》第 2 章（端点驱动 5 步法）+ 第 4-8 章（根因判定）
+ 第 11 章（OS 实证层 node-exporter 多源交叉）。

核心思路（与专栏理论一致）:
  Step 1  端点 RT 扫描   top_n(endpoint_resp_time) 定位最慢端点
  Step 2  Trace 追踪     queryBasicTraces(BY_DURATION) + queryTrace(完整 Span 树)
  Step 3  调用链分析     Span 按服务/组件聚合耗时 -> 定位瓶颈服务
  Step 4  根因判定       DB/CACHE/DOWNSTREAM/JVM/CODE 五类根因
  Step 4' 多源交叉       OS 实证层（CPU 主导/内存泄漏/网络重传）并入证据链（第 11 章）
  Step 5  证据链输出     每条结论携带可追溯证据

用法:
    python analyze.py --base-url http://oap-host:12800/graphql --minutes 15
    python analyze.py --base-url ... --prometheus-url http://prom-host:9090   # 开启多源交叉
    python analyze.py --demo          # 双源模拟数据演示全流程（OAP + node-exporter）
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sw_client import SwClient, SwClientError
from prom_client import PromClient, DemoPromClient, PromClientError
from os_empirical import analyze_os, OsSceneResult

# ----------------------------------------------------------------------
# 配置阈值（可配置，对应专栏"可配置阈值"设计目标）
# ----------------------------------------------------------------------
SLOW_ENDPOINT_THRESHOLD_MS = 1000.0      # 端点 RT 慢阈值
DB_SPAN_RATIO = 0.60                      # DB 耗时占比 > 60% 判 DB 瓶颈
CACHE_SPAN_RATIO = 0.60                   # 缓存耗时占比 > 60% 判缓存瓶颈
DOWNSTREAM_SPAN_RATIO = 0.60              # 下游服务耗时占比 > 60% 判下游瓶颈
SELF_CPU_RATIO = 0.70                     # 自身耗时 > 70% 且无下游 -> 代码/本地瓶颈


class Evidence:
    """证据链条目：一个可追溯的根因证据。"""

    def __init__(self, layer: str, desc: str, ref: Optional[str] = None) -> None:
        self.layer = layer      # L1-端点 / L2-Trace / L3-瓶颈 / L4-Span / L5-SQL
        self.desc = desc
        self.ref = ref          # traceId / serviceName / SQL 等引用

    def to_dict(self) -> Dict[str, str]:
        return {"层": self.layer, "证据": self.desc, "引用": self.ref or "-"}


def build_endpoint_scan(client: SwClient, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Step 1: 端点 RT 扫描，过滤出慢端点。"""
    all_endpoints = client.top_slow_endpoints(start, end, limit=20)
    slow = [ep for ep in all_endpoints if ep["resp_time_ms"] > SLOW_ENDPOINT_THRESHOLD_MS]
    return slow


# SkyWalking 常见组件名（用于根因归类）。
# 注意：真实 OAP 返回的是组件 display name（如 mysql-connector-java / HttpClient），
# 与 demo 数据的简化名（MySQL / Feign）不同，需同时收录（2026-08 demo-validation 实测发现）。
DB_COMPONENTS = {"MySQL", "JDBC", "PostgreSQL", "H2", "Oracle", "mongodb", "es",
                 "mysql-connector-java", "mysql-connector-j", "postgresql", "mongodb-driver", "oracle-jdbc-driver"}
CACHE_COMPONENTS = {"Redis", "Lettuce", "Jedis", "Memcached"}
HTTP_COMPONENTS = {"Feign", "SpringMVC", "RestTemplate", "OkHttp", "HTTP", "HttpClient",
                   "httpclient", "java-http-client"}


def span_duration(sp: Dict[str, Any]) -> float:
    try:
        return float(sp.get("endTime", 0)) - float(sp.get("startTime", 0))
    except (TypeError, ValueError):
        return 0.0


def build_span_stats(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Step 3: 从 Span 树聚合耗时。

    注意（Trace 归因的经典坑）：级联 Trace 中 Entry/Exit span 是嵌套计时的，
    例如 order->cart 的 Exit(56000ms) 已包含 cart->MySQL 的 Exit(55800ms)。
    简单累加会把 224s 的总耗时算成 56s 的 4 倍，并掩盖真正的瓶颈。

    因此本实现采用「最内层主导 Exit span」模型（对应专栏 3.5.8.3 的判定口径）：
      - Span 角色判定：带 peer 的为 Exit span（调用下游/DB/缓存），有 endpointName 的为 Entry span
      - 容器判定：DOWNSTREAM 类 Exit(Feign/HTTP) 若包含"另一服务"的 Entry span（按区间包含），
        说明它只是嵌套调用链的外壳（order->cart 的 Exit 里包着 cart 的 Entry 及其 MySQL Exit），
        外壳 span 比内脏 span 多出的 ~200ms 只是网络与框架开销，不是根因
      - 根因 = 非容器 Exit span（叶子）中耗时最长者，其 component 定根因类型
      - ratio = 叶子 Exit 耗时 / 整条 trace 耗时
    """
    entry_spans = [sp for sp in spans if not sp.get("peer") and sp.get("endpointName")]
    exit_spans = [sp for sp in spans if sp.get("peer") and span_duration(sp) > 0]

    def _contains(outer: Dict[str, Any], inner: Dict[str, Any]) -> bool:
        """outer 区间是否完全包含 inner 区间。"""
        o_start, o_end = float(outer.get("startTime", 0)), float(outer.get("endTime", 0))
        i_start, i_end = float(inner.get("startTime", 0)), float(inner.get("endTime", 0))
        return o_start <= i_start and o_end >= i_end

    def _is_container(sp: Dict[str, Any]) -> bool:
        """DOWNSTREAM Exit 内部包含另一服务的 Entry span => 嵌套外壳，非根因。"""
        if _classify(sp.get("component") or "local") != "DOWNSTREAM":
            return False
        own_svc = sp.get("serviceCode")
        return any(
            other.get("serviceCode") != own_svc and _contains(sp, other)
            for other in entry_spans
        )

    # 叶子 Exit span：去掉嵌套外壳后剩下的 Exit span
    leaf_exits = [sp for sp in exit_spans if not _is_container(sp)]
    dominant = max(leaf_exits or exit_spans, key=span_duration, default=None)

    # 整条 trace 总耗时：取 root/最长 span 的耗时作为口径
    total = max((span_duration(sp) for sp in spans), default=0.0)

    # 展示用组件耗时：只统计叶子 Exit span（外部调用真实耗时），避免嵌套重复叠加。
    # local 桶 = 总耗时 - 叶子 Exit 耗时之和，代表服务自身逻辑 + 框架开销（对应专栏 3.5.8.4）
    comp_duration: Dict[str, float] = defaultdict(float)
    db_sqls: List[Tuple[str, str]] = []   # (peer, sql)

    leaf_key = {id(sp): sp.get("component") or "local" for sp in leaf_exits}
    for sp in exit_spans:
        if id(sp) not in leaf_key:
            continue  # 容器 Exit 的耗时已体现在其内部叶子 Exit 上
        comp_duration[leaf_key[id(sp)]] += span_duration(sp)
    comp_duration["local"] = total - sum(comp_duration.values())

    db_sqls = []
    for sp in spans:
        tag_keys = {t.get("key") for t in sp.get("tags", [])}
        if "db.statement" in tag_keys:
            sql = next((t.get("value") for t in sp.get("tags", []) if t.get("key") == "db.statement"), "")
            db_sqls.append((sp.get("peer") or "-", sql))

    dominant_info: Optional[Dict[str, Any]] = None
    if dominant is not None:
        d = span_duration(dominant)
        dominant_info = {
            "component": dominant.get("component") or "local",
            "service": dominant.get("serviceCode") or "unknown",
            "peer": dominant.get("peer") or "-",
            "duration_ms": d,
            "ratio": (d / total) if total else 0.0,
            "sql": next((t.get("value") for t in dominant.get("tags", []) if t.get("key") == "db.statement"), None),
        }

    return {
        "total_ms": total,
        "service_durations": {},  # 保留字段位，供报告扩展
        "component_durations": dict(comp_duration),
        "db_spans": db_sqls,
        "dominant_exit": dominant_info,
        "exit_count": len(exit_spans),
    }


def _classify(component: str) -> str:
    if component in DB_COMPONENTS:
        return "DB"
    if component in CACHE_COMPONENTS:
        return "CACHE"
    if component in HTTP_COMPONENTS:
        return "DOWNSTREAM"
    return "CODE"


def judge_root_cause(stats: Dict[str, Any], slow_endpoints: List[Dict[str, Any]]) -> Tuple[str, str, List[Evidence]]:
    """Step 4: 根因判定 -- 返回 (根因类型, 一句话结论, 证据链)。"""
    total = stats["total_ms"]
    evidence: List[Evidence] = []
    dominant = stats.get("dominant_exit")

    # 证据：慢端点
    for ep in slow_endpoints[:3]:
        evidence.append(
            Evidence(
                "L1-端点",
                f"{ep['service_name']} - {ep['endpoint_name']} RT={ep['resp_time_ms']:.0f}ms",
                ep["endpoint_id"],
            )
        )

    if dominant is None:
        # 无 Exit span：整条耗时都在服务自身处理（代码级/本地逻辑）
        evidence.append(Evidence("L5-Span", f"无下游调用 span，自身处理占整条 trace（{total:.0f}ms）"))
        return "CODE_LEVEL", "无下游调用，代码级/本地处理耗时为主因（建议 Profiling / Arthas）", evidence

    kind = _classify(dominant["component"])
    ratio = dominant["ratio"]
    evidence.append(
        Evidence(
            "L3-瓶颈",
            f"瓶颈 span: {dominant['service']} 调用 {dominant['peer']} 耗时 "
            f"{dominant['duration_ms']:.0f}ms，占整条 trace {ratio * 100:.1f}%",
            dominant["peer"],
        )
    )

    if kind == "DB":
        sql_hint = dominant.get("sql") or (stats["db_spans"][0][1][:80] if stats["db_spans"] else "-")
        evidence.append(Evidence("L5-SQL", f"DB span 占比 {ratio * 100:.1f}%，抽样 SQL: {sql_hint}", dominant["peer"]))
        return "DB_BOTTLENECK", "DB 慢查询为主因（建议 EXPLAIN + 检查索引）", evidence

    if kind == "CACHE":
        evidence.append(Evidence("L4-Span", f"Redis span 占比 {ratio * 100:.1f}%（命中率/延迟需结合 exporter）"))
        return "CACHE_BOTTLENECK", "缓存瓶颈为主因（建议检查命中率与延迟）", evidence

    if kind == "DOWNSTREAM":
        evidence.append(Evidence("L4-Span", f"下游服务调用占比 {ratio * 100:.1f}%，需继续下钻最慢下游"))
        return "DOWNSTREAM_BOTTLENECK", "下游服务调用为主因（建议检查最慢下游）", evidence

    evidence.append(Evidence("L5-Span", f"自身处理占比 {ratio * 100:.1f}%（无显著 DB/缓存/下游）"))
    return "CODE_LEVEL", "代码级处理耗时为主因（建议 Profiling / Arthas 定位热点方法）", evidence


def collect_os_evidence(
    prom: Any, start: datetime, end: datetime, instance: str = "10.0.0.5:9100"
) -> Tuple[OsSceneResult, List[Evidence]]:
    """OS 实证层（第 11 章 11.3 精简四维（CPU/内存/网络/磁盘））：拉取 node-exporter 指标并入证据链。

    以瓶颈服务 IP 为关联键（SkyWalking 实例名 `{uuid}@{IP}` 与 node-exporter `{IP}:9100` 同 IP，
    见第 6 章 6.4 extractIpFromSwInstanceName）。prom 为 None（未配置 Prometheus）时返回 NOT_AVAILABLE。
    instance 可用 --os-instance 覆盖（demo 固定 10.0.0.5:9100；真实环境从瓶颈服务实例名解析 IP）。
    """
    if prom is None:
        return OsSceneResult(), [
            Evidence("L3+-OS", "未配置 Prometheus，OS 实证层不可用（缺 node-exporter 交叉验证）")
        ]

    try:
        cpu_modes = prom.cpu_modes(instance, start, end)
        mem_series = prom.mem_usage_series(instance, start, end)
        retrans = prom.tcp_retrans_ratio(instance, start, end)
        timewait = prom.tcp_timewait_count(instance, start, end)
        iowait_pct, await_ms, avgqu_sz = prom.disk_metrics(
            instance, start, end, cpu_modes.get("iowait", 0.0)
        )
    except PromClientError as exc:
        # Prometheus 不可达/无数据 → OS 实证层降级为 NOT_AVAILABLE，不影响主判定
        return OsSceneResult(), [
            Evidence("L3+-OS", f"Prometheus 不可用，OS 实证层降级 NOT_AVAILABLE（{exc}）")
        ]

    scene = analyze_os(cpu_modes, mem_series, retrans, timewait, iowait_pct, await_ms, avgqu_sz)

    evidence = [
        Evidence("L3+-OS", scene.summary[0], instance),
        Evidence("L3+-OS", scene.summary[1], instance),
        Evidence("L4-OS", scene.summary[2], instance),
        Evidence("L4-OS", scene.summary[3], instance),
    ]
    if scene.host_overloaded:
        evidence.append(Evidence("L3+-OS", "宿主机整体超载风险（CPU 饱和/内存泄漏），联动 JVM-OS 交叉（第 6 章）"))
    return scene, evidence


def analyze(
    client: SwClient,
    start: datetime,
    end: datetime,
    prom: Any = None,
    bottleneck_instance: Optional[str] = None,
    os_instance: str = "10.0.0.5:9100",
) -> Dict[str, Any]:
    """完整执行端点驱动 5 步分析。prom 为可选 Prometheus 客户端（多源交叉，第 11 章）。"""
    evidence: List[Evidence] = []
    slow = build_endpoint_scan(client, start, end)

    if not slow:
        return {
            "verdict": "HEALTHY",
            "summary": "无慢端点（RT 均低于阈值），未触发深度分析。",
            "evidence": [],
            "slow_endpoints": [],
        }

    # 取最慢端点查 Trace（Step 2）
    top = slow[0]
    traces = client.query_basic_traces(
        start, end, service_id=top["service_id"], endpoint_id=top["endpoint_id"],
        order="BY_DURATION", page_size=3,
    )
    spans: List[Dict[str, Any]] = []
    for tr in traces:
        trace_ids = tr.get("traceIds") or []
        if not trace_ids:
            continue
        for tid in trace_ids:
            detail = client.query_trace(tid)
            spans.extend(detail.get("spans", []))
            evidence.append(Evidence("L2-Trace", f"最慢 Trace {tid} 耗时 {tr.get('duration')}ms", tid))
            break  # 每个列表项取一个 traceId 即可
        if spans:
            break

    if not spans:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "summary": "端点慢但 Trace 为空，可能客户端超时未采到下游（建议扩大窗口）。",
            "evidence": [e.to_dict() for e in evidence],
            "slow_endpoints": slow[:5],
        }

    stats = build_span_stats(spans)
    root_cause, summary, extra_evidence = judge_root_cause(stats, slow)
    evidence.extend(extra_evidence)

    # 多源交叉（第 11 章）：OS 实证层并入证据链，并对判定做实证佐证/证伪
    os_scene, os_evidence = collect_os_evidence(prom, start, end, instance=os_instance)
    evidence.extend(os_evidence)
    if os_scene.host_overloaded:
        summary += "（OS 实证层提示宿主机超载风险，需先排除主机再归因应用——第 6 章 JVM-OS 交叉）"

    return {
        "verdict": root_cause,
        "summary": summary,
        "evidence": [e.to_dict() for e in evidence],
        "slow_endpoints": slow[:5],
        "span_stats": {
            "total_ms": stats["total_ms"],
            "service_durations": stats["service_durations"],
            "component_durations": stats["component_durations"],
            "dominant_exit": stats["dominant_exit"],
            "exit_count": stats["exit_count"],
        },
        "os_scene": {
            "cpu_dominant": os_scene.cpu.dominant,
            "host_overloaded": os_scene.host_overloaded,
            "mem_leak_risk": os_scene.memory.risk,
            "network_bottleneck": os_scene.network.bottleneck,
            "disk_bottleneck": os_scene.disk.bottleneck,
        },
    }


def print_report(result: Dict[str, Any]) -> None:
    print("=" * 56)
    print("SkyWalking 根因分析报告")
    print("=" * 56)
    print(f"[判定] {result['verdict']}")
    print(f"[结论] {result['summary']}")
    if result.get("span_stats"):
        s = result["span_stats"]
        print(f"[Trace 总耗时] {s['total_ms']:.0f}ms  (Exit span 数: {s['exit_count']})")
        if s.get("dominant_exit"):
            d = s["dominant_exit"]
            print(f"[主导 Exit] {d['service']} -> {d['peer']} ({d['component']}) "
                  f"{d['duration_ms']:.0f}ms 占 {d['ratio'] * 100:.1f}%")
        print(f"[组件耗时] " + ", ".join(f"{k}={v:.0f}ms" for k, v in s["component_durations"].items()))
    if result.get("os_scene"):
        o = result["os_scene"]
        print(f"\n[OS 实证层] CPU 主导={o['cpu_dominant']} · 内存泄漏={o['mem_leak_risk']} · "
              f"网络瓶颈={'是' if o['network_bottleneck'] else '否'} · "
              f"磁盘瓶颈={'是' if o['disk_bottleneck'] else '否'} · "
              f"宿主机超载={'是' if o['host_overloaded'] else '否'}")
    print("\n[证据链]")
    for e in result["evidence"]:
        print(f"  - [{e['层']}] {e['证据']}")
    print("\n[慢端点]")
    for ep in result["slow_endpoints"]:
        print(f"  - {ep['service_name']} | {ep['endpoint_name']} | {ep['resp_time_ms']:.0f}ms")


# ----------------------------------------------------------------------
# Demo 模式：用内置模拟数据演示全流程，无需真实 OAP
# ----------------------------------------------------------------------
class DemoClient:
    """模拟 SwClient（数据取自专栏压测案例脱敏版），用于离线演示/真实跑通测试。"""

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def top_slow_endpoints(self, *_: Any, **__: Any) -> List[Dict[str, Any]]:
        return [
            {"service_name": "order-svc", "service_id": "order-svc.1",
             "endpoint_name": "GET:/order/test/sql/real-slow", "endpoint_id": "order.1_GET...", "resp_time_ms": 56057.0},
            {"service_name": "cart-svc", "service_id": "cart-svc.1",
             "endpoint_name": "GET:/cart/test/sql/real-slow", "endpoint_id": "cart.1_GET...", "resp_time_ms": 56057.0},
            {"service_name": "member-svc", "service_id": "member-svc.1",
             "endpoint_name": "GET:/member/test/sql/real-slow", "endpoint_id": "member.1_GET...", "resp_time_ms": 23044.0},
        ]

    def query_basic_traces(self, *_: Any, **__: Any) -> List[Dict[str, Any]]:
        return [{"duration": 56501, "traceIds": ["demo.trace.001"], "isError": False, "start": 1782798828575}]

    def query_trace(self, trace_id: str) -> Dict[str, Any]:
        return {
            "spans": [
                {"serviceCode": "order-svc", "component": "SpringMVC", "startTime": 0, "endTime": 56501,
                 "endpointName": "GET:/order/test/sql/real-slow", "tags": []},
                {"serviceCode": "order-svc", "component": "Feign", "startTime": 1, "endTime": 56000,
                 "peer": "cart-svc", "tags": []},
                {"serviceCode": "cart-svc", "component": "SpringMVC", "startTime": 2, "endTime": 55999,
                 "endpointName": "GET:/cart/test/sql/real-slow", "tags": []},
                {"serviceCode": "cart-svc", "component": "MySQL", "startTime": 3, "endTime": 55800,
                 "peer": "db-host:3306",
                 "tags": [{"key": "db.statement", "value": "SELECT * FROM t_biz_order WHERE status=?"}]},
                {"serviceCode": "cart-svc", "component": "Feign", "startTime": 500, "endTime": 600,
                 "peer": "member-svc", "tags": []},
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="SkyWalking 端点驱动根因分析（多源交叉版）")
    parser.add_argument("--base-url", default="http://oap-host:12800/graphql", help="OAP GraphQL 地址")
    parser.add_argument("--prometheus-url", default=None,
                        help="Prometheus 地址（node-exporter 数据源，开启 OS 实证层交叉验证，第 11 章）")
    parser.add_argument("--minutes", type=int, default=15, help="分析时间窗口（分钟）")
    parser.add_argument("--os-instance", default="10.0.0.5:9100",
                        help="node-exporter 实例（{IP}:9100），OS 实证层关联键，默认 10.0.0.5:9100")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出分析结果（供 LLM 报告等下游消费）")
    parser.add_argument("--demo", action="store_true", help="使用内置模拟数据演示（OAP + node-exporter 双源）")
    args = parser.parse_args()

    end = datetime.now()
    start = end - timedelta(minutes=args.minutes)

    client: Any = DemoClient() if args.demo else SwClient(args.base_url)
    if args.demo:
        prom: Any = DemoPromClient()          # 离线演示：内置健康宿主机数据
    elif args.prometheus_url:
        prom = PromClient(args.prometheus_url)
    else:
        prom = None                           # 未配置 Prometheus：OS 层标记 NOT_AVAILABLE
    try:
        result = analyze(client, start, end, prom=prom, os_instance=args.os_instance)  # type: ignore[arg-type]
    except SwClientError as exc:
        # OAP 不可达/无 Trace → 降级 INSUFFICIENT_DATA，不崩溃（Ch15 §15.6 验收项 #7）
        result = {
            "verdict": "INSUFFICIENT_DATA",
            "summary": f"SkyWalking OAP 不可达或分析窗口内无数据：{exc}",
            "evidence": [
                {"层": "L1", "证据": "OAP GraphQL 请求失败，无法完成端点扫描"}
            ],
            "slow_endpoints": [],
            "span_stats": {},
            "os_scene": {
                "cpu_dominant": "-", "host_overloaded": False,
                "mem_leak_risk": "NONE", "network_bottleneck": False, "disk_bottleneck": False,
            },
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())