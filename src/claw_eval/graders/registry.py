"""默认使用共享 RubricGrader；任务可用 grader.py 提供自定义评分器。"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from .base import AbstractGrader
from .rubric import RubricGrader


def get_grader(task_dir: str | Path) -> AbstractGrader:
    """加载任务自定义评分器，无自定义文件时使用共享评分器。"""
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise FileNotFoundError(f"未找到任务目录:{task_dir}")
    grader_path = task_dir / "grader.py"
    if not grader_path.exists():
        return RubricGrader()

    module_name = f"task_grader_{Path(task_dir).name}"
    spec = importlib.util.spec_from_file_location(module_name, grader_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载评分器模块:{grader_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for _name, obj in inspect.getmembers(module, inspect.isclass):
        # 跳过 import 进来的基类,只取本文件定义的类
        if obj.__module__ != module.__name__:
            continue
        if issubclass(obj, AbstractGrader) and obj is not AbstractGrader:
            return obj()

    raise ValueError(f"{grader_path} 中未找到 AbstractGrader 子类")
