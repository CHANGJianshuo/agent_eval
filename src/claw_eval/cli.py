"""CLI —— claw-eval run / grade / batch / report / dashboard。"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import typer
import yaml

from .graders import scoring
from .graders.llm_judge import LLMJudge
from .graders.registry import get_grader
from .models.persona import load_persona
from .models.rubric import load_rubrics
from .models.task import TaskDefinition
from .runner import llm_client
from .runner.dialogue_loop import run_dialogue
from .runner.trace_io import load_trace

app = typer.Typer(add_completion=False, help="对话模型指令遵循自动评测系统")

_ROOT = Path(__file__).resolve().parents[2]      # 仓库根目录
_LOG_LOCK = threading.Lock()                     # 并发输出避免完全错行


# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------

def _load_models_cfg(path: str | None) -> dict:
    cfg_path = Path(path) if path else _ROOT / "configs" / "models.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _task_dir(task: str) -> Path:
    p = Path(task)
    return p if p.is_dir() else _ROOT / "tasks" / task


def _configure_provider(cfg: dict) -> None:
    """按 models.yaml 的 provider 段配置 LLM 网关(OpenAI 兼容)。"""
    prov = cfg.get("provider")
    if not prov:
        return
    base_url = prov.get("base_url")
    key_env = prov.get("api_key_env", "")
    api_key = os.environ.get(key_env) if key_env else None
    if base_url and not api_key:
        typer.echo(f"[warning] 环境变量 {key_env} 未设置,LLM 调用会失败。"
                   f"请先 export {key_env}=<你的 API key>")
    llm_client.configure(api_base=base_url, api_key=api_key)


def _make_judge(cfg: dict) -> LLMJudge:
    return LLMJudge(
        cfg["judge"]["model"],
        cfg["judge"].get("temperature", 0.0),
        cfg["judge"].get("reasoning_effort"),
    )


def _grade_trace(trace_path, task: TaskDefinition, rubrics, judge):
    _start, messages, _end = load_trace(trace_path)
    grader = get_grader(task.task_dir)
    result = grader.grade(messages, task, rubrics, judge)
    result.trace_path = str(trace_path)
    return result


def _echo_result(result, prefix: str = "") -> None:
    d = result.dimension_scores
    with _LOG_LOCK:
        typer.echo(f"{prefix}completion={d.completion}  robustness={d.robustness}  "
                   f"safety={d.safety}")
        for v in result.violations:
            typer.echo(f"{prefix}⚠ [{v.rubric_id}] 第{v.turn}轮 {v.detail}")


def _run_one_trial(task_dir: Path, task_def: TaskDefinition, rubrics,
                   persona_name: str, trial_idx: int, total_trials: int,
                   cfg: dict, judge, run_id: str) -> float:
    """跑一个 trial(线程安全:每个 trial 各自的 trace 路径)。

    输出落到 traces/<run_id>/ 子目录,便于按 run 分组回归对比。
    """
    persona_obj = load_persona(
        task_dir / "personas" / f"{persona_name}.yaml",
        personalities_dir=_ROOT / "personalities",
        noise_file=_ROOT / "configs" / "noise_profiles.yaml",
    )
    trace_path = (
        _ROOT / "traces" / run_id
        / f"{task_def.task_id}_{persona_name}_t{trial_idx + 1}.jsonl"
    )
    run_dialogue(
        task_def, persona_obj,
        sut_model=cfg["sut"]["model"],
        simulator_model=cfg["simulator"]["model"],
        trace_path=trace_path,
        sut_temperature=cfg["sut"].get("temperature", 0.7),
        simulator_temperature=cfg["simulator"].get("temperature", 0.7),
        sut_reasoning_effort=cfg["sut"].get("reasoning_effort"),
        simulator_reasoning_effort=cfg["simulator"].get("reasoning_effort"),
        simulator_seed=trial_idx + 1,    # 噪音掷骰种子,每 trial 不同但可复现
    )
    result = _grade_trace(trace_path, task_def, rubrics, judge)
    result.persona_id = persona_obj.id

    out_path = trace_path.with_suffix(".result.json")
    out_path.write_text(
        json.dumps(result.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    with _LOG_LOCK:
        typer.echo(f"  ✓ [{persona_name} t{trial_idx + 1}/{total_trials}] "
                   f"task_score={result.task_score} passed={result.passed}")
    return result.task_score


# ------------------------------------------------------------------
# 命令
# ------------------------------------------------------------------

@app.command()
def run(task: str = typer.Option(..., help="任务 id 或目录"),
        persona: str = typer.Option(..., help="persona 名"),
        trials: int = typer.Option(1, help="采样次数(Pass^k)"),
        config: str = typer.Option(None, help="模型配置 yaml"),
        no_judge: bool = typer.Option(False),
        label: str = typer.Option(
            "", help="run_id 标签;默认时间戳。用于回归对比")):
    """跑「单任务 × 单 persona × N trials」,出 trace + result + 单 case HTML。"""
    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    rubrics = load_rubrics(task_dir / "rubrics.yaml")
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)
    judge = None if no_judge else _make_judge(cfg)

    run_id = label or datetime.now().strftime("%Y%m%d_%H%M%S")
    typer.echo(f"\n=== {task_def.task_id} × {persona} "
               f"(trials={trials}, run_id={run_id}) ===")
    scores = [
        _run_one_trial(task_dir, task_def, rubrics, persona, i, trials,
                       cfg, judge, run_id)
        for i in range(trials)
    ]

    if trials > 1:
        ph = scoring.compute_pass_hat_k(scores, k=trials)
        typer.echo(f"\nPass^{trials} = {ph}   (task_scores={scores})")


@app.command()
def batch(task: str = typer.Option(..., help="任务 id 或目录"),
          personas: str = typer.Option(
              "", help="逗号分隔的 persona;留空 = 全部"),
          trials: int = typer.Option(1, help="每 persona 跑几次(uniform 模式)"),
          total: int = typer.Option(
              0, help="≥1 时按 sampling.yaml 比例分配 total 个 trial(否则用 --trials uniform)"),
          config: str = typer.Option(None),
          no_judge: bool = typer.Option(False),
          concurrency: int = typer.Option(
              0, help="并发对话数;0 = 用配置文件的默认"),
          dashboard_out: bool = typer.Option(True),
          label: str = typer.Option(
              "", help="run_id 标签(回归对比用);默认时间戳")):
    """跑「多 persona × N trials」,case 间并行;跑完自动出 dashboard。

    两种模式:
      uniform:   `--trials 3` —— 每个 persona 跑 3 次
      比例分配:  `--total 100` —— 按 sampling.yaml 权重把 100 个 trial 分给各 persona
    """
    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    rubrics = load_rubrics(task_dir / "rubrics.yaml")
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)
    judge = None if no_judge else _make_judge(cfg)

    n_workers = concurrency or int(cfg.get("concurrency", 4))

    if total > 0:
        # 比例分配模式
        from .sampling import allocate, load_sampling
        samp_file = task_dir / "sampling.yaml"
        if not samp_file.exists():
            typer.echo(f"[error] --total 模式需要 {samp_file}")
            raise typer.Exit(1)
        weights = load_sampling(samp_file).weights
        if personas:
            picked = {p.strip() for p in personas.split(",") if p.strip()}
            weights = {k: v for k, v in weights.items() if k in picked}
        alloc = {k: n for k, n in allocate(weights, total).items() if n > 0}
        pairs = [(name, i) for name, n in alloc.items() for i in range(n)]
        dist = ", ".join(f"{k}×{v}" for k, v in alloc.items())
        typer.echo(f"[batch · 比例分配] {task_def.task_id}: total={total} → "
                   f"{dist},并发 {n_workers}")
    else:
        # uniform 模式
        if personas:
            names = [p.strip() for p in personas.split(",") if p.strip()]
        else:
            names = sorted(p.stem for p in (task_dir / "personas").glob("*.yaml"))
        pairs = [(name, i) for name in names for i in range(trials)]
        typer.echo(f"[batch · uniform] {task_def.task_id}: {len(names)} persona × "
                   f"{trials} trials = {len(pairs)} 次,并发 {n_workers}")

    run_id = label or datetime.now().strftime("%Y%m%d_%H%M%S")
    typer.echo(f"  run_id = {run_id} → traces/{run_id}/")
    by_persona: dict[str, list[float]] = {name: [] for name in names}

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {
            ex.submit(_run_one_trial, task_dir, task_def, rubrics,
                      name, i, trials, cfg, judge, run_id): (name, i)
            for name, i in pairs
        }
        done = 0
        for fut in as_completed(futures):
            name, idx = futures[fut]
            done += 1
            try:
                score = fut.result()
                by_persona[name].append(score)
            except Exception as exc:  # noqa: BLE001
                with _LOG_LOCK:
                    typer.echo(f"  ✗ [{name} t{idx + 1}] 失败: {exc}")
            with _LOG_LOCK:
                typer.echo(f"      ({done}/{len(pairs)} 完成)")

    typer.echo("\n[batch] 汇总:")
    for name, scs in by_persona.items():
        if len(scs) > 1:
            ph = scoring.compute_pass_hat_k(scs, k=len(scs))
            typer.echo(f"  {name:>20}: scores={[round(s,3) for s in scs]}  "
                       f"Pass^{len(scs)}={ph}")
        else:
            typer.echo(f"  {name:>20}: scores={[round(s,3) for s in scs]}")

    if dashboard_out:
        typer.echo("\n[batch] 生成 dashboard…")
        try:
            from .report.builder import build_dashboard_from_dir
            out = build_dashboard_from_dir(_ROOT / "traces", _ROOT / "reports")
            typer.echo(f"Dashboard: {out}")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  [warn] dashboard 生成失败: {exc}")


@app.command()
def regression(task: str = typer.Option(..., help="任务 id 或目录"),
               old: str = typer.Option(..., help="旧版本 run_id(traces/ 子目录名)"),
               new: str = typer.Option(..., help="新版本 run_id"),
               threshold: float = typer.Option(0.05, help="显著性阈值"),
               out: str = typer.Option("", help="JSON 输出路径(默认 reports/)")):
    """对比同任务两次 run 的结果差异(rubric / persona / 维度三层 diff)。"""
    from .report.aggregate import load_results_dir
    from .report.regression import (
        compute_regression, format_regression_terminal, save_regression,
    )

    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")

    def _resolve(name: str) -> Path:
        p = Path(name)
        return p if p.is_dir() else _ROOT / "traces" / name

    old_dir = _resolve(old)
    new_dir = _resolve(new)
    if not old_dir.exists():
        typer.echo(f"[error] old run 不存在: {old_dir}")
        raise typer.Exit(1)
    if not new_dir.exists():
        typer.echo(f"[error] new run 不存在: {new_dir}")
        raise typer.Exit(1)

    old_results = load_results_dir(old_dir)
    new_results = load_results_dir(new_dir)

    rep = compute_regression(
        old_results, new_results, task_def.task_id,
        old_label=old, new_label=new, threshold=threshold)

    typer.echo(format_regression_terminal(rep))

    out_path = (Path(out) if out
                else _ROOT / "reports" / f"regression_{task_def.task_id}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_regression(rep, out_path)
    typer.echo(f"\nJSON → {out_path}")


@app.command()
def grade(trace: str = typer.Option(..., help="trace.jsonl 路径"),
          task: str = typer.Option(...),
          config: str = typer.Option(None),
          no_judge: bool = typer.Option(False)):
    """对已有 trace 重新评分。"""
    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    rubrics = load_rubrics(task_dir / "rubrics.yaml")
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)
    judge = None if no_judge else _make_judge(cfg)

    result = _grade_trace(Path(trace), task_def, rubrics, judge)
    typer.echo(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


@app.command()
def report(result: str = typer.Option(...)):
    """根据 result.json 渲染单 case HTML 报告。"""
    from .report.builder import build_case_report, load_result
    res = load_result(result)
    out = (_ROOT / "reports"
           / f"{Path(result).stem.replace('.result', '')}.html")
    p = build_case_report(res, out)
    typer.echo(f"报告: {p}")


@app.command()
def dashboard(traces_dir: str = typer.Option(None),
              out_dir: str = typer.Option(None)):
    """从 traces_dir 收集所有 result.json → 生成可视化 dashboard 网页。"""
    from .report.builder import build_dashboard_from_dir
    td = Path(traces_dir) if traces_dir else _ROOT / "traces"
    od = Path(out_dir) if out_dir else _ROOT / "reports"
    out = build_dashboard_from_dir(td, od)
    typer.echo(f"Dashboard: {out}")


@app.command()
def recommend(task: str = typer.Option(..., help="任务 id 或目录"),
              top: int = typer.Option(5, help="给出最弱的 N 条建议"),
              config: str = typer.Option(None),
              no_judge: bool = typer.Option(
                  False, help="只产聚合数据,不调 LLM 生成修改建议")):
    """分析评测结果产改进建议:找最弱 rubric + LLM 给「改 Prompt 哪几句」。

    输出 reports/recommendations_<task_id>.json,下次 dashboard 会自动显示。
    """
    from .models.rubric import load_rubrics
    from .report.aggregate import load_results_dir
    from .report.recommend import build_recommendations, save_recommendations

    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    rubrics = load_rubrics(task_dir / "rubrics.yaml")
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)

    results = [r for r in load_results_dir(_ROOT / "traces")
               if r.task_id == task_def.task_id]
    if not results:
        typer.echo(f"[error] traces/ 下没有 {task_def.task_id} 的 result.json")
        typer.echo(f"  请先跑 claw-eval batch --task {task} 产生评测结果")
        raise typer.Exit(1)

    judge_model = None if no_judge else cfg["judge"]["model"]
    typer.echo(f"分析 {len(results)} 个 result,找最弱 rubric…")
    if judge_model:
        typer.echo(f"将用 {judge_model} 生成可执行建议(可能 30-60s)")
    recs = build_recommendations(
        task_def, results, rubrics,
        judge_model=judge_model, top_n=top,
        reasoning_effort=cfg["judge"].get("reasoning_effort", "medium"))

    if not recs:
        typer.echo("✓ 没有明显弱的 rubric(全部 ≥0.8 或触发次数 <3)")
        return

    out_path = _ROOT / "reports" / f"recommendations_{task_def.task_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_recommendations(task_def.task_id, recs, out_path)
    typer.echo(f"\n✓ {len(recs)} 条建议 → {out_path.name}")
    for i, r in enumerate(recs, 1):
        typer.echo(f"  [{i}] {r['rubric_id']:<32} "
                   f"avg={r['avg_score']:.2f}  severity={r['severity']:.2f}")
    typer.echo(f"\n  下一步:claw-eval dashboard 重生成报告,顶部会显示建议")


@app.command("extract-rubric")
def extract_rubric_cmd(
        task: str = typer.Option(..., help="任务 id 或目录"),
        out: str = typer.Option("rubrics.draft.yaml",
                                help="草稿输出文件(相对 task 目录)"),
        config: str = typer.Option(None)):
    """从任务 Prompt 自动产 rubric YAML 草稿(LLM 抽取,带 category/confidence)。

    流程:extract-rubric → 写 rubrics.draft.yaml → claw-eval review 逐条人审转正。
    """
    from .models.rubric import save_rubrics
    from .rubric.extractor import extract_rubrics

    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)
    judge_model = cfg["judge"]["model"]

    typer.echo(f"调 LLM 抽取 rubric({judge_model})…")
    rubrics = extract_rubrics(
        task_def, judge_model,
        reasoning_effort=cfg["judge"].get("reasoning_effort", "medium"))

    out_path = task_dir / out
    save_rubrics(rubrics, out_path, include_meta=True)
    typer.echo(f"✓ 写出 {len(rubrics)} 条草稿 → {out_path}")
    typer.echo(f"  下一步:claw-eval review --task {task}")


@app.command()
def review(task: str = typer.Option(..., help="任务 id 或目录"),
           draft: str = typer.Option("rubrics.draft.yaml",
                                     help="草稿文件(相对 task 目录)")):
    """逐条人审 rubric 草稿,通过后转正为 rubrics.yaml。

    safety 类必须显式 accept/reject,不可跳过。
    """
    from .models.rubric import load_rubrics, save_rubrics
    from .rubric.reviewer import (
        apply_decisions, gate_blocked, interactive_review, summarize_state,
    )

    task_dir = _task_dir(task)
    draft_path = task_dir / draft
    if not draft_path.exists():
        typer.echo(f"[error] 草稿不存在:{draft_path}")
        typer.echo(f"  请先运行:claw-eval extract-rubric --task {task}")
        raise typer.Exit(1)

    drafts = load_rubrics(draft_path)
    typer.echo(f"=== 审核 {draft_path}({len(drafts)} 条)===")

    state = interactive_review(drafts)

    blocked = gate_blocked(state)
    if blocked:
        typer.echo(f"\n✗ 仍有 safety 类未审:{blocked}")
        raise typer.Exit(1)
    typer.echo(f"\n汇总:{summarize_state(state)}")

    final = apply_decisions(state)
    confirm = typer.prompt(f"\n将写入 {len(final)} 条到 rubrics.yaml,确认? (y/n)",
                           default="y").strip().lower()
    if confirm != "y":
        typer.echo("已取消")
        raise typer.Exit(0)

    target = task_dir / "rubrics.yaml"
    if target.exists():
        backup = task_dir / "rubrics.yaml.bak"
        target.replace(backup)
        typer.echo(f"  旧 rubrics.yaml 备份到 {backup}")
    save_rubrics(final, target, include_meta=False)
    typer.echo(f"✓ 已写入 {target}")


@app.command("extract-personas")
def extract_personas_cmd(
        task: str = typer.Option(..., help="任务 id 或目录"),
        out_dir: str = typer.Option("personas_draft",
                                    help="输出目录(相对 task 目录)"),
        config: str = typer.Option(None)):
    """从任务 Prompt 自动产 persona 剧本草稿(LLM 推荐 5-8 个 persona)。

    保存到 tasks/<task>/personas_draft/<id>.yaml,人工挑选后复制 / 编辑后写入 personas/。
    """
    from .user_simulator.extractor import extract_personas, save_persona_script

    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)

    typer.echo(f"调 LLM 抽取 persona({cfg['judge']['model']})…")
    scripts = extract_personas(
        task_def, cfg["judge"]["model"], _ROOT / "personalities",
        reasoning_effort=cfg["judge"].get("reasoning_effort", "medium"))

    out_path = task_dir / out_dir
    out_path.mkdir(parents=True, exist_ok=True)
    for s in scripts:
        save_persona_script(s, out_path / f"{s.id}.yaml")
    typer.echo(f"✓ 写出 {len(scripts)} 个 persona 草稿 → {out_path}/")
    typer.echo("  人工挑选后,把要的复制 / 编辑后写到 tasks/<task>/personas/")


@app.command()
def pipeline(task: str = typer.Option(..., help="任务 id 或目录"),
             from_step: int = typer.Option(1, "--from",
                                            help="从第几步开始(1-6)"),
             total: int = typer.Option(30,
                                        help="第 5 步 batch 跑多少 trial"),
             config: str = typer.Option(None),
             no_judge: bool = typer.Option(False)):
    """全流程编排:6 步显式 pipeline,关键节点人审 gate,不黑盒。

    1. extract-rubric    → tasks/<task>/rubrics.draft.yaml
    2. extract-personas  → tasks/<task>/personas_draft/*.yaml
    3. validate          → 一致性检查(命名/safety/触发可达)
    4. review            → 终端逐条审 rubric 草稿(safety 必审),转正 rubrics.yaml
    5. batch --total N   → 比例分配跑评测
    6. dashboard         → 出多页可视化

    任一步失败,修后 `--from <step>` 续跑。
    """
    from .models.rubric import load_rubrics, save_rubrics
    from .report.builder import build_dashboard_from_dir
    from .rubric.extractor import extract_rubrics
    from .rubric.reviewer import (
        apply_decisions, gate_blocked, interactive_review, summarize_state,
    )
    from .user_simulator.extractor import (
        extract_personas, save_persona_script,
    )
    from .validator import validate_task

    task_dir = _task_dir(task)
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)

    steps = ["extract-rubric", "extract-personas", "validate",
             "review", "batch", "dashboard"]

    typer.echo(f"\n═════ claw-eval pipeline · {task} ═════")
    typer.echo("6 步:" + " → ".join(steps))
    typer.echo(f"从第 {from_step} 步开始")

    def hdr(i: int, name: str) -> None:
        typer.echo(f"\n──── [{i}/6] {name} ────")

    # 1. extract-rubric
    if from_step <= 1:
        hdr(1, "extract-rubric(LLM 抽 rubric → 草稿)")
        task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
        rubrics = extract_rubrics(
            task_def, cfg["judge"]["model"],
            reasoning_effort=cfg["judge"].get("reasoning_effort", "medium"))
        save_rubrics(rubrics, task_dir / "rubrics.draft.yaml",
                     include_meta=True)
        typer.echo(f"✓ {len(rubrics)} 条 → rubrics.draft.yaml")

    # 2. extract-personas
    if from_step <= 2:
        hdr(2, "extract-personas(LLM 推荐 persona 剧本 → 草稿)")
        task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
        scripts = extract_personas(
            task_def, cfg["judge"]["model"], _ROOT / "personalities",
            reasoning_effort=cfg["judge"].get("reasoning_effort", "medium"))
        out = task_dir / "personas_draft"
        out.mkdir(parents=True, exist_ok=True)
        for s in scripts:
            save_persona_script(s, out / f"{s.id}.yaml")
        typer.echo(f"✓ {len(scripts)} 个 → personas_draft/")

    # 3. validate
    if from_step <= 3:
        hdr(3, "validate(命名 / safety / 触发可达性 / 状态机终止)")
        rep = validate_task(
            task_dir,
            personalities_dir=_ROOT / "personalities",
            noise_file=_ROOT / "configs" / "noise_profiles.yaml",
            sampling_file=task_dir / "sampling.yaml",
        )
        icons = {"error": "✗", "warning": "⚠", "info": "·"}
        for it in rep.issues:
            typer.echo(f"  {icons[it.level]} [{it.code}] {it.message}")
        if not rep.ok:
            typer.echo(f"✗ validate 有 {len(rep.errors)} 错误,修复后用 --from 3 续跑")
            raise typer.Exit(1)
        typer.echo("✓ 通过")

    # 4. review (rubric 人审 gate)
    if from_step <= 4:
        hdr(4, "review(逐条人审 rubric 草稿,safety 必审,通过后转正)")
        draft = task_dir / "rubrics.draft.yaml"
        if not draft.exists():
            typer.echo(f"✗ 草稿不存在 {draft},--from 1 重抽")
            raise typer.Exit(1)
        drafts = load_rubrics(draft)
        state = interactive_review(drafts)
        blocked = gate_blocked(state)
        if blocked:
            typer.echo(f"✗ safety 类未审:{blocked}")
            raise typer.Exit(1)
        final = apply_decisions(state)
        typer.echo(f"\n{summarize_state(state)},将写 {len(final)} 条")
        target = task_dir / "rubrics.yaml"
        if target.exists():
            target.replace(task_dir / "rubrics.yaml.bak")
            typer.echo("  旧 rubrics.yaml → rubrics.yaml.bak")
        save_rubrics(final, target, include_meta=False)
        typer.echo(f"✓ → {target}")

    # 5. batch
    if from_step <= 5:
        hdr(5, f"batch(比例分配,total={total})")
        # 复用 batch 命令(直接调函数会有 typer 装饰问题,这里用 subprocess 干净)
        import subprocess
        import sys
        cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
               "--task", task, "--total", str(total),
               "--no-dashboard-out"]
        if no_judge:
            cmd.append("--no-judge")
        if config:
            cmd += ["--config", config]
        result = subprocess.run(cmd, env={**os.environ, "PYTHONPATH": "src"})
        if result.returncode != 0:
            typer.echo("✗ batch 失败,--from 5 续跑")
            raise typer.Exit(1)

    # 6. dashboard
    if from_step <= 6:
        hdr(6, "dashboard(出多页可视化)")
        out = build_dashboard_from_dir(_ROOT / "traces", _ROOT / "reports")
        typer.echo(f"✓ → {out}")

    typer.echo(f"\n═════ pipeline 完成 ═════")


@app.command()
def editor(port: int = typer.Option(8501, help="Streamlit 端口")):
    """启动 Persona 编辑器(Streamlit 网页)—— 选性格、画状态机、配 noise、保存 YAML。

    需要先装 ui 依赖:`pip install -e ".[ui]"`(streamlit + pandas)。
    """
    import subprocess
    import sys
    app_file = Path(__file__).parent / "editor" / "app.py"
    if not app_file.exists():
        typer.echo(f"[error] 编辑器入口不存在:{app_file}")
        raise typer.Exit(1)
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_file),
           "--server.port", str(port), "--server.headless", "true"]
    typer.echo(f"启动 Persona 编辑器:http://localhost:{port}")
    typer.echo(f"(Ctrl+C 停止)")
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        typer.echo("[error] streamlit 未安装。执行:pip install -e '.[ui]'")
        raise typer.Exit(1)


@app.command()
def validate(task: str = typer.Option(..., help="任务 id 或目录")):
    """对任务做一致性检查 —— 命名 / safety 标记 / trigger 可达性 / 状态机终止 / sampling。"""
    from .validator import validate_task
    task_dir = _task_dir(task)
    rep = validate_task(
        task_dir,
        personalities_dir=_ROOT / "personalities",
        noise_file=_ROOT / "configs" / "noise_profiles.yaml",
        sampling_file=task_dir / "sampling.yaml",
    )
    typer.echo(f"\n=== 校验任务 {rep.task_id} ===")
    icons = {"error": "✗", "warning": "⚠", "info": "·"}
    for level in ("error", "warning", "info"):
        for it in [i for i in rep.issues if i.level == level]:
            typer.echo(f"  {icons[level]} [{it.code}] {it.message}")
    if rep.ok:
        if rep.warnings:
            typer.echo(f"\n✓ 通过({len(rep.warnings)} 警告,无错误)")
        else:
            typer.echo("\n✓ 通过 —— 无问题")
    else:
        typer.echo(f"\n✗ 校验失败:{len(rep.errors)} 错误")
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
