"""单次测试详情页 —— 一次 test (run) 的元信息 + 进度 + 报告 + 建议 + 对比。

从任务概览(task_overview)点测试卡片进入。聚焦本次 test。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml

from claw_eval.db import get_run, list_runs as db_list_runs
from claw_eval.editor._utils import (
    REPORTS_DIR,
    ROOT,
    TASKS_DIR,
    list_tasks,
)
from claw_eval.task_gen.versioning import list_versions


def render_test_detail() -> None:
    task = st.session_state.get("current_task")
    test_id = st.session_state.get("current_test_id")
    if not task or not test_id:
        st.session_state["view"] = "list"; st.rerun(); return
    if task not in list_tasks():
        st.session_state["view"] = "list"; st.rerun(); return

    test = get_run(test_id)
    if not test:
        st.error(f"测试 {test_id} 不在数据库里")
        if st.button("← 返回任务概览"):
            st.session_state["view"] = "task_overview"; st.rerun()
        return

    task_dir = TASKS_DIR / task

    # ----- 顶部 -----
    c_back, c_title = st.columns([1, 5])
    if c_back.button("← 返回任务概览", key="back_to_overview"):
        st.session_state["view"] = "task_overview"; st.rerun()
    c_title.title(f"🧪 测试:{test_id}")

    # ----- 元信息卡 -----
    status_badge = {
        "running": '<span style="color:#3370ff;">⏳ 跑批中</span>',
        "done": '<span style="color:#22c55e;">✓ 完成</span>',
        "failed": '<span style="color:#ef4444;">✗ 失败</span>',
        "created": '<span style="color:#94a3b8;">⏳ 配置中</span>',
    }.get(test.get("status"), test.get("status", "?"))

    pr = test.get("pass_rate")
    pr_str = f"{pr * 100:.1f}%" if pr is not None else "—"
    pr_color = ("#22c55e" if (pr or 0) >= 0.5 else
                 "#eab308" if (pr or 0) >= 0.2 else
                 "#ef4444" if pr is not None else "#94a3b8")
    score = test.get("task_score_avg")
    score_str = f"{score:.3f}" if score is not None else "—"

    st.markdown(f"""
<div class="eval-card">
  <div style="display:flex; justify-content:space-between; gap:24px; flex-wrap:wrap;">
    <div>
      <div style="color:#64748b; font-size:0.85rem;">所属任务</div>
      <div style="font-weight:600;">📋 {task}</div>
    </div>
    <div>
      <div style="color:#64748b; font-size:0.85rem;">状态</div>
      <div>{status_badge}</div>
    </div>
    <div>
      <div style="color:#64748b; font-size:0.85rem;">创建时间</div>
      <div>{test['created_at'][:16]}</div>
    </div>
    <div>
      <div style="color:#64748b; font-size:0.85rem;">agent 版本</div>
      <div><code>{test.get('agent_version') or '—'}</code></div>
    </div>
    <div>
      <div style="color:#64748b; font-size:0.85rem;">case 数</div>
      <div style="font-weight:700;">{test.get('n_results', 0)}</div>
    </div>
    <div>
      <div style="color:#64748b; font-size:0.85rem;">通过率</div>
      <div style="color:{pr_color}; font-size:1.4rem; font-weight:700;">{pr_str}</div>
    </div>
    <div>
      <div style="color:#64748b; font-size:0.85rem;">task_score 平均</div>
      <div style="font-weight:600;">{score_str}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ----- 4 步进度条 -----
    ms = _test_milestones(test, task)
    keys = ["m1", "m2", "m3", "m4"]
    labels = ["① 配置", "② 评测", "③ 报告", "④ 建议"]
    next_idx = next((i for i, k in enumerate(keys) if not ms[k]), None)
    pills = []
    for i, (k, lbl) in enumerate(zip(keys, labels)):
        if ms[k]:
            cls = "milestone-pill done"; mark = "✓ "
        elif i == next_idx:
            cls = "milestone-pill current"; mark = "⏳ "
        else:
            cls = "milestone-pill"; mark = ""
        pills.append(f'<span class="{cls}">{mark}{lbl}</span>')
    arrow = '<span class="milestone-arrow">→</span>'
    st.markdown(f"""
<div style="margin: 12px 0;">
  <strong style="margin-right:10px;">进度:</strong>
  {arrow.join(pills)}
</div>
""", unsafe_allow_html=True)

    # 下一步 hint
    if next_idx is not None:
        hints = [
            ("配置", "等跑批参数 ready"),
            ("评测", f"等待 batch 跑完 — 当前 status={test.get('status')}"),
            ("报告", "去「📊 报告」Tab,点「重生成 dashboard」"),
            ("建议", "去「💡 建议」Tab,点「跑 recommend」(LLM,~3-5 min)"),
        ]
        nlbl, nhint = hints[next_idx]
        st.markdown(f"""
<div class="next-step-hint">
  <div class="label">⏭ 下一步:<strong>{nlbl}</strong></div>
  <div style="font-size:0.92rem; color:#475569;">{nhint}</div>
</div>""", unsafe_allow_html=True)

    # ----- 跑批参数 -----
    with st.expander("📋 测试参数(只读)", expanded=False):
        params = test.get("params", {})
        st.json(params)

    # ----- 3 Tab -----
    tab_report, tab_rec, tab_cmp = st.tabs([
        "📊 报告", "💡 建议 + 自动应用", "🔄 对比其他测试"
    ])
    with tab_report: _tab_report(task, task_dir)
    with tab_rec: _tab_recommend(task, task_dir)
    with tab_cmp: _tab_compare(task, test_id)


# ============================ 工具 ============================

def _test_milestones(test: dict, task: str) -> dict:
    m1 = True
    m2 = test.get("status") == "done"
    m3 = (REPORTS_DIR / f"task_{task}.html").exists()
    m4 = False
    rec = REPORTS_DIR / f"recommendations_{task}.json"
    if rec.exists() and test.get("created_at"):
        try:
            test_dt = datetime.fromisoformat(test["created_at"])
            rec_dt = datetime.fromtimestamp(rec.stat().st_mtime)
            m4 = rec_dt >= test_dt
        except Exception:
            m4 = True
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


# ============================ Tab:报告 ============================

def _tab_report(task: str, task_dir: Path) -> None:
    report_path = REPORTS_DIR / f"task_{task}.html"
    if report_path.exists():
        if st.button("🔄 重生成 dashboard", key=f"rg_{task}"):
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            with st.spinner("生成中…"):
                subprocess.run([sys.executable, "-m", "claw_eval.cli",
                                  "dashboard"], env=env, cwd=str(ROOT))
            st.rerun()
        st.caption(f"📄 reports/task_{task}.html · "
                    f"{report_path.stat().st_size // 1024} KB")
        components.html(report_path.read_text(encoding="utf-8"),
                         height=900, scrolling=True)
    else:
        st.caption("还没有报告。")
        if st.button("📊 生成 dashboard", key=f"gd_{task}"):
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            with st.spinner("生成中…"):
                subprocess.run([sys.executable, "-m", "claw_eval.cli",
                                  "dashboard"], env=env, cwd=str(ROOT))
            st.rerun()


# ============================ Tab:建议 ============================

def _tab_recommend(task: str, task_dir: Path) -> None:
    from claw_eval.task_gen.apply_recommendation import (
        diff_stats, generate_prompt_patch, unified_diff,
    )
    from claw_eval.task_gen.versioning import save_version

    versions = list_versions(task_dir)

    rec_file = REPORTS_DIR / f"recommendations_{task}.json"
    if st.button("🔄 跑 recommend(LLM 3-5min)", key=f"rec_{task}"):
        cmd = [sys.executable, "-m", "claw_eval.cli", "recommend",
               "--task", task]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        with st.spinner("分析中…"):
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                      env=env, cwd=str(ROOT))
        st.code(proc.stdout[-2000:], language="text")
        if proc.returncode == 0:
            st.rerun()

    if not rec_file.exists():
        st.info("还没建议。点上方按钮跑 recommend。")
        return

    data = json.loads(rec_file.read_text(encoding="utf-8"))
    recs = data.get("recommendations", [])
    st.caption(f"上次:{data.get('generated_at', '?')}({len(recs)} 条)")

    applied = set()
    for v in versions:
        applied.update(v.applied_recs)

    for i, r in enumerate(recs, 1):
        rid = r["rubric_id"]
        is_applied = rid in applied
        with st.expander(
            f"[{i}] **{rid}** · avg={r['avg_score']:.2f}"
            + (f" · 预期 +{r['estimated_lift']:.2f}"
               if r.get('estimated_lift') else "")
            + (" ✓" if is_applied else ""),
            expanded=(i == 1 and not is_applied),
        ):
            if r.get("suggested_prompt_change"):
                st.markdown(r["suggested_prompt_change"])
            if r.get("rationale"):
                st.caption(r["rationale"])
            patch_key = f"p_{task}_{rid}"
            if not is_applied and st.button(
                "🤖 自动应用", key=f"ap_{task}_{rid}",
                disabled=not r.get("suggested_prompt_change")):
                with st.spinner("LLM 改写中(~30s)…"):
                    with open(ROOT / "configs" / "models.yaml") as f:
                        cfg = yaml.safe_load(f)
                    jm = cfg["judge"]["model"]
                    cur = yaml.safe_load(
                        (task_dir / "task.yaml").read_text(encoding="utf-8"))
                    new = generate_prompt_patch(cur.get("prompt", ""), r, jm)
                    st.session_state[patch_key] = {
                        "old": cur.get("prompt", ""), "new": new,
                        "cur_data": cur}
            if patch_key in st.session_state:
                p = st.session_state[patch_key]
                st.markdown(f"**Diff** 加 {diff_stats(p['old'], p['new'])['added']} "
                             f"删 {diff_stats(p['old'], p['new'])['removed']} 行")
                st.code(unified_diff(p["old"], p["new"]), language="diff")
                cA, cR = st.columns(2)
                if cA.button("✓ 接受", type="primary",
                               key=f"acc_{task}_{rid}"):
                    new_lbl = f"vN_{datetime.now().strftime('%m%d_%H%M')}_{rid.replace('.','_')}"
                    if not versions:
                        save_version(task_dir, "v1")
                    p["cur_data"]["prompt"] = p["new"]
                    (task_dir / "task.yaml").write_text(
                        yaml.safe_dump(p["cur_data"], allow_unicode=True,
                                        sort_keys=False),
                        encoding="utf-8")
                    save_version(task_dir, new_lbl,
                                  based_on=versions[-1].label if versions else "v1",
                                  applied_recs=[rid])
                    st.success(f"✓ 新版本 {new_lbl} - 下次新建测试用这个版本")
                    st.session_state.pop(patch_key, None); st.rerun()
                if cR.button("✗ 拒绝", key=f"rej_{task}_{rid}"):
                    st.session_state.pop(patch_key, None); st.rerun()


# ============================ Tab:对比 ============================

def _tab_compare(task: str, current_test_id: str) -> None:
    st.markdown("**跟其他测试做回归对比**")
    all_tests = db_list_runs(task_id=task)
    other_tests = [t for t in all_tests if t["run_id"] != current_test_id]
    if not other_tests:
        st.info("没有其他测试可对比。")
        return

    other_ids = [t["run_id"] for t in other_tests]
    c1, c2 = st.columns([2, 1])
    other = c1.selectbox("跟哪个测试对比?", other_ids,
                            key=f"cmp_{current_test_id}")
    thr = c2.slider("阈值", 0.01, 0.30, 0.05, 0.01,
                       key=f"thr_{current_test_id}")
    if st.button("🔄 跑回归对比", type="primary",
                  key=f"rcmp_{current_test_id}"):
        cmd = [sys.executable, "-m", "claw_eval.cli", "regression",
               "--task", task, "--old", other, "--new", current_test_id,
               "--threshold", str(thr)]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        with st.spinner("跑中…"):
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                      env=env, cwd=str(ROOT))
        st.code(proc.stdout, language="text")
        st.rerun()

    reg_file = REPORTS_DIR / f"regression_{task}.json"
    if reg_file.exists():
        data = json.loads(reg_file.read_text(encoding="utf-8"))
        if (data.get("new_label") == current_test_id
                or data.get("old_label") == current_test_id):
            st.markdown(f"### {data['old_label']} → {data['new_label']}")
            st.markdown(
                f"{data.get('n_improvements', 0)} 改进 · "
                f"{data.get('n_regressions', 0)} 退化")
            st.dataframe(pd.DataFrame([
                {"指标": "case 数", "old": data['old_total'], "new": data['new_total']},
                {"指标": "task_score",
                 "old": f"{data['old_score_avg']:.3f}",
                 "new": f"{data['new_score_avg']:.3f}"},
                {"指标": "通过率",
                 "old": f"{data['old_pass_rate']*100:.0f}%",
                 "new": f"{data['new_pass_rate']*100:.0f}%"},
            ]), hide_index=True, use_container_width=True)
            if data.get("by_rubric"):
                changes = [r for r in data["by_rubric"]
                           if r.get("significance") != "flat"]
                if changes:
                    st.markdown(f"**rubric 变化({len(changes)} 条)**")
                    st.dataframe(pd.DataFrame([
                        {"Rubric": r["rubric_id"],
                         "old": f"{r['old_avg']:.2f}" if r.get("old_avg") is not None else "—",
                         "new": f"{r['new_avg']:.2f}" if r.get("new_avg") is not None else "—",
                         "Δ": f"{r['delta']:+.2f}" if r.get("delta") is not None else "—",
                         "类型": r["significance"]} for r in changes
                    ]), hide_index=True, use_container_width=True)
