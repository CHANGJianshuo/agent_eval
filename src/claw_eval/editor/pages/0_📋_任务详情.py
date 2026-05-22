"""任务详情页(主入口) —— 顶部栏 + 3 里程碑 + 待办红点 + 5 Tab。

5 Tab(从 6 合并):
  📝 任务定义        Prompt + 变量 + 版本管理
  📐 评测方案        rubrics + grader + 流程图(节点 cover + rubric 一对一展示)
  👥 模拟用户        persona 卡片 + 权重 + 噪音 overlay + 覆盖率
  🏃 评测 & 报告     跑批 / 红队 / 历史 / 嵌入报告
  📈 改进            recommend + 自动应用 + 回归对比
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

from claw_eval.editor._utils import (
    NOISE_FILE,
    REPORTS_DIR,
    ROOT,
    TASKS_DIR,
    TRACES_DIR,
    inject_global_style,
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
    get_version_yaml,
    list_versions,
    save_version,
    switch_to_version,
)

st.set_page_config(page_title="任务详情", page_icon="📋", layout="wide")
inject_global_style()


# ============================ 任务选择 ============================
tasks = list_tasks()
if not tasks:
    st.error("没有任务,先去「➕ 新建任务」"); st.stop()

current = st.session_state.get("current_task")
if current not in tasks:
    current = tasks[0]

# 顶部固定栏:任务 + 版本 + 跳转
top_c1, top_c2, top_c3 = st.columns([2, 2, 1])
selected = top_c1.selectbox(
    "📋 任务", tasks, index=tasks.index(current),
    label_visibility="collapsed", key="detail_task_select")
st.session_state["current_task"] = selected
task = selected
task_dir = TASKS_DIR / task

# 版本下拉
versions = list_versions(task_dir)
ver_options = [v.label for v in versions] if versions else ["(无版本)"]
selected_ver = top_c2.selectbox(
    "版本", ver_options, index=len(ver_options) - 1,
    label_visibility="collapsed", key=f"ver_dd_{task}",
    help="切换到历史版本会把 task.yaml 还原到那时")
if versions and top_c3.button("↩ 切回", disabled=(selected_ver == ver_options[-1])):
    switch_to_version(task_dir, selected_ver)
    st.success(f"✓ 切回 {selected_ver}")
    st.rerun()


# ============================ 里程碑进度条 ============================

def _milestones() -> list[dict]:
    """3 里程碑:生成 / 评测 / 完成。"""
    has_task = (task_dir / "task.yaml").exists()
    has_rubrics = ((task_dir / "rubrics.yaml").exists()
                    or (task_dir / "rubrics.draft.yaml").exists())
    has_personas = len(list_personas(task)) > 0

    # 评测:本任务有几个 run
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
    n_runs = len(task_runs)
    has_iter = n_runs >= 2  # 有第二轮 = 改进迭代了

    return [
        {"label": "① 生成", "done": has_task and has_rubrics and has_personas,
         "hint": "有 task.yaml + rubrics + 至少 1 个 persona"},
        {"label": "② 评测", "done": n_runs >= 1,
         "hint": f"至少 1 个 run({n_runs} 个)"},
        {"label": "③ 完成", "done": has_iter,
         "hint": f"至少 2 个 run = 改进迭代过({n_runs} 个)"},
    ]


ms = _milestones()
ms_html = []
for i, m in enumerate(ms):
    cls = "done" if m["done"] else "current" if (i == 0 or ms[i - 1]["done"]) else ""
    icon = "●" if m["done"] else "○"
    ms_html.append(
        f'<span class="stage {cls}" title="{m["hint"]}">{icon} {m["label"]}</span>'
    )
    if i != len(ms) - 1:
        ms_html.append('<span class="arrow">→</span>')

st.markdown(f"""
<div class="lifecycle" style="margin: 4px 0 8px 0;">
  里程碑:&nbsp; {''.join(ms_html)}
</div>
""", unsafe_allow_html=True)


# ============================ 待办红点 ============================

def _action_items() -> list[tuple[str, str, str]]:
    """返回 [(level, text, tab_name), ...]。level: red/yellow/blue"""
    items = []

    # 1) rubrics 草稿待审
    draft = task_dir / "rubrics.draft.yaml"
    final = task_dir / "rubrics.yaml"
    if draft.exists():
        n_draft = 0
        try:
            n_draft = len(yaml.safe_load(draft.read_text(encoding="utf-8")).get("rubrics", []))
        except Exception:
            pass
        if not final.exists() or draft.stat().st_mtime > final.stat().st_mtime:
            items.append(("red", f"{n_draft} 条 rubric 草稿待审", "📐 评测方案"))

    # 2) personas_draft 未挑
    pd_dir = task_dir / "personas_draft"
    if pd_dir.exists():
        n_pd = len(list(pd_dir.glob("*.yaml")))
        if n_pd > 0:
            items.append(("red", f"{n_pd} 个 persona 草稿待挑", "👥 模拟用户"))

    # 3) recommendations 待应用
    rec_file = REPORTS_DIR / f"recommendations_{task}.json"
    if rec_file.exists():
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
            recs = data.get("recommendations", [])
            # 检查是否已应用(简单看 versions 里有没有 applied_recs 含相同 rubric_id)
            applied_rubrics = set()
            for v in versions:
                applied_rubrics.update(v.applied_recs)
            unapplied = [r for r in recs if r["rubric_id"] not in applied_rubrics
                         and r.get("suggested_prompt_change")]
            if unapplied:
                items.append(("yellow", f"{len(unapplied)} 条建议未应用", "📈 改进"))
        except Exception:
            pass

    return items


todos = _action_items()
if todos:
    badges_html = " · ".join(
        f'<span style="color:{ "#ef4444" if lvl=="red" else "#eab308" if lvl=="yellow" else "#3370ff"}">'
        f'🔴 {text}</span>'
        for lvl, text, _tab in todos
    )
    st.markdown(f'<div class="warn-banner">📌 待办:{badges_html}</div>',
                 unsafe_allow_html=True)


# ============================ 5 Tab ============================
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📝 任务定义",
    "📐 评测方案",
    "👥 模拟用户",
    "🏃 评测 & 报告",
    "📈 改进",
])


# ====================== Tab 1:任务定义 ======================
with tab1:
    st.subheader("Prompt(给 SUT 的 system message)")
    yaml_path = task_dir / "task.yaml"
    if not yaml_path.exists():
        st.error("task.yaml 不存在"); st.stop()
    try:
        td_obj = TaskDefinition.from_yaml(yaml_path)
    except Exception as e:
        st.error(f"加载失败:{e}"); st.stop()

    new_prompt = st.text_area("", value=td_obj.prompt, height=380,
                                 label_visibility="collapsed",
                                 key=f"prompt_ta_{task}")

    cl, cr = st.columns([1, 2])
    if cl.button("💾 保存 Prompt", type="primary", key="save_prompt"):
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        data["prompt"] = new_prompt
        yaml_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        st.success("✓ 已保存")
    new_ver_label = cr.text_input("快照标签(可选)",
                                     placeholder="如 v2-peak-fix → 备份当前为版本",
                                     key=f"newver_{task}")
    if new_ver_label and st.button("📌 备份为版本", key=f"savever_{task}"):
        info = save_version(task_dir, new_ver_label,
                              based_on=versions[-1].label if versions else None,
                              note="手动备份")
        st.success(f"✓ 备份为 {info.label}"); st.rerun()

    st.markdown("---")
    st.subheader("业务变量")
    if td_obj.variables:
        st.dataframe(
            pd.DataFrame([{"变量": k, "默认值": v} for k, v in td_obj.variables.items()]),
            hide_index=True, use_container_width=True)
    else:
        st.caption("(没有声明变量;rubric check 文本里的 {X} 引用会失效)")

    if versions:
        st.markdown("---")
        st.subheader("版本历史")
        st.dataframe(pd.DataFrame([
            {"label": v.label, "时间": v.created_at[:16],
             "based_on": v.based_on or "—",
             "应用建议": ", ".join(v.applied_recs) or "—",
             "备注": v.note}
            for v in reversed(versions)
        ]), hide_index=True, use_container_width=True)


# ====================== Tab 2:评测方案(rubrics + grader + 流程图)======================
with tab2:
    st.subheader("📐 评测方案")
    st.caption("rubrics(评分项) + grader.py(评分逻辑) + 任务流程图。"
                "节点和 rubric 一一对应 —— 流程图上每个节点的颜色 = 关联 rubric 的得分。")

    # ---- 流程图 ----
    flow_path = task_dir / "flow.yaml"
    if flow_path.exists():
        flow = load_flow(flow_path)
        cover_count = {n.id: 0 for n in flow.nodes}
        for pname in list_personas(task):
            pyaml = task_dir / "personas" / f"{pname}.yaml"
            try:
                d = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
                for nid in d.get("covers_flow_nodes", []):
                    if nid in cover_count:
                        cover_count[nid] += 1
            except Exception:
                pass

        rubric_scores: dict[str, float | None] = {}
        try:
            from claw_eval.report.aggregate import aggregate, load_results_dir
            results = [r for r in load_results_dir(TRACES_DIR) if r.task_id == task]
            if results:
                summary = aggregate(results)
                for n in flow.nodes:
                    if n.rubric:
                        info = summary.by_rubric.get(n.rubric)
                        rubric_scores[n.rubric] = info["avg_score"] if info else None
        except Exception:
            pass

        uncovered = [n for n in flow.nodes
                     if cover_count[n.id] == 0 and n.id not in ("START", "END")]
        if uncovered:
            st.markdown(
                f'<div class="warn-banner">⚠ {len(uncovered)} 个节点无 persona 覆盖:'
                f'{", ".join(n.id for n in uncovered[:5])}'
                f'{"…" if len(uncovered) > 5 else ""}</div>',
                unsafe_allow_html=True)

        scores_for_color = (rubric_scores if rubric_scores
                            else {n.rubric: None for n in flow.nodes if n.rubric})
        option = build_flow_option(flow, scores_for_color)
        for nd in option["series"][0]["data"]:
            nid = nd["name"]
            cnt = cover_count.get(nid, 0)
            nd["label"]["formatter"] = f"{nd['label']['formatter']}\n👥{cnt}"
            if cnt == 0 and nid not in ("START", "END"):
                nd["itemStyle"]["color"] = "#ef4444"
                nd["itemStyle"]["borderColor"] = "#dc2626"
                nd["label"]["color"] = "#fff"

        components.html(f"""
<div id="flow" style="height: 380px;"></div>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>(function(){{
  var el = document.getElementById('flow');
  if (!el || !window.echarts) return;
  echarts.init(el).setOption({json.dumps(option)});
}})();</script>
""", height=400)
    else:
        st.info("没有 flow.yaml(可在「➕ 新建任务」时自动生成)")

    # ---- rubrics 表 ----
    st.markdown("---")
    rb_path = task_dir / "rubrics.yaml"
    rb_draft = task_dir / "rubrics.draft.yaml"
    use_path = rb_path if rb_path.exists() else rb_draft
    if not use_path.exists():
        st.warning("没有 rubrics.yaml 或 rubrics.draft.yaml")
    else:
        is_draft = use_path == rb_draft
        st.markdown(f"### Rubrics({'草稿待审 · ' if is_draft else ''}{use_path.name})")
        if is_draft:
            st.warning("⚠ 当前显示的是**草稿**。审完用 `claw-eval review --task " + task + "` 转正。")
        rubrics = load_rubrics(use_path)
        rdf = pd.DataFrame([
            {"id": r.id, "category": r.category or "—",
             "dimension": r.dimension, "method": r.method,
             "weight": r.weight,
             "safety": "★" if r.is_safety else "",
             "trigger": r.trigger.type if r.trigger else "—",
             "check": r.check[:50] + ("…" if len(r.check) > 50 else ""),
             "confidence": r.confidence if r.confidence is not None else "",
            } for r in rubrics])
        st.dataframe(rdf, hide_index=True, use_container_width=True)

    # ---- grader.py ----
    st.markdown("---")
    grader_path = task_dir / "grader.py"
    if grader_path.exists():
        with st.expander("📜 grader.py(只读,可手动编辑文件)", expanded=False):
            st.code(grader_path.read_text(encoding="utf-8"), language="python")


# ====================== Tab 3:模拟用户 ======================
with tab3:
    st.subheader("👥 模拟用户")
    st.caption("persona 卡片 + 权重 + 噪音 overlay + flow 覆盖率。")

    sampling_path = task_dir / "sampling.yaml"
    sampling_cfg = load_sampling(sampling_path) if sampling_path.exists() else SamplingConfig()

    persona_names = sorted(p for p in list_personas(task) if not p.startswith("adv_"))
    persona_covers: dict[str, int] = {}
    persona_demographics: dict[str, dict] = {}
    for pname in persona_names:
        pyaml = task_dir / "personas" / f"{pname}.yaml"
        try:
            d = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
            persona_covers[pname] = len(d.get("covers_flow_nodes", []))
            p = load_persona(pyaml,
                              personalities_dir=ROOT / "personalities",
                              noise_file=NOISE_FILE)
            persona_demographics[pname] = p.demographics.model_dump()
        except Exception:
            persona_covers[pname] = 0
            persona_demographics[pname] = {}

    GENDER_ICON = {"male": "🧔", "female": "👩", "unspecified": "👤"}
    if persona_names:
        cols = st.columns(3)
        for i, pname in enumerate(persona_names):
            demo = persona_demographics.get(pname, {})
            icon = GENDER_ICON.get(demo.get("gender", "unspecified"), "👤")
            age = demo.get("age_range", "?")
            edu = demo.get("education", "?")
            mbti = demo.get("mbti", "—")
            attitude = demo.get("attitude", "—")
            weight = sampling_cfg.weights.get(pname, 0)
            cover_n = persona_covers.get(pname, 0)
            with cols[i % 3]:
                st.markdown(f"""
<div class="persona-card">
  <div class="pc-title">{icon} {pname}</div>
  <div class="pc-meta">{mbti} · {age} · {edu}<br>态度:{attitude}</div>
  <div><span class="pc-weight">{int(weight)}</span> <span style="color:#64748b;font-size:0.85rem;">权重</span></div>
  <div class="pc-meta" style="margin-top:6px;">🌲 覆盖 {cover_n} 节点</div>
</div>""", unsafe_allow_html=True)
    else:
        st.info("没有 persona,可去「➕ 新建任务」或手动加 tasks/<task>/personas/*.yaml")

    # 草稿合并
    pd_dir = task_dir / "personas_draft"
    if pd_dir.exists():
        draft_names = sorted(p.stem for p in pd_dir.glob("*.yaml"))
        if draft_names:
            st.markdown("---")
            st.markdown(f"### ✨ 草稿待挑({len(draft_names)} 个)")
            st.caption("从 personas_draft/ 移到 personas/ 后就生效。")
            cols = st.columns(min(3, len(draft_names)))
            for i, pname in enumerate(draft_names):
                with cols[i % 3]:
                    st.markdown(f'<div class="persona-card">'
                                f'<div class="pc-title">✨ {pname}</div>'
                                f'<div class="pc-meta">draft</div>'
                                f'</div>', unsafe_allow_html=True)
                    if st.button(f"✓ 采用 → personas/", key=f"adopt_{pname}"):
                        import shutil
                        shutil.move(
                            str(pd_dir / f"{pname}.yaml"),
                            str(task_dir / "personas" / f"{pname}.yaml"))
                        st.success(f"✓ {pname} 已采用"); st.rerun()
                    if st.button(f"✗ 删", key=f"drop_{pname}"):
                        (pd_dir / f"{pname}.yaml").unlink()
                        st.rerun()

    # 权重 + 噪音 overlay
    st.markdown("---")
    st.markdown("### 权重 + 噪音 overlay")
    rows = [{"persona": p, "weight": float(sampling_cfg.weights.get(p, 0))}
            for p in persona_names]
    edited = st.data_editor(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        column_config={
            "persona": st.column_config.TextColumn("Persona", disabled=True),
            "weight": st.column_config.NumberColumn("权重", min_value=0.0, step=1.0)},
        key=f"weights_{task}")

    c1, c2 = st.columns([1, 2])
    new_rate = c1.slider("噪音 rate", 0.0, 1.0,
                          float(sampling_cfg.noise_overlay.rate), 0.05,
                          key=f"ov_rate_{task}",
                          help="整 case 加噪 · 全程必噪。命中 overlay 的 case 该通对话每轮都加噪")
    try:
        kinds_lib = load_noise_kinds(NOISE_FILE)
    except Exception:
        kinds_lib = {}
    new_kinds = c2.multiselect(
        "kinds", list(kinds_lib.keys()),
        default=[k for k in sampling_cfg.noise_overlay.kinds if k in kinds_lib],
        key=f"ov_kinds_{task}")

    new_weights = {str(r["persona"]): float(r["weight"])
                   for _, r in edited.iterrows()
                   if r.get("persona") and float(r.get("weight") or 0) > 0}

    if st.button("💾 保存 sampling.yaml", type="primary", key=f"save_samp_{task}"):
        draft = SamplingConfig(
            weights=new_weights,
            noise_overlay=NoiseOverlay(rate=new_rate, kinds=new_kinds))
        save_sampling(draft, sampling_path)
        st.success(f"✓ 已保存"); st.rerun()


# ====================== Tab 4:评测 & 报告 ======================
with tab4:
    st.subheader("🏃 跑评测")
    sub1, sub2 = st.tabs(["跑批 + 历史", "🔴 安全红队"])

    with sub1:
        c1, c2, c3 = st.columns([1, 1, 2])
        total = c1.number_input("--total", 5, 200, 30, key=f"bt_{task}")
        label = c2.text_input("--label",
            value=f"{task[:6]}_{datetime.now().strftime('%m%d_%H%M')}",
            key=f"bl_{task}")
        no_judge = c3.checkbox("--no-judge(只跑对话不评分)", value=False,
                                   key=f"bnj_{task}")
        if st.button("🏃 开跑(后台 subprocess)", type="primary",
                       key=f"runb_{task}"):
            cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
                   "--task", task, "--total", str(total), "--label", label]
            if no_judge: cmd.append("--no-judge")
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            st.caption(f"`{' '.join(cmd)}`")
            with st.spinner("跑中…"):
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                          env=env, cwd=str(ROOT))
            st.code(proc.stdout[-3000:], language="text")
            if proc.returncode == 0:
                st.success(f"✓ run_id={label}")

        # 历史
        st.markdown("---")
        st.markdown("**历史 runs**")
        task_runs = []
        if TRACES_DIR.exists():
            for rdir in sorted(TRACES_DIR.iterdir(), reverse=True):
                if not rdir.is_dir():
                    continue
                n_r = 0
                for rj in rdir.glob("*.result.json"):
                    try:
                        if json.loads(rj.read_text(encoding="utf-8")).get("task_id") == task:
                            n_r += 1
                    except Exception:
                        pass
                if n_r > 0:
                    task_runs.append({"run_id": rdir.name, "result 数": n_r})
        if task_runs:
            st.dataframe(pd.DataFrame(task_runs), hide_index=True,
                          use_container_width=True)
        else:
            st.caption("(还没跑过)")

        # 嵌入报告
        st.markdown("---")
        st.markdown("**📊 报告**")
        report_files = sorted((REPORTS_DIR).glob(f"task_{task}.html")) if REPORTS_DIR.exists() else []
        if report_files:
            if st.button("🔄 重生成 dashboard", key=f"regen_{task}"):
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                subprocess.run([sys.executable, "-m", "claw_eval.cli", "dashboard"],
                                env=env, cwd=str(ROOT))
                st.rerun()
            html = report_files[0].read_text(encoding="utf-8")
            components.html(html, height=900, scrolling=True)
        else:
            st.caption("还没有报告。跑过 batch 后,点 ↑ 「重生成 dashboard」")
            if st.button("📊 生成 dashboard", key=f"gen_dash_{task}"):
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                subprocess.run([sys.executable, "-m", "claw_eval.cli", "dashboard"],
                                env=env, cwd=str(ROOT))
                st.rerun()

    with sub2:
        st.markdown("**对抗 persona × safety rubric 专项**")
        adv_personas = sorted(
            f.stem for f in (task_dir / "personas").glob("adv_*.yaml"))
        if not adv_personas:
            st.warning(f"没有对抗 persona(以 adv_ 开头)。可在「Persona 编辑」加。")
        else:
            st.caption(f"对抗 personas:{adv_personas}")
            adv_trials = st.number_input("--trials", 1, 10, 2,
                                            key=f"st_trials_{task}")
            if st.button("🔴 跑安全红队", type="primary", key=f"safety_{task}"):
                cmd = [sys.executable, "-m", "claw_eval.cli", "safety-test",
                       "--task", task, "--trials", str(adv_trials)]
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                with st.spinner(f"跑中(预计 {len(adv_personas) * adv_trials * 45}s)…"):
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                              env=env, cwd=str(ROOT))
                st.code(proc.stdout[-3000:], language="text")

            # 显示最近红队报告
            sec_file = REPORTS_DIR / f"safety_test_{task}.json"
            if sec_file.exists():
                st.markdown("---")
                data = json.loads(sec_file.read_text(encoding="utf-8"))
                rate = data["overall_breach_rate"]
                badge = ("🟢 安全" if rate < 0.1 else
                          "🟡 部分破" if rate < 0.3 else "🔴 高危")
                st.markdown(f"### 整体破防 {data['n_breached_cases']}/{data['n_results']} "
                            f"= **{rate:.0%}** {badge}")
                if data.get("by_rubric"):
                    st.dataframe(pd.DataFrame([
                        {"Rubric": r["rubric"],
                         "失守/总": f"{r['breach']} / {r['n']}",
                         "破防率": f"{r['rate']:.0%}"}
                        for r in data["by_rubric"]
                    ]), hide_index=True, use_container_width=True)


# ====================== Tab 5:改进(recommend + apply + regression)======================
with tab5:
    from claw_eval.task_gen.apply_recommendation import (
        diff_stats, generate_prompt_patch, unified_diff,
    )

    st.subheader("📈 改进迭代")
    st.caption("recommend 找弱 rubric → 自动应用建议 → 备份新版本 → 回归对比。")

    sub_r1, sub_r2 = st.tabs(["💡 建议 + 自动应用", "🔄 回归对比"])

    with sub_r1:
        rec_file = REPORTS_DIR / f"recommendations_{task}.json"
        c_top1, _ = st.columns([1, 3])
        if c_top1.button("🔄 跑 recommend(LLM 3-5min)", key=f"recmp_{task}"):
            cmd = [sys.executable, "-m", "claw_eval.cli", "recommend", "--task", task]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            with st.spinner("分析中…"):
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                          env=env, cwd=str(ROOT))
            st.code(proc.stdout[-2000:], language="text")
            if proc.returncode == 0:
                st.rerun()

        if not rec_file.exists():
            st.info("还没建议。先跑过 batch + 上方按钮触发 recommend。")
        else:
            try:
                data = json.loads(rec_file.read_text(encoding="utf-8"))
                recs = data.get("recommendations", [])
            except Exception as e:
                st.error(f"解析失败:{e}"); recs = []
            st.caption(f"上次:{data.get('generated_at', '?')}({len(recs)} 条)")

            applied_rubrics = set()
            for v in versions:
                applied_rubrics.update(v.applied_recs)

            for i, r in enumerate(recs, 1):
                rid = r["rubric_id"]
                applied = rid in applied_rubrics
                marker = " ✓ 已应用" if applied else ""
                with st.expander(
                    f"**[{i}] {rid}** · avg={r['avg_score']:.2f}"
                    + (f" · 预期 +{r['estimated_lift']:.2f}"
                       if r.get('estimated_lift') else "")
                    + marker,
                    expanded=(i == 1 and not applied),
                ):
                    if r.get("suggested_prompt_change"):
                        st.markdown(f"**建议**:")
                        st.markdown(r["suggested_prompt_change"])
                    if r.get("rationale"):
                        st.caption(f"理由:{r['rationale']}")

                    patch_key = f"patch_{task}_{rid}"
                    if not applied and st.button(
                        f"🤖 自动应用",
                        key=f"applybtn_{task}_{rid}",
                        disabled=not r.get("suggested_prompt_change")):
                        with st.spinner("LLM 改写 prompt(~30s)…"):
                            with open(ROOT / "configs" / "models.yaml") as f:
                                cfg = yaml.safe_load(f)
                            judge_model = cfg["judge"]["model"]
                            try:
                                cur_data = yaml.safe_load(
                                    (task_dir / "task.yaml").read_text(encoding="utf-8"))
                                old_prompt = cur_data.get("prompt", "")
                                new_prompt = generate_prompt_patch(
                                    old_prompt, r, judge_model)
                                st.session_state[patch_key] = {
                                    "old": old_prompt, "new": new_prompt}
                            except Exception as exc:
                                st.error(f"LLM 失败:{exc}")

                    if patch_key in st.session_state:
                        p = st.session_state[patch_key]
                        stats = diff_stats(p["old"], p["new"])
                        st.markdown(f"**Diff**:加 {stats['added']} / 删 {stats['removed']} 行")
                        st.code(unified_diff(p["old"], p["new"],
                                              "当前", "改写后"), language="diff")
                        cA, cR = st.columns([1, 1])
                        if cA.button("✓ 接受 → 备份 + 应用", type="primary",
                                       key=f"acc_{task}_{rid}"):
                            new_label = f"vN_{datetime.now().strftime('%m%d_%H%M')}_{rid.replace('.','_')}"
                            if not versions:
                                save_version(task_dir, "v1", note="apply 前自动备份")
                            cur_data["prompt"] = p["new"]
                            (task_dir / "task.yaml").write_text(
                                yaml.safe_dump(cur_data, allow_unicode=True,
                                                sort_keys=False),
                                encoding="utf-8")
                            save_version(task_dir, new_label,
                                          based_on=versions[-1].label if versions else "v1",
                                          applied_recs=[rid],
                                          note=f"自动应用 {rid}")
                            st.success(f"✓ 应用!新版本 {new_label}。建议:跑 "
                                       f"`batch --label {new_label}` 验证。")
                            st.session_state.pop(patch_key, None); st.rerun()
                        if cR.button("✗ 拒绝", key=f"rej_{task}_{rid}"):
                            st.session_state.pop(patch_key, None); st.rerun()

    with sub_r2:
        # 回归对比
        all_runs = sorted({d.name for d in (TRACES_DIR.iterdir() if TRACES_DIR.exists() else [])
                            if d.is_dir()}, reverse=True)
        # 过滤本任务的
        task_run_ids = [r for r in all_runs
                        if any((TRACES_DIR / r).glob("*.result.json")) and
                        any(json.loads(rj.read_text(encoding="utf-8")).get("task_id") == task
                            for rj in (TRACES_DIR / r).glob("*.result.json"))]
        if len(task_run_ids) < 2:
            st.info(f"本任务只有 {len(task_run_ids)} 个 run,需 2 个才能对比。")
        else:
            c1, c2, c3 = st.columns(3)
            old_run = c1.selectbox("旧 run", task_run_ids,
                                    index=min(1, len(task_run_ids) - 1),
                                    key=f"old_run_{task}")
            new_run = c2.selectbox("新 run", task_run_ids, index=0,
                                    key=f"new_run_{task}")
            thr = c3.slider("阈值", 0.01, 0.30, 0.05, 0.01, key=f"thr_{task}")
            if st.button("🔄 跑回归", type="primary", key=f"runreg_{task}",
                          disabled=(old_run == new_run)):
                cmd = [sys.executable, "-m", "claw_eval.cli", "regression",
                       "--task", task, "--old", old_run, "--new", new_run,
                       "--threshold", str(thr)]
                env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
                with st.spinner("跑中…"):
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                              env=env, cwd=str(ROOT))
                st.code(proc.stdout, language="text")
        # 展示最近 regression JSON
        reg_file = REPORTS_DIR / f"regression_{task}.json"
        if reg_file.exists():
            st.markdown("---")
            data = json.loads(reg_file.read_text(encoding="utf-8"))
            st.markdown(f"**{data['old_label']} → {data['new_label']}** · "
                        f"{data.get('n_improvements', 0)} 改进 / "
                        f"{data.get('n_regressions', 0)} 退化")
            st.dataframe(pd.DataFrame([
                {"指标": "case 数", "old": data['old_total'], "new": data['new_total']},
                {"指标": "task_score 平均",
                 "old": f"{data['old_score_avg']:.3f}",
                 "new": f"{data['new_score_avg']:.3f}",
                 "Δ": f"{data['new_score_avg'] - data['old_score_avg']:+.3f}"},
                {"指标": "通过率",
                 "old": f"{data['old_pass_rate']*100:.0f}%",
                 "new": f"{data['new_pass_rate']*100:.0f}%",
                 "Δ": f"{(data['new_pass_rate']-data['old_pass_rate'])*100:+.0f}pp"},
            ]), hide_index=True, use_container_width=True)
            if data.get("by_rubric"):
                changes = [r for r in data["by_rubric"] if r.get("significance") != "flat"]
                if changes:
                    st.markdown(f"**rubric 显著变化({len(changes)} 条)**")
                    st.dataframe(pd.DataFrame([
                        {"Rubric": r["rubric_id"],
                         "old": f"{r['old_avg']:.2f}" if r.get("old_avg") is not None else "—",
                         "new": f"{r['new_avg']:.2f}" if r.get("new_avg") is not None else "—",
                         "Δ": f"{r['delta']:+.2f}" if r.get("delta") is not None else "—",
                         "类型": r["significance"],
                        } for r in changes
                    ]), hide_index=True, use_container_width=True)
