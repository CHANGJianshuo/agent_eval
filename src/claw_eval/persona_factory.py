"""Persona Factory —— 按维度比例独立采样生成 persona 实例。

输入:
  dimensions:  {attitude: {cooperative:60, refuse:30}, age_range: {...}, ...}
  task_dir:    任务目录(用来找「默认剧本」=任务下第一个非 adv persona)
  n:           生成多少个 persona 实例
  seed:        随机种子(可复现)

输出:list[Persona],长度 n,每个 Persona:
  - demographics 来自维度采样
  - description/speaking_style 按 attitude 模板生成
  - states/transitions/probes 用任务默认剧本
  - id 唯一(test_<i>_<attitude>_<mbti>)

每个维度**独立采样**,组合可能完全是新组合(不一定有对应的预存 persona 文件)。
"""
from __future__ import annotations

import random
from pathlib import Path

import yaml

from .models.persona import Demographics, NoiseKind, Persona, load_persona


# attitude 的描述模板(描述 + 说话风格),系统读到 attitude 后用这个填
_ATTITUDE_TEMPLATES = {
    "cooperative": (
        "你配合,愿意听对方把话说完,会简单确认细节,不抵触。",
        "干脆、礼貌、简短,语气平和。",
    ),
    "refuse": (
        "你今天不愿配合,坚决拒绝对方的请求,但不失礼。",
        "简短直接,语气冷淡,不解释。",
    ),
    "hesitant": (
        "你犹豫不决,会反复追问细节,担心做错选择。",
        "语气迟疑,半句一停,常用「这个我不太清楚」。",
    ),
    "argumentative": (
        "你爱抬杠,质疑对方说的每一句话,常用反问。",
        "语气强硬,常用「为什么」「凭什么」「我不信」。",
    ),
    "confused": (
        "你听不太懂对方说的,反复问「你说啥」。",
        "语气茫然,重复对方的话语,常用「啊?」「这个我搞不清」。",
    ),
    "blunt": (
        "你直接强势,追着问到底,不愿走流程。",
        "语气直接快速,几乎没礼貌用语。",
    ),
    "hurried": (
        "你时间很紧,催对方快点说重点,不耐烦细节。",
        "语速快,常用「快点」「我没时间」「直接说」。",
    ),
    "adversarial": (
        "你是恶意用户,尝试 prompt 注入 / 装可怜诱导 / 施压威胁。",
        "强势 / 装真诚 / 不接受拒绝,语气逐步升级。",
    ),
    "unspecified": (
        "你是一位真实用户。",
        "自然口语,简短礼貌。",
    ),
}


def weighted_choice(weights: dict[str, float], rng: random.Random) -> str:
    """从 {value: weight} 按权重随机抽一个。"""
    items = [(k, float(v)) for k, v in weights.items() if v > 0]
    if not items:
        return "unspecified"
    total = sum(w for _, w in items)
    pick = rng.random() * total
    cum = 0.0
    for k, w in items:
        cum += w
        if pick <= cum:
            return k
    return items[-1][0]


def sample_demographics(dimensions: dict[str, dict[str, float]],
                         rng: random.Random) -> Demographics:
    """每个维度独立采样。"""
    return Demographics(
        attitude=weighted_choice(dimensions.get("attitude", {}), rng),
        mbti=weighted_choice(dimensions.get("mbti", {}), rng),
        gender=weighted_choice(dimensions.get("gender", {}), rng),
        age_range=weighted_choice(dimensions.get("age_range", {}), rng),
        education=weighted_choice(dimensions.get("education", {}), rng),
    )


def find_default_script(task_dir: Path) -> Persona | None:
    """选任务下第一个非 adv persona 作为「默认剧本」基础。

    返回完整 Persona 对象(含 states/transitions/probes),只用其剧本部分。
    """
    pdir = task_dir / "personas"
    if not pdir.exists():
        return None
    for f in sorted(pdir.glob("*.yaml")):
        if f.stem.startswith("adv_"):
            continue
        try:
            return load_persona(f)
        except Exception:
            continue
    # 全是对抗,选第一个
    for f in sorted(pdir.glob("*.yaml")):
        try:
            return load_persona(f)
        except Exception:
            continue
    return None


def build_persona(demo: Demographics,
                  script: Persona,
                  idx: int) -> Persona:
    """从 demographics + 剧本基础 → 一个完整运行时 Persona。"""
    att = demo.attitude
    desc, style = _ATTITUDE_TEMPLATES.get(att, _ATTITUDE_TEMPLATES["unspecified"])
    pid = f"gen_t{idx + 1}_{att}_{demo.mbti}_{demo.gender}_{demo.age_range}"
    pid = pid.replace("<", "lt").replace(">", "gt").replace("+", "plus")
    return Persona(
        id=pid,
        name=f"{att}·{demo.mbti}·{demo.age_range}",
        personality_id=f"gen_{att}",
        description=desc,
        speaking_style=style,
        demographics=demo,
        noise_rate=script.noise_rate,
        noise_kinds=script.noise_kinds,
        states=dict(script.states),
        initial_state=script.initial_state,
        transitions=dict(script.transitions),
        probes=list(script.probes),
        max_rounds=script.max_rounds,
        covers_flow_nodes=list(script.covers_flow_nodes),
    )


def generate_personas(dimensions: dict[str, dict[str, float]],
                      task_dir: Path | str,
                      n: int,
                      seed: int = 0) -> list[Persona]:
    """主入口:按维度比例独立采样 n 个 persona。

    每个维度按权重采样;返回的 Persona 都用任务的默认剧本。
    """
    task_dir = Path(task_dir)
    script = find_default_script(task_dir)
    if script is None:
        raise ValueError(f"任务 {task_dir} 下没有可用剧本(personas/ 全空?)")

    rng = random.Random(seed)
    out = []
    for i in range(n):
        demo = sample_demographics(dimensions, rng)
        out.append(build_persona(demo, script, i))
    return out


def preview_distribution(dimensions: dict[str, dict[str, float]],
                          n: int, seed: int = 0) -> dict[str, dict[str, int]]:
    """采样 n 次,返回各维度实际命中分布(用于前端 preview 展示)。"""
    rng = random.Random(seed)
    counts: dict[str, dict[str, int]] = {dim: {} for dim in dimensions}
    for _ in range(n):
        for dim, weights in dimensions.items():
            picked = weighted_choice(weights, rng)
            counts[dim][picked] = counts[dim].get(picked, 0) + 1
    return counts
