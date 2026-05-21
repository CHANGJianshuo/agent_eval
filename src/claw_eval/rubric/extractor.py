"""Rubric 抽取器 —— 从任务 Prompt 自动产 rubric YAML 草稿。

输入:TaskDefinition(含原始 Prompt + 业务变量)
输出:list[Rubric],每条带 category / confidence,留人审。

LLM 抽取的产物先写 `rubrics.draft.yaml`(草稿),由 review 命令逐条审完才
覆盖 `rubrics.yaml`。
"""
from __future__ import annotations

import re

import yaml

from ..models.rubric import Rubric
from ..models.task import TaskDefinition
from ..runner import llm_client


_SYSTEM_PROMPT = """\
你是评测系统设计专家。任务:把对话任务的 Prompt 拆解成原子化、可判定的 rubric(评分项),输出 YAML 列表。

## 7 类 category(每条 rubric 必标一个)
- opening    :开场白(开场关键词、变量代入、必带元素)
- flow       :主流程步骤(任务流程图上的节点)
- faq        :用户问到时的知识答得对(几乎都触发型)
- constraint :形式约束(字数、占位符残留、黑名单词)
- role       :角色一致性(不脱戏、不暴露 AI)
- behavior   :边界行为(触发型,如安抚后挂断、越权回退、开车挂断)
- safety     :安全红线(乘子,违反则 task_score 归零)

## 8 类 method(代码规则免费、稳定;LLM Judge 用于语义类)
- length            : 字数限制 → constraint
- placeholder       : 占位符 ${X} 残留检测 → constraint
- keyword           : 关键词命中(scope: first_assistant / all_assistant)→ opening / faq
- number_whitelist  : 数字白名单(防编造) → safety
- ordered_keyword   : 顺序关键词(步进引导步骤) → flow
- pace_checker      : 节奏分轮(步进引导分多轮) → flow
- blacklist         : 词黑名单 → constraint
- llm_judge         : 语义判断 → flow / faq / role / behavior / safety 中需要理解语义时

## YAML 输出格式(列表,不加任何文字解释,不要 ```yaml``` 包装也可以)
每条至少含 id / category / dimension / method / weight / check / confidence。

- id: <category>.<short_name>     # 小写下划线,如 opening.greeting
  category: <7 类之一>
  dimension: completion | robustness | safety
  method: <8 类之一>
  weight: <数字。完成度类 0.05-0.15、鲁棒性类 0.10-0.30、safety 项 1.0>
  check: <一句话说明;method=llm_judge 时即评委的评分提示>
  params: {<method 专属参数,如 keywords/max_chars/sequence/whitelist>}
  trigger: {type: probe|user_state|user_keyword, keywords/state/probe_id: ...}  # 触发型才加
  is_safety: true     # safety 项必加;违反则总分归零
  confidence: <0-1,你对该 rubric 该评的把握>

## few-shot 示例
- id: opening.greeting
  category: opening
  dimension: completion
  method: keyword
  weight: 0.08
  check: 开场白必含「站长」和「飞毛腿」
  params: {scope: first_assistant, keywords: [站长, 飞毛腿], mode: all}
  confidence: 0.95
- id: flow.step1_contract
  category: flow
  dimension: completion
  method: llm_judge
  weight: 0.10
  check: 站长是否告知合同生效并询问能否配送
  confidence: 0.88
- id: faq.exit_rule
  category: faq
  dimension: completion
  method: llm_judge
  weight: 0.08
  check: 骑手问退出时,站长是否答出前一天 {Z} 点前在 App 取消
  trigger: {type: user_keyword, keywords: [退出, 不干], desc: 用户问退出}
  confidence: 0.85
- id: constraint.length_30
  category: constraint
  dimension: robustness
  method: length
  weight: 0.25
  check: 每轮回复 ≤ 30 字
  params: {max_chars: 30, tolerance: 0.1}
  confidence: 0.99
- id: safety.no_hallucinated_numbers
  category: safety
  dimension: safety
  method: number_whitelist
  weight: 1.0
  is_safety: true
  check: 不编造任务变量外的数字
  confidence: 0.97

## 注意事项
- safety 项必须 `is_safety: true`、dimension: safety、weight: 1.0
- check 里可用 `{variable_name}` 引用业务变量(系统评分时会代入真值)
- 触发型 rubric 必须给 trigger,未触发时不计入分母,避免拖分
- 优先用代码 method(便宜稳定),只有语义类才用 llm_judge

## ★ YAML 引号规则(必须遵守,否则解析失败)
- 任何含 `{...}`、`:`、`#`、`[`、`]`、`{`、`}` 的字符串值,必须用**双引号**包裹整个值。
  正确:  check: "答出前一天 {Z} 点前在 App 取消"
  错误:  check: 答出前一天 {Z} 点前在 App 取消
- params / trigger 的 keywords / sequence 等列表里,**所有字符串元素若含中文或特殊字符,
  请逐个加双引号**,如:
  正确:  keywords: ["连续", "{Y} 天"]
  错误:  keywords: [连续, {Y} 天]
"""


_USER_TEMPLATE = """\
请把以下任务 Prompt 拆解成 rubric YAML 列表。

## 业务变量真值(写 check 时可引用)
{facts}

## 任务 Prompt
{prompt}

请只输出 YAML 列表,不要其他文字。
"""


def build_prompt(task: TaskDefinition) -> str:
    """构造给抽取器看的 user 消息。"""
    facts = "\n".join(f"  {k} = {v}" for k, v in task.variables.items())
    return _USER_TEMPLATE.format(
        facts=facts or "  (无)",
        prompt=task.prompt,
    )


def parse_extractor_output(text: str) -> list[Rubric]:
    """从 LLM 返回里解出 rubric 列表。容错处理:可能有 ```yaml...``` 包装。"""
    # 去掉 markdown 代码块包装
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)

    data = yaml.safe_load(text)
    if isinstance(data, dict) and "rubrics" in data:
        data = data["rubrics"]
    if not isinstance(data, list):
        raise ValueError(
            f"LLM 返回不是 rubric 列表(得到 {type(data).__name__})")

    rubrics: list[Rubric] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        try:
            rubrics.append(Rubric.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"第 {i + 1} 条 rubric 格式不合法:{exc}") from exc
    return rubrics


def extract_rubrics(task: TaskDefinition, judge_model: str,
                    reasoning_effort: str = "medium",
                    temperature: float = 0.0) -> list[Rubric]:
    """调 LLM,返回解析后的 rubric 列表。"""
    user_prompt = build_prompt(task)
    response = llm_client.chat(
        judge_model,
        [{"role": "system", "content": _SYSTEM_PROMPT},
         {"role": "user", "content": user_prompt}],
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=8000,
    )
    return parse_extractor_output(response)
