"""Rubric 人审 gate —— 终端逐条审核,safety 类强制人审才能转正。

工作流:
  draft (rubrics.draft.yaml) → review (逐条 a/r/e/s) → 转正(rubrics.yaml)

纯逻辑函数(测试用)和交互式 loop(CLI 用)分离。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..models.rubric import Rubric

# 决策类型
Decision = str       # accept / reject / edit / skip


@dataclass
class ReviewState:
    """一轮人审的完整状态。"""
    drafts: list[Rubric]
    decisions: dict[str, Decision] = field(default_factory=dict)
    edits: dict[str, Rubric] = field(default_factory=dict)        # id → 编辑后的 rubric

    def decision_for(self, rid: str) -> Decision:
        return self.decisions.get(rid, "skip")


# ---- 纯函数(便于单测)----

def needs_required_review(r: Rubric) -> bool:
    """safety 类(dimension=safety 或 is_safety=true)必须人审。"""
    return r.is_safety or r.dimension == "safety"


def gate_blocked(state: ReviewState) -> list[str]:
    """检查是否还有「必审但 skip」的 rubric。返回阻塞项 id 列表。"""
    blocked = []
    for r in state.drafts:
        if needs_required_review(r) and state.decision_for(r.id) == "skip":
            blocked.append(r.id)
    return blocked


def apply_decisions(state: ReviewState) -> list[Rubric]:
    """按决策产出最终 rubric 列表(写 rubrics.yaml 用)。

    accept:原样保留(标记 reviewed=True)
    edit:用 state.edits[id] 替换
    reject:剔除
    skip:剔除(非 safety 时;safety 必须显式 accept/reject/edit)
    """
    out = []
    for r in state.drafts:
        d = state.decision_for(r.id)
        if d == "reject" or d == "skip":
            continue
        if d == "edit" and r.id in state.edits:
            picked = state.edits[r.id]
        else:
            picked = r
        # 标记 reviewed,清空 confidence(已审,confidence 不再相关)
        picked = picked.model_copy(update={"reviewed": True})
        out.append(picked)
    return out


def format_rubric_summary(r: Rubric) -> str:
    """单条 rubric 简要呈现,用于审核时显示。"""
    lines = [f"  id={r.id}    category={r.category}    dimension={r.dimension}    "
             f"method={r.method}    weight={r.weight}"]
    if r.is_safety:
        lines.append("  ★ is_safety=true  (违反则总分归零)")
    if r.trigger:
        lines.append(f"  trigger={r.trigger.type} "
                     f"({r.trigger.keywords or r.trigger.state or r.trigger.probe_id})")
    if r.params:
        lines.append(f"  params={r.params}")
    if r.confidence is not None:
        lines.append(f"  抽取置信度={r.confidence}")
    lines.append(f"  check: {r.check}")
    return "\n".join(lines)


# ---- 交互式 loop(CLI 用)----

def interactive_review(drafts: list[Rubric],
                       prompt_fn: Callable[[str], str] = input,
                       echo: Callable[[str], None] = print) -> ReviewState:
    """终端逐条审核。返回最终 ReviewState。

    每条提示:[a] 接受  [r] 拒绝  [s] 跳过  ([s] 对 safety 无效)
    """
    state = ReviewState(drafts=list(drafts))
    n = len(drafts)
    for i, r in enumerate(drafts, 1):
        required = needs_required_review(r)
        echo(f"\n[{i}/{n}] {r.id}" + ("  ★ 必须审核" if required else ""))
        echo(format_rubric_summary(r))
        choices = "[a] 接受  [r] 拒绝" + ("" if required else "  [s] 跳过")
        while True:
            ans = prompt_fn(f"  {choices} > ").strip().lower()
            if ans in ("a", "accept"):
                state.decisions[r.id] = "accept"
                echo("  ✓ 接受")
                break
            if ans in ("r", "reject"):
                state.decisions[r.id] = "reject"
                echo("  ✗ 拒绝")
                break
            if ans in ("s", "skip"):
                if required:
                    echo("  ⚠ safety 类不可跳过,必须 a/r")
                    continue
                state.decisions[r.id] = "skip"
                echo("  ⏭ 跳过(不进 rubrics.yaml)")
                break
            echo(f"  无效输入 '{ans}',再试:{choices}")
    return state


def summarize_state(state: ReviewState) -> str:
    counts = {"accept": 0, "reject": 0, "skip": 0, "edit": 0}
    for d in state.decisions.values():
        counts[d] = counts.get(d, 0) + 1
    # 未表态的也算 skip
    untouched = len(state.drafts) - sum(counts.values())
    counts["skip"] += untouched
    return (f"{counts.get('accept', 0)} 接受 · "
            f"{counts.get('edit', 0)} 编辑 · "
            f"{counts.get('reject', 0)} 拒绝 · "
            f"{counts.get('skip', 0)} 跳过")
