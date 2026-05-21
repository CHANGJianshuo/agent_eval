"""apply_recommendation —— LLM 把 recommend 的「自然语言建议」落到 task.yaml prompt。

输入:原 prompt 文本 + recommendation dict({rubric_id, suggested_prompt_change, ...})
输出:修改后的完整 prompt 文本 + unified diff(给 UI 显示用)。
"""
from __future__ import annotations

import difflib
import re

from ..runner import llm_client


_SYSTEM_PROMPT = """\
你是 prompt 工程师。任务:把一条优化建议**落到完整的 SUT prompt 上**,输出修改后的完整 prompt。

## 约束(必须遵守)
1. **不要重写整个 prompt**,只在建议指向的位置做最小改动
2. **保留原有结构**(标题层级、段落顺序、变量占位符 `{X}` 等)
3. **不引入新的硬编码数字** —— 如果非要写数字,用变量占位符
4. **绝对不要承诺优惠 / 折扣** —— 即使建议这么说也不要
5. 改完后**整段 prompt 仍要语义完整**,SUT 看了能直接照做

## 输出格式
只输出修改后的完整 prompt 内容,不要 markdown 代码块包装,不要解释,不要 diff,
就是一段可以直接保存为 task.yaml 的 prompt 文本(从第一行起)。
"""


_USER_TEMPLATE = """\
## 原 prompt
```
{prompt}
```

## 优化建议
- 弱 rubric:{rubric_id}
- 当前平均分:{avg_score}
- 建议:
{suggested_change}

- 理由:{rationale}

请输出修改后的**完整** prompt 内容(不要 diff,不要解释)。
"""


def build_patch_prompt(prompt: str, rec: dict) -> tuple[str, str]:
    user = (
        _USER_TEMPLATE
        .replace("{prompt}", prompt)
        .replace("{rubric_id}", str(rec.get("rubric_id", "")))
        .replace("{avg_score}", str(rec.get("avg_score", "?")))
        .replace("{suggested_change}", str(rec.get("suggested_prompt_change", "")))
        .replace("{rationale}", str(rec.get("rationale", "")))
    )
    return _SYSTEM_PROMPT, user


def _strip_markdown_wrap(text: str) -> str:
    """LLM 偶尔会包 ```markdown,虽然 prompt 里禁止了。"""
    m = re.match(r"^\s*```(?:\w+)?\s*\n(.+?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip("\n")
    return text


def generate_prompt_patch(prompt: str, rec: dict,
                           judge_model: str,
                           reasoning_effort: str = "medium",
                           temperature: float = 0.0) -> str:
    """调 LLM 产新 prompt;返回 str。"""
    system, user = build_patch_prompt(prompt, rec)
    response = llm_client.chat(
        judge_model,
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=6000,
    )
    return _strip_markdown_wrap(response).strip()


def unified_diff(old: str, new: str,
                 old_label: str = "before",
                 new_label: str = "after",
                 context: int = 2) -> str:
    """返回 unified diff 字符串(用于 UI 展示和审阅)。"""
    diff = difflib.unified_diff(
        old.splitlines(keepends=False),
        new.splitlines(keepends=False),
        fromfile=old_label, tofile=new_label,
        n=context, lineterm="",
    )
    return "\n".join(diff)


def diff_stats(old: str, new: str) -> dict:
    """统计 diff 的「加 X 行 / 删 Y 行」。"""
    diff = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm=""))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return {"added": added, "removed": removed}
