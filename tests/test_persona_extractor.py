"""Persona 抽取器单测 —— 不真调 LLM,mock 输出测解析 / 校验 / 保存。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from claw_eval.models.persona import PersonaScript, load_persona
from claw_eval.models.task import TaskDefinition
from claw_eval.user_simulator.extractor import (
    build_prompt,
    extract_personas,
    list_personality_library,
    parse_personas_output,
    save_persona_script,
)

_ROOT = Path(__file__).resolve().parents[1]
_PERS_DIR = _ROOT / "personalities"


_VALID_YAML = """\
- id: cooperative_test
  personality: cooperative
  name: "测试合作型"
  initial_state: 接听
  states:
    接听: "接电话"
    确认: "确认完成"
  transitions:
    接听: 确认
    确认: END
  probes:
    - id: ask_x
      inject_at_turn: 2
      text: "请问退出怎么操作?"
      description: "触发 faq.exit"
  max_rounds: 5
- id: refuse_test
  personality: refuse
  name: "测试抵触型"
  initial_state: 接听
  states:
    接听: "..."
    拒绝: "..."
  transitions:
    接听: 拒绝
    拒绝: END
  max_rounds: 4
"""


# ----------------------------- 解析 -----------------------------

def test_parse_basic_list():
    scripts = parse_personas_output(
        _VALID_YAML, known_personalities={"cooperative", "refuse"})
    assert len(scripts) == 2
    assert scripts[0].id == "cooperative_test"
    assert scripts[0].personality == "cooperative"
    assert len(scripts[0].probes) == 1
    assert scripts[0].probes[0].inject_at_turn == 2


def test_parse_rejects_unknown_personality():
    bad = """- id: x
  personality: nonexistent_personality
  initial_state: a
  states: {a: "..."}
  transitions: {a: END}
"""
    with pytest.raises(ValueError, match="未知性格"):
        parse_personas_output(bad, known_personalities={"cooperative"})


def test_parse_with_markdown_codeblock():
    wrapped = f"```yaml\n{_VALID_YAML}```"
    scripts = parse_personas_output(
        wrapped, known_personalities={"cooperative", "refuse"})
    assert len(scripts) == 2


def test_parse_with_personas_wrapper():
    wrapped = f"personas:\n{_VALID_YAML}"
    scripts = parse_personas_output(
        wrapped, known_personalities={"cooperative", "refuse"})
    assert len(scripts) == 2


def test_parse_rejects_non_list():
    with pytest.raises(ValueError, match="persona 列表"):
        parse_personas_output("just a string", known_personalities=set())


def test_parse_propagates_field_errors():
    """缺 states 等必需字段 → 抛带 index 的清晰错误。"""
    bad = "- id: x\n  personality: cooperative\n"
    with pytest.raises(ValueError, match="第 1 个"):
        parse_personas_output(bad, known_personalities={"cooperative"})


# --------------------------- prompt 构造 ---------------------------

def test_build_prompt_lists_personalities():
    task = TaskDefinition(task_id="t", prompt="P", variables={"X": 1})
    personalities = list_personality_library(_PERS_DIR)
    system, user = build_prompt(task, personalities)
    # 性格库注入 system
    assert "cooperative" in system
    assert "refuse" in system
    # user 含任务 prompt 和变量
    assert "X = 1" in user
    assert "P" in user


# ------------------------- extract_personas 编排 -------------------------

def test_extract_personas_calls_llm_and_parses(monkeypatch):
    captured = {}

    def fake_chat(model, messages, temperature=0.7, **kwargs):
        captured["model"] = model
        captured["messages"] = messages
        return _VALID_YAML

    monkeypatch.setattr(
        "claw_eval.user_simulator.extractor.llm_client.chat", fake_chat)

    task = TaskDefinition(task_id="t", prompt="P", variables={})
    scripts = extract_personas(task, "fake-model", _PERS_DIR)

    assert captured["model"] == "fake-model"
    assert len(scripts) == 2


# --------------------------- 保存 + 重载 ---------------------------

def test_save_persona_round_trip(tmp_path: Path):
    scripts = parse_personas_output(
        _VALID_YAML, known_personalities={"cooperative", "refuse"})
    out = tmp_path / "personas_draft"
    out.mkdir()
    save_persona_script(scripts[0], out / "cooperative_test.yaml")

    # 重新 load_persona 应能合成完整 Persona(性格层 + 剧本层)
    persona = load_persona(
        out / "cooperative_test.yaml",
        personalities_dir=_PERS_DIR,
        noise_file=_ROOT / "configs" / "noise_profiles.yaml",
    )
    assert persona.id == "cooperative_test"
    assert persona.personality_id == "cooperative"
    assert persona.description       # 性格层合成进来了
    assert persona.states == scripts[0].states
    assert persona.noise_rate == 0.0


def test_save_persona_omits_default_noise(tmp_path: Path):
    """没配 noise 时,YAML 里不应出现 noise: 字段(干净)。"""
    scripts = parse_personas_output(
        _VALID_YAML, known_personalities={"cooperative", "refuse"})
    p = tmp_path / "x.yaml"
    save_persona_script(scripts[0], p)
    text = p.read_text(encoding="utf-8")
    assert "noise:" not in text
