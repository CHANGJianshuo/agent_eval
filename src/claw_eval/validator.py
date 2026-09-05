"""任务配置一致性检查 —— 机械检查,把笔误尽早暴露。

不替代人审权重(权重各任务可不同,交人决定);只查能自动发现的问题:
1. rubric id 命名规范(类别.名)
2. 非 safety rubric 权重 > 0
3. dimension=safety 的 rubric 必须 is_safety=true(乘子语义)
4. trigger 可达性 —— 每条触发型 rubric 至少有一个 persona 能触发
5. persona 状态机能否到达 END
6. sampling.yaml 引用的 persona 都存在
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .models.persona import Persona, load_persona
from .models.rubric import Rubric, TriggerSpec, load_rubrics
from .models.task import TaskDefinition
from .templating import MissingTemplateVariables, render_template
from .user_simulator.state_machine import StateMachine


@dataclass
class Issue:
    level: str           # error / warning / info
    code: str
    message: str


@dataclass
class ValidationReport:
    task_id: str
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def infos(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors


_RUBRIC_ID_RX = re.compile(r"^[a-z][a-z_0-9]*\.[a-z][a-z_0-9]*$")
_KNOWN_CATEGORIES = {
    "opening", "flow", "faq", "constraint",
    "role", "behavior", "safety",
}


# ---- 单项检查(便于独立单测)----

def check_rubric_naming(rubrics: list[Rubric]) -> list[Issue]:
    out: list[Issue] = []
    for r in rubrics:
        if not _RUBRIC_ID_RX.match(r.id):
            out.append(Issue(
                "warning", "naming",
                f"rubric id 不规范: '{r.id}'(建议: <category>.<name>,小写字母/数字/下划线)"))
        else:
            cat = r.id.split(".")[0]
            if cat not in _KNOWN_CATEGORIES:
                out.append(Issue(
                    "info", "naming",
                    f"rubric '{r.id}' 类别 '{cat}' 非标准 7 类({sorted(_KNOWN_CATEGORIES)})"))
    return out


def check_weights(rubrics: list[Rubric]) -> list[Issue]:
    """非 safety 项 weight 必须 > 0(不限定 sum,权重交人审)。"""
    out: list[Issue] = []
    for r in rubrics:
        if r.dimension != "safety" and not r.is_safety and r.weight <= 0:
            out.append(Issue(
                "error", "weight_zero",
                f"rubric '{r.id}' weight={r.weight}(非 safety 项应 >0)"))
    return out


def check_safety_flags(rubrics: list[Rubric]) -> list[Issue]:
    """dimension=safety 必须 is_safety=true(乘子语义)。"""
    out: list[Issue] = []
    for r in rubrics:
        if r.dimension == "safety" and not r.is_safety:
            out.append(Issue(
                "error", "safety_flag_missing",
                f"rubric '{r.id}' dimension=safety 但 is_safety=false —— 必须为 true(乘子模式)"))
        if r.is_safety and r.dimension != "safety":
            out.append(Issue(
                "warning", "safety_flag_misplaced",
                f"rubric '{r.id}' is_safety=true 但 dimension={r.dimension}"))
    return out


def check_template_variables(task: TaskDefinition,
                             rubrics: list[Rubric]) -> list[Issue]:
    """Prompt 与 rubric 引用的变量必须存在且不能仍是 TODO。"""
    out: list[Issue] = []
    for name, value in task.variables.items():
        if isinstance(value, str) and value.strip().startswith("<TODO"):
            out.append(Issue(
                "error", "variable_todo",
                f"变量 '{name}' 仍是占位值 {value!r},请填写业务真值"))

    templates = [("task.prompt", task.prompt)]
    templates.extend((f"rubric '{r.id}'", r.check) for r in rubrics)
    for source, text in templates:
        try:
            render_template(text, task.variables)
        except MissingTemplateVariables as exc:
            out.append(Issue(
                "error", "template_variable_missing",
                f"{source} 引用了未定义变量: {sorted(exc.names)}"))
    return out


def check_trigger_reachable(rubrics: list[Rubric],
                            personas: list[Persona]) -> list[Issue]:
    """每条带 trigger 的 rubric 至少要有一个 persona 能触发。"""
    out: list[Issue] = []
    for r in rubrics:
        if r.trigger and not _trigger_reachable(r.trigger, personas):
            out.append(Issue(
                "warning", "dead_rubric",
                f"rubric '{r.id}' 的 trigger ({r.trigger.type}) 未设计确定性覆盖，请检查实际 trace 中的触发情况"))
    return out


def _trigger_reachable(trigger: TriggerSpec, personas: list[Persona]) -> bool:
    for p in personas:
        if trigger.type == "probe":
            if any(pr.id == trigger.probe_id for pr in p.probes):
                return True
        elif trigger.type == "user_state":
            targets: set[str] = set()
            for spec in p.transitions.values():
                if isinstance(spec, str):
                    targets.add(spec)
                elif isinstance(spec, dict):
                    targets.update(spec)
            if trigger.state in p.states or trigger.state in targets:
                return True
        elif trigger.type == "user_keyword":
            # 简化:只在 probe 文本里查关键词;LLM 生成的话术里偶尔出现不算
            for pr in p.probes:
                if any(kw in pr.text for kw in trigger.keywords):
                    return True
    return False


def check_state_termination(personas: list[Persona],
                            max_steps: int = 50) -> list[Issue]:
    """图论检查:概率 transitions 支持下,必须**每个 reachable state 都能到达 END**。

    确定性 transitions 是该检查的特例(每个 state 唯一 outgoing)。
    """
    out: list[Issue] = []
    END_S = "END"
    for p in personas:
        # v2 剧本(scenario 自然语言)没有状态机,跳过该检查
        if getattr(p, "scenario", "") and not any(p.states.keys()):
            continue
        # 邻接表:每个 state → 所有可能的下一站
        adj: dict[str, set[str]] = {}
        for src in p.states.keys():
            spec = p.transitions.get(src)
            if spec is None:
                adj[src] = {END_S}
            elif isinstance(spec, str):
                adj[src] = {spec}
            elif isinstance(spec, dict):
                adj[src] = set(spec.keys())
            else:
                adj[src] = set()

        # 正向 BFS:从 initial_state 出发可达的所有 state
        reachable = {p.initial_state}
        stack = [p.initial_state]
        while stack:
            cur = stack.pop()
            if cur == END_S:
                continue
            for nxt in adj.get(cur, set()):
                if nxt not in reachable:
                    reachable.add(nxt)
                    stack.append(nxt)

        # 反向闭包:能到 END 的 state 集合
        can_reach_end = {END_S}
        changed = True
        while changed:
            changed = False
            for src, neighbors in adj.items():
                if src in can_reach_end:
                    continue
                if any(n in can_reach_end for n in neighbors):
                    can_reach_end.add(src)
                    changed = True

        # 检查:reachable 中每个 state 都必须能到达 END
        bad = reachable - can_reach_end - {END_S}
        if bad:
            out.append(Issue(
                "error", "no_terminate",
                f"persona '{p.id}' 状态机有 state 无法到达 END: {sorted(bad)}"
                f"(可能有环 / 漏写转移)"))
    return out


def check_sampling(weights: dict[str, float],
                   personas: list[Persona]) -> list[Issue]:
    out: list[Issue] = []
    persona_ids = {p.id for p in personas}
    for name, w in weights.items():
        if name not in persona_ids:
            out.append(Issue(
                "error", "sampling_orphan",
                f"sampling.yaml 引用了不存在的 persona '{name}'"))
        if w < 0:
            out.append(Issue(
                "warning", "sampling_negative",
                f"sampling.yaml 中 '{name}' 权重为负 ({w})"))
    missing = persona_ids - set(weights.keys())
    if missing:
        out.append(Issue(
            "info", "sampling_missing",
            f"sampling.yaml 没给以下 persona 配权重(将不被采样): {sorted(missing)}"))
    return out


# ---- 总入口 ----

def validate_task(task_dir: str | Path,
                  personalities_dir: str | Path | None = None,
                  noise_file: str | Path | None = None,
                  sampling_file: str | Path | None = None,
                  ) -> ValidationReport:
    task_dir = Path(task_dir)
    task = TaskDefinition.from_yaml(task_dir / "task.yaml")
    rep = ValidationReport(task_id=task.task_id)

    try:
        rubrics = load_rubrics(task_dir / "rubrics.yaml")
    except Exception as exc:  # noqa: BLE001
        rep.issues.append(Issue("error", "rubrics_load",
                                f"rubrics.yaml 加载失败: {exc}"))
        return rep

    personas: list[Persona] = []
    for f in sorted((task_dir / "personas").glob("*.yaml")):
        try:
            personas.append(load_persona(
                f, personalities_dir=personalities_dir, noise_file=noise_file))
        except Exception as exc:  # noqa: BLE001
            rep.issues.append(Issue(
                "error", "persona_load",
                f"persona {f.stem} 加载失败: {exc}"))

    rep.issues += check_rubric_naming(rubrics)
    from .graders.validation import validate_rule_params
    for r in rubrics:
        try:
            validate_rule_params(r)
        except (ValueError, TypeError, re.error) as exc:
            rep.issues.append(Issue("error", "rule_params", f"评分项 {r.id}: {exc}"))
    if not rubrics:
        rep.issues.append(Issue("error", "empty_rubrics", "至少需要一个评分项"))
    if len({r.id for r in rubrics}) != len(rubrics):
        rep.issues.append(Issue("error", "duplicate_rubric", "评分项 ID 重复"))
    for p in personas:
        if len({pr.id for pr in p.probes}) != len(p.probes):
            rep.issues.append(Issue("error", "duplicate_probe", f"剧本 {p.id} 探针 ID 重复"))
        if any(pr.inject_at_turn > p.max_rounds for pr in p.probes):
            rep.issues.append(Issue("error", "unreachable_probe", f"剧本 {p.id} 的探针轮次超过最大轮数"))
    try:
        from .models.flow import load_flow
        flow = load_flow(task_dir / "flow.yaml")
        if flow:
            ids = {node.id for node in flow.nodes}
            if len(ids) != len(flow.nodes) or any(len(e) != 2 or any(n not in ids for n in e) for e in flow.edges):
                rep.issues.append(Issue("error", "invalid_flow", "流程节点重复或边引用了不存在的节点"))
            rubric_ids = {r.id for r in rubrics}
            if any(n.rubric and n.rubric not in rubric_ids for n in flow.nodes):
                rep.issues.append(Issue("warning", "flow_rubric", "流程引用了不存在的评分项，相关节点没有评分依据"))
    except Exception as exc:
        rep.issues.append(Issue("error", "flow_load", f"流程读取失败: {exc}"))
    rep.issues += check_weights(rubrics)
    rep.issues += check_safety_flags(rubrics)
    rep.issues += check_template_variables(task, rubrics)
    rep.issues += check_trigger_reachable(rubrics, personas)
    rep.issues += check_state_termination(personas)

    if sampling_file and Path(sampling_file).exists():
        try:
            from .sampling import load_sampling
            samp = load_sampling(sampling_file)
            rep.issues += check_sampling(samp.weights, personas)
            from .models.persona import load_noise_kinds
            noise_path = Path(noise_file) if noise_file else task_dir.parent.parent / 'configs/noise_profiles.yaml'
            library = load_noise_kinds(noise_path) if noise_path.exists() else {}
            missing = set(samp.noise_overlay.kinds) - set(library)
            if missing or (samp.noise_overlay.rate > 0 and not samp.noise_overlay.kinds):
                rep.issues.append(Issue('error', 'sampling_noise', f'采样噪音种类缺失或无效: {sorted(missing)}'))
        except Exception as exc:  # noqa: BLE001
            rep.issues.append(Issue(
                "error", "sampling_load",
                f"sampling.yaml 加载失败: {exc}"))

    return rep


def require_valid_task(task_dir: Path, *, root: Path | None = None) -> ValidationReport:
    task_dir = Path(task_dir)
    root = root or task_dir.parent.parent
    rep = validate_task(task_dir, personalities_dir=root / "personalities",
                        noise_file=root / "configs" / "noise_profiles.yaml",
                        sampling_file=task_dir / "sampling.yaml")
    if rep.errors:
        raise ValueError("配置校验失败: " + "；".join(i.message for i in rep.errors))
    return rep
