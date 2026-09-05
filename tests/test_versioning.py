"""task.yaml 版本管理单测。"""
from __future__ import annotations

import yaml
from pathlib import Path

import pytest

from claw_eval.task_gen.versioning import (
    current_version_label,
    delete_version,
    get_version_yaml,
    list_versions,
    save_version,
    switch_to_version,
)


def _setup_task(tmp_path: Path, prompt: str = "v1 prompt") -> Path:
    td = tmp_path / "task"
    td.mkdir()
    (td / "task.yaml").write_text(
        yaml.safe_dump({"task_id": "t", "prompt": prompt},
                       allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return td


def test_save_version_creates_backup_and_history(tmp_path):
    td = _setup_task(tmp_path)
    info = save_version(td, "v1-initial", note="开局")
    assert info.label == "v1-initial"
    assert (td / ".versions" / "v1-initial.yaml").exists()
    assert (td / ".versions" / ".history.json").exists()


def test_save_version_overwrites_same_label(tmp_path):
    td = _setup_task(tmp_path)
    save_version(td, "v1")
    save_version(td, "v1", note="替换")
    assert len(list_versions(td)) == 1


def test_list_versions_in_order(tmp_path):
    td = _setup_task(tmp_path)
    save_version(td, "v1")
    save_version(td, "v2", based_on="v1")
    save_version(td, "v3", based_on="v2", applied_recs=["opening.x"])
    vs = list_versions(td)
    assert [v.label for v in vs] == ["v1", "v2", "v3"]
    assert vs[2].based_on == "v2"
    assert vs[2].applied_recs == ["opening.x"]


def test_current_version_label(tmp_path):
    td = _setup_task(tmp_path)
    assert current_version_label(td) is None
    save_version(td, "v1")
    assert current_version_label(td) == "v1"
    save_version(td, "v2")
    assert current_version_label(td) == "v2"


def test_switch_to_version_restores_yaml(tmp_path):
    td = _setup_task(tmp_path, "v1 content")
    save_version(td, "v1")
    # 改 task.yaml
    (td / "task.yaml").write_text(
        yaml.safe_dump({"task_id": "t", "prompt": "v2 content"},
                       allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    save_version(td, "v2")
    # 切回 v1
    switch_to_version(td, "v1")
    cur = yaml.safe_load((td / "task.yaml").read_text(encoding="utf-8"))
    assert cur["prompt"] == "v1 content"


def test_switch_unknown_version_raises(tmp_path):
    td = _setup_task(tmp_path)
    with pytest.raises(FileNotFoundError):
        switch_to_version(td, "nonexistent")


def test_get_version_yaml_returns_content(tmp_path):
    td = _setup_task(tmp_path, "hello")
    save_version(td, "v1")
    content = get_version_yaml(td, "v1")
    assert "hello" in content


def test_delete_version_removes_file_and_history(tmp_path):
    td = _setup_task(tmp_path)
    save_version(td, "v1")
    save_version(td, "v2")
    delete_version(td, "v1")
    assert not (td / ".versions" / "v1.yaml").exists()
    assert [v.label for v in list_versions(td)] == ["v2"]
