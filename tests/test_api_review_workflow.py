"""Regression tests for draft/approval API contracts."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from claw_eval.api import routes_tasks, routes_tests
from claw_eval.models.rubric import Rubric, load_rubrics, save_rubrics


def _rubric(*, check: str = "检查问候") -> Rubric:
    return Rubric(
        id="opening.greeting",
        dimension="completion",
        method="llm_judge",
        check=check,
        weight=0.1,
        category="opening",
    )


def _write_task(task_dir: Path) -> None:
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        yaml.safe_dump({
            "task_id": task_dir.name,
            "prompt": "你好 {name}",
            "variables": {"name": "王师傅"},
        }, allow_unicode=True),
        encoding="utf-8",
    )


def _write_persona(path: Path, persona_id: str = "happy_path") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({
            "id": persona_id,
            "name": "主流程",
            "scenario": "配合走完主流程。",
            "covers_flow_nodes": ["opening"],
            "max_rounds": 6,
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_update_rubrics_writes_loader_compatible_document(tmp_path: Path,
                                                           monkeypatch):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "demo"
    _write_task(task_dir)
    monkeypatch.setattr(routes_tasks, "TASKS_DIR", tasks_dir)

    response = routes_tasks.update_rubrics(
        "demo",
        routes_tasks.UpdateRubricsReq(
            rubrics=[_rubric().model_dump(exclude_none=True)],
            is_draft=True,
        ),
    )

    saved = task_dir / "rubrics.draft.yaml"
    assert response["file"] == "rubrics.draft.yaml"
    assert list((yaml.safe_load(saved.read_text(encoding="utf-8")) or {})) == ["rubrics"]
    assert len(load_rubrics(saved)) == 1
    assert not (task_dir / "rubrics.yaml").exists()


def test_update_rubrics_rejects_unknown_method(tmp_path: Path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "demo"
    _write_task(task_dir)
    monkeypatch.setattr(routes_tasks, "TASKS_DIR", tasks_dir)
    row = _rubric().model_dump(exclude_none=True)
    row["method"] = "unknown_method"

    with pytest.raises(HTTPException) as exc_info:
        routes_tasks.update_rubrics(
            "demo",
            routes_tasks.UpdateRubricsReq(rubrics=[row], is_draft=True),
        )

    assert exc_info.value.status_code == 422
    assert not (task_dir / "rubrics.draft.yaml").exists()


def test_approve_validates_and_promotes_drafts(tmp_path: Path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "demo"
    _write_task(task_dir)
    save_rubrics([_rubric()], task_dir / "rubrics.draft.yaml")
    _write_persona(task_dir / "personas_draft" / "happy_path.yaml")
    monkeypatch.setattr(routes_tasks, "TASKS_DIR", tasks_dir)

    before = routes_tasks.get_review_status("demo")
    assert before["rubrics_draft"] is True
    assert before["personas_pending"] == ["happy_path"]

    routes_tasks.approve_drafts(
        "demo",
        routes_tasks.ApproveReq(
            approve_rubrics=True,
            approve_personas=["happy_path"],
        ),
    )

    approved = load_rubrics(task_dir / "rubrics.yaml")
    assert approved[0].reviewed is True
    assert (task_dir / "personas" / "happy_path.yaml").exists()
    after = routes_tasks.get_review_status("demo")
    assert after["rubrics_draft"] is False
    assert after["personas_pending"] == []


def test_approved_task_can_start_without_generated_grader(tmp_path: Path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "demo"
    _write_task(task_dir)
    save_rubrics([_rubric()], task_dir / "rubrics.yaml")
    _write_persona(task_dir / "personas" / "happy_path.yaml")
    monkeypatch.setattr(routes_tests, "TASKS_DIR", tasks_dir)
    from claw_eval.db import repo
    monkeypatch.setattr(repo, "DEFAULT_DB", tmp_path / "test.db")
    monkeypatch.setattr(routes_tests, "ROOT", tmp_path)
    background = BackgroundTasks()

    response = routes_tests.start_test(
        "demo", routes_tests.NewTestRequest(test_id="run_1", total=1), background,
    )

    assert response.status == "running"
    assert len(background.tasks) == 1
    assert not (task_dir / "grader.py").exists()


def test_start_test_never_auto_approves_drafts(tmp_path: Path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "demo"
    _write_task(task_dir)
    save_rubrics([_rubric()], task_dir / "rubrics.draft.yaml")
    _write_persona(task_dir / "personas_draft" / "happy_path.yaml")
    monkeypatch.setattr(routes_tests, "TASKS_DIR", tasks_dir)

    with pytest.raises(HTTPException) as exc_info:
        routes_tests.start_test(
            "demo",
            routes_tests.NewTestRequest(test_id="run_1", total=1),
            BackgroundTasks(),
        )

    assert exc_info.value.status_code == 422
    assert "仍是草稿" in str(exc_info.value.detail)
    assert not (task_dir / "rubrics.yaml").exists()
    assert not (task_dir / "personas").exists()


def test_start_test_rejects_unsafe_test_id(tmp_path: Path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "demo"
    _write_task(task_dir)
    save_rubrics([_rubric()], task_dir / "rubrics.yaml")
    _write_persona(task_dir / "personas" / "happy_path.yaml")
    monkeypatch.setattr(routes_tests, "TASKS_DIR", tasks_dir)

    with pytest.raises(HTTPException) as exc_info:
        routes_tests.start_test(
            "demo",
            routes_tests.NewTestRequest(test_id="../escape", total=1),
            BackgroundTasks(),
        )

    assert exc_info.value.status_code == 422
    assert "test_id" in str(exc_info.value.detail)


def test_request_models_reject_unsafe_or_unbounded_inputs():
    with pytest.raises(ValidationError):
        routes_tasks.NewTaskRequest(
            task_id="../../escape",
            prompt="x" * 60,
        )
    with pytest.raises(ValidationError):
        routes_tests.NewTestRequest(total=501)
