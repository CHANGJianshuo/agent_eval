"""任务列表视图 —— 主入口。

修复 v2:
- 「进入」按钮挪到每行最右
- 新建任务表单加左上「← 返回」
- 多选删除用每行 checkbox(session_state)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from claw_eval.db import list_runs
from claw_eval.editor._utils import (
    REPORTS_DIR,
    ROOT,
    TASKS_DIR,
    list_personalities,
    list_personas,
    list_tasks,
)
from claw_eval.models.rubric import load_rubrics
from claw_eval.task_gen.versioning import list_versions


# ============================ 工具 ============================

def _milestones_of(task: str) -> dict:
    td = TASKS_DIR / task
    if not td.exists():
        return {"m1": False, "m2": False, "m3": False, "m4": False}
    m1 = (td / "rubrics.yaml").exists()
    has_p = len(list_personas(task)) > 0
    has_weights = False
    if (td / "sampling.yaml").exists():
        try:
            sd = yaml.safe_load((td / "sampling.yaml").read_text(encoding="utf-8")) or {}
            has_weights = bool(sd.get("weights"))
        except Exception:
            pass
    m2 = has_p and has_weights
    runs = list_runs(task_id=task)
    m3 = len(runs) >= 1
    has_rec = (REPORTS_DIR / f"recommendations_{task}.json").exists()
    m4 = has_rec or len(runs) >= 2
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


def _task_brief(task: str) -> str:
    yp = TASKS_DIR / task / "task.yaml"
    if not yp.exists():
        return ""
    try:
        d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        if d.get("description"):
            return str(d["description"])[:60]
        prompt = str(d.get("prompt", ""))
        for line in prompt.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:60]
    except Exception:
        pass
    return ""


def _last_pass_rate(task: str) -> float | None:
    runs = list_runs(task_id=task, limit=1)
    if runs and runs[0].get("pass_rate") is not None:
        return runs[0]["pass_rate"]
    return None


# ============================ 新建任务 inline 表单 ============================

def _render_new_task_form() -> None:
    c_back, c_title = st.columns([1, 5])
    if c_back.button("← 返回任务列表", key="back_new_task"):
        st.session_state["show_new_task"] = False
        st.rerun()
    c_title.subheader("➕ 新建任务")

    c1, c2 = st.columns([1, 1])
    task_id = c1.text_input("任务 ID(英文小写下划线)",
                              placeholder="如 live_upgrade_v2",
                              key="new_task_id")
    task_name = c2.text_input("任务名(可选,中文简介)",
                                 placeholder="如 课程平台直播升级通知",
                                 key="new_task_name")

    st.markdown("**任务 Prompt**(完整 SUT system prompt):")
    prompt = st.text_area("", height=320, label_visibility="collapsed",
                            placeholder="贴整段 prompt:# Role / # Task / # Conversation Flow ...",
                            key="new_task_prompt")

    ok_id = bool(task_id and re.fullmatch(r"[a-z][a-z0-9_]*", task_id))
    ok_new = bool(task_id) and not (TASKS_DIR / task_id).exists()
    ok_prompt = len(prompt.strip()) > 50

    if task_id and not ok_id:
        st.warning("任务 ID 不合法(英文小写下划线开头)")
    elif task_id and not ok_new:
        st.warning(f"tasks/{task_id}/ 已存在,换 ID 或先删除")
    elif prompt and not ok_prompt:
        st.warning("Prompt 太短(<50 字)")

    ready = ok_id and ok_new and ok_prompt

    if st.button("🚀 一键生成", type="primary", disabled=not ready):
        tmp_p = Path(f"/tmp/_gen_prompt_{task_id}.md")
        tmp_p.write_text(prompt, encoding="utf-8")
        cmd = [sys.executable, "-m", "claw_eval.cli", "generate-task",
               "--prompt", str(tmp_p), "--id", task_id]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        st.caption(f"`{' '.join(cmd)}`")
        log_pane = st.empty()
        with st.spinner("⏳ LLM 调用中(~3-5 分钟)…"):
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, cwd=str(ROOT), bufsize=1, text=True)
            lines = []
            while True:
                line = proc.stdout.readline() if proc.stdout else ""
                if not line and proc.poll() is not None:
                    break
                if line and not any(s in line for s in
                                       ("WARNING", "botocore", "LiteLLM")):
                    lines.append(line.rstrip())
                    log_pane.code("\n".join(lines[-25:]), language="text")
        if proc.returncode == 0:
            if task_name:
                yp = TASKS_DIR / task_id / "task.yaml"
                d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
                d["description"] = task_name
                yp.write_text(yaml.safe_dump(d, allow_unicode=True,
                                                sort_keys=False), encoding="utf-8")
            st.success(f"✓ 已生成 tasks/{task_id}/")
            st.session_state["show_new_task"] = False
            st.session_state["current_task"] = task_id
            st.session_state["view"] = "task_overview"
            st.rerun()
        else:
            st.error(f"✗ 生成失败 exit={proc.returncode}")


# ============================ 任务列表主体 ============================

def render_task_list() -> None:
    if st.session_state.get("show_new_task"):
        _render_new_task_form()
        return

    # 顶部:标题 + 右上「➕ 新建」
    c_title, c_new = st.columns([5, 1])
    c_title.title("📋 任务列表")
    if c_new.button("➕ 新建任务", type="primary"):
        st.session_state["show_new_task"] = True
        st.rerun()

    tasks = list_tasks()
    if not tasks:
        st.info("还没有任务。点右上「➕ 新建任务」开始。")
        return

    # ----- 顶部 4 指标 -----
    today = datetime.now().date()
    runs_all = list_runs(limit=10000)
    n_today = sum(1 for r in runs_all if r["created_at"][:10] == today.isoformat())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("任务数", len(tasks))
    c2.metric("用户模板", len(list_personalities()))
    c3.metric("Persona 总数", sum(len(list_personas(t)) for t in tasks))
    c4.metric("今日跑 run", n_today, delta=f"总 {len(runs_all)}")

    st.markdown("---")

    # ----- 表头 -----
    h_widths = [0.4, 1.6, 1.4, 0.7, 0.8, 0.7, 0.7, 2.5, 1]
    hcols = st.columns(h_widths)
    headers = ["☐", "任务 ID", "进度", "Rubric", "Persona", "版本",
                "通过率", "简介", ""]
    for hc, ht in zip(hcols, headers):
        hc.markdown(f"**{ht}**")
    st.markdown(
        '<hr style="margin: 4px 0 8px 0; border-color: var(--gray-200);">',
        unsafe_allow_html=True)

    # ----- 表体 -----
    if "selected_tasks" not in st.session_state:
        st.session_state["selected_tasks"] = set()

    selected = st.session_state["selected_tasks"]
    for t in tasks:
        cols = st.columns(h_widths)
        # ☐
        is_sel = t in selected
        new_sel = cols[0].checkbox("", value=is_sel,
                                       key=f"sel_{t}",
                                       label_visibility="collapsed")
        if new_sel != is_sel:
            if new_sel:
                selected.add(t)
            else:
                selected.discard(t)
        # 任务 ID
        cols[1].markdown(f"**{t}**")
        # 进度灯
        td = TASKS_DIR / t
        ms = _milestones_of(t)
        ms_str = ""
        for i, k in enumerate(["m1", "m2", "m3", "m4"]):
            done = ms[k]
            current = (not done and all(ms[f"m{j+1}"] for j in range(i)))
            color = "#22c55e" if done else "#eab308" if current else "#cbd5e1"
            ms_str += f'<span style="color:{color};font-size:1.05rem;">●</span>'
            if i < 3:
                ms_str += '<span style="color:#e2e8f0;">─</span>'
        cols[2].markdown(ms_str, unsafe_allow_html=True)
        # Rubric / Persona / Version
        n_rubric = 0
        try:
            if (td / "rubrics.yaml").exists():
                n_rubric = len(load_rubrics(td / "rubrics.yaml"))
            elif (td / "rubrics.draft.yaml").exists():
                n_rubric = len(load_rubrics(td / "rubrics.draft.yaml"))
        except Exception:
            pass
        cols[3].markdown(f"{n_rubric}")
        pl = list_personas(t)
        n_p = len([p for p in pl if not p.startswith("adv_")])
        n_a = len([p for p in pl if p.startswith("adv_")])
        cols[4].markdown(
            f"{n_p}" + (f' <span style="color:#ef4444;">+{n_a}对抗</span>'
                        if n_a else ""),
            unsafe_allow_html=True)
        n_v = len(list_versions(td))
        cols[5].markdown(f"v{n_v}" if n_v else "—")
        # 通过率
        pr = _last_pass_rate(t)
        if pr is not None:
            color = ("#22c55e" if pr >= 0.5
                      else "#eab308" if pr >= 0.2 else "#ef4444")
            cols[6].markdown(
                f'<span style="color:{color};font-weight:600;">{pr * 100:.0f}%</span>',
                unsafe_allow_html=True)
        else:
            cols[6].markdown("—")
        # 简介
        brief = _task_brief(t)
        cols[7].markdown(f'<span style="color:#475569;font-size:0.85rem;">{brief or "—"}</span>',
                          unsafe_allow_html=True)
        # 进入按钮(最右)
        if cols[8].button("→ 进入", key=f"enter_{t}",
                            use_container_width=True):
            st.session_state["current_task"] = t
            st.session_state["view"] = "task_overview"
            st.rerun()

    # ----- 底部操作 -----
    st.markdown(
        '<hr style="margin: 8px 0; border-color: var(--gray-200);">',
        unsafe_allow_html=True)
    c_l, c_r = st.columns([2, 4])
    if c_l.button(f"🗑 删除选中({len(selected)})",
                    disabled=not selected,
                    type="secondary"):
        for t in selected:
            shutil.rmtree(TASKS_DIR / t)
        st.session_state["selected_tasks"] = set()
        st.success(f"✓ 删除了 {len(selected)} 个任务")
        st.rerun()
    c_r.caption(f"已选 {len(selected)} 个 · 表格最右「→ 进入」单任务管理")
