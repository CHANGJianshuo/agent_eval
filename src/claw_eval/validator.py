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
                    f"rubric '{r.id}' 类别 '{cat}' 非标准 6 类({sorted(_KNOWN_CATEGORIES)})"))
    return out


def check_weights(rubrics: list[Rubric]) -> list[Issue]:
    """非 safety 项 weight 必须 > 0(不限定 sum,权重交人审)。"""
    out: list[Issue] = []
    for r in rubrics:
        if r.dimension != "safety" and r.weight <= 0:
            out.append(Issue(
                "warning", "weight_zero",
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


def check_trigger_reachable(rubrics: list[Rubric],
                            personas: list[Persona]) -> list[Issue]:
    """每条带 trigger 的 rubric 至少要有一个 persona 能触发。"""
    out: list[Issue] = []
    for r in rubrics:
        if r.trigger and not _trigger_reachable(r.trigger, personas):
            out.append(Issue(
                "warning", "dead_rubric",
                f"rubric '{r.id}' 的 trigger ({r.trigger.type}) 没有任何 persona 能触发 —— 该规则永远不计分"))
    return out


def _trigger_reachable(trigger: TriggerSpec, personas: list[Persona]) -> bool:
    for p in personas:
        if trigger.type == "probe":
            if any(pr.id == trigger.probe_id for pr in p.probes):
                return True
        elif trigger.type == "user_state":
            if (trigger.state in p.states
                    or trigger.state in set(p.transitions.values())):
                return True
        elif trigger.type == "user_keyword":
            # 简化:只在 probe 文本里查关键词;LLM 生成的话术里偶尔出现不算
            for pr in p.probes:
                if any(kw in pr.text for kw in trigger.keywords):
                    return True
    return False


def check_state_termination(personas: list[Persona],
                            max_steps: int = 50) -> list[Issue]:
    out: list[Issue] = []
    for p in personas:
        sm = StateMachine(p)
        for _ in range(max_steps):
            if sm.advance():
                break
        if not sm.finished:
            out.append(Issue(
                "error", "no_terminate",
                f"persona '{p.id}' 状态机 {max_steps} 步未到 END(可能转移表有环或漏写)"))
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
    rep.issues += check_weights(rubrics)
    rep.issues += check_safety_flags(rubrics)
    rep.issues += check_trigger_reachable(rubrics, personas)
    rep.issues += check_state_termination(personas)

    if sampling_file and Path(sampling_file).exists():
        try:
            from .sampling import load_sampling
            samp = load_sampling(sampling_file)
            rep.issues += check_sampling(samp.weights, personas)
        except Exception as exc:  # noqa: BLE001
            rep.issues.append(Issue(
                "error", "sampling_load",
                f"sampling.yaml 加载失败: {exc}"))

    return rep
