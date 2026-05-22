"""📊 工作台主页 —— Action Items + 任务卡片网格。

每个任务一张卡(阶段徽章 + 关键数字),顶部是 4 个汇总指标 + 待办列表。
点「→ 进入详情」跳任务详情页。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
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
from claw_eval.task_gen.versioning import list_versions


st.set_page_config(
    page_title="claw-eval · 工作台",
    page_icon="🎯",
    layout="wide",
)
inject_global_style()

st.title("🎯 工作台")
st.caption("Action Items + 任务进度一览;点任务卡片进入详情管理。")


# ============================ 顶部 4 指标卡 ============================

def _stats() -> dict:
    today = datetime.now().date()
    n_today_results = 0
    n_runs_in_24h = 0

    if TRACES_DIR.exists():
        for rdir in TRACES_DIR.iterdir():
            if not rdir.is_dir():
                continue
            for rj in rdir.glob("*.result.json"):
                mtime = datetime.fromtimestamp(rj.stat().st_mtime).date()
                if mtime == today:
                    n_today_results += 1
            # 看 trace 文件新旧
            jsonls = list(rdir.glob("*.jsonl"))
            if jsonls and (datetime.now() - datetime.fromtimestamp(
                    max(j.stat().st_mtime for j in jsonls))) < timedelta(hours=24):
                n_runs_in_24h += 1

    # 待人审 rubric
    n_pending_review = 0
    for task in list_tasks():
        draft = TASKS_DIR / task / "rubrics.draft.yaml"
        final = TASKS_DIR / task / "rubrics.yaml"
        if draft.exists():
            try:
                rs = yaml.safe_load(draft.read_text(encoding="utf-8")).get("rubrics", [])
                if not final.exists() or draft.stat().st_mtime > final.stat().st_mtime:
                    n_pending_review += len(rs)
            except Exception:
                pass

    # 待应用建议
    n_unapplied = 0
    for task in list_tasks():
        rec_file = REPORTS_DIR / f"recommendations_{task}.json"
        if not rec_file.exists():
            continue
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
            recs = data.get("recommendations", [])
            applied = set()
            for v in list_versions(TASKS_DIR / task):
                applied.update(v.applied_recs)
            unapplied = [r for r in recs if r["rubric_id"] not in applied
                         and r.get("suggested_prompt_change")]
            n_unapplied += len(unapplied)
        except Exception:
            pass

    return {
        "today_results": n_today_results,
        "pending_review": n_pending_review,
        "unapplied_recs": n_unapplied,
        "active_runs_24h": n_runs_in_24h,
    }


stats = _stats()
c1, c2, c3, c4 = st.columns(4)
c1.metric("今日新增 case", stats["today_results"])
c2.metric("待人审 rubric", stats["pending_review"],
           delta="🔴" if stats["pending_review"] > 0 else None,
           delta_color="inverse")
c3.metric("待应用建议", stats["unapplied_recs"],
           delta="🟡" if stats["unapplied_recs"] > 0 else None,
           delta_color="inverse")
c4.metric("24h 内活跃 run", stats["active_runs_24h"])

# ============================ Action Items ============================

def _action_items_global() -> list[dict]:
    items = []
    for task in list_tasks():
        td = TASKS_DIR / task

        if not (td / "task.yaml").exists():
            items.append({"level": "info", "task": task,
                          "text": "未初始化", "action": "去新建任务"})
            continue

        # 草稿待审
        draft = td / "rubrics.draft.yaml"
        final = td / "rubrics.yaml"
        if draft.exists() and (not final.exists()
                                  or draft.stat().st_mtime > final.stat().st_mtime):
            try:
                n = len(yaml.safe_load(draft.read_text(encoding="utf-8")).get("rubrics", []))
                items.append({"level": "red", "task": task,
                              "text": f"{n} 条 rubric 草稿待审",
                              "action": "去审"})
            except Exception:
                pass

        # personas_draft 未挑
        pd_dir = td / "personas_draft"
        if pd_dir.exists():
            n = len(list(pd_dir.glob("*.yaml")))
            if n > 0:
                items.append({"level": "red", "task": task,
                              "text": f"{n} 个 persona 草稿待挑",
                              "action": "去看"})

        # 未应用建议
        rec_file = REPORTS_DIR / f"recommendations_{task}.json"
        if rec_file.exists():
            try:
                data = json.loads(rec_file.read_text(encoding="utf-8"))
                recs = data.get("recommendations", [])
                applied = set()
                for v in list_versions(td):
                    applied.update(v.applied_recs)
                unapplied = [r for r in recs if r["rubric_id"] not in applied
                             and r.get("suggested_prompt_change")]
                if unapplied:
                    items.append({"level": "yellow", "task": task,
                                  "text": f"{len(unapplied)} 条新建议未应用",
                                  "action": "去应用"})
            except Exception:
                pass

        # 还没跑过 batch
        task_runs = set()
        if TRACES_DIR.exists():
            for rdir in TRACES_DIR.iterdir():
                if rdir.is_dir():
                    for rj in rdir.glob("*.result.json"):
                        try:
                            if json.loads(rj.read_text(encoding="utf-8")).get("task_id") == task:
                                task_runs.add(rdir.name); break
                        except Exception:
                            pass
        if not task_runs and final.exists():
            items.append({"level": "blue", "task": task,
                          "text": "已审完,还没跑过基线 batch",
                          "action": "去跑"})

    return items


todos = _action_items_global()
if todos:
    st.markdown("### 📌 待办")
    for it in todos:
        icon = {"red": "🔴", "yellow": "🟡", "blue": "🔵",
                "info": "ℹ️"}.get(it["level"], "·")
        c_l, c_r = st.columns([5, 1])
        c_l.markdown(f"{icon} **{it['task']}** · {it['text']}")
        if c_r.button(f"→ {it['action']}", key=f"goto_{it['task']}_{it['text'][:10]}"):
            st.session_state["current_task"] = it["task"]
            st.switch_page("pages/0_📋_任务详情.py")
else:
    st.markdown("### ✓ 所有任务都没有待办")
    st.caption("可以继续优化:跑新一轮评测、加 persona、做安全红队 等。")


# ============================ 任务卡片网格 ============================

st.markdown("---")
st.markdown("### 📋 任务一览")

tasks = list_tasks()
if not tasks:
    st.info("`tasks/` 下没有任务。去「➕ 新建任务」开始。")
    st.stop()


def _milestones_of(task: str) -> int:
    """返回当前达成的里程碑数 1-3。"""
    td = TASKS_DIR / task
    has_task = (td / "task.yaml").exists()
    has_rubrics = ((td / "rubrics.yaml").exists() or (td / "rubrics.draft.yaml").exists())
    has_personas = len(list_personas(task)) > 0

    task_runs = set()
    if TRACES_DIR.exists():
        for rdir in TRACES_DIR.iterdir():
            if rdir.is_dir():
                for rj in rdir.glob("*.result.json"):
                    try:
                        if json.loads(rj.read_text(encoding="utf-8")).get("task_id") == task:
                            task_runs.add(rdir.name); break
                    except Exception:
                        pass

    m1 = has_task and has_rubrics and has_personas
    m2 = len(task_runs) >= 1
    m3 = len(task_runs) >= 2
    return sum([m1, m2, m3])


def _task_card(task: str) -> None:
    td = TASKS_DIR / task
    n_rubrics = 0
    try:
        if (td / "rubrics.yaml").exists():
            n_rubrics = len(load_rubrics(td / "rubrics.yaml"))
        elif (td / "rubrics.draft.yaml").exists():
            n_rubrics = len(load_rubrics(td / "rubrics.draft.yaml"))
    except Exception:
        pass
    pl = list_personas(task)
    n_personas = len([p for p in pl if not p.startswith("adv_")])
    n_adv = len([p for p in pl if p.startswith("adv_")])

    ms = _milestones_of(task)
    ms_html = ""
    for i in range(3):
        ms_html += (
            '<span style="color:#22c55e;font-size:1.1rem;">●</span>'
            if i < ms else
            '<span style="color:#e2e8f0;font-size:1.1rem;">○</span>'
        )
        if i < 2:
            ms_html += '<span style="color:#cbd5e1;margin:0 2px;">─</span>'

    versions = list_versions(td)
    ver_badge = (f'<span class="badge badge-gray">v{len(versions)}</span>'
                 if versions else "")

    st.markdown(f"""
<div class="eval-card">
  <div style="display:flex; justify-content:space-between; align-items:center;">
    <h4 style="margin:0;">🧱 {task}</h4>
    <span style="font-size:0.9rem;">{ms_html}</span>
  </div>
  <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
    <span class="badge badge-gray">📐 {n_rubrics} rubric</span>
    <span class="badge badge-gray">👥 {n_personas} persona</span>
    {f'<span class="badge badge-danger">🔴 {n_adv} 对抗</span>' if n_adv else ''}
    {ver_badge}
  </div>
</div>""", unsafe_allow_html=True)
    if st.button(f"→ 进入 {task}", key=f"enter_{task}"):
        st.session_state["current_task"] = task
        st.switch_page("pages/0_📋_任务详情.py")


cols = st.columns(2)
for i, task in enumerate(tasks):
    with cols[i % 2]:
        _task_card(task)


# ============================ 底部:系统概览 ============================
st.markdown("---")
c1, c2, c3 = st.columns(3)
c1.metric("性格库", len(list_personalities()))
c2.metric("历史 run", len(list_runs()))
total_personas = sum(len(list_personas(t)) for t in tasks)
c3.metric("Persona 总数", total_personas)
