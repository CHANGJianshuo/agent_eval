"""概率转移状态机单测 —— 分布趋近 / seed 可复现 / 向后兼容 / validator 兼容。"""
from __future__ import annotations

import random
from collections import Counter

from claw_eval.models.persona import Persona, PersonaScript
from claw_eval.user_simulator.state_machine import END, StateMachine
from claw_eval.validator import check_state_termination


def _persona(transitions: dict[str, object], initial: str = "a",
             states: dict[str, str] | None = None) -> Persona:
    """构造一个最小可用 Persona(只关心 transitions 行为)。"""
    if states is None:
        states = {k: "" for k in transitions}
    return Persona(
        id="t", name="t", personality_id="x",
        description="d", speaking_style="s",
        states=states,
        initial_state=initial,
        transitions=transitions,
    )


# =========================== 向后兼容(确定性)===========================

def test_deterministic_transitions_still_work():
    """老格式 {state: str_target} 仍正常推进。"""
    p = _persona({"a": "b", "b": "END"}, initial="a")
    sm = StateMachine(p, rng=random.Random(0))
    assert sm.current == "a"
    finished = sm.advance()
    assert sm.current == "b"
    assert finished is False
    finished = sm.advance()
    assert sm.current == END
    assert finished is True


def test_missing_transition_goes_to_end():
    """state 在 states 但不在 transitions → 默认走 END(老行为)。"""
    p = _persona({}, initial="a", states={"a": "应答"})
    sm = StateMachine(p)
    assert sm.advance() is True


# =========================== 概率转移 ===========================

def test_probabilistic_transition_picks_from_weights():
    """概率 transition 用 RNG 抽样,无错。"""
    p = _persona({
        "a": {"b": 0.5, "c": 0.5},
        "b": "END",
        "c": "END",
    }, initial="a", states={"a": "", "b": "", "c": ""})
    sm = StateMachine(p, rng=random.Random(42))
    sm.advance()
    assert sm.current in ("b", "c")


def test_probabilistic_distribution_converges():
    """大量采样,实际分布应趋近权重。"""
    rng = random.Random(0)
    counts = Counter()
    for _ in range(2000):
        p = _persona({"a": {"x": 0.7, "y": 0.3}, "x": "END", "y": "END"},
                      initial="a", states={"a": "", "x": "", "y": ""})
        sm = StateMachine(p, rng=rng)
        sm.advance()
        counts[sm.current] += 1
    # 70/30 ± 3 个百分点宽容
    assert 0.66 < counts["x"] / 2000 < 0.74
    assert 0.26 < counts["y"] / 2000 < 0.34


def test_same_seed_same_path():
    """同 seed → 同路径(可复现)。"""
    spec = {"a": {"x": 0.5, "y": 0.5}, "x": "END", "y": "END"}

    def run(seed):
        p = _persona(spec, initial="a", states={"a": "", "x": "", "y": ""})
        sm = StateMachine(p, rng=random.Random(seed))
        sm.advance()
        return sm.current

    assert run(42) == run(42)
    assert run(42) == run(42)
    # 不同 seed 不一定相同(可能恰巧相同,但至少不崩)


def test_empty_dict_falls_through_to_end():
    """空 dict 视为终止。"""
    p = _persona({"a": {}}, initial="a", states={"a": ""})
    sm = StateMachine(p, rng=random.Random(0))
    assert sm.advance() is True


# =========================== Validator(图论)===========================

def test_validator_accepts_probabilistic_terminating():
    """所有分支都能到 END 的概率状态机应通过。"""
    p = _persona({
        "a": {"b": 0.6, "c": 0.4},
        "b": "END",
        "c": "END",
    }, initial="a", states={"a": "", "b": "", "c": ""})
    issues = check_state_termination([p])
    assert issues == []


def test_validator_catches_unreachable_end_in_one_branch():
    """概率某分支永远到不了 END → 应被识别。"""
    p = _persona({
        "a": {"b": 0.5, "c": 0.5},
        "b": "END",
        # c 无 transitions,默认 END → 也合法
    }, initial="a", states={"a": "", "b": "", "c": ""})
    # 这个情况下 c 默认走 END,合法,无 error
    assert check_state_termination([p]) == []

    # 制造一个真正的环:c 走回 c
    p_bad = _persona({
        "a": {"b": 0.5, "c": 0.5},
        "b": "END",
        "c": "c",                        # 自环
    }, initial="a", states={"a": "", "b": "", "c": ""})
    issues = check_state_termination([p_bad])
    assert len(issues) == 1
    assert "c" in issues[0].message


def test_validator_accepts_existing_deterministic_personas():
    """既有确定性 persona 在新 validator 下仍通过。"""
    p = _persona({
        "接听": "听介绍",
        "听介绍": "确认",
        "确认": "END",
    }, initial="接听",
    states={"接听": "", "听介绍": "", "确认": ""})
    assert check_state_termination([p]) == []
