"""HTML 报告与可视化网页生成。

多页结构:
- index.html        —— 跨任务总览(任务级对比 + 链接到各任务详情页)
- task_<id>.html    —— 单任务完整分析(该任务 persona × 该任务 rubric)
- cases/<x>.html    —— 单 case 报告(对话回放 + 违规高亮 + 维度雷达)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from ..models.trace import GradingResult, TraceMessage
from ..runner.trace_io import load_trace
from .aggregate import aggregate, load_results_dir

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def load_result(path: str | Path) -> GradingResult:
    """从 .result.json 加载 GradingResult。"""
    with open(path, encoding="utf-8") as f:
        return GradingResult.model_validate(json.load(f))


def _build_turn_view(messages: list[TraceMessage], result: GradingResult):
    """逐轮拼出展示用结构,带违规高亮。"""
    vmap: dict[int, list] = {}
    for v in result.violations:
        if v.turn is not None:
            vmap.setdefault(v.turn, []).append(v)
    return [{
        "turn": m.turn, "role": m.role, "text": m.text, "state": m.state,
        "is_probe": m.is_probe, "violations": vmap.get(m.turn, []),
    } for m in messages]


def build_case_report(result: GradingResult, out_path: str | Path) -> Path:
    """渲染单 case HTML 报告。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    messages: list[TraceMessage] = []
    if result.trace_path and Path(result.trace_path).exists():
        _start, messages, _end = load_trace(result.trace_path)

    html = _env().get_template("case_report.html.j2").render(
        result=result,
        dim=result.dimension_scores,
        turns=_build_turn_view(messages, result),
        triggered=[r for r in result.rubric_scores if r.triggered],
        skipped=[r for r in result.rubric_scores if not r.triggered],
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path


def build_dashboard(results: list[GradingResult], out_dir: str | Path,
                    task_names: dict[str, str] | None = None) -> Path:
    """按任务分组渲染:index 总览 + 每任务详情页 + 各单 case 报告。

    返回 index.html 路径。
    """
    out_dir = Path(out_dir)
    cases_dir = out_dir / "cases"
    # cases/ 是全量重建的产物目录,先清空避免旧命名残留
    if cases_dir.exists():
        shutil.rmtree(cases_dir)
    cases_dir.mkdir(parents=True, exist_ok=True)
    task_names = task_names or {}
    env = _env()

    # 按 task_id 分组
    groups: dict[str, list[GradingResult]] = {}
    for r in results:
        groups.setdefault(r.task_id, []).append(r)

    tasks_meta: list[dict] = []
    for task_id in sorted(groups):
        task_results = groups[task_id]
        summary = aggregate(task_results)

        # 每条 result 出单 case 报告,并把相对链接挂到 summary.runs 上
        for idx, (run, r) in enumerate(zip(summary.runs, task_results)):
            stem = f"{task_id}_{r.persona_id or 'unknown'}_{idx + 1:02d}"
            build_case_report(r, cases_dir / f"{stem}.html")
            run["report_link"] = f"cases/{stem}.html"

        page_file = f"task_{task_id}.html"
        html = env.get_template("task_page.html.j2").render(
            task_id=task_id,
            task_name=task_names.get(task_id, task_id),
            summary=summary,
        )
        (out_dir / page_file).write_text(html, encoding="utf-8")

        tasks_meta.append({
            "task_id": task_id,
            "name": task_names.get(task_id, task_id),
            "page": page_file,
            "summary": summary,
        })

    index_html = env.get_template("index.html.j2").render(
        tasks=tasks_meta,
        total_runs=len(results),
        task_count=len(groups),
    )
    out_path = out_dir / "index.html"
    out_path.write_text(index_html, encoding="utf-8")
    return out_path


def build_dashboard_from_dir(traces_dir: str | Path,
                             out_dir: str | Path) -> Path:
    """便捷入口:从 traces_dir 收集所有 result.json,生成多页 dashboard。

    顺带从 <repo>/tasks/<id>/task.yaml 取任务中文名。
    """
    results = load_results_dir(traces_dir)

    task_names: dict[str, str] = {}
    tasks_root = Path(traces_dir).resolve().parent / "tasks"
    if tasks_root.is_dir():
        for tdir in sorted(tasks_root.iterdir()):
            ty = tdir / "task.yaml"
            if ty.exists():
                try:
                    data = yaml.safe_load(ty.read_text(encoding="utf-8"))
                    task_names[data.get("task_id", tdir.name)] = \
                        data.get("task_name", tdir.name)
                except Exception:  # noqa: BLE001
                    pass

    return build_dashboard(results, out_dir, task_names)
