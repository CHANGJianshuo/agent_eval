"""Persona 三层加载 + 噪音 rate 单测 —— 不依赖 API。"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_eval.models.persona import (
    NoiseKind,
    Persona,
    ProbeConfig,
    load_noise_kinds,
    load_persona,
    load_personality,
)
from claw_eval.user_simulator.simulator import UserSimulator
from claw_eval.user_simulator.state_machine import StateMachine

_ROOT = Path(__file__).resolve().parents[1]
_PERS = _ROOT / "personalities"
_NOISE = _ROOT / "configs" / "noise_profiles.yaml"

_ALL = [
    ("meituan_rider", p) for p in
    ["cooperative", "refuse", "hesitant", "out_of_scope",
     "info_missing", "argumentative"]
] + [
    ("live_upgrade", p) for p in
    ["cooperative_owner", "invisible_channel", "driving",
     "busy_owner", "discount_seeker", "non_owner"]
]


def _load(task: str, persona: str):
    return load_persona(
        _ROOT / "tasks" / task / "personas" / f"{persona}.yaml",
        personalities_dir=_PERS, noise_file=_NOISE)


# ----------------------------- 性格层 -----------------------------

def test_personality_files_load():
    for pid in ["cooperative", "refuse", "hesitant", "argumentative",
                "confused", "blunt", "hurried"]:
        p = load_personality(_PERS / f"{pid}.yaml")
        assert p.id == pid
        assert p.description and p.speaking_style


# --------------------------- 噪音种类库 ---------------------------

def test_noise_kinds_library():
    kinds = load_noise_kinds(_NOISE)
    # 至少有这 4 种
    for kid in ("filler", "asr_error", "broken", "interrupt"):
        assert kid in kinds
        assert kinds[kid].instruction      # 每种都有 instruction 文字


# ------------------------- 三层合成 persona -------------------------

@pytest.mark.parametrize("task,persona", _ALL)
def test_persona_merges_three_layers(task: str, persona: str):
    p = _load(task, persona)
    # 剧本层
    assert p.states and p.initial_state and p.transitions
    # 性格层(合成进来了)
    assert p.description and p.speaking_style and p.personality_id
    # 噪音层 —— 默认 rate=0, kinds=[](全干净)
    assert p.noise_rate == 0.0
    assert p.noise_kinds == []


@pytest.mark.parametrize("task,persona", _ALL)
def test_every_persona_state_machine_terminates(task: str, persona: str):
    sm = StateMachine(_load(task, persona))
    seen = [sm.current]
    for _ in range(30):
        if sm.advance():
            break
        seen.append(sm.current)
    assert sm.finished, f"{task}/{persona} 状态机未终止: {seen}"


def test_personality_is_reused_across_tasks():
    """同一性格被多任务剧本复用 —— 三层拆分的核心收益。"""
    mt = _load("meituan_rider", "cooperative")
    lv = _load("live_upgrade", "cooperative_owner")
    assert mt.personality_id == lv.personality_id == "cooperative"
    assert mt.description == lv.description
    assert mt.states != lv.states


# ---------------------- 噪音 rate(per-turn 掷骰)----------------------

def _make_persona(noise_rate: float, kinds: list[NoiseKind]) -> Persona:
    return Persona(
        id="t", name="t", personality_id="p",
        description="d", speaking_style="s",
        noise_rate=noise_rate, noise_kinds=kinds,
        states={"接听": "..."}, initial_state="接听",
        transitions={"接听": "END"}, probes=[], max_rounds=5,
    )


_DIRTY = NoiseKind(id="dirty", name="d", instruction="dirty-instruction-XYZ")


def test_noise_rate_zero_never_injects():
    p = _make_persona(0.0, [_DIRTY])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    for _ in range(50):
        assert sim._roll_noise() == ""


def test_noise_rate_one_always_injects():
    p = _make_persona(1.0, [_DIRTY])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    for _ in range(20):
        assert "dirty-instruction-XYZ" in sim._roll_noise()


def test_noise_rate_partial_only_some_turns_dirty():
    """rate=0.5 应大约一半轮命中,不是全脏也不是全干净。"""
    p = _make_persona(0.5, [_DIRTY])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    dirty = sum(1 for _ in range(200) if sim._roll_noise())
    assert 60 <= dirty <= 140, f"rate=0.5 命中数 {dirty}/200 偏离太大"


def test_noise_seed_reproducible():
    """同 seed → 同噪音命中序列(保证可复现)。"""
    p = _make_persona(0.5, [_DIRTY])
    sim1 = UserSimulator(model="stub", persona=p, seed=7)
    sim2 = UserSimulator(model="stub", persona=p, seed=7)
    pat1 = [bool(sim1._roll_noise()) for _ in range(40)]
    pat2 = [bool(sim2._roll_noise()) for _ in range(40)]
    assert pat1 == pat2


def test_noise_empty_kinds_means_clean():
    """有 rate 但 kinds 为空 → 无可注入,永远干净。"""
    p = _make_persona(1.0, [])
    sim = UserSimulator(model="stub", persona=p, seed=42)
    for _ in range(20):
        assert sim._roll_noise() == ""
