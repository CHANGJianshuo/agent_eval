"""Persona 抽取器 —— 从任务 Prompt 自动产 persona 剧本草稿。

输入:TaskDefinition + 性格库
输出:list[PersonaScript],每个剧本引用一个已有性格 + 自己的状态/探针
保存:每个 persona 一个文件,写到 tasks/<task>/personas_draft/<id>.yaml
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..models.persona import Personality, PersonaScript, load_personality
from ..models.task import TaskDefinition
from ..runner import llm_client


_SYSTEM_PROMPT = """\
你是评测系统设计专家。任务:基于对话任务的 Prompt,推荐应该测哪几种用户(persona),输出 YAML 列表。

## 每个 persona 由 3 层构成
- 性格(personality):任务无关,从下方性格库中**选一个 id**(不要造新性格)
- 剧本(states/transitions/probes):**任务专属**,你设计
- 噪音(noise):默认不配(rate=0),专项需要再加

## 性格库(必须从这里选 personality 字段)
{personalities}

## 设计原则(很重要)
1. 主流程要覆盖 —— 至少 1 个合作型 persona 配合任务,完整走完主流程
2. 边界要覆盖 —— 任务里写的「拒绝/越权/特殊场景」每个都该有 persona 触发
3. **每条 trigger 型 rubric 应该有 persona 能触发它**(probes 文本里含 trigger 关键词)
4. persona 之间应**互补**,不重复测同一种情况
5. 数量建议 5-8 个,不要太多

## YAML 格式(列表)
- id: <短名,小写下划线>
  personality: <性格库 id>
  name: "<中文显示名>"
  initial_state: <某个 state 名>
  states:
    状态1: "你在该状态下该做什么(给模拟器看的指令)"
    状态2: "..."
  transitions:
    状态1: 状态2
    状态2: END                    # END 表示对话结束
  probes:                         # 可选;触发型 rubric 必须靠探针注入关键话术
    - id: <探针 id>
      inject_at_turn: <整数,在第几个用户轮注入>
      text: "强制注入的话术,可含触发 rubric 用的关键词"
      description: "本探针用来触发哪条 rubric"
  max_rounds: <整数,3-12>

## ★ YAML 引号规则(必须遵守)
任何字符串值若含 `{`、`:`、`#`、`[`、`]`、`{`、`}`、`?` 等特殊符号,**必须双引号包裹整个值**:
  正确:  text: "请问退出怎么操作?"
  错误:  text: 请问退出怎么操作?

## few-shot 例子(美团飞毛腿外呼任务)
- id: cooperative
  personality: cooperative
  name: "合作型骑手"
  initial_state: 接听
  states:
    接听: "你刚接起电话,礼貌应答一声。"
    听介绍: "你听站长讲飞毛腿合同的事,简单回应表示明白。"
    确认: "你愿意今天开始跑单,顺便确认细节。"
  transitions:
    接听: 听介绍
    听介绍: 确认
    确认: END
  probes:
    - id: ask_exit
      inject_at_turn: 2
      text: "我大概明白了。那要是哪天我想退出飞毛腿,该怎么弄?"
      description: "触发 faq.exit_rule"
  max_rounds: 6

- id: refuse
  personality: refuse
  name: "拒绝型骑手"
  initial_state: 接听
  states:
    接听: "你刚接起电话,简单应一声。"
    犹豫: "你今天不想跑单,但还没说死。"
    拒绝: "你明确说今天不跑了。"
    坚持拒绝: "不管站长怎么劝,你都坚持。"
  transitions:
    接听: 犹豫
    犹豫: 拒绝
    拒绝: 坚持拒绝
    坚持拒绝: END
  probes:
    - id: out_of_scope
      inject_at_turn: 3
      text: "对了,飞毛腿的佣金比例到底多少?能不能帮我换个站点?"
      description: "触发 behavior.out_of_scope"
  max_rounds: 7

只输出 YAML 列表,不要其他文字。
"""


_USER_TEMPLATE = """\
请为以下任务设计 5-8 个 persona,覆盖主流程 + 边界场景。

## 业务变量真值
{facts}

## 任务 Prompt
{prompt}
"""


def list_personality_library(personalities_dir: str | Path) -> list[Personality]:
    """读性格库 → Personality 列表(用于注入 prompt 让 LLM 知道有哪些性格可选)。"""
    pdir = Path(personalities_dir)
    out: list[Personality] = []
    for f in sorted(pdir.glob("*.yaml")):
        try:
            out.append(load_personality(f))
        except Exception:  # noqa: BLE001
            pass
    return out


def build_prompt(task: TaskDefinition,
                 personalities: list[Personality]) -> tuple[str, str]:
    pers_block = "\n".join(
        f"- {p.id} ({p.name}) — {p.description}"
        for p in personalities
    )
    facts = "\n".join(f"  {k} = {v}" for k, v in task.variables.items())
    # 用 replace 而非 format —— SYSTEM_PROMPT 里有 YAML 示例的 {…},.format() 会报错
    system = _SYSTEM_PROMPT.replace("{personalities}", pers_block)
    # user 模板里只有 {facts}/{prompt} 两个占位符,但 task.prompt 内部也可能有
    # {X}/{Y},因此同样用 replace 避免误解析
    user = (
        _USER_TEMPLATE
        .replace("{facts}", facts or "  (无)")
        .replace("{prompt}", task.prompt)
    )
    return system, user


def parse_personas_output(text: str,
                          known_personalities: set[str]) -> list[PersonaScript]:
    """从 LLM 返回里解出 persona 列表。"""
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = yaml.safe_load(text)
    if isinstance(data, dict) and "personas" in data:
        data = data["personas"]
    if not isinstance(data, list):
        raise ValueError(
            f"LLM 返回不是 persona 列表(得到 {type(data).__name__})")

    out: list[PersonaScript] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        try:
            script = PersonaScript.model_validate(item)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"第 {i + 1} 个 persona 格式不合法:{exc}") from exc
        if script.personality not in known_personalities:
            raise ValueError(
                f"persona '{script.id}' 引用了未知性格 '{script.personality}'"
                f"(性格库有:{sorted(known_personalities)})"
            )
        out.append(script)
    return out


def extract_personas(task: TaskDefinition, judge_model: str,
                     personalities_dir: str | Path,
                     reasoning_effort: str = "medium",
                     temperature: float = 0.0) -> list[PersonaScript]:
    personalities = list_personality_library(personalities_dir)
    system, user = build_prompt(task, personalities)
    response = llm_client.chat(
        judge_model,
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=10000,
    )
    return parse_personas_output(response, {p.id for p in personalities})


# ============== 阶段 2 新增:支持覆盖率 + 推荐权重 ==============

class ExtractedPersonaSet:
    """带 weights + coverage 的完整生成结果。"""
    def __init__(self, scripts: list[PersonaScript],
                 weights: dict[str, int],
                 coverage: dict[str, list[str]]):
        self.scripts = scripts
        self.weights = weights              # persona_id → 推荐权重(int,任意正数)
        self.coverage = coverage            # node_id → 覆盖它的 persona_ids


_COVERAGE_SYSTEM_ADDON = """\

## ★★ 额外要求(本次必须遵守)

1. 每个 persona 必须显式声明 `covers_flow_nodes` 字段 —— 一个 flow 节点 id 列表,
   表示该 persona 的剧本(states/probes)会触发哪些节点。
2. 每个 persona 必须给一个 `weight` 字段(整数,任意正数),表示建议占比。
   合理分布:合作型多(50-60),拒绝/抵触型 15-25,边界场景 5-15。
3. **必须保证下方 flow 节点列表里的每个节点都被至少一个 persona 覆盖**。
   覆盖判定:persona 的 probes 文本含相关关键词、或 states 的 instruction
   明确包含该节点的行为。

## 本任务的 flow 节点(必须覆盖)
{flow_nodes_block}
"""


def build_prompt_with_coverage(task: TaskDefinition,
                                personalities: list[Personality],
                                flow_nodes: list[tuple[str, str]]) -> tuple[str, str]:
    """flow_nodes: [(node_id, label), ...]。"""
    pers_block = "\n".join(
        f"- {p.id} ({p.name}) — {p.description}" for p in personalities)
    facts = "\n".join(f"  {k} = {v}" for k, v in task.variables.items())
    fn_block = "\n".join(f"- {nid}: {lbl}" for nid, lbl in flow_nodes)

    system_base = _SYSTEM_PROMPT.replace("{personalities}", pers_block)
    system = system_base + _COVERAGE_SYSTEM_ADDON.replace(
        "{flow_nodes_block}", fn_block)
    user = (
        _USER_TEMPLATE
        .replace("{facts}", facts or "  (无)")
        .replace("{prompt}", task.prompt)
    )
    return system, user


def parse_personas_with_coverage(text: str,
                                   known_personalities: set[str],
                                   flow_nodes: list[str]) -> ExtractedPersonaSet:
    """解析输出:把 weight + covers_flow_nodes 抽出,scripts 不带 weight。"""
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = yaml.safe_load(text)
    if isinstance(data, dict) and "personas" in data:
        data = data["personas"]
    if not isinstance(data, list):
        raise ValueError(f"LLM 返回不是 persona 列表(得到 {type(data).__name__})")

    scripts: list[PersonaScript] = []
    weights: dict[str, int] = {}
    coverage_inv: dict[str, list[str]] = {n: [] for n in flow_nodes}

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        # 把 weight 抽出,covers_flow_nodes 留在 item(PersonaScript 模型支持)
        w = int(item.pop("weight", 10))
        try:
            script = PersonaScript.model_validate(item)
        except Exception as exc:
            raise ValueError(f"第 {i + 1} 个 persona 格式不合法:{exc}") from exc
        if script.personality not in known_personalities:
            raise ValueError(
                f"persona '{script.id}' 引用了未知性格 '{script.personality}'")
        scripts.append(script)
        weights[script.id] = max(1, w)
        for nid in script.covers_flow_nodes:
            if nid in coverage_inv:
                coverage_inv[nid].append(script.id)

    return ExtractedPersonaSet(scripts, weights, coverage_inv)


def extract_personas_with_coverage(
        task: TaskDefinition, judge_model: str,
        personalities_dir: str | Path,
        flow_nodes: list[tuple[str, str]],
        reasoning_effort: str = "medium",
        temperature: float = 0.0) -> ExtractedPersonaSet:
    """阶段 2 用 —— 生成 persona 时附带 weight + covers_flow_nodes。

    flow_nodes: [(node_id, label), ...]
    """
    personalities = list_personality_library(personalities_dir)
    system, user = build_prompt_with_coverage(task, personalities, flow_nodes)
    response = llm_client.chat(
        judge_model,
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=12000,
    )
    return parse_personas_with_coverage(
        response,
        {p.id for p in personalities},
        [nid for nid, _ in flow_nodes],
    )


def save_persona_script(script: PersonaScript, path: str | Path) -> None:
    """剧本 → 可读 YAML(YAML 格式与编辑器的输出一致)。"""
    data = script.model_dump(exclude_none=True)
    # 清理默认值,YAML 更干净
    if data.get("noise") == {"rate": 0.0, "kinds": []}:
        data.pop("noise", None)
    if not data.get("probes"):
        data.pop("probes", None)
    if not data.get("name"):
        data.pop("name", None)
    Path(path).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8")
