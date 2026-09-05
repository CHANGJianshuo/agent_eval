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
from claw_eval.report import builder
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


def test_report_rebases_moved_nested_trace_and_finds_task(tmp_path: Path,
                                                          monkeypatch):
    root = tmp_path / "checkout"
    trace = root / "traces" / "run_1" / "demo.jsonl"
    trace.parent.mkdir(parents=True)
    _write_synthetic_trace(trace)
    task_dir = root / "tasks" / "demo"
    task_dir.mkdir(parents=True)

    monkeypatch.setattr(builder, "_PROJECT_ROOT", root)
    result = _result("/obsolete/location/traces/run_1/demo.jsonl")

    assert builder._infer_task_dir(result) == task_dir
    out = build_case_report(result, tmp_path / "rebased.html")
    assert "你好我是站长" in out.read_text(encoding="utf-8")


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


def test_dashboard_renders_regression_card(tmp_path: Path):
    """reports/regression_<task>.json 存在时,task_<task>.html 顶部含回归对比卡。"""
    trace = tmp_path / "demo.jsonl"
    _write_synthetic_trace(trace)
    res = _result(str(trace))
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # 写入合成 regression JSON
    (out_dir / "regression_demo.json").write_text(json.dumps({
        "comparable": True,
        "task_id": "demo",
        "old_label": "v1", "new_label": "v2-fixed",
        "old_total": 5, "new_total": 5,
        "old_pass_rate": 0.2, "new_pass_rate": 0.6,
        "old_score_avg": 0.40, "new_score_avg": 0.75,
        "by_dimension": [
            ["completion", 0.40, 0.78, 0.38],
            ["robustness", 0.55, 0.60, 0.05],
            ["safety", 1.0, 1.0, 0.0],
        ],
        "by_rubric": [
            {"rubric_id": "flow.step1", "dimension": "completion",
             "old_avg": 0.30, "new_avg": 0.85, "delta": 0.55,
             "significance": "improve", "old_n": 5, "new_n": 5},
            {"rubric_id": "constraint.length",
             "dimension": "robustness",
             "old_avg": 0.70, "new_avg": 0.50, "delta": -0.20,
             "significance": "regress", "old_n": 5, "new_n": 5},
            {"rubric_id": "new.rule", "dimension": "completion",
             "old_avg": None, "new_avg": 0.80, "delta": None,
             "significance": "added", "old_n": 0, "new_n": 5},
        ],
        "by_persona": [
            {"persona_id": "p1", "old_n": 5, "new_n": 5,
             "old_pass_rate": 0.2, "new_pass_rate": 0.8,
             "delta_pass_rate": 0.6,
             "old_completion": 0.4, "new_completion": 0.8}
        ],
        "n_improvements": 2, "n_regressions": 1,
        "threshold": 0.05,
    }, ensure_ascii=False), encoding="utf-8")

    build_dashboard([res], out_dir, task_names={"demo": "Demo"})

    html = (out_dir / "task_demo.html").read_text(encoding="utf-8")
    # 标题 + 元信息
    assert "📊 回归对比" in html
    assert "v1 → v2-fixed" in html
    assert "2 改进" in html and "1 退化" in html
    # 总览数字
    assert "0.400" in html and "0.750" in html               # task_score
    # 显著变化
    assert "flow.step1" in html                              # 改进
    assert "↑ 改进" in html
    assert "constraint.length" in html                       # 退化
    assert "↓ 退化" in html
    assert "new.rule" in html                                # 新增
    assert "+ 新增" in html


def test_dashboard_skips_regression_card_when_no_json(tmp_path: Path):
    """没有 regression_*.json 时,任务页不该出现回归对比卡。"""
    trace = tmp_path / "demo.jsonl"
    _write_synthetic_trace(trace)
    res = _result(str(trace))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    build_dashboard([res], out_dir, task_names={"demo": "Demo"})

    html = (out_dir / "task_demo.html").read_text(encoding="utf-8")
    assert "📊 回归对比" not in html


def test_dashboard_renders_safety_test_card(tmp_path: Path):
    """reports/safety_test_<task>.json 存在时,任务页含「🔴 安全红队」卡。"""
    trace = tmp_path / "demo.jsonl"
    _write_synthetic_trace(trace)
    res = _result(str(trace))
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    (out_dir / "safety_test_demo.json").write_text(json.dumps({
        "n_results": 6,
        "n_assessed_cases": 6,
        "n_unknown_cases": 0,
        "n_breached_cases": 4,
        "overall_breach_rate": 0.6667,
        "safety_rubrics": ["safety.x", "safety.y"],
        "by_rubric": [
            {"rubric": "safety.x", "n": 6, "breach": 5, "rate": 0.8333},
            {"rubric": "safety.y", "n": 6, "breach": 0, "rate": 0.0},
        ],
        "by_persona": [
            {"persona": "adv_inject", "n": 6, "breach": 4, "rate": 0.6667},
        ],
        "matrix": [],
    }, ensure_ascii=False), encoding="utf-8")

    build_dashboard([res], out_dir, task_names={"demo": "Demo"})

    html = (out_dir / "task_demo.html").read_text(encoding="utf-8")
    assert "🔴 安全红队报告" in html
    assert "safety.x" in html
    assert "83%" in html or "83%" in html.replace("%", "%")
    assert "⚠⚠⚠ 高危" in html                  # rate ≥ 0.5
    assert "adv_inject" in html
    assert "🔴 高威胁" in html                  # persona rate ≥ 0.5
    # 严重时显示加固建议
    assert "加固一句" in html


def test_dashboard_skips_safety_test_card_when_no_json(tmp_path: Path):
    trace = tmp_path / "demo.jsonl"
    _write_synthetic_trace(trace)
    res = _result(str(trace))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    build_dashboard([res], out_dir, task_names={"demo": "Demo"})

    html = (out_dir / "task_demo.html").read_text(encoding="utf-8")
    assert "🔴 安全红队报告" not in html
