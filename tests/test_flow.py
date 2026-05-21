"""任务流程图单测 —— 加载 / 着色阈值 / ECharts option 结构。"""
from __future__ import annotations

from pathlib import Path

from claw_eval.models.flow import FlowDiagram, FlowNode, load_flow
from claw_eval.models.trace import RubricScore
from claw_eval.report.flow_viz import (
    aggregate_rubric_scores,
    build_flow_option,
    case_rubric_scores,
    color_for,
)

_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------- color_for -----------------------------

def test_color_thresholds():
    assert color_for(None) == "#9ca3af"          # 灰 = 未触发
    assert color_for(0.0) == "#ef4444"           # 红
    assert color_for(0.49) == "#ef4444"
    assert color_for(0.5) == "#eab308"           # 黄(包含)
    assert color_for(0.79) == "#eab308"
    assert color_for(0.8) == "#22c55e"           # 绿(包含)
    assert color_for(1.0) == "#22c55e"


# --------------------------- 加载真实 flow ---------------------------

def test_meituan_flow_loads():
    flow = load_flow(_ROOT / "tasks" / "meituan_rider" / "flow.yaml")
    assert flow is not None
    ids = [n.id for n in flow.nodes]
    assert "opening" in ids and "step1" in ids and "step4" in ids
    # 流程图节点都关联到现存 rubric
    for n in flow.nodes:
        if n.rubric:
            assert "." in n.rubric        # 命名格式 category.name


def test_live_upgrade_flow_loads():
    flow = load_flow(_ROOT / "tasks" / "live_upgrade" / "flow.yaml")
    assert flow is not None
    assert any(n.rubric == "behavior.busy_retain" for n in flow.nodes)
    assert any(n.rubric == "safety.no_discount_promise" for n in flow.nodes)


def test_load_flow_missing_returns_none():
    assert load_flow(Path("/non/existent/flow.yaml")) is None


# ------------------- build_flow_option(ECharts 结构) -------------------

def _mini_flow() -> FlowDiagram:
    return FlowDiagram(
        nodes=[
            FlowNode(id="opening", label="开场", rubric="opening.x"),
            FlowNode(id="step1", label="step1", rubric="flow.step1"),
            FlowNode(id="faq", label="FAQ", rubric="faq.x", optional=True),
            FlowNode(id="end", label="结束"),    # 无 rubric
        ],
        edges=[["opening", "step1"], ["step1", "end"], ["step1", "faq"]],
    )


def test_build_flow_option_structure():
    flow = _mini_flow()
    scores = {"opening.x": 1.0, "flow.step1": 0.4, "faq.x": None}
    opt = build_flow_option(flow, scores)
    assert "series" in opt
    series = opt["series"][0]
    assert series["type"] == "graph"
    nodes_data = series["data"]
    assert len(nodes_data) == 4
    # 颜色按 score 上色
    colors = {n["name"]: n["itemStyle"]["color"] for n in nodes_data}
    assert colors["opening"] == "#22c55e"      # 1.0 绿
    assert colors["step1"] == "#ef4444"        # 0.4 红
    assert colors["faq"] == "#9ca3af"          # None 灰
    assert colors["end"] == "#9ca3af"          # 无 rubric 灰
    # links 完整
    assert len(series["links"]) == 3


def test_build_flow_option_auto_layout_assigns_positions():
    flow = _mini_flow()
    opt = build_flow_option(flow, {})
    for n in opt["series"][0]["data"]:
        assert isinstance(n["x"], (int, float))
        assert isinstance(n["y"], (int, float))


# ------------------------ rubric_scores 提取 ------------------------

def test_aggregate_rubric_scores_extracts_avg():
    by_rubric = {
        "a.x": {"n": 3, "score_sum": 2.1, "avg_score": 0.7, "dimension": "completion"},
        "b.y": {"n": 1, "score_sum": 0.0, "avg_score": 0.0, "dimension": "safety"},
    }
    out = aggregate_rubric_scores(by_rubric)
    assert out == {"a.x": 0.7, "b.y": 0.0}


def test_case_rubric_scores_untriggered_returns_none():
    rss = [
        RubricScore(rubric_id="a.x", dimension="completion", method="keyword",
                    weight=0.1, triggered=True, score=0.9),
        RubricScore(rubric_id="b.y", dimension="completion", method="llm_judge",
                    weight=0.1, triggered=False, score=0.0),
    ]
    out = case_rubric_scores(rss)
    assert out["a.x"] == 0.9
    assert out["b.y"] is None
