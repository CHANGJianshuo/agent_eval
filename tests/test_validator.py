"""一致性检查单测 —— 单项 check 行为 + 端到端对真实任务校验。"""
from __future__ import annotations

from pathlib import Path

from claw_eval.models.persona import Persona, ProbeConfig
from claw_eval.models.rubric import Rubric, TriggerSpec
from claw_eval.validator import (
    check_rubric_naming,
    check_safety_flags,
    check_sampling,
    check_state_termination,
    check_trigger_reachable,
    check_weights,
    validate_task,
)

_ROOT = Path(__file__).resolve().parents[1]


# ---- helpers 构造 synthetic 对象 ----

def _r(rid: str, dim: str = "completion", weight: float = 0.1,
       is_safety: bool = False, trigger: TriggerSpec | None = None,
       method: str = "keyword") -> Rubric:
    return Rubric(id=rid, dimension=dim, method=method, weight=weight,
                  check="", is_safety=is_safety, trigger=trigger)


def _p(pid: str, states: dict | None = None,
       transitions: dict | None = None,
       probes: list | None = None) -> Persona:
    return Persona(
        id=pid, name=pid, personality_id="x",
        description="d", speaking_style="s",
        states=states or {"接听": "..."},
        initial_state="接听",
        transitions=transitions or {"接听": "END"},
        probes=probes or [],
    )


# ---- 单项 check 测试 ----

def test_naming_warns_on_bad_id():
    issues = check_rubric_naming([_r("BadID-no-dot")])
    assert any(i.code == "naming" for i in issues)


def test_naming_info_on_unknown_category():
    issues = check_rubric_naming([_r("weird.something")])
    assert any(i.code == "naming" and i.level == "info" for i in issues)


def test_naming_ok_on_standard():
    issues = check_rubric_naming([_r("flow.step1")])
    assert not issues


def test_weights_warn_on_zero_non_safety():
    issues = check_weights([_r("flow.x", weight=0.0)])
    assert any(i.code == "weight_zero" for i in issues)


def test_weights_ignore_safety():
    issues = check_weights([_r("safety.x", dim="safety", weight=1.0, is_safety=True)])
    assert not issues


def test_safety_flag_error_on_missing():
    issues = check_safety_flags([_r("safety.x", dim="safety", is_safety=False)])
    assert any(i.code == "safety_flag_missing" and i.level == "error" for i in issues)


def test_safety_flag_warn_on_misplaced():
    issues = check_safety_flags([_r("flow.x", dim="completion", is_safety=True)])
    assert any(i.code == "safety_flag_misplaced" for i in issues)


def test_trigger_reachable_via_probe():
    persona = _p("p", probes=[ProbeConfig(id="pa", inject_at_turn=1, text="x")])
    rubric = _r("a.x", trigger=TriggerSpec(type="probe", probe_id="pa"))
    assert not check_trigger_reachable([rubric], [persona])    # reachable


def test_trigger_dead_when_no_probe_matches():
    persona = _p("p", probes=[ProbeConfig(id="pa", inject_at_turn=1, text="x")])
    rubric = _r("a.x", trigger=TriggerSpec(type="probe", probe_id="missing"))
    issues = check_trigger_reachable([rubric], [persona])
    assert any(i.code == "dead_rubric" for i in issues)


def test_trigger_reachable_via_user_keyword():
    persona = _p("p", probes=[ProbeConfig(id="pa", inject_at_turn=1, text="我要退出")])
    rubric = _r("a.x", trigger=TriggerSpec(type="user_keyword", keywords=["退出"]))
    assert not check_trigger_reachable([rubric], [persona])


def test_trigger_reachable_via_user_state():
    persona = _p("p", transitions={"接听": "坚持拒绝", "坚持拒绝": "END"})
    rubric = _r("a.x", trigger=TriggerSpec(type="user_state", state="坚持拒绝"))
    assert not check_trigger_reachable([rubric], [persona])


def test_state_termination_ok():
    persona = _p("p", transitions={"接听": "END"})
    assert not check_state_termination([persona])


def test_state_termination_detects_cycle():
    # A → B → A 的环,永远到不了 END
    persona = _p("p", states={"A": "...", "B": "..."},
                 transitions={"A": "B", "B": "A"})
    # 注:Persona 的 initial_state 仍是 "接听",改一下
    persona.initial_state = "A"
    issues = check_state_termination([persona], max_steps=10)
    assert any(i.code == "no_terminate" for i in issues)


def test_sampling_orphan_error():
    p = _p("real")
    issues = check_sampling({"real": 10, "ghost": 5}, [p])
    assert any(i.code == "sampling_orphan" and i.level == "error" for i in issues)


def test_sampling_missing_personas_info():
    p1 = _p("a")
    p2 = _p("b")
    issues = check_sampling({"a": 10}, [p1, p2])    # b 没配置权重
    assert any(i.code == "sampling_missing" for i in issues)


# ---- 端到端:实际任务应通过 ----

def test_fixture_task_validates_clean():
    fixture = Path(__file__).parent / "fixtures" / "meituan_rider_task"
    rep = validate_task(
        fixture,
        personalities_dir=_ROOT / "personalities",
        noise_file=_ROOT / "configs" / "noise_profiles.yaml",
        sampling_file=fixture / "sampling.yaml",
    )
    assert rep.ok, f"fixture 校验有错误: {rep.errors}"


def test_live_upgrade_task_validates_clean():
    rep = validate_task(
        _ROOT / "tasks" / "live_upgrade",
        personalities_dir=_ROOT / "personalities",
        noise_file=_ROOT / "configs" / "noise_profiles.yaml",
        sampling_file=_ROOT / "tasks" / "live_upgrade" / "sampling.yaml",
    )
    assert rep.ok, f"直播校验有错误: {rep.errors}"


def test_live_upgrade_no_more_dead_rubrics():
    """直播任务在补了 busy_owner/discount_seeker 后,不应再有死规则警告。"""
    rep = validate_task(
        _ROOT / "tasks" / "live_upgrade",
        personalities_dir=_ROOT / "personalities",
        noise_file=_ROOT / "configs" / "noise_profiles.yaml",
    )
    dead = [i for i in rep.warnings if i.code == "dead_rubric"]
    assert not dead, f"仍有死规则: {[i.message for i in dead]}"
