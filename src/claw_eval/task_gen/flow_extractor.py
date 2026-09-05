"""Flow Extractor —— 从任务 prompt 抽取 flow.yaml(主流程节点 + 分支)。

主流程:线性 N 步(opening / step1 / step2 / ...)
分支:条件型(if-else 必走)+ 触发型(optional=True,只在 trigger 时走)
"""
from __future__ import annotations

import re

import yaml

from ..models.flow import FlowDiagram
from ..runner import llm_client


_SYSTEM_PROMPT = """\
你是评测系统设计专家。任务:从一段对话任务的描述(prompt)中,抽取**逻辑分支图**(flow.yaml),
描述 SUT 在对话中应走的主流程节点 + 条件分支 + 触发型边界行为。

## 输出格式(YAML,顶层 nodes + edges)

```yaml
nodes:
  - {id: opening, label: "开场+身份确认", rubric: opening.identity}
  - {id: step1,   label: "告知合同生效", rubric: flow.step1}
  - {id: step2,   label: "说明Y天约束", rubric: flow.step2}
  - {id: faq_exit, label: "答退出规则", rubric: faq.exit, optional: true}
  - {id: oos,     label: "越权回退", rubric: behavior.out_of_scope, optional: true}
edges:
  - [opening, step1]
  - [step1, step2]
  - [step2, faq_exit]    # 触发分支:用户问退出时走
  - [faq_exit, step1]    # 答完回主流程
```

## 规则
1. **id**:全小写下划线;主流程用 `step1` `step2` 等(没有歧义,简短);分支用语义 id(`faq_exit`、`busy_retain`、`out_of_scope`)
2. **label**:中文短语,≤ 12 字,描述该节点该做的事
3. **rubric**:关联的 rubric id,格式 `<category>.<short_name>`(后续 rubric 抽取会用到该 id);如果该节点不打算评分,留空
4. **optional**:`true` 表示该节点是**触发型分支**(只在用户问相关问题时才走);省略 = 主流程必走
5. **edges**:节点之间的连边,主流程一条线串起,分支节点用边连到触发的位置
6. 主流程节点 5-9 个为佳,触发分支 2-5 个;不要过细
7. 一定要有 **opening**(开场)节点;有的话也加 **end**(显式结束)
8. 字符串若含 `{`、`:`、`#`、`?`、`!` 等特殊字符,必须**双引号包裹整个字符串值**

## 设计原则
- 主流程是「**必走的步骤**」,从 opening 到 end 串起来
- 触发型分支是「**用户问到时才答**」的 FAQ + 「**特定情境下应做**」的边界行为
- 每个 rubric id 后续抽取会用到,**先设计好 id**(`flow.*` / `faq.*` / `behavior.*` / `opening.*` / `constraint.*` / `safety.*`)

只输出 YAML 内容,不要 markdown 代码块包装也行,不要其他文字。
"""


_USER_TEMPLATE = """\
请从以下对话任务描述中抽取 flow.yaml:

{prompt}

输出 YAML(nodes + edges)。
"""


def build_prompt(task_prompt: str) -> tuple[str, str]:
    return _SYSTEM_PROMPT, _USER_TEMPLATE.replace("{prompt}", task_prompt)


def parse_flow_output(text: str) -> FlowDiagram:
    """从 LLM 返回解出 FlowDiagram。容错 ```yaml``` 包装。"""
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        preview = text[:200] if text else "(空)"
        raise ValueError(
            f"LLM 返回不是 dict(得到 {type(data).__name__}): {preview}")
    return FlowDiagram.model_validate(data)


def extract_flow(task_prompt: str, judge_model: str,
                 reasoning_effort: str = "medium",
                 temperature: float = 0.0,
                 max_attempts: int = 3) -> FlowDiagram:
    """调 LLM 抽 flow.yaml，失败自动重试。"""
    system, user = build_prompt(task_prompt)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        response = llm_client.chat(
            judge_model,
            messages,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            max_tokens=4000,
        )
        try:
            return parse_flow_output(response)
        except (ValueError, yaml.YAMLError) as exc:
            last_err = exc
            if attempt < max_attempts - 1:
                messages.extend([
                    {"role": "assistant", "content": response},
                    {
                        "role": "user",
                        "content": (
                            "上面的 YAML 无法解析，错误为："
                            f"{exc}。请只返回修正后的完整 YAML；尤其要给包含冒号、"
                            "花括号或井号的字符串加双引号，不要解释。"
                        ),
                    },
                ])
    raise RuntimeError(
        f"flow 抽取失败（重试 {max_attempts} 次）: {last_err}")


def save_flow(flow: FlowDiagram, path) -> None:
    """FlowDiagram → YAML(节点 + 边)。"""
    from pathlib import Path
    data = {
        "nodes": [n.model_dump(exclude_none=True) for n in flow.nodes],
        "edges": [list(e) for e in flow.edges],
    }
    Path(path).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8")
