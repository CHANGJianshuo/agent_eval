"""Flow → DOT 字符串(Streamlit st.graphviz_chart 渲染最稳)。

节点颜色规则:
  - 无 persona 覆盖(非 START/END):红
  - 有 rubric 分数 ≥0.8:绿
  - 0.5-0.8:黄
  - <0.5:红浅
  - 未触发 / 无 rubric:灰
"""
from __future__ import annotations

from claw_eval.models.flow import FlowDiagram


_COLORS = {
    "good":      ("#dcfce7", "#22c55e", "#15803d"),     # fill / border / text
    "med":       ("#fef3c7", "#eab308", "#b45309"),
    "bad":       ("#fee2e2", "#ef4444", "#991b1b"),
    "neutral":   ("#f1f5f9", "#cbd5e1", "#475569"),
    "uncovered": ("#fef2f2", "#ef4444", "#991b1b"),     # 0 cover = 警告
}


def _color_class(score: float | None, cnt: int, is_terminal: bool) -> str:
    if cnt == 0 and not is_terminal:
        return "uncovered"
    if score is None:
        return "neutral"
    if score >= 0.8:
        return "good"
    if score >= 0.5:
        return "med"
    return "bad"


def flow_to_dot(flow: FlowDiagram,
                cover_count: dict[str, int] | None = None,
                rubric_scores: dict[str, float | None] | None = None) -> str:
    cover_count = cover_count or {}
    rubric_scores = rubric_scores or {}

    lines = [
        "digraph G {",
        "  rankdir=LR;",
        "  bgcolor=\"transparent\";",
        ('  node [shape=box, style="rounded,filled", '
         'fontname="PingFang SC, Microsoft YaHei", fontsize=11, '
         'margin=\"0.15,0.08\"];'),
        '  edge [color="#94a3b8", arrowsize=0.6];',
    ]

    for n in flow.nodes:
        is_terminal = n.id in ("START", "END") or n.optional
        score = rubric_scores.get(n.rubric) if n.rubric else None
        cnt = cover_count.get(n.id, 0)
        cc = _color_class(score, cnt, is_terminal=(n.id in ("START", "END")))
        fill, border, txt = _COLORS[cc]

        # 多行 label:节点 label + 👥N + 分数
        label_parts = [n.label]
        if not is_terminal or n.rubric:
            label_parts.append(f"👥 {cnt}")
        if score is not None:
            label_parts.append(f"{score:.2f}")
        # graphviz \n 用 \\n 转义
        label = "\\n".join(label_parts).replace('"', '\\"')

        lines.append(
            f'  "{n.id}" [label="{label}", fillcolor="{fill}", '
            f'color="{border}", fontcolor="{txt}"];'
        )

    for src, dst in flow.edges:
        lines.append(f'  "{src}" -> "{dst}";')

    lines.append("}")
    return "\n".join(lines)
