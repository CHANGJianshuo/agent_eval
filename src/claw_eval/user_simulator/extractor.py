"""剧本抽取器 —— 从任务 Prompt + Flow 图自动产剧本草稿。

v2 设计:
- 剧本 = 场景描述(scenario) + 探针(probes) + 覆盖节点(covers_flow_nodes)
- 与性格完全解耦,运行时独立组合
- 每个剧本覆盖 flow 图的一条逻辑路径
- 触发型节点(optional=true)必须靠探针保证触发
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..models.flow import FlowDiagram, FlowNode
from ..models.persona import PersonaScript, ProbeConfig
from ..runner import llm_client


_SYSTEM_PROMPT = """\
你是评测系统设计专家。任务:基于对话任务的 Prompt 和流程图(flow),设计一组**剧本**,
使每条逻辑分支都被至少一个剧本覆盖。

## 剧本是什么
剧本描述模拟用户在一通电话中**走哪条逻辑路径**。
- 剧本只管「做什么」(逻辑分支),不管「怎么说」(语气/态度)
- 语气/态度由独立的性格层控制,跑测试时和剧本正交组合
- 所以剧本里的 scenario 描述应该是**行为中立**的,不带态度词

## Flow 图(本任务的逻辑节点 + 连边)
{flow_block}

其中 `optional: true` 的节点是**触发型分支**——只有用户主动问到/触发时才走。
这类节点**必须靠探针(probe)保证触发**。

## 设计原则
1. **主流程必须有一个剧本走完全程**(所有 optional=false 的节点)
2. **每个触发型节点至少被一个剧本的探针覆盖**
3. 剧本之间应**互补**——每个剧本覆盖不同的分支组合
4. 探针文本要**自然**,像真人说的话,不能太生硬
5. `inject_at_turn` 要合理——主流程节点走到一定程度后再注入触发型问题
6. 数量建议 4-8 个剧本
7. 如果 flow 有条件分支(如「用户拒绝 → 安慰后挂断」),需要有剧本走这条路径

## 输出格式(YAML 列表)
```yaml
- id: <短名,小写下划线,描述这条路径>
  name: "<中文显示名>"
  scenario: |
    你是接到电话的用户。<描述该用户在这通电话中的行为轨迹>
    <只描述做什么,不描述态度/语气>
  covers_flow_nodes:
    - opening
    - step1
    - ...
  probes:
    - id: <探针 id>
      inject_at_turn: <整数,在第几个用户轮注入>
      text: "<强制注入的话术>"
      description: "<触发哪个 flow 节点>"
  max_rounds: <整数,6-12>
```

## ★ YAML 引号规则
任何字符串值若含 `{`、`:`、`#`、`[`、`]`、`?` 等特殊符号,**必须双引号包裹**。
scenario 字段建议用 `|` 块标量语法。

## few-shot 示例
假设 flow 有:opening → step1 → step2 → step3 → end,以及触发型 faq_exit 和 oos:

- id: happy_path
  name: "主流程完整路径"
  scenario: |
    你是接到电话的用户。配合对方走完所有流程步骤,
    在每个环节给出简短回应表示了解。对话自然结束后回复 [DONE]。
  covers_flow_nodes: [opening, step1, step2, step3, end]
  probes: []
  max_rounds: 8

- id: ask_exit_rule
  name: "中途问退出规则"
  scenario: |
    你是接到电话的用户。在对方介绍到中间时,你想了解退出相关的规则。
    问完后继续配合走完剩余流程。
  covers_flow_nodes: [opening, step1, step2, faq_exit, step3, end]
  probes:
    - id: trigger_faq_exit
      inject_at_turn: 2
      text: "对了我想问一下,万一之后不想做了,要怎么退出?"
      description: "触发 faq_exit 节点"
  max_rounds: 8

- id: refuse_midway
  name: "中途拒绝"
  scenario: |
    你是接到电话的用户。听完前面的介绍后,表示自己今天没办法参与。
    对方可能会挽留,你仍然维持自己的决定。
  covers_flow_nodes: [opening, step1, step2, comfort_end]
  probes: []
  max_rounds: 6

只输出 YAML 列表,不要其他文字。
"""


_USER_TEMPLATE = """\
请为以下任务设计剧本,确保覆盖所有 flow 节点。

## 业务变量真值
{facts}

## 任务 Prompt
{prompt}

输出 YAML 列表。
"""


def _format_flow_block(flow: FlowDiagram) -> str:
    lines = ["节点:"]
    for n in flow.nodes:
        opt = " (optional, 触发型)" if n.optional else ""
        lines.append(f"  - {n.id}: {n.label}{opt}")
    lines.append("连边:")
    for e in flow.edges:
        lines.append(f"  - {e[0]} → {e[1]}")
    return "\n".join(lines)


def build_prompt(task_prompt: str, flow: FlowDiagram,
                 variables: dict | None = None) -> tuple[str, str]:
    flow_block = _format_flow_block(flow)
    system = _SYSTEM_PROMPT.replace("{flow_block}", flow_block)
    facts = "\n".join(f"  {k} = {v}" for k, v in (variables or {}).items())
    user = (
        _USER_TEMPLATE
        .replace("{facts}", facts or "  (无)")
        .replace("{prompt}", task_prompt)
    )
    return system, user


def parse_scripts_output(text: str, flow: FlowDiagram) -> list[PersonaScript]:
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = yaml.safe_load(text)
    if isinstance(data, dict) and "scripts" in data:
        data = data["scripts"]
    if not isinstance(data, list):
        raise ValueError(
            f"LLM 返回不是列表(得到 {type(data).__name__})")

    all_node_ids = {n.id for n in flow.nodes}
    scripts: list[PersonaScript] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        item.pop("weight", None)
        item.setdefault("scenario", "")
        item.setdefault("covers_flow_nodes", [])
        try:
            script = PersonaScript.model_validate(item)
        except Exception as exc:
            raise ValueError(f"第 {i + 1} 个剧本格式不合法:{exc}") from exc
        if all_node_ids:
            bad_nodes = set(script.covers_flow_nodes) - all_node_ids
            if bad_nodes:
                raise ValueError(
                    f"剧本 '{script.id}' 引用了不存在的 flow 节点:{bad_nodes}")
        scripts.append(script)
    return scripts


class ExtractedScriptSet:
    """带覆盖率分析的生成结果。"""
    def __init__(self, scripts: list[PersonaScript], flow: FlowDiagram):
        self.scripts = scripts
        all_node_ids = {n.id for n in flow.nodes}
        covered = set()
        for s in scripts:
            covered.update(s.covers_flow_nodes)
        self.coverage = {
            nid: [s.id for s in scripts if nid in s.covers_flow_nodes]
            for nid in all_node_ids
        }
        self.uncovered = [nid for nid in all_node_ids if nid not in covered]


def extract_scripts(task_prompt: str, flow: FlowDiagram,
                    judge_model: str,
                    variables: dict | None = None,
                    reasoning_effort: str = "medium",
                    temperature: float = 0.0,
                    max_attempts: int = 3) -> ExtractedScriptSet:
    system, user = build_prompt(task_prompt, flow, variables)
    last_err: Exception | None = None
    for _attempt in range(max_attempts):
        response = llm_client.chat(
            judge_model,
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            max_tokens=12000,
        )
        try:
            scripts = parse_scripts_output(response, flow)
            return ExtractedScriptSet(scripts, flow)
        except (ValueError, yaml.YAMLError) as exc:
            last_err = exc
    raise RuntimeError(
        f"剧本抽取失败（重试 {max_attempts} 次）: {last_err}")


def save_script(script: PersonaScript, path: str | Path) -> None:
    data = script.model_dump(exclude_none=True)
    for key in ("noise", "personality", "states", "initial_state", "transitions"):
        v = data.get(key)
        if not v or v == "" or v == {} or (isinstance(v, dict) and v == {"rate": 0.0, "kinds": []}):
            data.pop(key, None)
    if not data.get("probes"):
        data.pop("probes", None)
    Path(path).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8")


# ============== 向后兼容:老版本 API ==============

def list_personality_library(personalities_dir: str | Path):
    """读性格库(仅供向后兼容的老代码调用)。"""
    from ..models.persona import Personality, load_personality
    pdir = Path(personalities_dir)
    out = []
    for f in sorted(pdir.glob("*.yaml")):
        try:
            out.append(load_personality(f))
        except Exception:
            pass
    return out


# 保留旧名称供 cli.py import 不报错
save_persona_script = save_script


def extract_personas_with_coverage(task, judge_model, personalities_dir,
                                   flow_nodes, **kwargs):
    """向后兼容包装 —— 内部调 extract_scripts。"""
    from ..models.flow import FlowDiagram, FlowNode
    flow = FlowDiagram(
        nodes=[FlowNode(id=nid, label=lbl) for nid, lbl in flow_nodes],
        edges=[],
    )
    result = extract_scripts(
        task.prompt, flow, judge_model,
        variables=task.variables,
        reasoning_effort=kwargs.get("reasoning_effort", "medium"),
        temperature=kwargs.get("temperature", 0.0),
        max_attempts=kwargs.get("max_attempts", 3),
    )

    class _Compat:
        def __init__(self, r):
            self.scripts = r.scripts
            self.weights = {s.id: 10 for s in r.scripts}
            self.coverage = r.coverage
    return _Compat(result)


def extract_personas(task, judge_model, personalities_dir, **kwargs):
    """向后兼容。"""
    return extract_personas_with_coverage(
        task, judge_model, personalities_dir,
        flow_nodes=[], **kwargs).scripts


def parse_personas_with_coverage(text, known_personalities, flow_nodes):
    """向后兼容 v1 解析(test_task_gen.py 用到)。"""
    flow = FlowDiagram(
        nodes=[FlowNode(id=nid, label=nid) for nid in flow_nodes],
        edges=[],
    )
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = yaml.safe_load(text)
    if isinstance(data, dict) and "personas" in data:
        data = data["personas"]
    if not isinstance(data, list):
        raise ValueError(f"LLM 返回不是 persona 列表(得到 {type(data).__name__})")

    scripts = []
    weights = {}
    coverage_inv = {n: [] for n in flow_nodes}

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        w = int(item.pop("weight", 10))
        personality = item.get("personality", "")
        if personality and personality not in known_personalities:
            raise ValueError(
                f"persona '{item.get('id', i)}' 引用了未知性格 '{personality}'")
        try:
            script = PersonaScript.model_validate(item)
        except Exception as exc:
            raise ValueError(f"第 {i + 1} 个 persona 格式不合法:{exc}") from exc
        scripts.append(script)
        weights[script.id] = max(1, w)
        for nid in script.covers_flow_nodes:
            if nid in coverage_inv:
                coverage_inv[nid].append(script.id)

    class _Result:
        pass
    r = _Result()
    r.scripts = scripts
    r.weights = weights
    r.coverage = coverage_inv
    return r


def parse_personas_output(text, known_personalities):
    """向后兼容 v1 解析。"""
    flow = FlowDiagram(nodes=[], edges=[])
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = yaml.safe_load(text)
    if isinstance(data, dict) and "personas" in data:
        data = data["personas"]
    if not isinstance(data, list):
        raise ValueError(
            f"LLM 返回不是 persona 列表(得到 {type(data).__name__})")
    scripts = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        item.pop("weight", None)
        personality = item.get("personality", "")
        if personality and personality not in known_personalities:
            raise ValueError(
                f"persona '{item.get('id', i)}' 引用了未知性格 '{personality}'"
                f"(性格库有:{sorted(known_personalities)})")
        try:
            script = PersonaScript.model_validate(item)
        except Exception as exc:
            raise ValueError(f"第 {i + 1} 个 persona 格式不合法:{exc}") from exc
        scripts.append(script)
    return scripts
