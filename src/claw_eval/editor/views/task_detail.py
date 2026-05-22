"""任务详情视图 —— 单任务管理。

顶部:任务选 + 版本下拉 + 返回
4 里程碑:① 方案 / ② 模拟用户 / ③ 评测 / ④ 报告
4 Tab:📐 评测方案 / 👥 模拟用户 / 🏃 评测(含历史+复用) / 📈 报告 & 建议
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
import streamlit.components.v1 as components
import yaml

from claw_eval.db import list_runs as db_list_runs
from claw_eval.editor._utils import (
    NOISE_FILE,
    REPORTS_DIR,
    ROOT,
    TASKS_DIR,
    TRACES_DIR,
    list_personas,
    list_tasks,
)
from claw_eval.models.flow import load_flow
from claw_eval.models.persona import load_noise_kinds, load_persona
from claw_eval.models.rubric import load_rubrics
from claw_eval.models.task import TaskDefinition
from claw_eval.report.flow_viz import build_flow_option
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


def render_task_detail() -> None:
    task = st.session_state.get("current_task")
    if not task or task not in list_tasks():
        st.warning("任务不存在,返回列表"); st.session_state["view"] = "list"
        st.rerun(); return

    task_dir = TASKS_DIR / task

    # ----- 顶部栏 -----
    c_back, c_title, c_ver = st.columns([1, 3, 2])
    if c_back.button("← 返回任务列表"):
        st.session_state["view"] = "list"; st.rerun()
    c_title.title(f"📋 {task}")
    versions = list_versions(task_dir)
    ver_labels = [v.label for v in versions] if versions else ["(无版本)"]
    selected_ver = c_ver.selectbox("版本", ver_labels,
                                      index=len(ver_labels) - 1,
                                      key=f"ver_{task}")
    if versions and selected_ver != ver_labels[-1]:
        if st.button("↩ 切回此版本"):
            switch_to_version(task_dir, selected_ver); st.rerun()

    # ----- 4 里程碑 -----
    ms = _milestones(task)
    icons = []
    for k, label in [("m1", "① 评测方案"), ("m2", "② 模拟用户"),
                      ("m3", "③ 评测"), ("m4", "④ 报告")]:
        cls = "done" if ms[k] else (
            "current" if all(ms[f"m{j}"] for j in range(1, int(k[1])))
            else "")
        icon = "●" if ms[k] else "○"
        icons.append(f'<span class="stage {cls}">{icon} {label}</span>')
    arrow = '<span class="arrow">→</span>'
    st.markdown(f"""
<div class="lifecycle" style="margin:4px 0 8px 0;">
  里程碑:&nbsp; {arrow.join(icons)}
</div>
""", unsafe_allow_html=True)

    # ----- 待办红点 -----
    todos = _action_items(task, versions)
    if todos:
        badges = " · ".join(
            f'<span style="color:{"#ef4444" if l=="red" else "#eab308"}">'
            f'🔴 {t}</span>' for l, t in todos)
        st.markdown(f'<div class="warn-banner">📌 {badges}</div>',
                     unsafe_allow_html=True)

    # ----- 4 Tab -----
    tab1, tab2, tab3, tab4 = st.tabs([
        "📐 评测方案", "👥 模拟用户", "🏃 评测", "📈 报告 & 建议",
    ])

    with tab1: _tab_scheme(task, task_dir, versions)
    with tab2: _tab_users(task, task_dir)
    with tab3: _tab_eval(task, task_dir)
    with tab4: _tab_report(task, task_dir, versions)


# ============================ 工具 ============================

def _milestones(task: str) -> dict:
    td = TASKS_DIR / task
    m1 = (td / "rubrics.yaml").exists() and (td / "grader.py").exists()
    has_p = len(list_personas(task)) > 0
    has_sampling = (td / "sampling.yaml").exists()
    has_weights = False
    if has_sampling:
        try:
            sd = yaml.safe_load((td / "sampling.yaml").read_text(encoding="utf-8")) or {}
            has_weights = bool(sd.get("weights"))
        except Exception:
            pass
    m2 = has_p and has_weights
    runs = db_list_runs(task_id=task)
    m3 = len(runs) >= 1
    has_rec = (REPORTS_DIR / f"recommendations_{task}.json").exists()
    m4 = has_rec or len(runs) >= 2
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


def _action_items(task: str, versions: list) -> list[tuple[str, str]]:
    td = TASKS_DIR / task
    items = []
    draft = td / "rubrics.draft.yaml"
    final = td / "rubrics.yaml"
    if draft.exists() and (not final.exists()
                              or draft.stat().st_mtime > final.stat().st_mtime):
        try:
            n = len(yaml.safe_load(draft.read_text(encoding="utf-8")).get("rubrics", []))
            items.append(("red", f"{n} 条 rubric 草稿待审 → Tab 📐"))
        except Exception:
            pass
    pd_dir = td / "personas_draft"
    if pd_dir.exists():
        n = len(list(pd_dir.glob("*.yaml")))
        if n > 0:
            items.append(("red", f"{n} 个 persona 草稿待挑 → Tab 👥"))
    rec_file = REPORTS_DIR / f"recommendations_{task}.json"
    if rec_file.exists():
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
            applied = set()
            for v in versions:
                applied.update(v.applied_recs)
            unapplied = [r for r in data.get("recommendations", [])
                         if r["rubric_id"] not in applied
                         and r.get("suggested_prompt_change")]
            if unapplied:
                items.append(("yellow", f"{len(unapplied)} 条建议未应用 → Tab 📈"))
        except Exception:
            pass
    return items


# ============================ Tab 1:评测方案 ============================

def _tab_scheme(task: str, task_dir: Path, versions: list) -> None:
    st.subheader("📐 评测方案 = Prompt + Rubrics + Grader")

    # --- Prompt ---
    yaml_path = task_dir / "task.yaml"
    if not yaml_path.exists():
        st.error("task.yaml 不存在"); return
    try:
        td_obj = TaskDefinition.from_yaml(yaml_path)
    except Exception as e:
        st.error(str(e)); return

    with st.expander("📝 任务 Prompt(给 SUT 的 system message)",
                       expanded=True):
        new_prompt = st.text_area("", value=td_obj.prompt, height=280,
                                     label_visibility="collapsed",
                                     key=f"prompt_{task}")
        c1, c2 = st.columns([1, 2])
        if c1.button("💾 保存", type="primary", key=f"savep_{task}"):
            d = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            d["prompt"] = new_prompt
            yaml_path.write_text(yaml.safe_dump(d, allow_unicode=True,
                                                  sort_keys=False), encoding="utf-8")
            st.success("✓ 已保存"); st.rerun()
        new_ver = c2.text_input("快照版本(可选)", placeholder="如 v3-fix",
                                  key=f"newver_{task}")
        if new_ver and st.button("📌 备份为版本", key=f"snap_{task}"):
            save_version(task_dir, new_ver,
                          based_on=versions[-1].label if versions else None)
            st.success(f"✓ {new_ver}"); st.rerun()

    # --- Rubrics ---
    rb = task_dir / "rubrics.yaml"
    draft = task_dir / "rubrics.draft.yaml"
    use_path = rb if rb.exists() else draft
    if use_path.exists():
        is_draft = use_path == draft
        st.markdown(f"### Rubrics({'草稿待审 · ' if is_draft else ''}{use_path.name})")
        if is_draft:
            st.warning(f"⚠ 草稿!`claw-eval review --task {task}` 转正")
        rubrics = load_rubrics(use_path)
        rdf = pd.DataFrame([
            {"id": r.id, "category": r.category or "—",
             "dim": r.dimension, "method": r.method, "weight": r.weight,
             "safety": "★" if r.is_safety else "",
             "check": r.check[:50]}
            for r in rubrics])
        st.dataframe(rdf, hide_index=True, use_container_width=True)

    # --- 流程图 ---
    flow_path = task_dir / "flow.yaml"
    if flow_path.exists():
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
        try:
            from claw_eval.report.aggregate import aggregate, load_results_dir
            results = [r for r in load_results_dir(TRACES_DIR) if r.task_id == task]
            summary = aggregate(results) if results else None
            rubric_scores = ({n.rubric: (summary.by_rubric.get(n.rubric, {}).get("avg_score")
                                if summary else None)
                              for n in flow.nodes if n.rubric})
        except Exception:
            rubric_scores = {}

        with st.expander("🌲 流程图(节点 = rubric 一对一)",
                           expanded=False):
            uncovered = [n for n in flow.nodes if cover_count[n.id] == 0
                         and n.id not in ("START", "END")]
            if uncovered:
                st.markdown(
                    f'<div class="warn-banner">⚠ {len(uncovered)} 个节点无 persona 覆盖</div>',
                    unsafe_allow_html=True)
            option = build_flow_option(flow, rubric_scores or {n.rubric: None for n in flow.nodes if n.rubric})
            for nd in option["series"][0]["data"]:
                nid = nd["name"]
                cnt = cover_count.get(nid, 0)
                nd["label"]["formatter"] = f"{nd['label']['formatter']}\n👥{cnt}"
                if cnt == 0 and nid not in ("START", "END"):
                    nd["itemStyle"]["color"] = "#ef4444"
                    nd["label"]["color"] = "#fff"
            components.html(f"""
<div id="flow" style="height:380px;"></div>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>(function(){{
  var el=document.getElementById('flow');
  if(!el||!window.echarts) return;
  echarts.init(el).setOption({json.dumps(option)});
}})();</script>""", height=400)

    # --- grader.py ---
    grader_path = task_dir / "grader.py"
    if grader_path.exists():
        with st.expander("📜 grader.py(只读)"):
            st.code(grader_path.read_text(encoding="utf-8"), language="python")


# ============================ Tab 2:模拟用户 ============================

def _tab_users(task: str, task_dir: Path) -> None:
    st.subheader("👥 任务模拟用户(选用户模板 + 任务剧本)")
    st.caption("「用户模板」在「⚙️ 全局配置」里管;这里管该任务用哪些模板 + 各自的剧本和权重。")

    sampling_path = task_dir / "sampling.yaml"
    cfg = load_sampling(sampling_path) if sampling_path.exists() else SamplingConfig()

    persona_names = sorted(p for p in list_personas(task)
                              if not p.startswith("adv_"))

    persona_covers = {}
    persona_demo = {}
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
        cols = st.columns(3)
        for i, pname in enumerate(persona_names):
            demo = persona_demo.get(pname, {})
            icon = GENDER.get(demo.get("gender", "unspecified"), "👤")
            weight = cfg.weights.get(pname, 0)
            cover_n = persona_covers.get(pname, 0)
            with cols[i % 3]:
                st.markdown(f"""
<div class="persona-card">
  <div class="pc-title">{icon} {pname}</div>
  <div class="pc-meta">{demo.get('mbti', '—')} · {demo.get('age_range', '?')} · {demo.get('education', '?')}<br>
    态度:{demo.get('attitude', '—')}</div>
  <div><span class="pc-weight">{int(weight)}</span> <span style="font-size:0.85rem;color:#64748b;">权重</span></div>
  <div class="pc-meta" style="margin-top:6px;">🌲 覆盖 {cover_n} 节点</div>
</div>""", unsafe_allow_html=True)

    # 草稿采用/删
    pd_dir = task_dir / "personas_draft"
    if pd_dir.exists() and list(pd_dir.glob("*.yaml")):
        st.markdown("---")
        st.markdown(f"### ✨ 草稿待挑")
        for pname in sorted(p.stem for p in pd_dir.glob("*.yaml")):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"• `{pname}`")
            if c2.button(f"✓ 采用", key=f"adopt_{pname}"):
                shutil.move(str(pd_dir / f"{pname}.yaml"),
                              str(task_dir / "personas" / f"{pname}.yaml"))
                st.rerun()
            if c3.button(f"✗ 删", key=f"drop_{pname}"):
                (pd_dir / f"{pname}.yaml").unlink(); st.rerun()

    # 权重 + 噪音
    st.markdown("---")
    st.markdown("### 权重 + 噪音 overlay")
    rows = [{"persona": p, "weight": float(cfg.weights.get(p, 0))}
            for p in persona_names]
    edited = st.data_editor(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        column_config={
            "persona": st.column_config.TextColumn("Persona", disabled=True),
            "weight": st.column_config.NumberColumn("权重", min_value=0.0, step=1.0)},
        key=f"w_{task}")
    c1, c2 = st.columns([1, 2])
    new_rate = c1.slider("噪音 rate", 0.0, 1.0,
                          float(cfg.noise_overlay.rate), 0.05,
                          key=f"nr_{task}")
    try:
        kinds_lib = load_noise_kinds(NOISE_FILE)
    except Exception:
        kinds_lib = {}
    new_kinds = c2.multiselect(
        "kinds", list(kinds_lib.keys()),
        default=[k for k in cfg.noise_overlay.kinds if k in kinds_lib],
        key=f"nk_{task}")
    new_weights = {str(r["persona"]): float(r["weight"])
                   for _, r in edited.iterrows()
                   if r.get("persona") and float(r.get("weight") or 0) > 0}
    if st.button("💾 保存 sampling.yaml", type="primary", key=f"ssamp_{task}"):
        save_sampling(SamplingConfig(
            weights=new_weights,
            noise_overlay=NoiseOverlay(rate=new_rate, kinds=new_kinds),
        ), sampling_path)
        st.success("✓ 已保存"); st.rerun()


# ============================ Tab 3:评测(含历史 + 复用)============================

def _tab_eval(task: str, task_dir: Path) -> None:
    st.subheader("🏃 评测")

    sub_run, sub_red, sub_hist = st.tabs(["跑批", "🔴 安全红队", "📚 历史 + 复用"])

    with sub_run:
        # 复用上一次参数(若 session_state 有)
        reuse = st.session_state.pop(f"reuse_params_{task}", None)
        c1, c2, c3 = st.columns([1, 1, 2])
        total_init = int(reuse.get("total", 30)) if reuse else 30
        total = c1.number_input("--total", 5, 200, total_init,
                                   key=f"bt_{task}")
        default_label = f"{task[:6]}_{datetime.now().strftime('%m%d_%H%M')}"
        label_init = reuse.get("label", default_label) if reuse else default_label
        label = c2.text_input("--label", value=label_init, key=f"bl_{task}")
        no_judge_init = bool(reuse.get("no_judge", False)) if reuse else False
        no_judge = c3.checkbox("--no-judge", value=no_judge_init,
                                  key=f"bnj_{task}")
        if reuse:
            st.caption(f"🔁 复用自 `{reuse.get('label', '?')}` 的参数")

        if st.button("🏃 开跑(后台)", type="primary", key=f"runb_{task}"):
            cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
                   "--task", task, "--total", str(total), "--label", label]
            if no_judge:
                cmd.append("--no-judge")
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            with st.spinner("跑中…"):
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                          env=env, cwd=str(ROOT))
            st.code(proc.stdout[-3000:], language="text")
            if proc.returncode == 0:
                st.success(f"✓ run_id={label}"); st.rerun()

    with sub_red:
        adv_personas = sorted(f.stem for f in (task_dir / "personas").glob("adv_*.yaml"))
        if not adv_personas:
            st.info("没有对抗 persona(adv_ 开头)。")
        else:
            st.caption(f"对抗 personas:{adv_personas}")
            adv_trials = st.number_input("--trials", 1, 10, 2,
                                            key=f"st_{task}")
            if st.button("🔴 跑安全红队", type="primary", key=f"sgo_{task}"):
                cmd = [sys.executable, "-m", "claw_eval.cli", "safety-test",
                       "--task", task, "--trials", str(adv_trials)]
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                with st.spinner(f"跑中(~{len(adv_personas)*adv_trials*45}s)…"):
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                              env=env, cwd=str(ROOT))
                st.code(proc.stdout[-3000:], language="text")

            sec_file = REPORTS_DIR / f"safety_test_{task}.json"
            if sec_file.exists():
                data = json.loads(sec_file.read_text(encoding="utf-8"))
                rate = data["overall_breach_rate"]
                badge = ("🟢 安全" if rate < 0.1 else
                          "🟡 部分破" if rate < 0.3 else "🔴 高危")
                st.markdown(f"### 整体破防 {rate:.0%} {badge}")
                if data.get("by_rubric"):
                    st.dataframe(pd.DataFrame([
                        {"rubric": r["rubric"], "失守": f"{r['breach']}/{r['n']}",
                         "破防率": f"{r['rate']:.0%}"}
                        for r in data["by_rubric"]
                    ]), hide_index=True, use_container_width=True)

    with sub_hist:
        st.markdown("**评测历史(从 SQLite 读)**")
        st.caption("每行带「复用参数」按钮 —— 下次跑批可用同样配置。")
        runs = db_list_runs(task_id=task)
        if not runs:
            st.caption("(还没跑过)")
        else:
            for r in runs[:20]:
                with st.container():
                    cA, cB, cC, cD, cE = st.columns([2, 2, 1, 1, 1])
                    cA.markdown(f"**{r['run_id']}**")
                    cA.caption(f"{r['created_at'][:16]}")
                    cB.markdown(
                        f"agent: `{r.get('agent_version') or '—'}`"
                    )
                    cB.caption(
                        f"params: total={r['params'].get('total','?')} "
                        f"njudge={r['params'].get('no_judge', False)}"
                    )
                    cC.markdown(f"**{r.get('n_results', 0)}** case")
                    cD.markdown(
                        f"**{(r.get('pass_rate') or 0) * 100:.0f}%**" +
                        (" 🟢" if (r.get('pass_rate') or 0) >= 0.5 else
                         " 🟡" if (r.get('pass_rate') or 0) >= 0.2 else " 🔴")
                    )
                    if cE.button("↻ 复用", key=f"reuse_{r['run_id']}"):
                        st.session_state[f"reuse_params_{task}"] = r["params"]
                        st.success("已填入「跑批」子 Tab,切过去看"); st.rerun()
                    st.markdown("---")


# ============================ Tab 4:报告 & 建议 ============================

def _tab_report(task: str, task_dir: Path, versions: list) -> None:
    st.subheader("📈 报告 & 改进建议")
    sub_report, sub_rec, sub_reg = st.tabs([
        "📊 报告", "💡 建议 + 自动应用", "🔄 回归对比",
    ])

    with sub_report:
        report_path = REPORTS_DIR / f"task_{task}.html"
        if report_path.exists():
            if st.button("🔄 重生成 dashboard", key=f"regen_{task}"):
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                subprocess.run([sys.executable, "-m", "claw_eval.cli",
                                  "dashboard"], env=env, cwd=str(ROOT))
                st.rerun()
            components.html(report_path.read_text(encoding="utf-8"),
                             height=900, scrolling=True)
        else:
            st.caption("没有报告。跑过 batch 后:")
            if st.button("📊 生成 dashboard", key=f"gd_{task}"):
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                subprocess.run([sys.executable, "-m", "claw_eval.cli",
                                  "dashboard"], env=env, cwd=str(ROOT))
                st.rerun()

    with sub_rec:
        from claw_eval.task_gen.apply_recommendation import (
            diff_stats, generate_prompt_patch, unified_diff,
        )

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
            st.info("还没建议。先跑过 batch + 上方按钮触发。")
        else:
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
                    + (f" · +{r['estimated_lift']:.2f}" if r.get('estimated_lift') else "")
                    + (" ✓" if is_applied else ""),
                    expanded=(i == 1 and not is_applied),
                ):
                    if r.get("suggested_prompt_change"):
                        st.markdown(r["suggested_prompt_change"])
                    if r.get("rationale"):
                        st.caption(r["rationale"])
                    patch_key = f"p_{task}_{rid}"
                    if not is_applied and st.button(
                        "🤖 自动应用",
                        key=f"ap_{task}_{rid}",
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
                            st.success(f"✓ 新版本 {new_lbl}")
                            st.session_state.pop(patch_key, None); st.rerun()
                        if cR.button("✗ 拒绝", key=f"rej_{task}_{rid}"):
                            st.session_state.pop(patch_key, None); st.rerun()

    with sub_reg:
        runs_for_task = db_list_runs(task_id=task)
        if len(runs_for_task) < 2:
            st.info(f"本任务只有 {len(runs_for_task)} 个 run,需 2 个才能对比。")
        else:
            ids = [r["run_id"] for r in runs_for_task]
            c1, c2, c3 = st.columns(3)
            old_run = c1.selectbox("旧 run", ids, index=1, key=f"or_{task}")
            new_run = c2.selectbox("新 run", ids, index=0, key=f"nr2_{task}")
            thr = c3.slider("阈值", 0.01, 0.30, 0.05, 0.01,
                              key=f"thr2_{task}")
            if st.button("🔄 跑回归", type="primary", key=f"rr_{task}",
                          disabled=(old_run == new_run)):
                cmd = [sys.executable, "-m", "claw_eval.cli", "regression",
                       "--task", task, "--old", old_run, "--new", new_run,
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
            st.markdown(f"**{data['old_label']} → {data['new_label']}** · "
                        f"{data.get('n_improvements', 0)} 改进 / "
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
