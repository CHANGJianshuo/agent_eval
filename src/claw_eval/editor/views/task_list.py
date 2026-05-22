"""任务列表视图 —— 主入口。

特性:
  - 任务卡片网格(每卡片显示 4 里程碑 + 关键数字 + 简介)
  - 多选删除
  - 右上「➕ 新建任务」按钮(展开成 inline 表单)
"""
from __future__ import annotations

import json
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
    PERSONALITIES_DIR,
    REPORTS_DIR,
    ROOT,
    TASKS_DIR,
    TRACES_DIR,
    list_personalities,
    list_personas,
    list_tasks,
)
from claw_eval.models.rubric import load_rubrics
from claw_eval.task_gen.versioning import list_versions


# ============================ 工具 ============================

def _milestones_of(task: str) -> dict:
    """4 步:① 评测方案 ② 模拟用户 ③ 评测 ④ 报告。"""
    td = TASKS_DIR / task
    if not td.exists():
        return {"m1": False, "m2": False, "m3": False, "m4": False}

    # ① 评测方案:rubrics.yaml(转正) + grader.py
    m1 = (td / "rubrics.yaml").exists() and (td / "grader.py").exists()
    # ② 模拟用户:personas/ 至少 1 + sampling.yaml 配权重
    has_p = len(list_personas(task)) > 0
    has_sampling = (td / "sampling.yaml").exists()
    if has_sampling:
        try:
            sd = yaml.safe_load((td / "sampling.yaml").read_text(encoding="utf-8")) or {}
            has_weights = bool(sd.get("weights"))
        except Exception:
            has_weights = False
    else:
        has_weights = False
    m2 = has_p and has_weights
    # ③ 评测:至少 1 个 run
    runs_for_task = list_runs(task_id=task)
    m3 = len(runs_for_task) >= 1
    # ④ 报告:有 recommendations 或 至少 2 个 run
    has_rec = (REPORTS_DIR / f"recommendations_{task}.json").exists()
    m4 = has_rec or len(runs_for_task) >= 2
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


def _task_brief(task: str) -> str:
    """读 task.yaml 的 description / 自动从 prompt 截"""
    td = TASKS_DIR / task
    yp = td / "task.yaml"
    if not yp.exists():
        return ""
    try:
        d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        if d.get("description"):
            return str(d["description"])[:80]
        # 退化:从 prompt 第一行非空提取
        prompt = str(d.get("prompt", ""))
        for line in prompt.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:80]
    except Exception:
        pass
    return ""


def _last_pass_rate(task: str) -> float | None:
    runs = list_runs(task_id=task, limit=1)
    if runs and runs[0].get("pass_rate") is not None:
        return runs[0]["pass_rate"]
    return None


# ============================ 新建任务表单 ============================

def render_new_task_form() -> None:
    """inline 表单。完成后用 generate-task CLI 跑生成。"""
    st.subheader("➕ 新建任务")
    cancel_c, _ = st.columns([1, 5])
    if cancel_c.button("← 取消", key="cancel_new"):
        st.session_state["show_new_task"] = False
        st.rerun()

    c1, c2 = st.columns([1, 1])
    task_id = c1.text_input("任务 ID(英文小写下划线)",
                              placeholder="如 live_upgrade_v2",
                              key="new_task_id")
    task_name = c2.text_input("任务名(可选,中文)",
                                 placeholder="如 课程平台直播升级通知",
                                 key="new_task_name")

    st.markdown("**任务 Prompt**(完整 SUT system prompt 描述):")
    prompt = st.text_area("", height=320, label_visibility="collapsed",
                            placeholder="贴整段任务描述,如\n# Role: ...\n# Task: ...\n# Conversation Flow:\n## Step 1: ...",
                            key="new_task_prompt")

    # 校验
    ok_id = bool(task_id and re.fullmatch(r"[a-z][a-z0-9_]*", task_id))
    ok_new = bool(task_id) and not (TASKS_DIR / task_id).exists()
    ok_prompt = len(prompt.strip()) > 50

    if task_id and not ok_id:
        st.warning("任务 ID 不合法(英文小写下划线开头)")
    elif task_id and not ok_new:
        st.warning(f"tasks/{task_id}/ 已存在,换个 ID 或先删除")
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
            # 写一行 description(若提供 task_name)
            if task_name:
                yp = TASKS_DIR / task_id / "task.yaml"
                d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
                d["description"] = task_name
                yp.write_text(yaml.safe_dump(d, allow_unicode=True,
                                                sort_keys=False), encoding="utf-8")
            st.success(f"✓ 已生成 tasks/{task_id}/")
            st.session_state["show_new_task"] = False
            st.session_state["current_task"] = task_id
            st.session_state["view"] = "detail"
            st.rerun()
        else:
            st.error(f"✗ 生成失败 exit={proc.returncode}")


# ============================ 任务表格 ============================

def render_task_list() -> None:
    # 顶部:标题 + 右上「➕ 新建」
    c_title, c_new = st.columns([5, 1])
    c_title.title("📋 任务列表")
    if c_new.button("➕ 新建任务", type="primary"):
        st.session_state["show_new_task"] = True
        st.rerun()

    if st.session_state.get("show_new_task"):
        render_new_task_form()
        st.markdown("---")

    tasks = list_tasks()
    if not tasks:
        st.info("还没有任务。点右上「➕ 新建任务」开始。")
        return

    # ----- 顶部 4 指标卡 -----
    n_runs_total = len(list_runs(limit=10000))
    today = datetime.now().date()
    n_today = sum(1 for r in list_runs(limit=200)
                   if r["created_at"][:10] == today.isoformat())
    n_personality = len(list_personalities())
    n_persona_total = sum(len(list_personas(t)) for t in tasks)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("任务数", len(tasks))
    c2.metric("用户模板", n_personality)
    c3.metric("Persona 总数", n_persona_total)
    c4.metric("今日跑 run", n_today, delta=f"总 {n_runs_total}")

    st.markdown("---")

    # ----- 表格 -----
    rows = []
    for t in tasks:
        td = TASKS_DIR / t
        ms = _milestones_of(t)
        stage_icons = (
            ("●" if ms["m1"] else "○") + " "
            + ("●" if ms["m2"] else "○") + " "
            + ("●" if ms["m3"] else "○") + " "
            + ("●" if ms["m4"] else "○")
        )
        rubric_n = 0
        try:
            if (td / "rubrics.yaml").exists():
                rubric_n = len(load_rubrics(td / "rubrics.yaml"))
            elif (td / "rubrics.draft.yaml").exists():
                rubric_n = len(load_rubrics(td / "rubrics.draft.yaml"))
        except Exception:
            pass
        p_list = list_personas(t)
        n_p = len([p for p in p_list if not p.startswith("adv_")])
        n_a = len([p for p in p_list if p.startswith("adv_")])
        n_v = len(list_versions(td))
        pr = _last_pass_rate(t)
        rows.append({
            "选": False,
            "任务 ID": t,
            "进度": stage_icons,
            "Rubric": rubric_n,
            "Persona": n_p,
            "对抗": n_a if n_a else "",
            "版本": f"v{n_v}" if n_v else "—",
            "最近通过率": f"{pr * 100:.0f}%" if pr is not None else "—",
            "简介": _task_brief(t),
        })
    df = pd.DataFrame(rows)
    edited = st.data_editor(
        df, hide_index=True, use_container_width=True,
        column_config={
            "选": st.column_config.CheckboxColumn("☐", width="small"),
            "任务 ID": st.column_config.TextColumn(disabled=True),
            "进度": st.column_config.TextColumn("① ② ③ ④", disabled=True),
            "Rubric": st.column_config.NumberColumn(disabled=True),
            "Persona": st.column_config.NumberColumn(disabled=True),
            "对抗": st.column_config.TextColumn(disabled=True),
            "版本": st.column_config.TextColumn(disabled=True),
            "最近通过率": st.column_config.TextColumn(disabled=True),
            "简介": st.column_config.TextColumn(disabled=True, width="large"),
        },
        key="task_list_table",
    )

    selected = [r["任务 ID"] for _, r in edited.iterrows() if r.get("选")]

    # ----- 操作行 -----
    c1, c2, c3 = st.columns([1, 1, 4])
    if c1.button(f"🗑 删除选中 ({len(selected)})", disabled=not selected,
                 type="secondary"):
        for t in selected:
            shutil.rmtree(TASKS_DIR / t)
        st.success(f"✓ 删了 {len(selected)} 个")
        st.rerun()

    st.markdown("---")
    st.markdown("**👇 点任务名进入详情**")

    # 任务点击按钮(用 button 一组,不直接放表格)
    cols = st.columns(min(4, len(tasks)))
    for i, t in enumerate(tasks):
        if cols[i % 4].button(f"→ {t}", key=f"go_{t}",
                                use_container_width=True):
            st.session_state["current_task"] = t
            st.session_state["view"] = "detail"
            st.rerun()
