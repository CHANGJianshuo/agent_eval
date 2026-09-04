"""Persona 库相关 endpoints。"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..dimensions import load_dimensions, save_dimensions
from ..models.persona import PersonaScript, load_personality


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


def _usage_counts() -> dict[str, dict[str, int]]:
    """统计每个维度属性值在 personalities/ 里的使用次数。"""
    data = load_dimensions()
    usage: dict[str, dict[str, int]] = {
        d["key"]: {v["value"]: 0 for v in d.get("values", [])}
        for d in data.get("dimensions", [])
    }
    if PERSONALITIES_DIR.exists():
        for pf in PERSONALITIES_DIR.glob("*.yaml"):
            try:
                p = load_personality(pf)
                for key in usage:
                    v = getattr(p.demographics, key, None)
                    if v in usage[key]:
                        usage[key][v] += 1
            except Exception:
                pass
    return usage


@router.get("/persona-library")
def get_persona_library():
    """维度库(从 configs/dimensions.yaml 读)+ 每个属性值的使用统计。"""
    data = load_dimensions()
    usage = _usage_counts()
    dims = []
    for d in data.get("dimensions", []):
        key = d["key"]
        values = []
        for vv in d.get("values", []):
            values.append({
                "value": vv.get("value", ""),
                "label": vv.get("label", vv.get("value", "")),
                "desc": vv.get("desc", ""),
                # attitude 维度特有
                "description": vv.get("description", ""),
                "speaking_style": vv.get("speaking_style", ""),
                "usage_count": usage.get(key, {}).get(vv.get("value"), 0),
            })
        dims.append({"dim": key, "label": d.get("label", key), "values": values})
    return {"dimensions": dims}


class DimValue(BaseModel):
    value: str
    label: str = ""
    desc: str = ""
    description: str = ""        # attitude 维度:用户模拟器自我描述
    speaking_style: str = ""     # attitude 维度:说话风格


class DimensionIn(BaseModel):
    dim: str                     # 维度 key
    label: str
    values: list[DimValue]


class PersonaLibraryIn(BaseModel):
    dimensions: list[DimensionIn]


@router.put("/persona-library")
def update_persona_library(body: PersonaLibraryIn):
    """保存整个维度库(全局配置页编辑后调)。"""
    # 校验:key 唯一、非空;value 维度内唯一
    seen_keys = set()
    out_dims = []
    for d in body.dimensions:
        if not d.dim.strip():
            raise HTTPException(400, "维度 key 不能为空")
        if d.dim in seen_keys:
            raise HTTPException(400, f"维度 key 重复:{d.dim}")
        seen_keys.add(d.dim)
        seen_vals = set()
        vals = []
        for v in d.values:
            if not v.value.strip():
                raise HTTPException(400, f"维度 {d.dim} 有空属性值")
            if v.value in seen_vals:
                raise HTTPException(400, f"维度 {d.dim} 属性值重复:{v.value}")
            seen_vals.add(v.value)
            row = {"value": v.value, "label": v.label or v.value, "desc": v.desc}
            # attitude 才存 description/speaking_style
            if v.description or v.speaking_style:
                row["description"] = v.description
                row["speaking_style"] = v.speaking_style
            vals.append(row)
        out_dims.append({"key": d.dim, "label": d.label or d.dim, "values": vals})
    save_dimensions({"dimensions": out_dims})
    return {"ok": True, "n_dimensions": len(out_dims)}


@router.get("/tasks/{task_id}/scripts")
def get_task_scripts(task_id: str):
    """该任务下所有剧本(状态机/探针),按场景展示。同时读 personas/ 和 personas_draft/。"""
    td = TASKS_DIR / task_id
    personas_dir = td / "personas"
    draft_dir = td / "personas_draft"

    def _yaml_equal(left: Path, right: Path) -> bool:
        try:
            left_data = yaml.safe_load(left.read_text(encoding="utf-8")) or {}
            right_data = yaml.safe_load(right.read_text(encoding="utf-8")) or {}
            return PersonaScript.model_validate(left_data).model_dump() == \
                PersonaScript.model_validate(right_data).model_dump()
        except Exception:
            return False

    # One row per script id. A changed draft overrides its approved baseline so
    # the reviewer sees the content that will actually be approved.
    selected: dict[str, tuple[Path, bool]] = {}
    if personas_dir.exists():
        for path in sorted(personas_dir.glob("*.yaml")):
            selected[path.stem] = (path, False)
    if draft_dir.exists():
        for path in sorted(draft_dir.glob("*.yaml")):
            approved = personas_dir / path.name
            if not approved.exists() or not _yaml_equal(path, approved):
                selected[path.stem] = (path, True)

    all_files = [selected[key] for key in sorted(selected)]
    if not all_files:
        return {"scripts": []}

    out = []
    for pf, is_draft in all_files:
        try:
            d = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
            probes = d.get("probes", [])
            scenario = d.get("scenario", "")
            states = d.get("states", {})
            out.append({
                "id": d.get("id", pf.stem),
                "filename": pf.name,
                "name": d.get("name", pf.stem),
                "scenario": scenario,
                "is_adversarial": pf.stem.startswith("adv_"),
                "is_draft": is_draft,
                "probes": probes,
                "max_rounds": d.get("max_rounds", 12),
                "covers_flow_nodes": d.get("covers_flow_nodes", []),
                "n_probes": len(probes),
                # v1 兼容
                "personality": d.get("personality", ""),
                "states": states,
                "transitions": d.get("transitions", {}),
                "initial_state": d.get("initial_state", ""),
                "n_states": len(states),
            })
        except Exception:
            pass

    return {"scripts": out}


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
