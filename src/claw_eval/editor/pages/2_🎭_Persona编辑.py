"""Persona 编辑器 —— Streamlit 单页 UI。

启动:
  claw-eval editor          # 推荐
  streamlit run src/claw_eval/editor/app.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from claw_eval.editor.dot import build_dot
from claw_eval.models.persona import (
    NoiseSpec,
    PersonaScript,
    ProbeConfig,
    load_noise_kinds,
    load_personality,
)
from claw_eval.user_simulator.state_machine import StateMachine

# 移到 pages/ 后,直接用共享工具拿根目录
from claw_eval.editor._utils import (
    NOISE_FILE as _NOISE_FILE,
    PERSONALITIES_DIR as _PERS_DIR,
    ROOT as _ROOT,
    TASKS_DIR as _TASKS_DIR,
)


# ============================== 工具函数 ==============================

def list_tasks() -> list[str]:
    return sorted(d.name for d in _TASKS_DIR.iterdir() if d.is_dir())


def list_personas(task: str) -> list[str]:
    return sorted(p.stem for p in (_TASKS_DIR / task / "personas").glob("*.yaml"))


def list_personalities() -> list[str]:
    return sorted(p.stem for p in _PERS_DIR.glob("*.yaml"))


def load_script(task: str, persona: str) -> PersonaScript:
    path = _TASKS_DIR / task / "personas" / f"{persona}.yaml"
    with open(path, encoding="utf-8") as f:
        return PersonaScript.model_validate(yaml.safe_load(f))


def blank_script() -> PersonaScript:
    return PersonaScript(
        id="new_persona",
        personality=(list_personalities() or ["cooperative"])[0],
        states={"接听": "你刚接起电话,礼貌应答一声。"},
        initial_state="接听",
        transitions={"接听": "END"},
        max_rounds=6,
    )


def script_to_yaml(script: PersonaScript) -> str:
    """剧本 → 可读 YAML(中文不转义,字段保持声明顺序)。"""
    data = script.model_dump(exclude_none=True)
    # 噪音默认 → 直接省掉这一段,YAML 干净
    if data.get("noise") == {"rate": 0.0, "kinds": []}:
        data.pop("noise", None)
    if not data.get("probes"):
        data.pop("probes", None)
    if not data.get("name"):
        data.pop("name", None)
    return yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def validate_script(script: PersonaScript) -> list[tuple[str, str]]:
    """返回 (level, message) 列表。level ∈ {error, warning, info}。"""
    issues: list[tuple[str, str]] = []

    if not script.id or not script.id.replace("_", "").isalnum():
        issues.append(("error", f"id 不合法: '{script.id}'(仅字母数字下划线)"))

    if script.personality not in list_personalities():
        issues.append(("error",
                       f"性格 '{script.personality}' 在 personalities/ 里不存在"))

    if not script.states:
        issues.append(("error", "至少要有一个状态"))
    if script.initial_state not in script.states:
        issues.append(("error",
                       f"初始状态 '{script.initial_state}' 不在 states 列表中"))

    # 转移引用合法
    valid_targets = set(script.states.keys()) | {"END"}
    for fr, to in script.transitions.items():
        if fr not in script.states:
            issues.append(("error",
                           f"转移源 '{fr}' 不在 states 中"))
        if to not in valid_targets:
            issues.append(("error",
                           f"转移目标 '{to}' 既不在 states 也不是 END"))

    # 状态机能否到达 END
    try:
        # 用一个临时 Persona 跑(仅需 states / initial / transitions)
        from claw_eval.models.persona import Persona
        tmp = Persona(
            id=script.id, name=script.id, personality_id="x",
            description="d", speaking_style="s",
            states=script.states, initial_state=script.initial_state,
            transitions=script.transitions, probes=[], max_rounds=20,
        )
        sm = StateMachine(tmp)
        for _ in range(50):
            if sm.advance():
                break
        if not sm.finished:
            issues.append(("error", "状态机 50 步未到 END(可能有环或漏写转移)"))
    except Exception as exc:  # noqa: BLE001
        issues.append(("warning", f"状态机校验出错: {exc}"))

    # 探针 inject_at_turn 不重复
    turns = [p.inject_at_turn for p in script.probes]
    if len(turns) != len(set(turns)):
        issues.append(("warning", "多个探针的 inject_at_turn 重复 —— 后注入的会覆盖"))

    # 噪音 kinds 引用合法
    if script.noise.kinds:
        try:
            kinds_lib = load_noise_kinds(_NOISE_FILE)
            for k in script.noise.kinds:
                if k not in kinds_lib:
                    issues.append(("warning",
                                   f"noise.kinds 引用了不存在的种类 '{k}'"))
        except Exception:  # noqa: BLE001
            pass

    return issues


# ================================ 页面 ================================

st.set_page_config(page_title="Persona 编辑器", page_icon="🎭", layout="wide")
st.title("🎭 Persona 编辑器")
st.caption("性格库 · 任务剧本 · 噪音档,三层合成。修改后点底部「保存」写回 YAML。")

# ---- Sidebar:任务 + persona 选择 ----
with st.sidebar:
    st.markdown("### 选择")
    tasks = list_tasks()
    if not tasks:
        st.error("tasks/ 下没有任务")
        st.stop()
    task = st.selectbox("任务", tasks, key="task_select")

    personas = list_personas(task)
    options = ["✨ 新建 persona"] + personas
    chosen = st.radio("Persona", options, key=f"persona_radio_{task}")
    is_new = chosen == options[0]
    persona_name = "" if is_new else chosen

    st.markdown("---")
    st.markdown("### 性格库(只读)")
    for pid in list_personalities():
        try:
            p = load_personality(_PERS_DIR / f"{pid}.yaml")
            st.caption(f"**{p.name}** ({pid}) — {p.description[:30]}…")
        except Exception:  # noqa: BLE001
            pass

# ---- 当前剧本 ----
if is_new:
    base = blank_script()
    key_suffix = f"{task}__new"
else:
    base = load_script(task, persona_name)
    key_suffix = f"{task}__{persona_name}"

# ============================== 编辑表单 ==============================

st.markdown("### ① 基本信息")
c1, c2 = st.columns([1, 1])
new_id = c1.text_input("Persona ID", base.id, key=f"id_{key_suffix}")
new_name = c2.text_input("显示名(可选)", base.name, key=f"name_{key_suffix}")

personality_ids = list_personalities()
p_idx = personality_ids.index(base.personality) if base.personality in personality_ids else 0
new_personality = st.selectbox("性格底色", personality_ids, index=p_idx,
                                key=f"pers_{key_suffix}")
# 性格预览
try:
    p = load_personality(_PERS_DIR / f"{new_personality}.yaml")
    st.info(f"**{p.name}**\n\n{p.description}\n\n*说话风格:{p.speaking_style}*")
except Exception:
    pass

# --------------------------- 状态机 ---------------------------
st.markdown("### ② 状态机")
left, right = st.columns([1, 1])

with left:
    st.caption("**states** —— 状态名 + 该状态下用户该做的事")
    states_df = pd.DataFrame(
        [{"state": k, "instruction": v} for k, v in base.states.items()])
    edited_states = st.data_editor(
        states_df, num_rows="dynamic", key=f"states_{key_suffix}",
        use_container_width=True,
        column_config={
            "state": st.column_config.TextColumn("状态名", required=True),
            "instruction": st.column_config.TextColumn("指令(给模拟器看的)"),
        })

    st.caption("**transitions** —— 从 → 到(到 END 即终态)")
    trans_df = pd.DataFrame(
        [{"from": k, "to": v} for k, v in base.transitions.items()])
    edited_trans = st.data_editor(
        trans_df, num_rows="dynamic", key=f"trans_{key_suffix}",
        use_container_width=True,
        column_config={
            "from": st.column_config.TextColumn("从状态", required=True),
            "to": st.column_config.TextColumn("到状态", required=True),
        })

    # 当前状态列表
    current_states = [
        r["state"] for _, r in edited_states.iterrows()
        if pd.notna(r.get("state")) and str(r["state"]).strip()
    ]
    if current_states:
        init_idx = current_states.index(base.initial_state) \
            if base.initial_state in current_states else 0
        new_initial = st.selectbox("初始状态", current_states, index=init_idx,
                                    key=f"init_{key_suffix}")
    else:
        new_initial = ""
        st.warning("先在 states 表里加状态")

with right:
    st.caption("**状态图预览**(实时)")
    trans_pairs = [
        (str(r["from"]), str(r["to"])) for _, r in edited_trans.iterrows()
        if pd.notna(r.get("from")) and pd.notna(r.get("to"))
        and str(r.get("from")).strip() and str(r.get("to")).strip()
    ]
    if current_states and trans_pairs:
        dot = build_dot(current_states, trans_pairs, new_initial)
        st.graphviz_chart(dot, use_container_width=True)
    else:
        st.info("加状态和转移后会自动渲染")

# --------------------------- 探针 ---------------------------
st.markdown("### ③ 探针(在第 N 个用户轮强制注入话术,确保关键场景被覆盖)")
probes_df = pd.DataFrame([
    {"id": p.id, "inject_at_turn": p.inject_at_turn,
     "text": p.text, "description": p.description}
    for p in base.probes
])
if probes_df.empty:
    probes_df = pd.DataFrame(
        columns=["id", "inject_at_turn", "text", "description"])
edited_probes = st.data_editor(
    probes_df, num_rows="dynamic", key=f"probes_{key_suffix}",
    use_container_width=True,
    column_config={
        "id": st.column_config.TextColumn("探针 id"),
        "inject_at_turn": st.column_config.NumberColumn("注入第几用户轮", min_value=1),
        "text": st.column_config.TextColumn("强制注入的话术"),
        "description": st.column_config.TextColumn("说明 / 用来触发哪条 rubric"),
    })

# --------------------------- 噪音 ---------------------------
st.markdown("### ④ 噪音(per-turn 掷骰,可复现)")
try:
    kinds_lib = load_noise_kinds(_NOISE_FILE)
except Exception:  # noqa: BLE001
    kinds_lib = {}
c3, c4 = st.columns([1, 2])
new_noise_rate = c3.slider(
    "rate(每轮命中概率)", 0.0, 1.0, base.noise.rate, 0.05,
    key=f"noise_rate_{key_suffix}",
    help="0 = 全干净;0.25 = 1/4 轮带噪;1.0 = 每轮都脏")
new_noise_kinds = c4.multiselect(
    "kinds(命中时从中随机抽一种)", list(kinds_lib.keys()),
    default=[k for k in base.noise.kinds if k in kinds_lib],
    key=f"noise_kinds_{key_suffix}",
    help="种类来自 configs/noise_profiles.yaml")

# --------------------------- 其他 ---------------------------
new_max_rounds = st.number_input(
    "max_rounds(最大对话轮数)", 1, 30, base.max_rounds,
    key=f"max_rounds_{key_suffix}")

# ============================ 组装 + 校验 ============================

states_dict = {
    str(r["state"]).strip(): str(r.get("instruction") or "")
    for _, r in edited_states.iterrows()
    if pd.notna(r.get("state")) and str(r["state"]).strip()
}
trans_dict = {
    str(r["from"]).strip(): str(r["to"]).strip()
    for _, r in edited_trans.iterrows()
    if pd.notna(r.get("from")) and pd.notna(r.get("to"))
    and str(r["from"]).strip() and str(r["to"]).strip()
}
probes_list = []
for _, r in edited_probes.iterrows():
    if not (pd.notna(r.get("id")) and str(r["id"]).strip()):
        continue
    probes_list.append(ProbeConfig(
        id=str(r["id"]).strip(),
        inject_at_turn=int(r.get("inject_at_turn") or 1),
        text=str(r.get("text") or ""),
        description=str(r.get("description") or ""),
    ))

draft = PersonaScript(
    id=new_id.strip() or "unnamed",
    personality=new_personality,
    noise=NoiseSpec(rate=new_noise_rate, kinds=new_noise_kinds),
    name=new_name.strip(),
    states=states_dict,
    initial_state=new_initial,
    transitions=trans_dict,
    probes=probes_list,
    max_rounds=int(new_max_rounds),
)

st.markdown("### ⑤ 校验")
issues = validate_script(draft)
if not issues:
    st.success("✓ 全部检查通过,可以保存")
else:
    for level, msg in issues:
        if level == "error":
            st.error(f"✗ {msg}")
        elif level == "warning":
            st.warning(f"⚠ {msg}")
        else:
            st.info(f"· {msg}")

# --------------------------- YAML 预览 + 保存 ---------------------------
st.markdown("### ⑥ YAML 预览 + 保存")
yaml_text = script_to_yaml(draft)
st.code(yaml_text, language="yaml")

errors = [i for i in issues if i[0] == "error"]
c5, c6 = st.columns([1, 3])
save_clicked = c5.button("💾 保存为 YAML", type="primary",
                         disabled=bool(errors))
if save_clicked:
    target = _TASKS_DIR / task / "personas" / f"{draft.id}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml_text, encoding="utf-8")
    c6.success(f"✓ 已保存到 {target.relative_to(_ROOT)}")
    st.balloons()
elif errors:
    c6.warning("有 error,先解决再保存")
else:
    c6.caption(f"将保存到 tasks/{task}/personas/{draft.id}.yaml")
