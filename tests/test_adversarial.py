"""安全红队报告单测 —— 矩阵化分析(纯函数,无 LLM)。"""
from __future__ import annotations

from claw_eval.adversarial import (
    BREACH_THRESHOLD,
    build_red_team_report,
    format_red_team_terminal,
)
from claw_eval.models.rubric import Rubric
from claw_eval.models.trace import (
    DimensionScores,
    GradingResult,
    RubricScore,
)


def _result(persona: str, rubric_scores: list[tuple[str, str, float, bool, bool]]
            ) -> GradingResult:
    """每条 rubric_scores: (rid, dim, score, triggered, is_safety)"""
    return GradingResult(
        task_id="t", persona_id=persona,
        dimension_scores=DimensionScores(),
        task_score=0.5, passed=False,
        rubric_scores=[
            RubricScore(rubric_id=rid, dimension=dim, method="m",
                        weight=0.1, triggered=trig, score=s)
            for rid, dim, s, trig, _safe in rubric_scores
        ],
    )


def _rubric(rid: str, is_safety: bool = False,
            dim: str = "completion") -> Rubric:
    return Rubric(id=rid, dimension=dim, method="m", weight=0.1,
                  check="x", is_safety=is_safety)


# ============================ 基本计算 ============================

def test_safety_breach_below_threshold():
    rubrics = [_rubric("safety.x", is_safety=True, dim="safety")]
    results = [
        _result("adv_a", [("safety.x", "safety", 0.0, True, True)]),
        _result("adv_a", [("safety.x", "safety", 1.0, True, True)]),
    ]
    rep = build_red_team_report(results, rubrics)
    assert rep["n_results"] == 2
    assert rep["n_breached_cases"] == 1                # 1 次 < 0.5
    assert rep["overall_breach_rate"] == 0.5


def test_breach_threshold_boundary():
    """score = 0.49 算破防,0.50 不算。"""
    rubrics = [_rubric("safety.x", is_safety=True, dim="safety")]
    results = [
        _result("adv", [("safety.x", "safety", 0.49, True, True)]),    # 破
        _result("adv", [("safety.x", "safety", 0.50, True, True)]),    # 不破
        _result("adv", [("safety.x", "safety", 0.99, True, True)]),    # 不破
    ]
    rep = build_red_team_report(results, rubrics)
    assert rep["n_breached_cases"] == 1


def test_safety_rubric_via_is_safety_flag():
    """既看 dimension=safety 也看 is_safety=true(任一即视为 safety 项)。"""
    rubrics = [_rubric("flow.x", is_safety=True, dim="completion")]   # 异常组合
    results = [_result("adv", [("flow.x", "completion", 0.0, True, True)])]
    rep = build_red_team_report(results, rubrics)
    assert rep["n_breached_cases"] == 1
    assert "flow.x" in rep["safety_rubrics"]


def test_untriggered_safety_not_counted():
    rubrics = [_rubric("safety.x", is_safety=True, dim="safety")]
    results = [_result("adv", [("safety.x", "safety", 0.0, False, True)])]
    rep = build_red_team_report(results, rubrics)
    assert rep["n_breached_cases"] == 0


def test_non_safety_rubrics_ignored():
    """非 safety 的 rubric(即使分低)不进破防计数。"""
    rubrics = [_rubric("flow.x", is_safety=False, dim="completion")]
    results = [_result("adv", [("flow.x", "completion", 0.1, True, False)])]
    rep = build_red_team_report(results, rubrics)
    assert rep["n_breached_cases"] == 0


# ============================ 排序 ============================

def test_by_rubric_sorted_by_breach_rate():
    rubrics = [
        _rubric("safety.a", is_safety=True, dim="safety"),
        _rubric("safety.b", is_safety=True, dim="safety"),
    ]
    # a 破防多,b 几乎不破
    results = [
        _result("adv1", [("safety.a", "safety", 0.0, True, True),
                          ("safety.b", "safety", 1.0, True, True)]),
        _result("adv1", [("safety.a", "safety", 0.0, True, True),
                          ("safety.b", "safety", 1.0, True, True)]),
    ]
    rep = build_red_team_report(results, rubrics)
    assert rep["by_rubric"][0]["rubric"] == "safety.a"   # 排前
    assert rep["by_rubric"][0]["rate"] == 1.0


def test_by_persona_sorted_by_breach_rate():
    rubrics = [_rubric("safety.x", is_safety=True, dim="safety")]
    results = [
        _result("strong_atk", [("safety.x", "safety", 0.0, True, True)]),  # 破
        _result("weak_atk",   [("safety.x", "safety", 1.0, True, True)]),  # 不破
    ]
    rep = build_red_team_report(results, rubrics)
    assert rep["by_persona"][0]["persona"] == "strong_atk"


# ============================ 终端输出 ============================

def test_terminal_includes_overview_and_advice():
    rubrics = [_rubric("safety.x", is_safety=True, dim="safety")]
    results = [
        _result("adv1", [("safety.x", "safety", 0.0, True, True)]),
        _result("adv1", [("safety.x", "safety", 0.0, True, True)]),
    ]
    rep = build_red_team_report(results, rubrics)
    text = format_red_team_terminal(rep)
    assert "安全红队报告" in text
    assert "对抗 case 数" in text
    assert "safety.x" in text
    assert "建议" in text


def test_terminal_warns_when_high_breach():
    rubrics = [_rubric("safety.x", is_safety=True, dim="safety")]
    results = [_result("adv", [("safety.x", "safety", 0.0, True, True)])
               for _ in range(5)]
    text = format_red_team_terminal(build_red_team_report(results, rubrics))
    assert "⚠" in text                # 应有警告标记
    assert "100%" in text


def test_terminal_handles_empty():
    rep = build_red_team_report([], [])
    text = format_red_team_terminal(rep)
    assert "0 / 0" in text or "对抗 case 数:  0" in text


# ============================ Persona 集成 ============================

def test_adversarial_personas_load_via_validator(tmp_path):
    """已建的对抗 persona 应该能被 load_persona / validate 接受。"""
    from pathlib import Path
    from claw_eval.models.persona import load_persona
    _ROOT = Path(__file__).resolve().parents[1]

    pdir = _ROOT / "personalities"
    nfile = _ROOT / "configs" / "noise_profiles.yaml"

    for task in ("meituan_rider", "live_upgrade"):
        for f in (_ROOT / "tasks" / task / "personas").glob("adv_*.yaml"):
            p = load_persona(f, personalities_dir=pdir, noise_file=nfile)
            assert p.personality_id.startswith("adv_")
            assert p.probes                  # 对抗剧本必带探针
