"""Validated task edits with conflict checks and complete revision snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory, NamedTemporaryFile
from uuid import uuid4
import fcntl
import yaml

from ..runs import digest


class EditConflict(ValueError):
    pass


@contextmanager
def task_lock(task_dir: Path):
    with (task_dir / ".edit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def task_files(task_dir: Path) -> dict[str, str]:
    paths = [*task_dir.glob("*.yaml"), *task_dir.glob("personas/*.yaml"),
             *task_dir.glob("personas_draft/*.yaml")]
    return {str(p.relative_to(task_dir)): p.read_text(encoding="utf-8") for p in sorted(paths)}


def revision(task_dir: Path) -> str:
    return digest(task_files(task_dir))


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as f:
        f.write(text)
        temp = Path(f.name)
    temp.replace(path)


def validate_files(files: dict[str, str], task_dir: Path):
    from ..models.task import TaskDefinition
    from ..models.rubric import Rubric
    from ..models.persona import PersonaScript, load_noise_kinds
    from ..graders.validation import validate_rule_params
    from ..runs import validate_id
    from ..validator import check_template_variables, require_valid_task, check_weights
    task = TaskDefinition.model_validate(yaml.safe_load(files["task.yaml"]))
    original = TaskDefinition.model_validate(yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8")))
    if task.task_id != original.task_id:
        raise ValueError("不能通过配置编辑更改任务 ID")
    rubrics = []
    for name, content in files.items():
        if name in {"rubrics.yaml", "rubrics.draft.yaml"}:
            rows = [Rubric.model_validate(r) for r in (yaml.safe_load(content) or {}).get("rubrics", [])]
            if not rows or len({r.id for r in rows}) != len(rows):
                raise ValueError(f"{name}: 评分项不能为空且 ID 不可重复")
            errors = [i.message for i in check_weights(rows) if i.level == "error"]
            if errors:
                raise ValueError("；".join(errors))
            for row in rows:
                validate_rule_params(row)
            rubrics.extend(rows)
        elif name.startswith(("personas/", "personas_draft/")):
            script = PersonaScript.model_validate(yaml.safe_load(content))
            validate_id(script.id)
            if script.id != Path(name).stem:
                raise ValueError("剧本文件名必须与 ID 一致")
            if not script.scenario and not script.states:
                raise ValueError("剧本至少需要场景描述或状态机")
            noise_path = task_dir.parent.parent / 'configs/noise_profiles.yaml'
            library = load_noise_kinds(noise_path) if noise_path.exists() else {}
            if set(script.noise.kinds) - set(library) or (script.noise.rate > 0 and not script.noise.kinds):
                raise ValueError(f"{name}: 噪音种类缺失或不存在")
            if len({p.id for p in script.probes}) != len(script.probes) or any(p.inject_at_turn > script.max_rounds for p in script.probes):
                raise ValueError("探针 ID 重复或注入轮次超出剧本轮数")
    errors = check_template_variables(task, rubrics)
    if errors:
        raise ValueError("；".join(i.message for i in errors))
    with TemporaryDirectory() as tmp:
        staged = Path(tmp)
        for name, content in files.items():
            write_text(staged / name, content)
        if "rubrics.yaml" in files:
            require_valid_task(staged, root=task_dir.parent.parent)


def commit_files(task_dir: Path, changes: dict[str, str | None], *,
                 expected_revision: str | None = None, note: str = "",
                 applied_recs: list[str] | None = None) -> dict:
    from .versioning import save_version
    with task_lock(task_dir):
        before = task_files(task_dir)
        if expected_revision is not None and expected_revision != digest(before):
            raise EditConflict("配置已被其他操作修改，请刷新后重新检查差异")
        after = dict(before)
        for name, content in changes.items():
            parts = Path(name).parts
            allowed = (name in {"task.yaml", "rubrics.yaml", "rubrics.draft.yaml", "sampling.yaml", "flow.yaml"}
                       or (len(parts) == 2 and parts[0] in {"personas", "personas_draft"}
                           and parts[1].endswith(".yaml") and ".." not in parts))
            if not allowed or Path(name).is_absolute():
                raise ValueError("不支持的配置文件")
            if content is None:
                after.pop(name, None)
            else:
                after[name] = content
        validate_files(after, task_dir)
        if before == after:
            return {"ok": True, "revision": digest(after), "version": None}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6]
        baseline = "before_" + stamp
        save_version(task_dir, baseline, note="修改前自动备份")
        try:
            for name in set(before) | set(after):
                if name not in after:
                    (task_dir / name).unlink(missing_ok=True)
                elif before.get(name) != after[name]:
                    write_text(task_dir / name, after[name])
            label = "v_" + stamp
            save_version(task_dir, label, based_on=baseline, note=note, applied_recs=applied_recs)
        except Exception:
            for name in set(before) | set(after):
                if name in before:
                    write_text(task_dir / name, before[name])
                else:
                    (task_dir / name).unlink(missing_ok=True)
            raise
        return {"ok": True, "revision": digest(after), "version": label}
