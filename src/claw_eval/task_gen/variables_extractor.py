"""Variables Extractor —— 从任务 prompt 抽出业务参数(数字 / 时间 / 占位符)。

输出 dict[str, str | int | float],对应 task.yaml 的 variables 段。
prompt 里 `{X}` `{Y}` `{Z}` 等占位符,以及描述里出现的具体数字(可能需要参数化),都会被抽出。
"""
from __future__ import annotations

import json
import re

from ..runner import llm_client
from ..templating import placeholder_names


_SYSTEM_PROMPT = """\
你是配置工程师。从任务 prompt 中找出**需要参数化的业务变量**,输出 JSON 字典。

## 什么是需要参数化的变量
- prompt 里已经用 `{X}` `{Y}` 形式占位的(必须抽出)
- 具体数字(15-20 字限制、5 折扣、3 小时…)若上下文表明这是业务参数,可以抽出
- 不要抽出「永远不变的常量」(如「您好」、地名、产品名)
- 不要抽出复杂表达式

## 输出格式
{"X": 25, "Y": 3, "tolerance_pct": 10, "Z": 22}

数字尽量用 int / float;字符串用 ""。给一个合理的默认值,业务方可以后续改。

只输出 JSON,不要任何文字解释,不要 markdown 包装。
"""


_USER_TEMPLATE = """\
任务 prompt:

{prompt}

请输出 JSON 字典(变量名 → 默认值)。
"""


def parse_variables_output(text: str) -> dict:
    """从 LLM 返回解出 dict。容错 ```json``` 包装。"""
    m = re.search(r"```(?:json)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    text = text.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试找最大的 {...} 段
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            raise
    if not isinstance(data, dict):
        return {}
    return data


def extract_variables(task_prompt: str, judge_model: str,
                       reasoning_effort: str = "low",
                       temperature: float = 0.0) -> dict:
    """调 LLM 抽业务变量。reasoning_effort=low 即可(任务简单)。"""
    user = _USER_TEMPLATE.replace("{prompt}", task_prompt)
    response = llm_client.chat(
        judge_model,
        [{"role": "system", "content": _SYSTEM_PROMPT},
         {"role": "user", "content": user}],
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=1500,
    )
    return parse_variables_output(response)


def auto_detect_placeholders(task_prompt: str) -> set[str]:
    """抽取 ``{name}`` / ``${name}`` 占位符,不依赖 LLM。"""
    return placeholder_names(task_prompt)
