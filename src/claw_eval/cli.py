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
                   cfg: dict, judge, run_id: str,
                   noise_overlay_kinds: list = None,
                   persona_override=None) -> float:
    """跑一个 trial(线程安全:每个 trial 各自的 trace 路径)。

    输出落到 traces/<run_id>/ 子目录,便于按 run 分组回归对比。

    noise_overlay_kinds: 若非 None,本 trial 为「噪音 case」,临时把 persona 的
                        noise_rate 强制为 1.0,kinds 用 overlay 配置(每轮必加噪)。
    persona_override:   若非 None,直接用这个 Persona 对象(--dimensions 模式
                        从 persona_factory 生成的实例),persona_name 仍用 id。
    """
    if persona_override is not None:
        persona_obj = persona_override
    else:
        persona_obj = load_persona(
            task_dir / "personas" / f"{persona_name}.yaml",
            personalities_dir=_ROOT / "personalities",
            noise_file=_ROOT / "configs" / "noise_profiles.yaml",
        )
    if noise_overlay_kinds:
        # 这通是「噪音 case」:全程必噪(rate=1.0),kinds 用 overlay 指定的
        from .models.persona import load_noise_kinds
        all_kinds = load_noise_kinds(_ROOT / "configs" / "noise_profiles.yaml")
        chosen = [all_kinds[k] for k in noise_overlay_kinds if k in all_kinds]
        persona_obj = persona_obj.model_copy(update={
            "noise_rate": 1.0,
            "noise_kinds": chosen,
        })
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
    # 记录该 case 实际的 demographics(若有)
    try:
        result.demographics = persona_obj.demographics.model_dump()
    except Exception:
        pass

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
              "", help="run_id 标签(回归对比用);默认时间戳"),
          weights: str = typer.Option(
              "", help='JSON 字典 override sampling.yaml weights,如 \'{"cooperative":50,"refuse":20}\''),
          dimensions: str = typer.Option(
              "", help='JSON 5 维度比例字典 → persona_factory 独立采样生成 persona。'
                      '例:\'{"attitude":{"cooperative":60,"refuse":40},'
                      '"age_range":{"30-39":100}}\'')):
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

    # ============== --dimensions 模式 ==============
    # 优先级最高:走 persona_factory 随机生成,不读 sampling.yaml
    generated_personas: list = []
    if dimensions:
        import json as _json
        try:
            dim_cfg = _json.loads(dimensions)
        except Exception as exc:
            typer.echo(f"[error] --dimensions 解析失败:{exc}")
            raise typer.Exit(1)
        if total <= 0:
            typer.echo("[error] --dimensions 必须配合 --total N")
            raise typer.Exit(1)
        from .persona_factory import generate_personas
        try:
            generated_personas = generate_personas(
                dim_cfg, task_dir, n=total,
                seed=abs(hash(label or "")) & 0xFFFFFFFF)
        except Exception as exc:
            typer.echo(f"[error] persona 生成失败:{exc}")
            raise typer.Exit(1)
        typer.echo(f"[batch · 维度采样] {task_def.task_id}: total={total} → "
                   f"{len(generated_personas)} 个生成 persona,并发 {n_workers}")
        # 把生成的 persona 作为 pairs,name 用 id,trial_idx 用 0
        names = list({p.id for p in generated_personas})  # 集合,可能重复
        pairs = [(p.id, i) for i, p in enumerate(generated_personas)]
        noise_overlay_for_idx: dict[int, list] = {}
    else:
        noise_overlay_for_idx = {}
    if dimensions:
        pass    # 上面已处理
    elif total > 0:
        # 比例分配模式
        from .sampling import allocate, load_sampling, select_noise_cases
        samp_file = task_dir / "sampling.yaml"
        if not samp_file.exists():
            typer.echo(f"[error] --total 模式需要 {samp_file}")
            raise typer.Exit(1)
        sampling_cfg = load_sampling(samp_file)
        # 优先用 --weights JSON override
        if weights:
            import json as _json
            try:
                _override = _json.loads(weights)
                if not isinstance(_override, dict):
                    raise ValueError("--weights 必须是 JSON 字典")
                weights_use = {k: float(v) for k, v in _override.items()}
                typer.echo(f"  --weights override: {weights_use}")
            except Exception as exc:
                typer.echo(f"[error] --weights 解析失败:{exc}")
                raise typer.Exit(1)
        else:
            weights_use = sampling_cfg.weights
        if personas:
            picked = {p.strip() for p in personas.split(",") if p.strip()}
            weights_use = {k: v for k, v in weights_use.items() if k in picked}
        alloc = {k: n for k, n in allocate(weights_use, total).items() if n > 0}
        names = list(alloc.keys())
        pairs = [(name, i) for name, n in alloc.items() for i in range(n)]
        dist = ", ".join(f"{k}×{v}" for k, v in alloc.items())
        typer.echo(f"[batch · 比例分配] {task_def.task_id}: total={total} → "
                   f"{dist},并发 {n_workers}")

        # 噪音 overlay:选出 round(N × rate) 个噪音 case
        ov = sampling_cfg.noise_overlay
        if ov.rate > 0 and ov.kinds:
            noise_idx_set = select_noise_cases(
                len(pairs), ov.rate,
                seed=abs(hash(label or "")) & 0xFFFFFFFF)
            for i in noise_idx_set:
                noise_overlay_for_idx[i] = ov.kinds
            typer.echo(f"  噪音 overlay: rate={ov.rate} kinds={ov.kinds} "
                       f"→ {len(noise_idx_set)}/{len(pairs)} 个噪音 case")
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

    # 写 DB:开跑时 status=running,跑完 update status=done + 指标
    try:
        from .db import append_run, update_run
        from .task_gen.versioning import current_version_label
        agent_ver = current_version_label(task_dir)
        run_params = {
            "label": run_id,
            "total": total,
            "trials": trials,
            "personas": personas,
            "concurrency": n_workers,
            "no_judge": no_judge,
            "config": config,
        }
        append_run(run_id, task_def.task_id, run_params,
                    agent_version=agent_ver)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"  (DB 写入失败 → 跳过历史索引:{exc})")

    # 索引 → 生成的 persona 对象(--dimensions 模式)
    persona_map: dict[int, object] = {}
    if generated_personas:
        for i, p in enumerate(generated_personas):
            persona_map[i] = p

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {
            ex.submit(_run_one_trial, task_dir, task_def, rubrics,
                      name, i, trials, cfg, judge, run_id,
                      noise_overlay_for_idx.get(i),
                      persona_map.get(i)): (name, i)
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
    all_scores = []
    n_pass = 0
    for name, scs in by_persona.items():
        all_scores.extend(scs)
        n_pass += sum(1 for s in scs if s >= 0.75)
        if len(scs) > 1:
            ph = scoring.compute_pass_hat_k(scs, k=len(scs))
            typer.echo(f"  {name:>20}: scores={[round(s,3) for s in scs]}  "
                       f"Pass^{len(scs)}={ph}")
        else:
            typer.echo(f"  {name:>20}: scores={[round(s,3) for s in scs]}")

    # 更新 DB 状态
    try:
        from .db import update_run
        pass_rate = n_pass / len(all_scores) if all_scores else 0.0
        score_avg = (sum(all_scores) / len(all_scores)) if all_scores else 0.0
        update_run(run_id, status="done", n_results=len(all_scores),
                    pass_rate=pass_rate, task_score_avg=score_avg)
    except Exception:  # noqa: BLE001
        pass

    if dashboard_out:
        typer.echo("\n[batch] 生成 dashboard…")
        try:
            from .report.builder import build_dashboard_from_dir
            out = build_dashboard_from_dir(_ROOT / "traces", _ROOT / "reports")
            typer.echo(f"Dashboard: {out}")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  [warn] dashboard 生成失败: {exc}")


@app.command("safety-test")
def safety_test(task: str = typer.Option(..., help="任务 id 或目录"),
                trials: int = typer.Option(
                    2, help="每对抗 persona 跑几次(默认 2,够采样不烧 token)"),
                concurrency: int = typer.Option(0),
                config: str = typer.Option(None),
                label: str = typer.Option(
                    "", help="run_id 标签;默认 safety_<时间戳>")):
    """安全红队 —— 对抗 persona × safety rubric 专项,出红队报告。

    自动选择 tasks/<task>/personas/ 下所有 adv_*.yaml 作为对抗 persona。
    """
    import json as _json
    from .adversarial import (
        build_red_team_report, format_red_team_terminal,
    )
    from .report.aggregate import load_results_dir

    task_dir = _task_dir(task)
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    rubrics = load_rubrics(task_dir / "rubrics.yaml")
    cfg = _load_models_cfg(config)
    _configure_provider(cfg)
    judge = _make_judge(cfg)

    adv_personas = sorted(
        f.stem for f in (task_dir / "personas").glob("adv_*.yaml"))
    if not adv_personas:
        typer.echo("[error] 没找到对抗 persona(以 adv_ 开头的剧本)")
        typer.echo(f"  请在 tasks/{task}/personas/ 下加 adv_*.yaml")
        raise typer.Exit(1)

    typer.echo(f"\n═══ 安全红队测试 · {task_def.task_id} ═══")
    typer.echo(f"对抗 persona({len(adv_personas)}):{adv_personas}")
    typer.echo(f"× {trials} trials = {len(adv_personas) * trials} 次")

    n_workers = concurrency or int(cfg.get("concurrency", 4))
    run_id = label or ("safety_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    typer.echo(f"  run_id = {run_id} → traces/{run_id}/\n")

    pairs = [(name, i) for name in adv_personas for i in range(trials)]
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {
            ex.submit(_run_one_trial, task_dir, task_def, rubrics,
                      name, i, trials, cfg, judge, run_id): (name, i)
            for name, i in pairs
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                with _LOG_LOCK:
                    typer.echo(f"  ✗ failed: {exc}")
            with _LOG_LOCK:
                typer.echo(f"  ({done}/{len(pairs)})")

    results = [r for r in load_results_dir(_ROOT / "traces" / run_id)
               if r.task_id == task_def.task_id]
    report = build_red_team_report(results, rubrics)
    typer.echo(format_red_team_terminal(report))

    out_path = _ROOT / "reports" / f"safety_test_{task_def.task_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        _json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8")
    typer.echo(f"\nJSON → {out_path.relative_to(_ROOT)}")


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


@app.command("generate-task")
def generate_task_cmd(
        prompt: str = typer.Option(..., help="任务描述文件路径(.md/.txt)"),
        id: str = typer.Option(..., "--id", help="新任务 id(目录名)"),
        config: str = typer.Option(None)):
    """从一段任务描述自动产出 task.yaml + flow.yaml + rubrics + personas + grader.py。

    流程(每步会进度提示):
    1. 抽业务变量(LLM low effort,~10s)
    2. 抽 flow.yaml(LLM medium,~30s)
    3. 抽 rubrics(LLM medium,~60s)
    4. 抽 personas + 比例 + 覆盖率(LLM medium,~60s)
    5. 生成 grader.py(模板填空,即时)
    6. 写出全部文件到 tasks/<id>/

    审核后 `claw-eval review --task <id>` 把 rubric 草稿转正。
    """
    import yaml as _yaml
    from .models.rubric import save_rubrics
    from .sampling import NoiseOverlay, SamplingConfig, save_sampling
    from .task_gen.flow_extractor import extract_flow, save_flow
    from .task_gen.grader_generator import save_grader
    from .task_gen.variables_extractor import (
        auto_detect_placeholders, extract_variables,
    )
    from .rubric.extractor import extract_rubrics
    from .user_simulator.extractor import (
        extract_personas_with_coverage, save_persona_script,
    )

    prompt_path = Path(prompt)
    if not prompt_path.exists():
        typer.echo(f"[error] 文件不存在:{prompt_path}")
        raise typer.Exit(1)
    task_prompt = prompt_path.read_text(encoding="utf-8")

    task_id = id
    task_dir = _ROOT / "tasks" / task_id
    if task_dir.exists():
        typer.echo(f"[error] tasks/{task_id}/ 已存在,先删除或换 id")
        raise typer.Exit(1)
    task_dir.mkdir(parents=True)
    (task_dir / "personas").mkdir(exist_ok=True)
    personas_draft_dir = task_dir / "personas_draft"
    personas_draft_dir.mkdir(exist_ok=True)

    cfg = _load_models_cfg(config)
    _configure_provider(cfg)
    judge_model = cfg["judge"]["model"]
    effort_low = "low"
    effort_med = cfg["judge"].get("reasoning_effort", "medium")

    typer.echo(f"\n═══ 生成任务 {task_id} ═══\n")

    # ① 业务变量
    typer.echo("① 抽业务变量(LLM low effort)…")
    auto_vars = {k: f"<TODO {k}>" for k in auto_detect_placeholders(task_prompt)}
    try:
        llm_vars = extract_variables(task_prompt, judge_model, effort_low)
    except Exception as exc:
        typer.echo(f"   ⚠ LLM 变量抽取失败:{exc};仅保留 placeholder")
        llm_vars = {}
    variables = {**auto_vars, **llm_vars}
    typer.echo(f"   ✓ {len(variables)} 个变量:{list(variables.keys())}")

    # ② flow.yaml
    typer.echo("\n② 抽 flow.yaml(LLM 中等 effort)…")
    flow = extract_flow(task_prompt, judge_model, reasoning_effort=effort_med)
    save_flow(flow, task_dir / "flow.yaml")
    typer.echo(f"   ✓ {len(flow.nodes)} 个节点 + {len(flow.edges)} 条边")

    # 写 task.yaml(prompt + variables)
    task_yaml = {
        "task_id": task_id,
        "prompt": task_prompt,
        "variables": variables,
    }
    (task_dir / "task.yaml").write_text(
        _yaml.safe_dump(task_yaml, allow_unicode=True, sort_keys=False,
                        default_flow_style=False),
        encoding="utf-8")
    typer.echo(f"   写出 task.yaml({len(task_prompt)} 字符)")

    # ③ rubrics(草稿)
    typer.echo("\n③ 抽 rubrics 草稿(LLM 中等 effort)…")
    from .models.task import TaskDefinition
    task_def = TaskDefinition.from_yaml(task_dir / "task.yaml")
    rubrics = extract_rubrics(task_def, judge_model, reasoning_effort=effort_med)
    save_rubrics(rubrics, task_dir / "rubrics.draft.yaml", include_meta=True)
    typer.echo(f"   ✓ {len(rubrics)} 条 rubric 草稿 → rubrics.draft.yaml")

    # ④ personas + 比例(必须覆盖 flow 节点)
    typer.echo("\n④ 抽 personas + 比例 + 覆盖率…")
    flow_node_list = [(n.id, n.label) for n in flow.nodes]
    pset = extract_personas_with_coverage(
        task_def, judge_model, _ROOT / "personalities",
        flow_node_list, reasoning_effort=effort_med)
    for s in pset.scripts:
        save_persona_script(s, personas_draft_dir / f"{s.id}.yaml")
    typer.echo(f"   ✓ {len(pset.scripts)} 个 persona 草稿 → personas_draft/")

    # sampling.yaml(草稿)
    sampling = SamplingConfig(weights={k: v for k, v in pset.weights.items()},
                                noise_overlay=NoiseOverlay())
    save_sampling(sampling, task_dir / "sampling.yaml")
    typer.echo(f"   写出 sampling.yaml(权重 {list(pset.weights.values())})")

    # 覆盖检查
    uncovered = [n for n, who in pset.coverage.items() if not who]
    if uncovered:
        typer.echo(f"   ⚠ 仍有 {len(uncovered)} 个 flow 节点无 persona 覆盖:{uncovered}")
    else:
        typer.echo("   ✓ 所有 flow 节点都有 persona 覆盖")

    # ⑤ grader.py
    typer.echo("\n⑤ 生成 grader.py(模板填空)…")
    save_grader(task_id, task_dir / "grader.py")
    typer.echo(f"   ✓ grader.py")

    typer.echo(f"\n═══ 完成 ═══")
    typer.echo(f"任务目录:tasks/{task_id}/")
    typer.echo(f"下一步:")
    typer.echo(f"  1. 审核草稿:tasks/{task_id}/rubrics.draft.yaml & personas_draft/")
    typer.echo(f"  2. 转正 rubric:claw-eval review --task {task_id}")
    typer.echo(f"  3. 复制 personas_draft/ 想要的 persona 到 personas/")
    typer.echo(f"  4. 跑批:claw-eval batch --task {task_id} --total 30 --label v1")


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
def web(port: int = typer.Option(8000, help="FastAPI 端口"),
         host: str = typer.Option("0.0.0.0", help="监听地址")):
    """启动 FastAPI web 服务(新 React 前端 + REST API)。

    依赖:`pip install -e '.[web]'`(fastapi + uvicorn)
    前端 dev 模式:cd web && npm install && npm run dev(占 :5173)
    或先 npm run build,FastAPI 同端口托管 SPA。

    Swagger:http://localhost:8000/docs
    """
    try:
        import uvicorn
        from claw_eval.api.app import create_app
    except ImportError:
        typer.echo("[error] FastAPI / uvicorn 未装,跑:pip install -e '.[web]'")
        raise typer.Exit(1)
    app_ = create_app()
    typer.echo(f"启动 web: http://localhost:{port}/")
    typer.echo(f"Swagger:  http://localhost:{port}/docs")
    typer.echo(f"前端 dev:cd web && npm install && npm run dev")
    uvicorn.run(app_, host=host, port=port, log_level="info")


@app.command()
def editor(port: int = typer.Option(8501, help="Streamlit 端口")):
    """[legacy] 启动 Streamlit 编辑器。

    ⚠ Streamlit UI 已**冻结开发**,新功能只在 React UI (`claw-eval web`) 加。
    保留是为了 fallback,不需要装 Node.js。
    """
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
