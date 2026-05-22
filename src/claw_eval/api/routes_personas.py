"""Persona 库相关 endpoints。"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter

from ..models.persona import load_personality


def _root() -> Path:
    cur = Path(__file__).resolve()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


ROOT = _root()
PERSONALITIES_DIR = ROOT / "personalities"
TASKS_DIR = ROOT / "tasks"


router = APIRouter()


# 5 维度的属性字典 + 描述
DIMENSIONS = {
    "attitude": {
        "label": "性格",
        "values": [
            {"value": "cooperative", "label": "合作型", "desc": "配合、礼貌、简短"},
            {"value": "refuse", "label": "抵触型", "desc": "不愿做、坚决"},
            {"value": "hesitant", "label": "犹豫型", "desc": "反复追问"},
            {"value": "argumentative", "label": "抬杠型", "desc": "质疑、爱反问"},
            {"value": "confused", "label": "茫然型", "desc": "不清楚"},
            {"value": "blunt", "label": "直接强势型", "desc": "追着问、直接"},
            {"value": "hurried", "label": "匆忙型", "desc": "急、话少"},
            {"value": "adversarial", "label": "对抗型", "desc": "注入/社工/施压"},
        ],
    },
    "mbti": {
        "label": "MBTI",
        "values": [
            {"value": a + b + c + d, "label": a + b + c + d, "desc": ""}
            for a in "IE" for b in "NS" for c in "FT" for d in "JP"
        ],
    },
    "gender": {
        "label": "性别",
        "values": [
            {"value": "male", "label": "男", "desc": ""},
            {"value": "female", "label": "女", "desc": ""},
        ],
    },
    "age_range": {
        "label": "年龄段",
        "values": [
            {"value": "<20", "label": "<20", "desc": ""},
            {"value": "20-29", "label": "20-29", "desc": ""},
            {"value": "30-39", "label": "30-39", "desc": ""},
            {"value": "40-49", "label": "40-49", "desc": ""},
            {"value": "50+", "label": "50+", "desc": ""},
        ],
    },
    "education": {
        "label": "教育",
        "values": [
            {"value": "primary", "label": "小学", "desc": ""},
            {"value": "middle", "label": "初中", "desc": ""},
            {"value": "high", "label": "高中", "desc": ""},
            {"value": "college", "label": "本科", "desc": ""},
            {"value": "postgrad", "label": "研究生及以上", "desc": ""},
        ],
    },
}


@router.get("/persona-library")
def get_persona_library():
    """5 维度的属性字典 + 每个属性值的使用统计。"""
    # 统计使用次数
    usage: dict[str, dict[str, int]] = {
        dim: {v["value"]: 0 for v in cfg["values"]}
        for dim, cfg in DIMENSIONS.items()
    }
    if PERSONALITIES_DIR.exists():
        for pf in PERSONALITIES_DIR.glob("*.yaml"):
            try:
                p = load_personality(pf)
                for dim in DIMENSIONS:
                    v = getattr(p.demographics, dim)
                    if v in usage[dim]:
                        usage[dim][v] += 1
            except Exception:
                pass

    # 拼接 dimension 数据 + 使用次数
    dims = []
    for dim, cfg in DIMENSIONS.items():
        values = []
        for vv in cfg["values"]:
            values.append({
                **vv,
                "usage_count": usage[dim].get(vv["value"], 0),
            })
        dims.append({
            "dim": dim,
            "label": cfg["label"],
            "values": values,
        })
    return {"dimensions": dims}


@router.get("/tasks/{task_id}/personas")
def get_task_personas(task_id: str):
    """该任务下所有 persona,带 demographics。"""
    td = TASKS_DIR / task_id
    if not td.exists():
        return {"personas": []}

    out = []
    for pf in (td / "personas").glob("*.yaml"):
        try:
            d = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
            personality_id = d.get("personality", "")
            personality = None
            if personality_id:
                try:
                    personality = load_personality(
                        PERSONALITIES_DIR / f"{personality_id}.yaml")
                except Exception:
                    pass
            demo = personality.demographics.model_dump() if personality else {}
            out.append({
                "id": pf.stem,
                "is_adversarial": pf.stem.startswith("adv_"),
                "personality_id": personality_id,
                "covers_flow_nodes": d.get("covers_flow_nodes", []),
                "demographics": demo,
                "max_rounds": d.get("max_rounds", 6),
            })
        except Exception:
            pass

    # 读 sampling.yaml 取默认权重
    weights = {}
    sp = td / "sampling.yaml"
    if sp.exists():
        try:
            sd = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            weights = sd.get("weights", {})
        except Exception:
            pass
    for p in out:
        p["default_weight"] = float(weights.get(p["id"], 0))

    return {"personas": out}
