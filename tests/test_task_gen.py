"""任务生成器单测 —— 解析 / 模板填空 / 占位符抽取(无 LLM)。"""
from __future__ import annotations

import pytest

from claw_eval.task_gen.flow_extractor import parse_flow_output
from claw_eval.task_gen.grader_generator import _camel_case, generate_grader
from claw_eval.task_gen.variables_extractor import (
    auto_detect_placeholders,
    parse_variables_output,
)
from claw_eval.user_simulator.extractor import (
    parse_personas_with_coverage,
)


# ============================ flow_extractor ============================

def test_parse_flow_basic():
    text = """
nodes:
  - {id: opening, label: 开场, rubric: opening.greeting}
  - {id: step1, label: 告知, rubric: flow.step1}
  - {id: end, label: 结束}
edges:
  - [opening, step1]
  - [step1, end]
"""
    f = parse_flow_output(text)
    assert len(f.nodes) == 3
    assert f.nodes[0].id == "opening"
    assert f.nodes[0].rubric == "opening.greeting"
    assert len(f.edges) == 2


def test_parse_flow_with_markdown_wrap():
    text = """```yaml
nodes:
  - {id: a, label: A}
edges: []
```"""
    f = parse_flow_output(text)
    assert len(f.nodes) == 1


def test_parse_flow_with_optional_branches():
    text = """
nodes:
  - {id: main, label: 主, rubric: flow.x}
  - {id: faq_x, label: FAQ, rubric: faq.x, optional: true}
edges:
  - [main, faq_x]
"""
    f = parse_flow_output(text)
    main = next(n for n in f.nodes if n.id == "main")
    faq = next(n for n in f.nodes if n.id == "faq_x")
    assert main.optional is False
    assert faq.optional is True


# ============================ variables_extractor ============================

def test_parse_variables_basic_json():
    out = parse_variables_output('{"X": 25, "Y": 3, "tol": 0.1}')
    assert out == {"X": 25, "Y": 3, "tol": 0.1}


def test_parse_variables_with_markdown_wrap():
    out = parse_variables_output('```json\n{"a": 1}\n```')
    assert out == {"a": 1}


def test_parse_variables_extracts_from_messy_response():
    """LLM 偶尔会在 JSON 外加文字,要能容错。"""
    out = parse_variables_output('Here are the vars: {"X": 5} hope this helps')
    assert out == {"X": 5}


def test_parse_variables_empty_returns_empty():
    assert parse_variables_output("") == {}


def test_auto_detect_placeholders():
    prompt = "你好 {rider_name},今天 {X} 单,完成 {Y} 天"
    s = auto_detect_placeholders(prompt)
    assert s == {"rider_name", "X", "Y"}


def test_auto_detect_no_placeholders():
    assert auto_detect_placeholders("纯文本没占位符") == set()


# ============================ grader_generator ============================

def test_camel_case():
    assert _camel_case("meituan_rider") == "MeituanRider"
    assert _camel_case("live_upgrade") == "LiveUpgrade"
    assert _camel_case("simple") == "Simple"


def test_generate_grader_contains_required_pieces():
    code = generate_grader("my_task")
    assert "class MyTaskGrader" in code
    assert "AbstractGrader" in code
    assert "def grade(" in code
    assert "_is_triggered" in code
    assert "compute_dimension_scores" in code


def test_generate_grader_is_valid_python():
    """生成的 grader.py 至少能编译通过(语法 OK)。"""
    code = generate_grader("xyz")
    compile(code, "<generated>", "exec")


# ============================ persona_extractor with coverage ============================

_PERSONA_YAML_WITH_COVERAGE = """
- id: cooperative_test
  personality: cooperative
  initial_state: 接听
  states:
    接听: 应答
    确认: 同意
  transitions:
    接听: 确认
    确认: END
  covers_flow_nodes: [opening, step1, step2]
  weight: 50
  max_rounds: 5
- id: refuse_test
  personality: refuse
  initial_state: 接听
  states:
    接听: 应答
    拒绝: 不
  transitions:
    接听: 拒绝
    拒绝: END
  covers_flow_nodes: [opening, hangup]
  weight: 20
  max_rounds: 4
"""


def test_parse_personas_with_coverage_returns_weights():
    pset = parse_personas_with_coverage(
        _PERSONA_YAML_WITH_COVERAGE,
        known_personalities={"cooperative", "refuse"},
        flow_nodes=["opening", "step1", "step2", "hangup", "missing_node"],
    )
    assert len(pset.scripts) == 2
    assert pset.weights == {"cooperative_test": 50, "refuse_test": 20}


def test_parse_personas_with_coverage_builds_inverse_index():
    pset = parse_personas_with_coverage(
        _PERSONA_YAML_WITH_COVERAGE,
        known_personalities={"cooperative", "refuse"},
        flow_nodes=["opening", "step1", "step2", "hangup", "missing_node"],
    )
    # opening 被两个 persona 覆盖
    assert set(pset.coverage["opening"]) == {"cooperative_test", "refuse_test"}
    # missing_node 没人覆盖
    assert pset.coverage["missing_node"] == []
    # step1 只 cooperative 覆盖
    assert pset.coverage["step1"] == ["cooperative_test"]


def test_parse_personas_with_coverage_strips_weight_from_script():
    """weight 字段不能进 PersonaScript model(它不在 schema 里)。"""
    pset = parse_personas_with_coverage(
        _PERSONA_YAML_WITH_COVERAGE,
        known_personalities={"cooperative", "refuse"},
        flow_nodes=["opening"],
    )
    # PersonaScript dump 出来不含 weight
    for s in pset.scripts:
        assert not hasattr(s, "weight")


def test_parse_personas_with_coverage_rejects_unknown_personality():
    bad = """
- id: x
  personality: nonexistent
  initial_state: a
  states: {a: ""}
  transitions: {a: END}
  weight: 10
"""
    with pytest.raises(ValueError, match="未知性格"):
        parse_personas_with_coverage(bad, {"cooperative"}, ["a"])
