"""报告渲染单测:单 case HTML + dashboard 都能生成且含关键内容。"""
from __future__ import annotations

import json
from pathlib import Path

from claw_eval.models.trace import (
    DimensionScores,
    GradingResult,
    RubricScore,
    Violation,
)
from claw_eval.report.builder import build_case_report, build_dashboard


def _write_synthetic_trace(path: Path) -> None:
    """造一份最小可读 trace 文件。"""
    events = [
        {"event": "dialogue_start", "trace_id": "t1", "task_id": "demo",
         "persona_id": "p1", "sut_model": "stub",
         "simulator_model": "stub", "ts": "2026-05-20T00:00:00"},
        {"event": "turn", "turn": 1, "role": "user",
         "text": "喂", "state": "接听", "is_probe": False, "probe_id": None},
        {"event": "turn", "turn": 2, "role": "assistant",
         "text": "你好我是站长",
         "state": None, "is_probe": False, "probe_id": None},
        {"event": "turn", "turn": 3, "role": "user",
         "text": "嗯", "state": None, "is_probe": False, "probe_id": None},
        {"event": "turn", "turn": 4, "role": "assistant",
         "text": "今天合同生效啦",
         "state": None, "is_probe": False, "probe_id": None},
        {"event": "dialogue_end", "turn_count": 4, "end_reason": "done"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _result(trace_path: str) -> GradingResult:
    return GradingResult(
        task_id="demo",
        persona_id="p1",
        dimension_scores=DimensionScores(
            completion=0.85, robustness=0.7, safety=1.0),
        task_score=0.82,
        passed=True,
        rubric_scores=[
            RubricScore(rubric_id="opening.greeting", dimension="completion",
                        method="keyword", weight=0.1, triggered=True,
                        score=1.0, reasoning="命中"),
            RubricScore(rubric_id="flow.step1", dimension="completion",
                        method="llm_judge", weight=0.2, triggered=True,
                        score=0.7, reasoning="部分到位",
                        evidence_turn=4),
            RubricScore(rubric_id="faq.exit", dimension="completion",
                        method="llm_judge", weight=0.05, triggered=False,
                        reasoning="未问退出"),
            RubricScore(rubric_id="safety.x", dimension="safety",
                        method="llm_judge", weight=1.0, triggered=True,
                        score=1.0, reasoning="无问题"),
        ],
        violations=[
            Violation(rubric_id="flow.step1", turn=4,
                      detail="只说了合同生效,没问能否配送",
                      evidence="今天合同生效啦"),
        ],
        trace_path=trace_path,
    )


def test_build_case_report(tmp_path: Path):
    trace = tmp_path / "demo.jsonl"
    _write_synthetic_trace(trace)
    res = _result(str(trace))
    out = build_case_report(res, tmp_path / "case.html")

    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # 关键内容都在
    assert "demo" in html and "p1" in html              # task & persona
    assert "0.8200" in html                              # task_score
    assert "通过" in html                                # pass badge
    assert "你好我是站长" in html                        # conversation
    assert "flow.step1" in html                          # rubric id
    assert "只说了合同生效" in html                      # violation detail
    assert "未问退出" in html                            # skipped rubric reasoning


def test_build_dashboard_multipage(tmp_path: Path):
    trace = tmp_path / "demo.jsonl"
    _write_synthetic_trace(trace)
    res = _result(str(trace))
    out = build_dashboard([res], tmp_path / "out")

    # index = 跨任务总览,只到任务粒度
    assert out.exists() and out.name == "index.html"
    index_html = out.read_text(encoding="utf-8")
    assert "跨任务总览" in index_html
    assert "demo" in index_html                       # 任务 id
    assert "task_demo.html" in index_html             # 链接到任务详情页

    # 任务详情页 = 该任务的 persona × rubric
    task_page = tmp_path / "out" / "task_demo.html"
    assert task_page.exists()
    tp_html = task_page.read_text(encoding="utf-8")
    assert "p1" in tp_html                            # persona
    assert "flow.step1" in tp_html                    # rubric(热力图列)
    assert "cases/" in tp_html                        # 单 case 链接
    assert "返回跨任务总览" in tp_html

    # 单 case HTML 也被同步写出
    cases = tmp_path / "out" / "cases"
    assert cases.exists()
    assert any(p.suffix == ".html" for p in cases.iterdir())


def test_build_dashboard_empty_results(tmp_path: Path):
    out = build_dashboard([], tmp_path / "out")
    html = out.read_text(encoding="utf-8")
    assert "还没有任何 result.json" in html
