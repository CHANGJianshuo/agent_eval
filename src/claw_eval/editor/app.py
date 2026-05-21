"""控制台首页 = 任务列表(任务驱动工作流)。

每个任务一张卡片,显示生命周期阶段 + 关键数字。点「进入」跳详情页。
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st
import yaml

from claw_eval.editor._utils import (
    PERSONALITIES_DIR,
    REPORTS_DIR,
    TASKS_DIR,
    TRACES_DIR,
    inject_global_style,
    list_personalities,
    list_personas,
    list_runs,
    list_tasks,
)
from claw_eval.models.rubric import load_rubrics


st.set_page_config(
    page_title="claw-eval 评测控制台",
    page_icon="🎯",
    layout="wide",
)
inject_global_style()

st.title("🎯 claw-eval 评测控制台")
st.caption("任务驱动工作流:每个任务一张卡片,显示阶段 + 关键数字。点「进入」管理。")


# --------------------- 工具:推断任务生命周期阶段 ---------------------

def _stage_of(task: str) -> tuple[str, str]:
    """返回 (stage_id, stage_label)。

    ① generated     —— 有 task.yaml(草稿/已建)
    ② confirmed     —— 有 rubrics.yaml + 至少 1 个 persona
    ③ baseline      —— 至少 1 个 run
    ④ improving     —— 至少 2 个 run(用于 regression)
    ⑤ finalized     —— 显式 marker(暂不做,留接口)
    """
    td = TASKS_DIR / task
    if not (td / "task.yaml").exists():
        return ("0", "未初始化")
    has_rubrics = (td / "rubrics.yaml").exists()
    has_personas = len(list_personas(task)) > 0
    if not (has_rubrics and has_personas):
        return ("1", "① 已生成(待审)")
    # 看是否有 run for this task
    task_runs = []
    if TRACES_DIR.exists():
        for rdir in TRACES_DIR.iterdir():
            if not rdir.is_dir():
                continue
            # 是否含此任务的 result.json
            for rj in rdir.glob("*.result.json"):
                try:
                    import json as _json
                    data = _json.loads(rj.read_text(encoding="utf-8"))
                    if data.get("task_id") == task:
                        task_runs.append(rdir.name)
                        break
                except Exception:  # noqa: BLE001
                    pass
    n_runs = len(set(task_runs))
    if n_runs == 0:
        return ("2", "② 已确认(待跑)")
    if n_runs == 1:
        return ("3", "③ 有 baseline")
    return ("4", "④ 改进迭代中")


_STAGE_BADGE = {
    "0": "badge-gray",
    "1": "badge-info",
    "2": "badge-info",
    "3": "badge-warning",
    "4": "badge-success",
}


# --------------------- 主区:任务列表 ---------------------

tasks = list_tasks()

# 顶部统计
c1, c2, c3, c4 = st.columns(4)
c1.metric("任务数", len(tasks))
c2.metric("性格库", len(list_personalities()))
c3.metric("历史 run", len(list_runs()))
total_personas = sum(len(list_personas(t)) for t in tasks)
c4.metric("Persona 总数", total_personas)

st.markdown("---")
st.subheader("📋 任务卡片")

if not tasks:
    st.info("`tasks/` 下没有任务。下轮做的「新建任务」页可以贴 prompt 一键生成。")
    st.stop()


def _task_card(task: str) -> None:
    td = TASKS_DIR / task
    stage_id, stage_label = _stage_of(task)
    badge_class = _STAGE_BADGE[stage_id]

    # 关键数字
    n_rubrics = 0
    try:
        if (td / "rubrics.yaml").exists():
            n_rubrics = len(load_rubrics(td / "rubrics.yaml"))
    except Exception:  # noqa: BLE001
        pass
    persona_list = list_personas(task)
    n_personas = len([p for p in persona_list if not p.startswith("adv_")])
    n_adv = len([p for p in persona_list if p.startswith("adv_")])

    sampling_info = ""
    sp = td / "sampling.yaml"
    if sp.exists():
        try:
            data = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            ov = data.get("noise_overlay", {})
            if isinstance(ov, dict) and ov.get("rate", 0) > 0:
                sampling_info = f"噪音 {int(ov['rate'] * 100)}%"
        except Exception:  # noqa: BLE001
            pass

    has_flow = (td / "flow.yaml").exists()

    # 卡片 HTML
    st.markdown(f"""
<div class="eval-card">
  <div style="display:flex; justify-content:space-between; align-items:center;">
    <h4 style="margin:0;">🧱 {task}</h4>
    <span class="badge {badge_class}">{stage_label}</span>
  </div>
  <div style="margin-top:10px; display:flex; gap:12px; flex-wrap:wrap;">
    <span class="badge badge-gray">📐 {n_rubrics} rubric</span>
    <span class="badge badge-gray">👥 {n_personas} persona</span>
    {f'<span class="badge badge-danger">🔴 {n_adv} 对抗</span>' if n_adv else ''}
    {f'<span class="badge badge-info">🌲 流程图</span>' if has_flow else ''}
    {f'<span class="badge badge-warning">{sampling_info}</span>' if sampling_info else ''}
  </div>
</div>
""", unsafe_allow_html=True)
    cols = st.columns([1, 1, 4])
    if cols[0].button("→ 进入详情", key=f"enter_{task}"):
        st.session_state["current_task"] = task
        st.switch_page("pages/0_📋_任务详情.py")
    if cols[1].button("📊 看报告", key=f"report_{task}"):
        st.session_state["current_task"] = task
        st.switch_page("pages/3_📊_报告查看.py")


cols = st.columns(2)
for i, task in enumerate(tasks):
    with cols[i % 2]:
        _task_card(task)

st.markdown("---")
st.caption("👈 左侧菜单可直接访问 Persona 编辑器 / 跑批 / 回归 / 红队 等通用工具。")
