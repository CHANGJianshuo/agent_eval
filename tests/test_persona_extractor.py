"""剧本抽取器单测 —— 不真调 LLM,mock 输出测解析 / 校验 / 保存。

v2 测试:scenario 模式(无状态机,无性格绑定)
v1 兼容测试:老格式(states + personality)仍可解析
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from claw_eval.models.flow import FlowDiagram, FlowNode
from claw_eval.models.persona import PersonaScript, load_persona
from claw_eval.models.task import TaskDefinition
from claw_eval.user_simulator.extractor import (
    build_prompt,
    extract_scripts,
    parse_scripts_output,
    save_script,
    # 向后兼容
    extract_personas,
    save_persona_script,
)

_ROOT = Path(__file__).resolve().parents[1]
_PERS_DIR = _ROOT / "personalities"


_SAMPLE_FLOW = FlowDiagram(nodes=[
    FlowNode(id="opening", label="开场"),
    FlowNode(id="step1", label="告知合同"),
    FlowNode(id="step2", label="说明要求"),
    FlowNode(id="faq_exit", label="答退出规则", optional=True),
    FlowNode(id="end", label="结束"),
], edges=[
    ["opening", "step1"], ["step1", "step2"],
    ["step2", "end"], ["step2", "faq_exit"], ["faq_exit", "end"],
])


_VALID_V2_YAML = """\
- id: happy_path
  name: "主流程完整路径"
  scenario: |
    你是接到电话的用户。配合对方走完所有流程步骤。
  covers_flow_nodes: [opening, step1, step2, end]
  max_rounds: 8

- id: ask_exit
  name: "中途问退出规则"
  scenario: |
    你是接到电话的用户。中途想了解退出规则。
  covers_flow_nodes: [opening, step1, step2, faq_exit, end]
  probes:
    - id: trigger_exit
      inject_at_turn: 2
      text: "我想问一下,要怎么退出?"
      description: "触发 faq_exit"
  max_rounds: 8
"""


_VALID_V1_YAML = """\
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
  covers_flow_nodes: [opening, step1]
"""


# ========================= v2 解析 =========================

def test_parse_v2_scripts():
    scripts = parse_scripts_output(_VALID_V2_YAML, _SAMPLE_FLOW)
    assert len(scripts) == 2
    assert scripts[0].id == "happy_path"
    assert scripts[0].scenario.startswith("你是接到电话的用户")
    assert scripts[0].personality == ""
    assert "opening" in scripts[0].covers_flow_nodes
    assert len(scripts[1].probes) == 1


def test_parse_v2_with_codeblock():
    wrapped = f"```yaml\n{_VALID_V2_YAML}```"
    scripts = parse_scripts_output(wrapped, _SAMPLE_FLOW)
    assert len(scripts) == 2


def test_parse_v2_rejects_bad_flow_node():
    bad = """\
- id: x
  scenario: "test"
  covers_flow_nodes: [nonexistent_node]
  max_rounds: 6
"""
    with pytest.raises(ValueError, match="不存在的 flow 节点"):
        parse_scripts_output(bad, _SAMPLE_FLOW)


def test_parse_v2_rejects_non_list():
    with pytest.raises(ValueError, match="列表"):
        parse_scripts_output("just a string", _SAMPLE_FLOW)


def test_coverage_analysis():
    from claw_eval.user_simulator.extractor import ExtractedScriptSet
    scripts = parse_scripts_output(_VALID_V2_YAML, _SAMPLE_FLOW)
    sset = ExtractedScriptSet(scripts, _SAMPLE_FLOW)
    assert sset.uncovered == []
    assert "opening" in sset.coverage
    assert len(sset.coverage["faq_exit"]) == 1


def test_coverage_detects_uncovered():
    from claw_eval.user_simulator.extractor import ExtractedScriptSet
    partial = """\
- id: partial
  scenario: "only covers opening"
  covers_flow_nodes: [opening]
  max_rounds: 6
"""
    scripts = parse_scripts_output(partial, _SAMPLE_FLOW)
    sset = ExtractedScriptSet(scripts, _SAMPLE_FLOW)
    assert len(sset.uncovered) > 0
    assert "step1" in sset.uncovered


# ========================= v2 prompt 构造 =========================

def test_build_prompt_includes_flow():
    system, user = build_prompt("任务描述", _SAMPLE_FLOW, {"X": 1})
    assert "opening" in system
    assert "faq_exit" in system
    assert "触发型" in system
    assert "X = 1" in user


# ========================= v2 extract 编排 =========================

def test_extract_scripts_calls_llm(monkeypatch):
    captured = {}

    def fake_chat(model, messages, temperature=0.7, **kwargs):
        captured["model"] = model
        return _VALID_V2_YAML

    monkeypatch.setattr(
        "claw_eval.user_simulator.extractor.llm_client.chat", fake_chat)

    sset = extract_scripts("任务描述", _SAMPLE_FLOW, "fake-model")
    assert captured["model"] == "fake-model"
    assert len(sset.scripts) == 2
    assert sset.uncovered == []


# ========================= v2 保存 =========================

def test_save_v2_round_trip(tmp_path: Path):
    scripts = parse_scripts_output(_VALID_V2_YAML, _SAMPLE_FLOW)
    out = tmp_path / "test.yaml"
    save_script(scripts[1], out)
    text = out.read_text(encoding="utf-8")
    assert "scenario" in text
    assert "personality" not in text
    assert "states" not in text
    data = yaml.safe_load(text)
    assert data["id"] == "ask_exit"
    assert len(data["probes"]) == 1


def test_save_v2_omits_empty_fields(tmp_path: Path):
    scripts = parse_scripts_output(_VALID_V2_YAML, _SAMPLE_FLOW)
    out = tmp_path / "test.yaml"
    save_script(scripts[0], out)
    text = out.read_text(encoding="utf-8")
    assert "noise:" not in text
    assert "probes:" not in text
    assert "states:" not in text


# ========================= v1 兼容 =========================

def test_v1_format_still_parseable():
    """老格式(states + personality)仍可通过 PersonaScript 加载。"""
    scripts = parse_scripts_output(_VALID_V1_YAML, _SAMPLE_FLOW)
    assert len(scripts) == 1
    s = scripts[0]
    assert s.personality == "cooperative"
    assert s.states == {"接听": "接电话", "确认": "确认完成"}
    assert s.scenario == ""


def test_v1_load_persona_still_works(tmp_path: Path):
    """老格式剧本仍可通过 load_persona 合成运行时 Persona。"""
    scripts = parse_scripts_output(_VALID_V1_YAML, _SAMPLE_FLOW)
    out = tmp_path / "cooperative_test.yaml"
    # 写出时保留 v1 字段
    data = scripts[0].model_dump(exclude_none=True)
    Path(out).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    persona = load_persona(
        out,
        personalities_dir=_PERS_DIR,
        noise_file=_ROOT / "configs" / "noise_profiles.yaml",
    )
    assert persona.id == "cooperative_test"
    assert persona.personality_id == "cooperative"
    assert persona.description
    assert persona.states == {"接听": "接电话", "确认": "确认完成"}


def test_backward_compat_extract_personas(monkeypatch):
    """extract_personas 旧接口仍可调用。"""
    def fake_chat(model, messages, temperature=0.7, **kwargs):
        return _VALID_V2_YAML

    monkeypatch.setattr(
        "claw_eval.user_simulator.extractor.llm_client.chat", fake_chat)

    task = TaskDefinition(task_id="t", prompt="P", variables={})
    scripts = extract_personas(task, "fake-model", _PERS_DIR)
    assert len(scripts) == 2
