"""Immutable run inputs. A run ID is reserved once; credentials stay outside snapshots."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from importlib.metadata import version, PackageNotFoundError
from datetime import datetime, timezone
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def validate_id(value: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError("ID 必须以字母或数字开头，仅包含字母、数字、下划线和连字符，最多 128 字符")
    return value


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def grading_fingerprint(root: Path) -> str:
    return digest({str(p.relative_to(root / "src")): hashlib.sha256(p.read_bytes()).hexdigest()
                   for folder in ("graders", "models", "user_simulator", "runner")
                   for p in sorted((root / "src" / "claw_eval" / folder).rglob("*.py"))})


def atomic_json(path: Path, value) -> None:
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        tmp = Path(f.name)
    tmp.replace(path)


def _public_config(value):
    if isinstance(value, dict):
        return {k: _public_config(v) for k, v in value.items()
                if k.lower() not in {"api_key", "token", "password", "secret", "authorization"}}
    if isinstance(value, list):
        return [_public_config(v) for v in value]
    return value


def _prepare_run_unlocked(root: Path, task_dir: Path, run_id: str, models: dict, params: dict,
                *, inputs_root: Path | None = None) -> dict:
    """Reserve directory before any calls and copy only runtime inputs, never history."""
    from .db import append_run, get_run, update_run
    from .models.task import TaskDefinition
    validate_id(run_id)
    inputs_root = inputs_root or root
    task = TaskDefinition.from_yaml(task_dir / "task.yaml")
    validate_id(task.task_id)
    if get_run(run_id):
        raise FileExistsError(f"运行 {run_id} 已存在，请使用新的 ID")
    run_dir = root / "traces" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    inputs = run_dir / "inputs"
    target = inputs / "tasks" / task.task_id
    (target / "personas").mkdir(parents=True)
    for name in ("task.yaml", "rubrics.yaml", "flow.yaml", "sampling.yaml", "grader.py"):
        if (task_dir / name).is_file():
            shutil.copyfile(task_dir / name, target / name)
    for path in sorted((task_dir / "personas").glob("*.yaml")):
        shutil.copyfile(path, target / "personas" / path.name)
    for directory in ("personalities", "configs"):
        (inputs / directory).mkdir()
    for path in sorted((inputs_root / "personalities").glob("*.yaml")):
        shutil.copyfile(path, inputs / "personalities" / path.name)
    for name in ("noise_profiles.yaml", "dimensions.yaml"):
        if (inputs_root / "configs" / name).is_file():
            shutil.copyfile(inputs_root / "configs" / name, inputs / "configs" / name)
    atomic_json(inputs / "models.json", _public_config(models))
    files = {str(p.relative_to(inputs)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(inputs.rglob("*")) if p.is_file()}
    versions = {}
    for package in ("litellm", "pydantic", "pyyaml", "jinja2"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not installed"
    manifest = {
        "schema_version": 1, "run_id": run_id, "task_id": task.task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "params": params, "files": files, "input_hash": digest(files),
        "python": sys.version.split()[0], "dependencies": versions,
        "engine_hash": digest({str(p.relative_to(root / "src")): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted((root / "src" / "claw_eval").rglob("*.py"))}),
        "grading_hash": grading_fingerprint(root),
    }
    atomic_json(run_dir / "manifest.json", manifest)
    append_run(run_id, task.task_id, {**params, "input_hash": manifest["input_hash"]},
               agent_version=manifest["input_hash"][:12])
    update_run(run_id, status="prepared")
    return manifest


def prepare_replay(root: Path, source_id: str, run_id: str) -> dict:
    """Reuse frozen inputs and exact resolved personas under a new run ID."""
    source = load_manifest(root, source_id)
    if not source.get("cases_hash"):
        raise ValueError("源运行尚未生成用例清单，不能复跑")
    source_dir = root / "traces" / source_id
    inputs = source_dir / "inputs"
    manifest = prepare_run(root, inputs / "tasks" / source["task_id"], run_id,
                           json.loads((inputs / "models.json").read_text(encoding="utf-8")),
                           {**source["params"], "label": run_id}, inputs_root=inputs)
    manifest.update(replay_of=source_id, replay_cases_hash=source["cases_hash"])
    shutil.copyfile(source_dir / "cases.json", root / "traces" / run_id / "replay_cases.json")
    atomic_json(root / "traces" / run_id / "manifest.json", manifest)
    return manifest


def load_manifest(root: Path, run_id: str) -> dict:
    validate_id(run_id)
    run_dir = root / "traces" / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("run_id") != run_id:
        raise ValueError("运行清单版本或 ID 不匹配")
    validate_id(manifest["task_id"])
    inputs = (run_dir / "inputs").resolve()
    actual = {str(p.relative_to(inputs)) for p in inputs.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    if actual != set(manifest["files"]):
        raise ValueError("输入快照的文件集合已改变")
    for name, expected in manifest["files"].items():
        path = (inputs / name).resolve()
        if not path.is_relative_to(inputs) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"输入快照校验失败: {name}")
    if digest(manifest["files"]) != manifest["input_hash"]:
        raise ValueError("输入快照清单校验失败")
    if manifest.get("cases_hash"):
        cases = json.loads((run_dir / "cases.json").read_text(encoding="utf-8"))
        if digest(cases) != manifest["cases_hash"]:
            raise ValueError("运行用例清单校验失败")
    return manifest


def prepare_run(root: Path, task_dir: Path, run_id: str, models: dict, params: dict,
                *, inputs_root: Path | None = None) -> dict:
    from .task_gen.config_store import task_lock
    from .validator import require_valid_task
    from contextlib import nullcontext
    with (nullcontext() if inputs_root is not None else task_lock(task_dir)):
        require_valid_task(task_dir, root=inputs_root or root)
        return _prepare_run_unlocked(root, task_dir, run_id, models, params, inputs_root=inputs_root)


def prepare_candidate(root: Path, source_id: str, run_id: str, prompt: str) -> dict:
    """Change only the SUT prompt; freeze the benchmark cases and grading configuration."""
    from tempfile import TemporaryDirectory
    import yaml
    source = load_manifest(root, source_id)
    if not source.get("cases_hash"):
        raise ValueError("基准运行没有完整用例清单")
    if source.get("grading_hash") != grading_fingerprint(root):
        raise ValueError(f"基准评分引擎版本不同或缺少版本记录，请先重建基准：claw-eval replay --run-id {source_id} --label 新基准ID")
    from .report.aggregate import load_results_dir
    results = load_results_dir(root / 'traces' / source_id)
    if not results or any(r.status != 'complete' for r in results):
        raise ValueError("基准必须完成全部用例评分，异常或未评分的运行不能作为回归基准")
    source_dir = root / "traces" / source_id
    inputs = source_dir / "inputs"
    with TemporaryDirectory() as tmp:
        staged = Path(tmp) / source["task_id"]
        shutil.copytree(inputs / "tasks" / source["task_id"], staged)
        task_file = staged / "task.yaml"
        task = yaml.safe_load(task_file.read_text(encoding="utf-8"))
        if not prompt.strip():
            raise ValueError("候选 Prompt 不能为空")
        task["prompt"] = prompt
        task_file.write_text(yaml.safe_dump(task, allow_unicode=True, sort_keys=False), encoding="utf-8")
        manifest = prepare_run(root, staged, run_id,
                               json.loads((inputs / "models.json").read_text(encoding="utf-8")),
                               {**source["params"], "label": run_id}, inputs_root=inputs)
    manifest.update(candidate_of=source_id, replay_of=source_id, replay_cases_hash=source["cases_hash"])
    shutil.copyfile(source_dir / "cases.json", root / "traces" / run_id / "replay_cases.json")
    atomic_json(root / "traces" / run_id / "manifest.json", manifest)
    return manifest
