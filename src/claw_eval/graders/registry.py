"""动态加载 tasks/<id>/grader.py 里的 AbstractGrader 子类。

无中央注册表 —— 纯靠约定:每个任务目录有一个 grader.py,里面有一个
AbstractGrader 子类。加新任务 = 新建目录,不改任何中央代码。
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from .base import AbstractGrader


def get_grader(task_dir: str | Path) -> AbstractGrader:
    """从 <task_dir>/grader.py 动态加载并实例化评分器。"""
    grader_path = Path(task_dir) / "grader.py"
    if not grader_path.exists():
        raise FileNotFoundError(f"未找到评分器:{grader_path}")

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
