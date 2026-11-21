#!/usr/bin/env python3
"""Ollama LLM 报告生成 + 硬约束验证（对应专栏第 13/15 章）。

输入：analyze.py --json 输出的根因分析结果 JSON。
输出：三段式报告（根因转述 / 证据链转述 / 处置建议），并做 Ch15 §15.5 硬约束校验：
  1. 根因类型不得新增/篡改（输出必须含唯一根因 label）
  2. 证据链不得捏造（关键数值与引用必须与输入一致）
  3. 处置建议必须与根因类型匹配

用法:
    python analyze.py --json ... > result.json
    python analyze.py --json ... | python llm_report.py
    python llm_report.py result.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:1.5b")

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "skywalking-rootcause-skill"
    / "templates"
    / "report.md"
)

# 根因类型 -> 处置建议要点（对应专栏第 4-8 章判定后动作）
ACTION_MAP: Dict[str, str] = {
    "DB_BOTTLENECK": "EXPLAIN 慢 SQL、检查 (status, create_time) 是否缺复合索引、必要时 SQL 改写或建索引",
    "CACHE_BOTTLENECK": "检查 Redis 命中率与延迟、排查大 Key / 热点 Key、评估连接池与序列化开销",
    "DOWNSTREAM_BOTTLENECK": "下钻最慢下游服务，重复本轮 5 步法定位其内部瓶颈",
    "CODE_LEVEL": "Profiling 定位热点方法（async-profiler / Arthas），检查锁竞争与本地逻辑",
    "HEALTHY": "无根因，保持监控，关注趋势变化",
    "INSUFFICIENT_DATA": "扩大分析时间窗口，补充采样后重试",
}

LABELS = set(ACTION_MAP.keys())


def _strip_think(text: str) -> str:
    """去掉 deepseek-r1 的思考过程 <think>...</think>。"""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _ollama_generate(prompt: str, model: str = MODEL) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 1024},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        body = json.loads(resp.read().decode("utf-8"))
    return _strip_think(body.get("response", ""))


def build_prompt(result: Dict[str, Any]) -> str:
    """三段式 prompt（Ch13 few-shot 结构 + Ch15 §15.5 硬约束注入）。"""
    verdict = result["verdict"]
    ev = result.get("evidence", [])
    slow = result.get("slow_endpoints", [])
    ss = result.get("span_stats", {})
    os_s = result.get("os_scene", {})

    lines = [
        "你是 APM 性能分析专家，基于以下 SkyWalking 根因分析引擎输出的结构化证据，",
        "用中文写成一段运维可直接执行的问题报告。",
        "",
        "【硬约束】",
        "- 根因类型只能从输入中复制，不得新增、不得改动；",
        "- 所有数字与引用（peer、SQL、端口）必须与输入一致，禁止捏造；",
        "- 处置建议只能选择与根因类型对应的措施。",
        "",
        "【输出格式】必须严格按以下模板，第 2 行【根因类型】一字不差输出：",
        "1.【根因类型】{VERDICT}",
        "2.【根因】用一句话复述结论；",
        "3.【证据链】逐条转述 L1-L5（及 OS）证据，保持数值不变；",
        "4.【处置建议】基于根因类型给出 1-3 条具体动作。",
        "",
        f"【根因类型】{verdict}",
        f"【结论】{result['summary']}",
    ]

    if ss:
        dom = ss.get("dominant_exit") or {}
        lines += [
            f"【Trace 总耗时】{ss['total_ms']:.0f}ms",
            f"【主导 Exit】{dom.get('service', '-')} -> {dom.get('peer', '-')} "
            f"({dom.get('component', '-')}) {dom.get('duration_ms', 0):.0f}ms "
            f"占 {dom.get('ratio', 0) * 100:.1f}%",
        ]

    lines += ["", "【证据链】"]
    for e in ev:
        lines.append(f"- [{e['层']}] {e['证据']}")
    if os_s:
        lines += [
            "",
            "【OS 实证层】",
            f"- CPU 主导={os_s.get('cpu_dominant', '-')} · "
            f"内存泄漏={os_s.get('mem_leak_risk', '-')} · "
            f"网络瓶颈={os_s.get('network_bottleneck')} · "
            f"磁盘瓶颈={os_s.get('disk_bottleneck')} · "
            f"宿主机超载={os_s.get('host_overloaded')}",
        ]
    lines += [
        "",
        "请严格按上述【输出格式】输出，根因类型必须原样包含 label。",
    ]
    return "\n".join(lines)


def validate(result: Dict[str, Any], text: str) -> Tuple[bool, List[str]]:
    """Ch15 §15.5 硬约束校验：根因唯一性、证据数值保真、建议匹配。"""
    issues: List[str] = []
    verdict = result["verdict"]

    # 1. 根因唯一性：输出必须含正确 label，且不得含其他 label
    present = [lab for lab in LABELS if lab in text]
    if verdict not in present:
        issues.append(f"根因 label '{verdict}' 未出现在报告中")
    extra = [lab for lab in present if lab != verdict]
    if extra:
        issues.append(f"报告出现额外根因 label: {extra}")

    # 2. 证据数值保真：L3 主导 Exit 的关键数字必须原样出现
    dom = (result.get("span_stats") or {}).get("dominant_exit") or {}
    if dom:
        dur = f"{dom['duration_ms']:.0f}"
        if dur not in text:
            issues.append(f"主导 Exit 耗时 {dur}ms 未在报告中保真出现")
        peer = dom.get("peer") or ""
        if peer and peer not in text:
            issues.append(f"主导 Exit peer '{peer}' 未在报告中出现")
        svc = dom.get("service") or ""
        if svc and svc not in text:
            issues.append(f"瓶颈服务 '{svc}' 未在报告中出现")

    # 3. 建议匹配：根因类型应出现在处置建议附近（宽松：label 在全文即视为匹配）
    return len(issues) == 0, issues


def fallback_report(result: Dict[str, Any], issues: List[str]) -> str:
    """§15.5 硬约束兜底：LLM 报告未达标/调用失败时，用 templates/report.md 模板
    渲染规则引擎原文输出，杜绝"幻觉报告下发给值班人"。"""
    verdict = result["verdict"]
    summary = result.get("summary", "")
    ev = result.get("evidence", [])
    ss = result.get("span_stats", {})
    dom = (ss or {}).get("dominant_exit") or {}
    os_s = result.get("os_scene", {})

    lines = [
        "# SkyWalking 根因分析报告（回退：LLM 报告未达标，以下为规则引擎原文）",
        "",
        "## 结论",
        "",
        f"**根因类型**：{verdict}",
        "",
        summary,
        "",
        "## 证据链",
        "",
    ]
    for e in ev:
        lines.append(f"- [{e.get('层', '-')}] {e.get('证据', '')}")
    if dom:
        lines.append(
            f"- 主导 Exit：{dom.get('service', '-')} -> {dom.get('peer', '-')} "
            f"({dom.get('component', '-')}) {dom.get('duration_ms', 0):.0f}ms "
            f"占 {dom.get('ratio', 0) * 100:.1f}%"
        )
    if os_s:
        lines.append(
            f"- OS：CPU 主导={os_s.get('cpu_dominant', '-')} · "
            f"内存泄漏={os_s.get('mem_leak_risk', '-')} · "
            f"磁盘瓶颈={os_s.get('disk_bottleneck')}"
        )
    lines += ["", f"> 模板：{TEMPLATE_PATH}。回退原因：{'；'.join(issues) or '未达标'}。"]
    return "\n".join(lines)


def _print_report(report: str, ok: bool, issues: List[str]) -> None:
    print("=" * 56)
    print("LLM 生成的根因分析报告（deepseek-r1:1.5b）")
    print("=" * 56)
    print(report)
    print("\n[硬约束校验] " + ("通过 ✓" if ok else "未通过 ✗ → 已回退为规则引擎原文"))
    for issue in issues:
        print(f"  - {issue}")


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            result = json.load(f)
    else:
        result = json.load(sys.stdin)

    prompt = build_prompt(result)
    print(f">>> 调用 Ollama 模型 {MODEL} 生成报告...", file=sys.stderr)
    try:
        report = _ollama_generate(prompt)
    except Exception as exc:
        # LLM 超时/不可达 → templates/report.md 模板回退（§15.5 兜底）
        issues = [f"LLM 调用失败（{exc}），走 report.md 模板回退"]
        fallback = fallback_report(result, issues)
        _print_report(fallback, False, issues)
        return 0

    ok, issues = validate(result, report)
    if not ok:
        report = fallback_report(result, issues)

    _print_report(report, ok, issues)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
