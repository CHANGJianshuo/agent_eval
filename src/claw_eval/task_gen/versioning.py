"""task.yaml 版本管理 —— 备份 / 列表 / 切换。

存储:
  tasks/<id>/task.yaml                    当前版本(SUT 跑批用的就是这个)
  tasks/<id>/.versions/<label>.yaml       某版本的备份
  tasks/<id>/.versions/.history.json      版本元数据
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class VersionInfo:
    label: str
    created_at: str
    based_on: str | None = None              # 从哪个版本派生
    applied_recs: list[str] = field(default_factory=list)   # 应用了哪些 rubric 的建议
    note: str = ""


# ============================ 内部辅助 ============================

def _versions_dir(task_dir: Path) -> Path:
    return Path(task_dir) / ".versions"


def _history_path(task_dir: Path) -> Path:
    return _versions_dir(task_dir) / ".history.json"


def _load_history(task_dir: Path) -> list[VersionInfo]:
    p = _history_path(task_dir)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [VersionInfo(**v) for v in data.get("versions", [])]


def _save_history(task_dir: Path, versions: list[VersionInfo]) -> None:
    p = _history_path(task_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    from ..runs import atomic_json
    atomic_json(p, {"versions": [asdict(v) for v in versions]})


# ============================ 公开 API ============================

def save_version(task_dir: str | Path, label: str,
                 based_on: str | None = None,
                 applied_recs: list[str] | None = None,
                 note: str = "") -> VersionInfo:
    """把当前 task.yaml 备份成 .versions/<label>.yaml,并写元数据。"""
    task_dir = Path(task_dir)
    from ..runs import validate_id
    validate_id(label)
    src = task_dir / "task.yaml"
    if not src.exists():
        raise FileNotFoundError(f"{src} 不存在")
    _versions_dir(task_dir).mkdir(parents=True, exist_ok=True)
    dst = _versions_dir(task_dir) / f"{label}.yaml"
    shutil.copy2(src, dst)
    from .config_store import task_files
    from ..runs import atomic_json
    atomic_json(_versions_dir(task_dir) / f"{label}.snapshot.json", task_files(task_dir))

    versions = _load_history(task_dir)
    # 已有同名则覆盖
    versions = [v for v in versions if v.label != label]
    info = VersionInfo(
        label=label,
        created_at=datetime.now().isoformat(timespec="seconds"),
        based_on=based_on,
        applied_recs=list(applied_recs or []),
        note=note,
    )
    versions.append(info)
    _save_history(task_dir, versions)
    return info


def list_versions(task_dir: str | Path) -> list[VersionInfo]:
    """返回版本列表,按创建时间升序。"""
    return _load_history(Path(task_dir))


def current_version_label(task_dir: str | Path) -> str | None:
    """返回当前 task.yaml 对应的版本 label(最近一次 save 的版本,
    如果之后 task.yaml 被修改,可能不一致)。"""
    versions = _load_history(Path(task_dir))
    if not versions:
        return None
    from .config_store import task_files
    current = task_files(Path(task_dir))
    for version in reversed(versions):
        if get_version_files(task_dir, version.label) == current:
            return version.label
    return None


def switch_to_version(task_dir: str | Path, label: str) -> None:
    """把指定版本恢复成当前 task.yaml。"""
    task_dir = Path(task_dir)
    from ..runs import validate_id
    validate_id(label)
    src = _versions_dir(task_dir) / f"{label}.yaml"
    if not src.exists():
        raise FileNotFoundError(f"版本 {label} 不存在:{src}")
    from .config_store import commit_files, task_files
    files = get_version_files(task_dir, label)
    changes = {name: files.get(name) for name in set(task_files(task_dir)) | set(files)}
    commit_files(task_dir, changes, note=f"恢复版本 {label}")


def get_version_yaml(task_dir: str | Path, label: str) -> str:
    """读取某版本的 yaml 文本(用于 diff 比对)。"""
    task_dir = Path(task_dir)
    from ..runs import validate_id
    validate_id(label)
    src = _versions_dir(task_dir) / f"{label}.yaml"
    if not src.exists():
        raise FileNotFoundError(f"版本 {label} 不存在")
    return src.read_text(encoding="utf-8")


def delete_version(task_dir: str | Path, label: str) -> None:
    """删除某版本(也从历史里移除)。"""
    task_dir = Path(task_dir)
    from ..runs import validate_id
    validate_id(label)
    f = _versions_dir(task_dir) / f"{label}.yaml"
    if f.exists():
        f.unlink()
    (_versions_dir(task_dir) / f"{label}.snapshot.json").unlink(missing_ok=True)
    versions = [v for v in _load_history(task_dir) if v.label != label]
    _save_history(task_dir, versions)


def get_version_files(task_dir: Path, label: str) -> dict[str, str]:
    from ..runs import validate_id
    validate_id(label)
    snapshot = _versions_dir(Path(task_dir)) / f"{label}.snapshot.json"
    if snapshot.exists():
        return json.loads(snapshot.read_text(encoding="utf-8"))
    # Legacy versions contain the prompt only. Preserve the other current files.
    from .config_store import task_files
    return {**task_files(Path(task_dir)), "task.yaml": get_version_yaml(task_dir, label)}
