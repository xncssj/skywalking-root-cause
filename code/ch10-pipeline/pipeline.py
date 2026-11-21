"""第 10 章设计模式四件套的可运行骨架（与正文代码一致，防文档漂移）。

包含：模板方法（AbstractLayerHandler）、责任链（AnalysisPipeline）、
策略（RootCauseStrategy Protocol）、Spec DSL（and_/or_/not_）。

验证：``python -m pytest test_pipeline.py -q``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, List, Optional, Protocol, TypeVar

from typing import TYPE_CHECKING


@dataclass
class AnalysisContext:
    """分析上下文：各层共享的判定数据。"""

    endpoint_rt_ms: float = 0.0
    db_ratio: float = 0.0
    oldgen_ratio: float = 0.0
    old_gc_ratio: float = 0.0
    used_memory: float = 0.0
    maxmemory: float = 0.0
    sql_not_null: bool = False
    root_cause: Optional[str] = None


@dataclass
class RootCause:
    kind: str
    confidence: float = 0.0


class AbstractLayerHandler:
    """模板方法：每层的骨架，统一 preCheck / doHandle / 异常处理。"""

    def handle(self, ctx: AnalysisContext) -> bool:
        if not self.pre_check(ctx):  # 数据是否可用
            return False
        try:
            return self.do_handle(ctx)  # 子类实现
        except Exception as exc:
            return self.handle_error(exc, ctx)  # 统一异常处理

    def do_handle(self, ctx: AnalysisContext) -> bool:
        raise NotImplementedError

    def pre_check(self, ctx: AnalysisContext) -> bool:
        return True

    def handle_error(self, exc: Exception, ctx: AnalysisContext) -> bool:
        return False


class AnalysisPipeline:
    """责任链：按 L1-L7 顺序执行，任一 handler 返回 True（已定案）即短路。"""

    def __init__(self, handlers: List[AbstractLayerHandler]):
        self.handlers = handlers

    def run(self, ctx: AnalysisContext) -> Optional[str]:
        """needs 短路返回 true/false → 链中断或继续（与 execute_async 返回语义一致）。"""
        for h in self.handlers:
            if h.handle(ctx):
                return ctx.root_cause
        return None


class L6SynthesisHandler(AbstractLayerHandler):
    """综合判定层示例：已有 root_cause 即定案（短路）。"""

    def do_handle(self, ctx: AnalysisContext) -> bool:
        return ctx.root_cause is not None


class RootCauseStrategy(Protocol):
    """策略协议：8 个根因各实现 support/resolve（DB/CACHE/JVM/DOWNSTREAM/NETWORK/CODE/OS/UNKNOWN）。"""

    def support(self, ctx: AnalysisContext) -> bool: ...

    def resolve(self, ctx: AnalysisContext) -> RootCause: ...


C = TypeVar("C")


class Spec(Generic[C]):
    """DSL 化判据：and / or / not 组合算子（纯函数，可测、可下沉 YAML）。"""

    def __init__(self, fn: Callable[[C], bool]):
        self._fn = fn

    def __call__(self, ctx: C) -> bool:
        return self._fn(ctx)

    def and_(self, other: "Spec[C]") -> "Spec[C]":
        return Spec(lambda c: self(c) and other(c))

    def or_(self, other: "Spec[C]") -> "Spec[C]":
        return Spec(lambda c: self(c) or other(c))

    @staticmethod
    def not_(spec: "Spec[C]") -> "Spec[C]":
        return Spec(lambda c: not spec(c))


# 10.6.1 的 JVM_FULL_GC 复合判据（正文同款）
def jvm_full_gc_spec() -> Spec[AnalysisContext]:
    return (
        Spec(lambda c: c.oldgen_ratio > 0.85)  # 老年代 > 85%
        .and_(Spec(lambda c: c.old_gc_ratio > 0.10))  # AND 老年代 GC 占比 > 10%
        .and_(
            Spec.not_(  # 且 NOT 缓存淘汰解释一切
                Spec(lambda c: c.used_memory >= c.maxmemory * 0.95)
            )
        )
    )
