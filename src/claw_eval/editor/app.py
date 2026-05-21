"""控制台首页 —— 任务总览。

Streamlit 自动把 pages/ 子目录里的 .py 做成左侧导航菜单。
启动:claw-eval editor(或 streamlit run src/claw_eval/editor/app.py)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from claw_eval.editor._utils import (
    REPORTS_DIR,
    TASKS_DIR,
    TRACES_DIR,
    list_personas,
    list_runs,
    list_tasks,
)
from claw_eval.models.rubric import load_rubrics


st.set_page_config(
    page_title="claw-eval 控制台",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🎯 claw-eval 评测控制台")
st.caption("一站式管理:Persona 比例 · 编辑 · 报告查看 · 跑批 · 回归 · 安全红队")

# --------------------- 侧边栏 ---------------------
with st.sidebar:
    st.markdown("### 导航")
    st.caption("点击下方页面切换功能区。")
    st.markdown("---")
    st.markdown("### 仓库信息")
    st.caption(f"任务数:{len(list_tasks())}")
    st.caption(f"历史 run:{len(list_runs())}")

# --------------------- 主区:任务总览 ---------------------
st.subheader("📋 任务总览")

tasks = list_tasks()
if not tasks:
    st.warning("`tasks/` 目录下没有任务。")
    st.stop()


def _task_summary(task: str) -> dict:
    """快速汇总单个任务的信息。"""
    td = TASKS_DIR / task
    n_rubrics = 0
    try:
        rb_path = td / "rubrics.yaml"
        if rb_path.exists():
            n_rubrics = len(load_rubrics(rb_path))
    except Exception:  # noqa: BLE001
        pass

    n_personas = len(list_personas(task))
    n_adv = sum(1 for p in list_personas(task) if p.startswith("adv_"))

    sampling_info = "—"
    sp = td / "sampling.yaml"
    if sp.exists():
        try:
            data = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            w = data.get("weights", {})
            ov = data.get("noise_overlay", {})
            ov_rate = ov.get("rate", 0) if isinstance(ov, dict) else 0
            sampling_info = f"{len(w)} 类比例" + (f" · 噪音 {int(ov_rate*100)}%" if ov_rate else "")
        except Exception:  # noqa: BLE001
            pass

    has_flow = (td / "flow.yaml").exists()

    return {
        "任务": task,
        "Rubric 数": n_rubrics,
        "Persona 数": n_personas,
        "对抗 persona": n_adv,
        "采样配置": sampling_info,
        "流程图": "✓" if has_flow else "—",
    }


df = pd.DataFrame([_task_summary(t) for t in tasks])
st.dataframe(df, hide_index=True, use_container_width=True)

# --------------------- 快捷入口卡片 ---------------------
st.markdown("---")
st.subheader("🚀 快捷入口")
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**👥 Persona 比例**")
    st.caption("可视化编辑 sampling.yaml,管理用户类型比例 + 噪音 overlay。")
with c2:
    st.markdown("**🎭 Persona 编辑**")
    st.caption("选性格 + 画状态机 + 配探针 + 设噪音 + 实时校验 + 保存 YAML。")
with c3:
    st.markdown("**📊 报告查看**")
    st.caption("嵌入查看 dashboard,跨任务/任务详情/单 case 多页 HTML。")

c4, c5, c6 = st.columns(3)
with c4:
    st.markdown("**🏃 跑批评测**")
    st.caption("触发 batch,UI 配 persona/total/label,后台跑进度可见。")
with c5:
    st.markdown("**📈 回归对比**")
    st.caption("选两个 run 对比,看 task_score/rubric/persona 三层 diff。")
with c6:
    st.markdown("**🔴 安全红队**")
    st.caption("对抗 persona × safety 专项,看 SUT 在攻击下破防率。")

st.markdown("---")
st.caption("👈 用左侧菜单切到具体页面。所有改动写到 `tasks/` `personalities/` `traces/` 三个根目录。")

# --------------------- 历史 runs ---------------------
runs = list_runs()
if runs:
    st.markdown("---")
    st.subheader("⌛ 历史 run")
    run_rows = []
    for r in sorted(runs, reverse=True)[:12]:
        rdir = TRACES_DIR / r
        n_results = len(list(rdir.glob("*.result.json")))
        n_jsonl = len(list(rdir.glob("*.jsonl")))
        run_rows.append({"run_id": r, "trace 数": n_jsonl,
                         "result 数": n_results})
    st.dataframe(pd.DataFrame(run_rows), hide_index=True,
                  use_container_width=True)
