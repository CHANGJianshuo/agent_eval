"""控制台多页共享工具。"""
from __future__ import annotations

from pathlib import Path

# 仓库根目录 = .../src/claw_eval/editor/_utils.py 的 parents[3]
ROOT = Path(__file__).resolve().parents[3]
TASKS_DIR = ROOT / "tasks"
PERSONALITIES_DIR = ROOT / "personalities"
NOISE_FILE = ROOT / "configs" / "noise_profiles.yaml"
REPORTS_DIR = ROOT / "reports"
TRACES_DIR = ROOT / "traces"


def list_tasks() -> list[str]:
    if not TASKS_DIR.exists():
        return []
    return sorted(d.name for d in TASKS_DIR.iterdir() if d.is_dir())


def list_personas(task: str) -> list[str]:
    pd = TASKS_DIR / task / "personas"
    if not pd.exists():
        return []
    return sorted(p.stem for p in pd.glob("*.yaml"))


def list_personalities() -> list[str]:
    if not PERSONALITIES_DIR.exists():
        return []
    return sorted(p.stem for p in PERSONALITIES_DIR.glob("*.yaml"))


def list_runs() -> list[str]:
    """返回 traces/ 下所有 run_id 子目录。"""
    if not TRACES_DIR.exists():
        return []
    return sorted(d.name for d in TRACES_DIR.iterdir() if d.is_dir())
