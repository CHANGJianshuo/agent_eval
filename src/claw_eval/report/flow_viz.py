"""把 FlowDiagram + 评分数据 → ECharts graph 配置(节点按 pass 率着色)。"""
from __future__ import annotations

from typing import Any

from ..models.flow import FlowDiagram


def color_for(score: float | None) -> str:
    """节点颜色:
    None  → 灰(未触发 / 无关联 rubric)
    <0.5  → 红
    <0.8  → 黄
    ≥0.8  → 绿
    """
    if score is None:
        return "#9ca3af"
    if score < 0.5:
        return "#ef4444"
    if score < 0.8:
        return "#eab308"
    return "#22c55e"


def _auto_layout(flow: FlowDiagram) -> dict[str, tuple[float, float]]:
    """没手工坐标时:主流程在 y=0 横向排,optional 节点在 y=±130 上下两排
    分别递增 x,避免任何节点位置重叠。"""
    pos: dict[str, tuple[float, float]] = {}
    main_step = 180.0
    opt_step = 170.0
    main_x = 0.0
    opt_top_x = 90.0          # 起点稍偏,与主线 x=0 错开
    opt_bot_x = 90.0
    use_top = True
    for n in flow.nodes:
        if n.x is not None and n.y is not None:
            pos[n.id] = (n.x, n.y)
            continue
        if n.optional:
            if use_top:
                pos[n.id] = (opt_top_x, 140.0)
                opt_top_x += opt_step
            else:
                pos[n.id] = (opt_bot_x, -140.0)
                opt_bot_x += opt_step
            use_top = not use_top
        else:
            pos[n.id] = (main_x, 0.0)
            main_x += main_step
    return pos


def aggregate_rubric_scores(by_rubric: dict[str, dict]) -> dict[str, float | None]:
    """从 aggregate.AggregateSummary.by_rubric 提取 {rubric_id: avg_score}。"""
    return {rid: br.get("avg_score") for rid, br in by_rubric.items()}


def case_rubric_scores(rubric_scores_list) -> dict[str, float | None]:
    """从 GradingResult.rubric_scores 提取 {rubric_id: score | None(未触发)}。"""
    out: dict[str, float | None] = {}
    for rs in rubric_scores_list:
        out[rs.rubric_id] = rs.score if rs.triggered else None
    return out


def build_flow_option(flow: FlowDiagram,
                      rubric_scores: dict[str, float | None]) -> dict[str, Any]:
    """flow + rubric_id→score → ECharts option dict。"""
    pos = _auto_layout(flow)
    nodes_data = []
    for n in flow.nodes:
        x, y = pos[n.id]
        score = rubric_scores.get(n.rubric) if n.rubric else None
        color = color_for(score)
        # 节点上显示:label + 得分(若有)
        if n.rubric:
            tag = f"{score:.2f}" if score is not None else "未触发"
        else:
            tag = ""
        label_text = n.label + ("\n" + tag if tag else "")
        text_color = (
            "#fff" if score is not None and score >= 0.5
            else "#1f2329"
        )
        nodes_data.append({
            "name": n.id,
            "x": x, "y": y,
            "symbol": "roundRect",
            "symbolSize": [130, 52],
            "itemStyle": {"color": color, "borderColor": "#fff", "borderWidth": 1},
            "label": {
                "show": True,
                "position": "inside",
                "color": text_color,
                "fontSize": 11,
                "formatter": label_text,
                "lineHeight": 15,
            },
        })
    links = [{"source": s, "target": t} for s, t in flow.edges]
    return {
        "tooltip": {"show": False},
        "animation": False,
        "series": [{
            "type": "graph",
            "layout": "none",
            "roam": True,
            "edgeSymbol": ["none", "arrow"],
            "edgeSymbolSize": 7,
            "lineStyle": {"color": "#cbd5e1", "width": 1.5},
            "data": nodes_data,
            "links": links,
        }],
    }
