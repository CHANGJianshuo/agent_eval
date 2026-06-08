"""Persona 维度库 —— 从 configs/dimensions.yaml 读写。

维度库是「公用」资源:定义有哪些维度、每维度有哪些属性值。
- 全局配置页可视化增删编辑(GET/PUT /api/persona-library)
- 新建测试从库里选维度 + 属性配比例
- persona_factory 采样时,attitude 维度的 description/speaking_style 也从这里取

加新属性值 / 加新维度:改 yaml 或用 UI,保存即生效,无需改代码。
"""
from __future__ import annotations

from pathlib import Path

import yaml


def _root() -> Path:
    cur = Path(__file__).resolve()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


DIMENSIONS_FILE = _root() / "configs" / "dimensions.yaml"


# 兜底默认(配置文件缺失时用,保证系统不崩)
_FALLBACK = {
    "dimensions": [
        {"key": "attitude", "label": "性格", "values": [
            {"value": "cooperative", "label": "合作型", "desc": "配合",
             "description": "你配合。", "speaking_style": "礼貌简短。"},
        ]},
    ]
}


def load_dimensions() -> dict:
    """读维度库。返回 {dimensions: [{key,label,values:[...]}, ...]}。"""
    if not DIMENSIONS_FILE.exists():
        return _FALLBACK
    try:
        data = yaml.safe_load(DIMENSIONS_FILE.read_text(encoding="utf-8")) or {}
        if not data.get("dimensions"):
            return _FALLBACK
        return data
    except Exception:
        return _FALLBACK


def save_dimensions(data: dict) -> None:
    """写维度库(全局配置页保存时调)。"""
    DIMENSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DIMENSIONS_FILE.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False),
        encoding="utf-8")


def attitude_templates() -> dict[str, tuple[str, str]]:
    """取 attitude 维度的 {value: (description, speaking_style)} 映射。

    persona_factory 采样到某性格时,用这里的描述/风格填充 persona。
    新增的性格属性值若没写 description,用兜底文案。
    """
    out: dict[str, tuple[str, str]] = {
        "unspecified": ("你是一位真实用户。", "自然口语,简短礼貌。"),
    }
    for dim in load_dimensions().get("dimensions", []):
        if dim.get("key") != "attitude":
            continue
        for v in dim.get("values", []):
            val = v.get("value")
            if not val:
                continue
            desc = v.get("description") or f"你是一位{v.get('label', val)}的用户。"
            style = v.get("speaking_style") or "自然口语。"
            out[val] = (desc, style)
    return out


def dimension_keys() -> list[str]:
    """所有维度的 key 列表。"""
    return [d["key"] for d in load_dimensions().get("dimensions", []) if d.get("key")]
