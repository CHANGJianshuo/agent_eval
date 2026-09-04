"""Task/rubric template rendering regression tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_eval.models.task import TaskDefinition
from claw_eval.templating import MissingTemplateVariables, render_template


_ROOT = Path(__file__).resolve().parents[1]


def test_render_supports_canonical_and_legacy_dollar_syntax():
    rendered = render_template(
        "你好 {name} / ${name}，每天 {count} 单",
        {"name": "王师傅", "count": 20},
    )

    assert rendered == "你好 王师傅 / 王师傅，每天 20 单"
    assert "$王师傅" not in rendered


def test_render_supports_only_standalone_legacy_letters():
    rendered = render_template(
        "完成 X 单；EXTRA 和 XRay 不是占位符",
        {"X": 15},
    )

    assert rendered == "完成 15 单；EXTRA 和 XRay 不是占位符"


def test_render_does_not_interpret_normal_json_braces():
    text = '返回 JSON: {"status": "ok"}'
    assert render_template(text, {}) == text


def test_render_fails_fast_on_missing_named_variable():
    with pytest.raises(MissingTemplateVariables) as exc_info:
        render_template("你好 {name}，完成 ${count} 单", {"name": "王师傅"})

    assert exc_info.value.names == frozenset({"count"})


@pytest.mark.parametrize(
    "task_file",
    sorted((_ROOT / "tasks").glob("*/task.yaml")),
    ids=lambda path: path.parent.name,
)
def test_current_task_prompts_render_without_placeholders(task_file: Path):
    task = TaskDefinition.from_yaml(task_file)
    rendered = task.rendered_prompt()

    assert rendered
    assert "${" not in rendered
