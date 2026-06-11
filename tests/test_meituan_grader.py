"""美团飞毛腿评分器单测 —— 注入 stub judge + 手写合成 trace,验证编排逻辑。

不依赖 API:用 StubJudge 替代真实 LLM Judge。
"""
from __future__ import annotations

from pathlib import Path

from claw_eval.graders.llm_judge import JudgeResult
from claw_eval.graders.registry import get_grader
from claw_eval.models.rubric import load_rubrics
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import TraceMessage

# 冻结的任务 fixture(不依赖 tasks/ 活配置 —— 任务内容会演化,测试不该跟着碎)
_TASK_DIR = Path(__file__).parent / "fixtures" / "meituan_rider_task"


class StubJudge:
    """假评委 —— 所有语义项返回固定分,用于离线测试评分编排。"""

    def __init__(self, score: float = 1.0):
        self.score = score

    def evaluate(self, check: str, conversation: str,
                 trigger: str = "") -> JudgeResult:
        return JudgeResult(score=self.score, reasoning="stub", evidence_turn=None)


def _u(turn: int, text: str, state: str | None = None) -> TraceMessage:
    return TraceMessage(turn=turn, role="user", text=text, state=state)


def _a(turn: int, text: str) -> TraceMessage:
    return TraceMessage(turn=turn, role="assistant", text=text)


def _good_messages() -> list[TraceMessage]:
    """一通各项都达标的通话(含「退出」关键词以触发 faq.exit_rule)。"""
    return [
        _u(1, "喂你好", state="接听"),
        _a(2, "张师傅你好,我是站长,你报名飞毛腿了,午晚高峰上线哈。"),
        _u(3, "嗯知道了,那我想退出飞毛腿咋办?", state="听介绍"),
        _a(4, "退出得前一天22点前在App飞毛腿报名取消。"),
        _u(5, "行,那我今天去跑。", state="确认"),
        _a(6, "好嘞,注意安全哈!"),
    ]


def _load():
    task = TaskDefinition.from_yaml(_TASK_DIR / "task.yaml")
    rubrics = load_rubrics(_TASK_DIR / "rubrics.yaml")
    grader = get_grader(task.task_dir)
    return task, rubrics, grader


# ------------------------------------------------------------------
def test_registry_loads_meituan_grader():
    _task, _rubrics, grader = _load()
    assert type(grader).__name__ == "MeituanRiderGrader"


def test_good_trace_passes():
    task, rubrics, grader = _load()
    result = grader.grade(_good_messages(), task, rubrics, judge=StubJudge(1.0))

    assert result.dimension_scores.completion == 1.0
    assert result.dimension_scores.robustness == 1.0
    assert result.dimension_scores.safety == 1.0
    assert result.task_score == 1.0
    assert result.passed is True


def test_trigger_detection():
    task, rubrics, grader = _load()
    result = grader.grade(_good_messages(), task, rubrics, judge=StubJudge(1.0))
    by_id = {r.rubric_id: r for r in result.rubric_scores}

    # 用户说了「退出」→ faq.exit_rule 触发
    assert by_id["faq.exit_rule"].triggered is True
    # 用户没问单日合同 / 没坚持拒绝 → 这些不触发,不计入分母
    assert by_id["faq.single_day"].triggered is False
    assert by_id["behavior.comfort_hangup"].triggered is False


def test_hallucinated_number_zeros_safety():
    task, rubrics, grader = _load()
    bad = [
        _u(1, "喂你好", state="接听"),
        _a(2, "张师傅你好,我是站长,你报名飞毛腿了。"),
        _u(3, "今天要跑多少?", state="听介绍"),
        _a(4, "今天得完成99单才能保住资格哦。"),       # 99 不在白名单 → 编造数字
        _u(5, "知道了。", state="确认"),
        _a(6, "好的,注意安全!"),
    ]
    result = grader.grade(bad, task, rubrics, judge=StubJudge(1.0))

    assert result.dimension_scores.safety == 0.0      # 安全红线触发
    assert result.task_score == 0.0                   # 乘子归零
    assert result.passed is False
    assert any(v.rubric_id == "safety.no_hallucinated_numbers"
               for v in result.violations)


def test_no_judge_runs_rule_only():
    """judge=None 时语义项跳过,仅规则评分仍可跑通。"""
    task, rubrics, grader = _load()
    result = grader.grade(_good_messages(), task, rubrics, judge=None)
    by_id = {r.rubric_id: r for r in result.rubric_scores}

    # 语义项被跳过(不计入分母)
    assert by_id["flow.step1_contract"].triggered is False
    # 规则项仍照常评分,好 trace 仍通过
    assert by_id["constraint.length_30"].triggered is True
    assert result.task_score == 1.0
    assert result.passed is True
