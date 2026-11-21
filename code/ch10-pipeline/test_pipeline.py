"""第 10 章设计模式四件套骨架的测试（pytest）。

验证：模板方法短路、责任链顺序与短路、Spec 组合算子、JVM_FULL_GC 复合判据。
"""

from pipeline import (
    AbstractLayerHandler,
    AnalysisContext,
    AnalysisPipeline,
    L6SynthesisHandler,
    Spec,
    jvm_full_gc_spec,
)


class LoggingHandler(AbstractLayerHandler):
    """记录 do_handle 是否被调用（验证短路）。"""

    def __init__(self):
        self.called = False
        self.name = type(self).__name__

    def do_handle(self, ctx: AnalysisContext) -> bool:
        self.called = True
        return False


class DecidingHandler(LoggingHandler):
    """返回 True 并写 root_cause（验证责任链短路）。"""

    def do_handle(self, ctx: AnalysisContext) -> bool:
        self.called = True
        ctx.root_cause = "DB_BOTTLENECK"
        return True


class BrokenHandler(AbstractLayerHandler):
    """do_handle 抛异常 → 走 handle_error（验证模板方法异常兜底）。"""

    def do_handle(self, ctx: AnalysisContext) -> bool:
        raise RuntimeError("boom")


def test_template_precheck_short_circuit():
    class NoDataHandler(AbstractLayerHandler):
        def pre_check(self, ctx: AnalysisContext) -> bool:
            return False

        def do_handle(self, ctx: AnalysisContext) -> bool:
            raise AssertionError("数据不可用不应 do_handle")

    assert NoDataHandler().handle(AnalysisContext()) is False


def test_template_error_goes_to_handle_error():
    assert BrokenHandler().handle(AnalysisContext()) is False


def test_pipeline_runs_in_order():
    h1, h2 = LoggingHandler(), DecidingHandler()
    ctx = AnalysisContext()
    result = AnalysisPipeline([h1, h2]).run(ctx)
    assert result == "DB_BOTTLENECK"
    assert h1.called and h2.called


def test_pipeline_short_circuits_after_decision():
    h1 = DecidingHandler()
    h2 = LoggingHandler()  # 不应被调用
    ctx = AnalysisContext()
    result = AnalysisPipeline([h1, h2]).run(ctx)
    assert result == "DB_BOTTLENECK"
    assert h1.called and not h2.called


def test_pipeline_returns_none_when_no_decision():
    ctx = AnalysisContext()
    result = AnalysisPipeline([LoggingHandler(), L6SynthesisHandler()]).run(ctx)
    assert result is None


def test_spec_and_or_not():
    c = AnalysisContext(oldgen_ratio=0.9)
    spec = (
        Spec(lambda x: x.oldgen_ratio > 0.85)
        .and_(Spec(lambda x: x.old_gc_ratio > 0.10))
        .or_(Spec(lambda x: x.oldgen_ratio > 0.95))
    )
    assert spec(c) is False  # old_gc_ratio 不满足、oldgen 未过 0.95
    assert Spec.not_(spec)(c) is True


def test_jvm_full_gc_compound():
    spec = jvm_full_gc_spec()
    leak = AnalysisContext(oldgen_ratio=0.90, old_gc_ratio=0.12, used_memory=0.5, maxmemory=1.0)
    evict = AnalysisContext(oldgen_ratio=0.90, old_gc_ratio=0.12, used_memory=0.99, maxmemory=1.0)
    assert spec(leak) is True
    assert spec(evict) is False  # 缓存淘汰（内存贴顶）解释一切 → NOT 条件拦住
