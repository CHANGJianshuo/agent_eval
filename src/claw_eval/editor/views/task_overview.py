"""任务概览页 —— 中间层:任务的多次测试列表 + 新建测试 + 任务级配置。

3 层导航:
  任务列表 → [task_overview] → 单次测试详情(test_detail)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from claw_eval.db import list_runs as db_list_runs
from claw_eval.editor._utils import (
    NOISE_FILE,
    REPORTS_DIR,
    ROOT,
    TASKS_DIR,
    list_personas,
    list_tasks,
)
from claw_eval.editor.views.flow_dot import flow_to_dot
from claw_eval.models.flow import load_flow
from claw_eval.models.persona import load_noise_kinds, load_persona
from claw_eval.models.rubric import load_rubrics
from claw_eval.models.task import TaskDefinition
from claw_eval.sampling import (
    NoiseOverlay,
    SamplingConfig,
    load_sampling,
    save_sampling,
)
from claw_eval.task_gen.versioning import (
    list_versions,
    save_version,
    switch_to_version,
)


def render_task_overview() -> None:
    task = st.session_state.get("current_task")
    if not task or task not in list_tasks():
        st.session_state["view"] = "list"; st.rerun(); return

    task_dir = TASKS_DIR / task

    # ----- 顶部 -----
    c_back, c_title, c_ver = st.columns([1, 3, 2])
    if c_back.button("← 返回任务列表", key="back_to_list"):
        st.session_state["view"] = "list"; st.rerun()
    c_title.title(f"📋 {task}")
    versions = list_versions(task_dir)
    ver_labels = [v.label for v in versions] if versions else ["(无版本)"]
    selected_ver = c_ver.selectbox("当前 Prompt 版本",
                                      ver_labels,
                                      index=len(ver_labels) - 1,
                                      key=f"ovv_{task}")
    if versions and selected_ver != ver_labels[-1]:
        if c_ver.button("↩ 切回此版本", key=f"swov_{task}"):
            switch_to_version(task_dir, selected_ver); st.rerun()

    # 简介
    brief = _task_brief(task)
    if brief:
        st.caption(f"📝 {brief}")

    # ----- 测试列表 + 新建按钮 -----
    st.markdown("---")
    c_l, c_r = st.columns([5, 1])
    c_l.subheader("🧪 测试历史")
    if c_r.button("➕ 新建测试", type="primary", key=f"new_test_{task}"):
        st.session_state[f"show_new_test_{task}"] = True
        st.rerun()

    if st.session_state.get(f"show_new_test_{task}"):
        _render_new_test_form(task, task_dir, versions)
        st.markdown("---")

    # 测试卡片列表(从 DB 读 runs,作为 tests)
    tests = db_list_runs(task_id=task)
    if not tests:
        st.info("还没有测试。点右上「➕ 新建测试」开始。")
    else:
        for t in tests:
            _render_test_card(t, task)

    # ----- 任务级配置(可折叠)-----
    st.markdown("---")
    with st.expander("⚙️ 任务级配置(Prompt / Rubrics / 模拟用户 / 流程图)",
                       expanded=False):
        _render_task_config(task, task_dir, versions)


# ============================ 工具 ============================

def _task_brief(task: str) -> str:
    yp = TASKS_DIR / task / "task.yaml"
    if not yp.exists():
        return ""
    try:
        d = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
        if d.get("description"):
            return str(d["description"])[:100]
        prompt = str(d.get("prompt", ""))
        for line in prompt.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:100]
    except Exception:
        pass
    return ""


def _test_milestones(test: dict, task: str) -> dict:
    """单次测试的 4 步进度。

    ① 配置完成:test created(test 存在即满足)
    ② 跑批完成:status == done
    ③ 报告生成:reports/task_<task>.html 存在(任务级 shared)
    ④ 建议产出:recommendations_<task>.json 存在 且 mtime > test created_at
    """
    m1 = True  # test 存在就满足
    m2 = (test.get("status") == "done")
    m3 = (REPORTS_DIR / f"task_{task}.html").exists()
    rec = REPORTS_DIR / f"recommendations_{task}.json"
    m4 = False
    if rec.exists():
        try:
            test_ct = test.get("created_at", "")
            # 简单比较:rec mtime 应 >= test ct
            from datetime import datetime as _dt
            test_dt = _dt.fromisoformat(test_ct) if test_ct else None
            rec_dt = _dt.fromtimestamp(rec.stat().st_mtime)
            m4 = (test_dt is None or rec_dt >= test_dt)
        except Exception:
            m4 = True
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


def _render_test_card(test: dict, task: str) -> None:
    ms = _test_milestones(test, task)
    keys = ["m1", "m2", "m3", "m4"]
    labels = ["配置", "评测", "报告", "建议"]

    # 找下一步
    next_idx = next((i for i, k in enumerate(keys) if not ms[k]), None)

    pills = []
    for i, (k, lbl) in enumerate(zip(keys, labels)):
        if ms[k]:
            color, bg = "#15803d", "#dcfce7"
            mark = "✓"
        elif i == next_idx:
            color, bg = "#b45309", "#fef3c7"
            mark = "⏳"
        else:
            color, bg = "#94a3b8", "#f1f5f9"
            mark = ""
        pills.append(
            f'<span style="display:inline-block;padding:3px 10px;'
            f'border-radius:12px;background:{bg};color:{color};'
            f'font-size:0.78rem;font-weight:500;margin:0 2px;">'
            f'{mark} {lbl}</span>'
        )
    arrow = '<span style="color:#cbd5e1;margin:0 0px;">→</span>'

    pr = test.get("pass_rate")
    pr_str = f"{pr * 100:.0f}%" if pr is not None else "—"
    pr_color = ("#22c55e" if (pr or 0) >= 0.5 else
                 "#eab308" if (pr or 0) >= 0.2 else
                 "#ef4444" if pr is not None else "#94a3b8")

    status_badge = {
        "running": '<span style="color:#3370ff;">⏳ 跑批中</span>',
        "done": '<span style="color:#22c55e;">✓ 完成</span>',
        "failed": '<span style="color:#ef4444;">✗ 失败</span>',
        "created": '<span style="color:#94a3b8;">⏳ 配置中</span>',
    }.get(test.get("status"), test.get("status", "?"))

    st.markdown(f"""
<div class="eval-card" style="padding:14px 18px;">
  <div style="display:flex; justify-content:space-between; align-items:start;">
    <div>
      <strong style="font-size:1.05rem;">🧪 {test['run_id']}</strong>
      &nbsp;&nbsp;{status_badge}
      <div style="color:#64748b; font-size:0.85rem; margin-top:4px;">
        {test['created_at'][:16]} · agent: <code>{test.get('agent_version') or '—'}</code>
        · total={test['params'].get('total','?')}
        · {test.get('n_results', 0)} case
      </div>
    </div>
    <div style="text-align:right;">
      <div style="color:{pr_color}; font-size:1.4rem; font-weight:700;">{pr_str}</div>
      <div style="color:#64748b; font-size:0.78rem;">通过率</div>
    </div>
  </div>
  <div style="margin-top:8px;">{arrow.join(pills)}</div>
</div>
""", unsafe_allow_html=True)

    c1, c2, c3 = st.columns([2, 1, 4])
    if c1.button("→ 看详情", key=f"go_test_{test['run_id']}",
                   use_container_width=True):
        st.session_state["current_test_id"] = test["run_id"]
        st.session_state["view"] = "test_detail"
        st.rerun()
    if c2.button("↻ 复用", key=f"rt_{test['run_id']}"):
        st.session_state[f"prefill_params_{task}"] = test["params"]
        st.session_state[f"show_new_test_{task}"] = True
        st.rerun()


# ============================ 新建测试表单 ============================

def _render_new_test_form(task: str, task_dir: Path, versions: list) -> None:
    st.markdown("### ➕ 新建测试")
    c_back, _ = st.columns([1, 5])
    if c_back.button("← 取消", key=f"cancel_new_{task}"):
        st.session_state[f"show_new_test_{task}"] = False
        st.rerun()

    prefill = st.session_state.pop(f"prefill_params_{task}", None)

    # 自动 test_id
    existing = db_list_runs(task_id=task)
    next_n = len(existing) + 1
    default_id = f"test_{next_n:03d}_{datetime.now().strftime('%m%d_%H%M')}"

    c1, c2 = st.columns([2, 1])
    test_id = c1.text_input("测试号(test_id / label)", value=default_id,
                                 key=f"nt_id_{task}")

    # 用哪个版本
    ver_options = ["当前 task.yaml"] + [v.label for v in versions]
    use_ver = c2.selectbox("Prompt 版本", ver_options, index=0,
                              key=f"nt_ver_{task}")

    c3, c4, c5 = st.columns([1, 1, 1])
    total_init = int(prefill.get("total", 30)) if prefill else 30
    total = c3.number_input("--total", 5, 200, total_init, key=f"nt_total_{task}")
    no_judge = c4.checkbox("--no-judge", value=False, key=f"nt_nj_{task}")
    auto_rec = c5.checkbox(
        "跑完自动出建议", value=False, key=f"nt_ar_{task}",
        help="再 +3-5 min LLM 调用")

    if prefill:
        st.caption(f"🔁 从历史 test 复用了参数(total={prefill.get('total')})")

    if st.button("🚀 启动测试", type="primary", key=f"nt_go_{task}"):
        # 若选了历史版本,先切到那个版本
        if use_ver != "当前 task.yaml":
            switch_to_version(task_dir, use_ver)
        cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
               "--task", task, "--total", str(total), "--label", test_id]
        if no_judge:
            cmd.append("--no-judge")
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        log_pane = st.empty()
        with st.spinner(f"⏳ 跑批中(预计 {total // 4 + 2} 分钟)…"):
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                     env=env, cwd=str(ROOT))
        st.code(proc.stdout[-3000:], language="text")
        if proc.returncode == 0:
            st.success(f"✓ 测试 {test_id} 完成")
            if auto_rec:
                with st.spinner("LLM 生成改进建议…"):
                    rec_proc = subprocess.run(
                        [sys.executable, "-m", "claw_eval.cli",
                         "recommend", "--task", task],
                        capture_output=True, text=True,
                        env=env, cwd=str(ROOT))
                if rec_proc.returncode == 0:
                    st.success("✓ 建议生成完")
            st.session_state[f"show_new_test_{task}"] = False
            st.session_state["current_test_id"] = test_id
            st.session_state["view"] = "test_detail"
            st.rerun()
        else:
            st.error(f"✗ 失败 exit={proc.returncode}")


# ============================ 任务级配置(可折叠展开)============================

def _render_task_config(task: str, task_dir: Path, versions: list) -> None:
    yaml_path = task_dir / "task.yaml"
    if not yaml_path.exists():
        st.error("task.yaml 不存在"); return
    try:
        td_obj = TaskDefinition.from_yaml(yaml_path)
    except Exception as e:
        st.error(str(e)); return

    # Prompt
    with st.expander("📝 任务 Prompt", expanded=False):
        new_prompt = st.text_area("", value=td_obj.prompt, height=280,
                                     label_visibility="collapsed",
                                     key=f"to_prompt_{task}")
        cc1, cc2 = st.columns([1, 2])
        if cc1.button("💾 保存 Prompt", type="primary",
                        key=f"to_sp_{task}"):
            d = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            d["prompt"] = new_prompt
            yaml_path.write_text(yaml.safe_dump(d, allow_unicode=True,
                                                  sort_keys=False), encoding="utf-8")
            st.success("✓"); st.rerun()
        new_ver = cc2.text_input("快照版本(可选)", placeholder="如 v3-fix",
                                    key=f"to_nv_{task}")
        if new_ver and st.button("📌 备份当前为新版本", key=f"to_snap_{task}"):
            save_version(task_dir, new_ver,
                          based_on=versions[-1].label if versions else None,
                          note="手动备份")
            st.success(f"✓ {new_ver}"); st.rerun()

    # Rubrics
    rb = task_dir / "rubrics.yaml"
    draft = task_dir / "rubrics.draft.yaml"
    use_path = rb if rb.exists() else draft
    if use_path.exists():
        with st.expander(f"📐 Rubrics({use_path.name})", expanded=False):
            rubrics = load_rubrics(use_path)
            rdf = pd.DataFrame([
                {"id": r.id, "category": r.category or "—",
                 "dim": r.dimension, "method": r.method, "weight": r.weight,
                 "safety": "★" if r.is_safety else "",
                 "check": r.check[:50]}
                for r in rubrics])
            st.dataframe(rdf, hide_index=True, use_container_width=True)

    # 流程图
    if (task_dir / "flow.yaml").exists():
        with st.expander("🌲 任务流程图", expanded=False):
            _render_flow_inline(task, task_dir)

    # 模拟用户
    with st.expander("👥 模拟用户(persona + 权重 + 噪音)", expanded=False):
        _render_personas_inline(task, task_dir)

    # 版本历史
    if versions:
        with st.expander(f"📜 版本历史({len(versions)} 个)", expanded=False):
            vdf = pd.DataFrame([
                {"label": v.label, "时间": v.created_at[:16],
                 "based_on": v.based_on or "—",
                 "应用建议": ", ".join(v.applied_recs) or "—",
                 "备注": v.note}
                for v in reversed(versions)
            ])
            st.dataframe(vdf, hide_index=True, use_container_width=True)


def _render_flow_inline(task: str, task_dir: Path) -> None:
    flow_path = task_dir / "flow.yaml"
    try:
        flow = load_flow(flow_path)
        cover_count = {n.id: 0 for n in flow.nodes}
        for pname in list_personas(task):
            try:
                d = yaml.safe_load(
                    (task_dir / "personas" / f"{pname}.yaml").read_text(encoding="utf-8")) or {}
                for nid in d.get("covers_flow_nodes", []):
                    if nid in cover_count:
                        cover_count[nid] += 1
            except Exception:
                pass
        rubric_scores = {}
        try:
            from claw_eval.report.aggregate import aggregate, load_results_dir
            results = [r for r in load_results_dir(ROOT / "traces") if r.task_id == task]
            if results:
                summary = aggregate(results)
                for n in flow.nodes:
                    if n.rubric:
                        info = summary.by_rubric.get(n.rubric)
                        rubric_scores[n.rubric] = info["avg_score"] if info else None
        except Exception:
            pass
        dot = flow_to_dot(flow, cover_count, rubric_scores)
        st.graphviz_chart(dot)
    except Exception as e:
        st.error(f"流程图渲染失败:{e}")


def _render_personas_inline(task: str, task_dir: Path) -> None:
    sampling_path = task_dir / "sampling.yaml"
    cfg = load_sampling(sampling_path) if sampling_path.exists() else SamplingConfig()
    persona_names = sorted(p for p in list_personas(task)
                              if not p.startswith("adv_"))
    persona_demo = {}
    persona_covers = {}
    for pname in persona_names:
        pyaml = task_dir / "personas" / f"{pname}.yaml"
        try:
            d = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
            persona_covers[pname] = len(d.get("covers_flow_nodes", []))
            p = load_persona(pyaml,
                              personalities_dir=ROOT / "personalities",
                              noise_file=NOISE_FILE)
            persona_demo[pname] = p.demographics.model_dump()
        except Exception:
            persona_covers[pname] = 0
            persona_demo[pname] = {}

    GENDER = {"male": "🧔", "female": "👩", "unspecified": "👤"}
    if persona_names:
        cols = st.columns(min(3, len(persona_names)))
        for i, pname in enumerate(persona_names):
            demo = persona_demo.get(pname, {})
            icon = GENDER.get(demo.get("gender", "unspecified"), "👤")
            weight = cfg.weights.get(pname, 0)
            cover_n = persona_covers.get(pname, 0)
            with cols[i % len(cols)]:
                st.markdown(f"""
<div class="persona-card">
  <div class="pc-title">{icon} {pname}</div>
  <div class="pc-meta">{demo.get('mbti', '—')} · {demo.get('age_range', '?')}<br>
    态度:{demo.get('attitude', '—')}</div>
  <div><span class="pc-weight">{int(weight)}</span> <span style="font-size:0.85rem;color:#64748b;">权重</span></div>
  <div class="pc-meta" style="margin-top:6px;">🌲 覆盖 {cover_n} 节点</div>
</div>""", unsafe_allow_html=True)

    # 草稿采用
    pd_dir = task_dir / "personas_draft"
    if pd_dir.exists() and list(pd_dir.glob("*.yaml")):
        st.markdown("**✨ 草稿待挑**")
        for pname in sorted(p.stem for p in pd_dir.glob("*.yaml")):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"• `{pname}`")
            if c2.button("✓ 采用", key=f"ovad_{pname}"):
                shutil.move(str(pd_dir / f"{pname}.yaml"),
                              str(task_dir / "personas" / f"{pname}.yaml"))
                st.rerun()
            if c3.button("✗ 删", key=f"ovdr_{pname}"):
                (pd_dir / f"{pname}.yaml").unlink(); st.rerun()

    # 权重 + 噪音 编辑
    st.markdown("**权重 + 噪音 overlay**")
    rows = [{"persona": p, "weight": float(cfg.weights.get(p, 0))}
            for p in persona_names]
    edited = st.data_editor(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        column_config={
            "persona": st.column_config.TextColumn(disabled=True),
            "weight": st.column_config.NumberColumn(min_value=0.0, step=1.0)},
        key=f"ovw_{task}")
    c1, c2 = st.columns([1, 2])
    new_rate = c1.slider("噪音 rate", 0.0, 1.0,
                          float(cfg.noise_overlay.rate), 0.05,
                          key=f"ovnr_{task}")
    try:
        kinds_lib = load_noise_kinds(NOISE_FILE)
    except Exception:
        kinds_lib = {}
    new_kinds = c2.multiselect(
        "kinds", list(kinds_lib.keys()),
        default=[k for k in cfg.noise_overlay.kinds if k in kinds_lib],
        key=f"ovnk_{task}")
    new_weights = {str(r["persona"]): float(r["weight"])
                   for _, r in edited.iterrows()
                   if r.get("persona") and float(r.get("weight") or 0) > 0}
    if st.button("💾 保存任务级配置", type="primary",
                  key=f"ovss_{task}"):
        save_sampling(SamplingConfig(
            weights=new_weights,
            noise_overlay=NoiseOverlay(rate=new_rate, kinds=new_kinds),
        ), sampling_path)
        st.success("✓ 已保存"); st.rerun()
