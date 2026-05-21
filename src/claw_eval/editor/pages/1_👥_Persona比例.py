"""Persona 比例 + 噪音 overlay 管理 —— 编辑 sampling.yaml。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from claw_eval.editor._utils import (
    NOISE_FILE,
    TASKS_DIR,
    list_personas,
    list_tasks,
)
from claw_eval.models.persona import load_noise_kinds
from claw_eval.sampling import (
    NoiseOverlay,
    SamplingConfig,
    load_sampling,
    save_sampling,
)

st.set_page_config(page_title="Persona 比例", page_icon="👥", layout="wide")
st.title("👥 Persona 比例管理")
st.caption("可视化编辑 `sampling.yaml`:用户类型比例(weights)+ 噪音 overlay(整通带噪 case 占比)。")

# ---------- 选任务 ----------
tasks = list_tasks()
if not tasks:
    st.error("`tasks/` 下没有任务。"); st.stop()
task = st.selectbox("任务", tasks, key="ratio_task")
task_dir = TASKS_DIR / task
sampling_path = task_dir / "sampling.yaml"

if sampling_path.exists():
    cfg = load_sampling(sampling_path)
else:
    cfg = SamplingConfig()

st.caption(f"配置文件:`{sampling_path.relative_to(TASKS_DIR.parent)}`"
           + ("" if sampling_path.exists() else "(新建)"))

# ---------- 用户类型比例 ----------
st.markdown("### ① 用户类型比例(weights)")
st.caption("不要求和为 100;大余数法按权重分配 `--total` 个 trial。对抗 persona (adv_*) 不进 batch,在「安全红队」页单独跑。")

personas_in_task = [p for p in list_personas(task) if not p.startswith("adv_")]
# 把现有 weights 和当前 personas 合并
rows = []
for p in personas_in_task:
    rows.append({"persona": p, "weight": float(cfg.weights.get(p, 0))})
# 也保留 sampling.yaml 里出现但 persona 已删的(让用户能看到 + 删除)
for p in cfg.weights:
    if p not in personas_in_task and not p.startswith("adv_"):
        rows.append({"persona": p, "weight": float(cfg.weights[p]),
                     "_warning": "persona 文件已不存在"})

df = pd.DataFrame(rows) if rows else pd.DataFrame(
    columns=["persona", "weight"])

edited = st.data_editor(
    df, hide_index=True, use_container_width=True,
    column_config={
        "persona": st.column_config.TextColumn("Persona", required=True),
        "weight": st.column_config.NumberColumn(
            "权重", min_value=0.0, step=1.0,
            help="任意非负数,不需要和为 100"),
        "_warning": st.column_config.TextColumn("⚠", disabled=True),
    },
    key=f"weights_{task}", num_rows="fixed",
)

# 实时算分配预览
total_preview = st.number_input(
    "预览:total = ", min_value=0, max_value=1000, value=30, step=10,
    help="假设跑 N 个 trial,看每 persona 分到几个")

new_weights: dict[str, float] = {}
for _, r in edited.iterrows():
    name = str(r.get("persona") or "").strip()
    if not name:
        continue
    new_weights[name] = float(r.get("weight") or 0)

if new_weights and total_preview > 0:
    from claw_eval.sampling import allocate
    alloc = allocate(new_weights, total_preview)
    total_w = sum(v for v in new_weights.values() if v > 0)

    # 饼图(用 plotly 比 matplotlib 中文友好)
    try:
        import plotly.express as px
        pie_df = pd.DataFrame([
            {"persona": k, "weight": v, "比例": f"{v/total_w*100:.0f}%" if total_w else "0%"}
            for k, v in new_weights.items() if v > 0
        ])
        col_l, col_r = st.columns([2, 3])
        with col_l:
            if not pie_df.empty:
                fig = px.pie(pie_df, names="persona", values="weight",
                              hole=0.3, height=320)
                fig.update_traces(textinfo="label+percent")
                st.plotly_chart(fig, use_container_width=True)
        with col_r:
            st.caption(f"按当前权重 + total={total_preview} 分配:")
            alloc_df = pd.DataFrame([
                {"persona": k, "权重": new_weights.get(k, 0),
                 "分配 trial": v}
                for k, v in sorted(alloc.items(), key=lambda x: -x[1])
                if v > 0
            ])
            st.dataframe(alloc_df, hide_index=True,
                          use_container_width=True)
    except ImportError:
        st.info("装上 plotly 可看饼图:`pip install plotly`")
        st.dataframe(pd.DataFrame(list(alloc.items()),
                                    columns=["persona", "trial 数"]),
                      hide_index=True)

# ---------- 噪音 overlay ----------
st.markdown("### ② 噪音 overlay(case-level)")
st.caption("**整 case 加噪 + 全程必噪**:命中 overlay 的 case,该通对话每轮都加噪。"
           "rate=0.1 表示 10% 的 case 是噪音 case;rate=0 不加噪。")

c1, c2 = st.columns([1, 2])
new_rate = c1.slider(
    "rate(噪音 case 占比)", 0.0, 1.0, float(cfg.noise_overlay.rate), 0.05,
    key=f"noise_rate_{task}",
    help="0 = 全部干净;0.10 = 10% 的 case 整通带噪")

try:
    kinds_lib = load_noise_kinds(NOISE_FILE)
except Exception:  # noqa: BLE001
    kinds_lib = {}
new_kinds = c2.multiselect(
    "kinds(命中时随机抽一种)", list(kinds_lib.keys()),
    default=[k for k in cfg.noise_overlay.kinds if k in kinds_lib],
    key=f"noise_kinds_{task}",
    help="种类来自 configs/noise_profiles.yaml")

# 展开各噪音种类的指令说明
if new_kinds:
    with st.expander("展开看选中种类的具体指令"):
        for k in new_kinds:
            kind = kinds_lib.get(k)
            if kind:
                st.markdown(f"**{k}**:{kind.instruction}")

# ---------- 保存 ----------
st.markdown("### ③ YAML 预览 + 保存")
draft = SamplingConfig(
    weights={k: v for k, v in new_weights.items() if v > 0},
    noise_overlay=NoiseOverlay(rate=new_rate, kinds=new_kinds),
)

import yaml as _yaml
draft_data: dict = {"weights": draft.weights}
if draft.noise_overlay.rate > 0 or draft.noise_overlay.kinds:
    draft_data["noise_overlay"] = draft.noise_overlay.model_dump()
yaml_text = _yaml.safe_dump(draft_data, allow_unicode=True,
                              sort_keys=False, default_flow_style=False)
st.code(yaml_text, language="yaml")

if st.button("💾 保存到 sampling.yaml", type="primary",
             disabled=not draft.weights):
    save_sampling(draft, sampling_path)
    st.success(f"✓ 已保存到 {sampling_path.relative_to(TASKS_DIR.parent)}")
    st.balloons()
elif not draft.weights:
    st.warning("先给至少一个 persona 加权重再保存")
