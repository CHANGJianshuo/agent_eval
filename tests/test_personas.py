"""Persona 三层加载单测:性格 / 剧本 / 噪音 —— 不依赖 API。"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_eval.models.persona import (
    load_noise_profiles,
    load_persona,
    load_personality,
)
from claw_eval.user_simulator.state_machine import StateMachine

_ROOT = Path(__file__).resolve().parents[1]
_PERS = _ROOT / "personalities"
_NOISE = _ROOT / "configs" / "noise_profiles.yaml"

# (task, persona) 全集
_ALL = [
    ("meituan_rider", p) for p in
    ["cooperative", "refuse", "hesitant", "out_of_scope",
     "info_missing", "argumentative"]
] + [
    ("live_upgrade", p) for p in
    ["cooperative_owner", "invisible_channel", "driving"]
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


# ----------------------------- 噪音层 -----------------------------

def test_noise_profiles_load():
    profiles = load_noise_profiles(_NOISE)
    assert "clean" in profiles
    assert profiles["clean"].instruction == ""        # clean 档无指令
    assert profiles["heavy"].instruction != ""        # heavy 档有指令


# ------------------------- 三层合成 persona -------------------------

@pytest.mark.parametrize("task,persona", _ALL)
def test_persona_merges_three_layers(task: str, persona: str):
    p = _load(task, persona)
    # 剧本层
    assert p.states and p.initial_state and p.transitions
    # 性格层(合成进来了)
    assert p.description and p.speaking_style
    assert p.personality_id
    # 噪音层
    assert p.noise_id == "clean"
    assert p.noise_instruction == ""


@pytest.mark.parametrize("task,persona", _ALL)
def test_every_persona_state_machine_terminates(task: str, persona: str):
    sm = StateMachine(_load(task, persona))
    seen = [sm.current]
    for _ in range(30):                                # 防死循环
        if sm.advance():
            break
        seen.append(sm.current)
    assert sm.finished, f"{task}/{persona} 状态机未终止: {seen}"


def test_personality_is_reused_across_tasks():
    """同一性格被多个任务的剧本复用 —— 三层拆分的核心收益。"""
    mt = _load("meituan_rider", "cooperative")
    lv = _load("live_upgrade", "cooperative_owner")
    # 两个不同任务的剧本,引用同一个性格
    assert mt.personality_id == "cooperative"
    assert lv.personality_id == "cooperative"
    assert mt.description == lv.description            # 性格内容完全一致
    # 但剧本不同(状态机不一样)
    assert mt.states != lv.states
