"""任务详情页 —— 6 Tab 工作流入口。

从首页「→ 进入详情」按钮过来,通过 session_state["current_task"] 接收任务 ID。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
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
from claw_eval.models.persona import load_persona, load_noise_kinds
from claw_eval.models.rubric import load_rubrics
from claw_eval.models.task import TaskDefinition
from claw_eval.report.flow_viz import build_flow_option, color_for
from claw_eval.sampling import (
    NoiseOverlay,
    SamplingConfig,
    load_sampling,
    save_sampling,
)

st.set_page_config(page_title="任务详情", page_icon="📋", layout="wide")
inject_global_style()

# --------------------- 任务选择 ---------------------
tasks = list_tasks()
if not tasks:
    st.error("没有任务。"); st.stop()

current = st.session_state.get("current_task")
if current not in tasks:
    current = tasks[0]

c1, c2 = st.columns([3, 1])
selected = c1.selectbox("任务", tasks, index=tasks.index(current))
st.session_state["current_task"] = selected

task = selected
task_dir = TASKS_DIR / task
td = task_dir  # alias

# --------------------- 顶部:进度阶段条 ---------------------
def _stage(t: str) -> tuple[str, str]:
    if not (TASKS_DIR / t / "task.yaml").exists():
        return ("0", "未初始化")
    has_rubrics = (TASKS_DIR / t / "rubrics.yaml").exists()
    n_p = len(list_personas(t))
    if not (has_rubrics and n_p > 0):
        return ("1", "① 已生成(待审)")
    task_runs = set()
    if TRACES_DIR.exists():
        for rdir in TRACES_DIR.iterdir():
            if rdir.is_dir():
                for rj in rdir.glob("*.result.json"):
                    try:
                        if json.loads(rj.read_text(encoding="utf-8")).get("task_id") == t:
                            task_runs.add(rdir.name); break
                    except Exception:
                        pass
    n_runs = len(task_runs)
    if n_runs == 0: return ("2", "② 已确认")
    if n_runs == 1: return ("3", "③ 有 baseline")
    return ("4", "④ 改进中")


stage_id, stage_label = _stage(task)
stages = [
    ("0", "未初始化"),
    ("1", "已生成"),
    ("2", "已确认"),
    ("3", "有 baseline"),
    ("4", "改进中"),
]
html_stages = []
for sid, slbl in stages:
    if sid < stage_id:
        html_stages.append(f'<span class="stage done">{slbl}</span>')
    elif sid == stage_id:
        html_stages.append(f'<span class="stage current">{slbl}</span>')
    else:
        html_stages.append(f'<span class="stage">{slbl}</span>')
    if sid != stages[-1][0]:
        html_stages.append('<span class="arrow">→</span>')
st.markdown(f"""
<div class="lifecycle">
  <strong>{task}</strong> · 当前阶段:&nbsp; {''.join(html_stages)}
</div>
""", unsafe_allow_html=True)

# --------------------- 6 Tab ---------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🌲 逻辑分支", "📝 Prompt", "📐 评测方案",
    "👥 模拟用户", "🏃 评测", "📈 改进",
])


# ========================== Tab 1:逻辑分支 ==========================
with tab1:
    st.subheader("任务流程图 + 节点级覆盖")
    st.caption("每个节点显示:**👥 N persona 覆盖** + **rubric 平均得分**(若有跑批)。"
               "缺覆盖的节点高亮红色 —— 提示需要补 persona 或修改剧本声明。")

    flow_path = task_dir / "flow.yaml"
    if not flow_path.exists():
        st.warning(f"本任务还没有 `flow.yaml`。可在「评测方案」Tab 里补,"
                   "或下轮的「任务生成器」自动产出。")
    else:
        flow = load_flow(flow_path)
        # 算每个节点的 cover 数(从 persona YAML 的 covers_flow_nodes 声明里数)
        cover_count: dict[str, int] = {}
        for n in flow.nodes:
            cover_count[n.id] = 0
        for pname in list_personas(task):
            pyaml = task_dir / "personas" / f"{pname}.yaml"
            try:
                d = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
                for nid in d.get("covers_flow_nodes", []):
                    if nid in cover_count:
                        cover_count[nid] += 1
            except Exception:
                pass

        # 算每个节点的 rubric 平均得分
        rubric_scores: dict[str, float | None] = {}
        # 取最近一个有该任务 result 的 run
        try:
            from claw_eval.report.aggregate import aggregate, load_results_dir
            results = [r for r in load_results_dir(TRACES_DIR)
                       if r.task_id == task]
            if results:
                summary = aggregate(results)
                for n in flow.nodes:
                    if n.rubric:
                        info = summary.by_rubric.get(n.rubric)
                        rubric_scores[n.rubric] = (
                            info["avg_score"] if info else None)
        except Exception:
            pass

        # 顶部告警:缺覆盖节点
        uncovered = [n for n in flow.nodes if cover_count[n.id] == 0
                     and n.id not in ("START", "END")]
        if uncovered:
            st.markdown(
                f'<div class="warn-banner">⚠ <strong>{len(uncovered)} 个节点没有任何 persona 声明覆盖</strong>:'
                f'{", ".join(n.id for n in uncovered[:5])}'
                f'{"…" if len(uncovered) > 5 else ""} · '
                'persona YAML 加 <code>covers_flow_nodes</code> 字段声明即可。</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div class="eval-card">✓ 所有节点都有 persona 声明覆盖</div>',
                         unsafe_allow_html=True)

        # 渲染 ECharts:节点 label 拼 cover 数
        # 改造 build_flow_option:直接用,但我们修 node label 来加 cover 数
        scores_for_color = rubric_scores if rubric_scores else {n.rubric: None for n in flow.nodes if n.rubric}
        option = build_flow_option(flow, scores_for_color)
        # 改 label:加 cover 数
        for nd in option["series"][0]["data"]:
            nid = nd["name"]
            cnt = cover_count.get(nid, 0)
            old_formatter = nd["label"]["formatter"]
            nd["label"]["formatter"] = f"{old_formatter}\n👥{cnt}"
            # cover=0 节点用红色填充覆盖(优先于 rubric 颜色)
            if cnt == 0 and nid not in ("START", "END"):
                nd["itemStyle"]["color"] = "#ef4444"
                nd["itemStyle"]["borderColor"] = "#dc2626"
                nd["label"]["color"] = "#fff"

        # 嵌入 ECharts
        import json as _json
        st.components.v1.html(f"""
<div id="flow" style="height: 420px;"></div>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>
(function(){{
  var el = document.getElementById('flow');
  if (!el || !window.echarts) return;
  echarts.init(el).setOption({_json.dumps(option)});
}})();
</script>
""", height=440)

        # 覆盖详情
        st.markdown("**节点覆盖详情**")
        rows = []
        for n in flow.nodes:
            covered_by = []
            for pname in list_personas(task):
                pyaml = task_dir / "personas" / f"{pname}.yaml"
                try:
                    d = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
                    if n.id in d.get("covers_flow_nodes", []):
                        covered_by.append(pname)
                except Exception:
                    pass
            rows.append({
                "节点": n.id,
                "标签": n.label,
                "Cover 数": len(covered_by),
                "覆盖 persona": ", ".join(covered_by) or "—",
                "rubric": n.rubric or "—",
                "rubric 平均分": (f"{rubric_scores[n.rubric]:.2f}"
                              if n.rubric and rubric_scores.get(n.rubric) is not None
                              else "—"),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ========================== Tab 2:Prompt ==========================
with tab2:
    from claw_eval.task_gen.versioning import (
        get_version_yaml, list_versions, save_version, switch_to_version,
    )

    st.subheader("任务 Prompt(task.yaml)+ 版本管理")
    yaml_path = task_dir / "task.yaml"
    if not yaml_path.exists():
        st.error("task.yaml 不存在"); st.stop()

    versions = list_versions(task_dir)

    # 版本切换栏
    if versions:
        c_v1, c_v2 = st.columns([3, 1])
        ver_labels = [v.label for v in versions]
        ver_idx = ver_labels.index(versions[-1].label)
        chosen_ver = c_v1.selectbox(
            "切换到历史版本(读 .versions/<label>.yaml)",
            ver_labels, index=ver_idx, key=f"verselect_{task}",
            help="当前 task.yaml 等同于上一次 save_version 的版本")
        if c_v2.button("↩ 切回此版本", key=f"verswitch_{task}",
                        disabled=(chosen_ver == versions[-1].label)):
            switch_to_version(task_dir, chosen_ver)
            st.success(f"✓ task.yaml 已切到 {chosen_ver}")
            st.rerun()

    try:
        td_obj = TaskDefinition.from_yaml(yaml_path)
    except Exception as e:
        st.error(f"加载失败:{e}"); st.stop()

    st.markdown("**Prompt(给 SUT 的 system message)**")
    new_prompt = st.text_area("", value=td_obj.prompt,
                                height=380, label_visibility="collapsed",
                                key=f"prompt_ta_{task}")

    st.markdown("**业务变量**")
    if td_obj.variables:
        st.dataframe(
            pd.DataFrame([{"变量": k, "值": v} for k, v in td_obj.variables.items()]),
            hide_index=True, use_container_width=True,
        )
    else:
        st.caption("(没有声明变量)")

    # 保存 + 备份版本
    c_s1, c_s2 = st.columns([1, 2])
    if c_s1.button("💾 保存 Prompt", type="primary", key="save_prompt"):
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        data["prompt"] = new_prompt
        yaml_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                            default_flow_style=False),
            encoding="utf-8")
        st.success("✓ 已保存。改了 prompt 后跑 batch 可能跟旧版评测结果有差异。")
    new_ver_label = c_s2.text_input("版本 label(留空不备份)",
                                       placeholder="如 v2-peak-fix",
                                       key=f"newver_{task}")
    if new_ver_label and st.button("📌 备份当前为新版本", key=f"savever_{task}"):
        info = save_version(task_dir, new_ver_label,
                              based_on=versions[-1].label if versions else None,
                              note="手动备份")
        st.success(f"✓ 已备份为 {info.label}")
        st.rerun()

    # 版本历史
    if versions:
        st.markdown("---")
        st.markdown("**版本历史**")
        st.dataframe(pd.DataFrame([
            {"label": v.label, "时间": v.created_at[:16],
             "based_on": v.based_on or "—",
             "应用建议": ", ".join(v.applied_recs) or "—",
             "备注": v.note}
            for v in reversed(versions)
        ]), hide_index=True, use_container_width=True)


# ========================== Tab 3:评测方案 ==========================
with tab3:
    st.subheader("评测方案:rubrics + grader.py")
    rb_path = task_dir / "rubrics.yaml"
    if not rb_path.exists():
        st.warning("没有 rubrics.yaml")
    else:
        rubrics = load_rubrics(rb_path)
        st.caption(f"共 {len(rubrics)} 条 rubric")
        rdf = pd.DataFrame([
            {
                "id": r.id,
                "category": r.category or "—",
                "dimension": r.dimension,
                "method": r.method,
                "weight": r.weight,
                "is_safety": "★" if r.is_safety else "",
                "trigger": r.trigger.type if r.trigger else "—",
                "check": r.check[:60] + ("…" if len(r.check) > 60 else ""),
            }
            for r in rubrics
        ])
        st.dataframe(rdf, hide_index=True, use_container_width=True)

    st.markdown("---")
    grader_path = task_dir / "grader.py"
    if grader_path.exists():
        with st.expander("📜 grader.py(只读)", expanded=False):
            st.code(grader_path.read_text(encoding="utf-8"), language="python")
    else:
        st.warning("没有 grader.py")


# ========================== Tab 4:模拟用户 ==========================
with tab4:
    st.subheader("模拟用户管理")
    st.caption("卡片化展示;权重/噪音 overlay 编辑保存到 sampling.yaml。")

    sampling_path = task_dir / "sampling.yaml"
    if sampling_path.exists():
        sampling_cfg = load_sampling(sampling_path)
    else:
        sampling_cfg = SamplingConfig()

    persona_names = sorted(p for p in list_personas(task) if not p.startswith("adv_"))

    # 算每个 persona 的 covers_flow_nodes 数
    persona_covers: dict[str, int] = {}
    persona_demographics: dict[str, dict] = {}
    for pname in persona_names:
        pyaml = task_dir / "personas" / f"{pname}.yaml"
        try:
            d = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
            persona_covers[pname] = len(d.get("covers_flow_nodes", []))
            # 加载完整 Persona 拿 demographics
            p = load_persona(pyaml,
                              personalities_dir=ROOT / "personalities",
                              noise_file=NOISE_FILE)
            persona_demographics[pname] = p.demographics.model_dump()
        except Exception:
            persona_covers[pname] = 0
            persona_demographics[pname] = {}

    # 卡片网格(3 列)
    GENDER_ICON = {"male": "🧔", "female": "👩", "unspecified": "👤"}
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
  <div class="pc-meta">
    {mbti} · {age} · {edu}<br>
    态度:{attitude}
  </div>
  <div style="display:flex; align-items:baseline; gap:6px;">
    <span class="pc-weight">{int(weight)}</span>
    <span style="color:#64748b; font-size:0.85rem;">权重</span>
  </div>
  <div class="pc-meta" style="margin-top:6px;">
    🌲 覆盖 {cover_n} 节点
  </div>
</div>
""", unsafe_allow_html=True)

    # 比例编辑表格
    st.markdown("---")
    st.markdown("**权重编辑**")
    rows = [{"persona": p, "weight": float(sampling_cfg.weights.get(p, 0))}
            for p in persona_names]
    edited = st.data_editor(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        column_config={
            "persona": st.column_config.TextColumn("Persona", disabled=True),
            "weight": st.column_config.NumberColumn("权重", min_value=0.0,
                                                      step=1.0),
        }, key=f"weights_{task}",
    )

    # 噪音 overlay
    st.markdown("**噪音 overlay**(整 case 加噪 · 全程必噪)")
    c1, c2 = st.columns([1, 2])
    new_rate = c1.slider("rate", 0.0, 1.0,
                          float(sampling_cfg.noise_overlay.rate), 0.05,
                          key=f"ov_rate_{task}")
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
            noise_overlay=NoiseOverlay(rate=new_rate, kinds=new_kinds),
        )
        save_sampling(draft, sampling_path)
        st.success(f"✓ 已保存 {sampling_path.relative_to(ROOT)}")


# ========================== Tab 5:评测 ==========================
with tab5:
    st.subheader("跑评测 + 历史 runs")

    c1, c2, c3 = st.columns([1, 1, 2])
    total = c1.number_input("--total", 5, 200, 30, key=f"batch_total_{task}")
    label = c2.text_input("--label", f"{task[:3]}_{__import__('datetime').datetime.now().strftime('%m%d_%H%M')}",
                            key=f"batch_label_{task}")
    no_judge = c3.checkbox("--no-judge", value=False, key=f"batch_nj_{task}")

    if st.button("🏃 开跑(后台)", type="primary", key=f"run_batch_{task}"):
        cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
               "--task", task, "--total", str(total),
               "--label", label]
        if no_judge: cmd.append("--no-judge")
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        st.caption(f"`{' '.join(cmd)}`")
        with st.spinner("跑批中…"):
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                    env=env, cwd=str(ROOT))
        st.code(proc.stdout[-3000:] + proc.stderr[-1000:], language="text")
        if proc.returncode == 0:
            st.success(f"✓ run_id={label}")

    # 历史 runs(只列本任务的)
    st.markdown("---")
    st.markdown("**本任务的历史 runs**")
    task_runs = []
    if TRACES_DIR.exists():
        for rdir in sorted(TRACES_DIR.iterdir(), reverse=True):
            if not rdir.is_dir():
                continue
            n_result = 0
            for rj in rdir.glob("*.result.json"):
                try:
                    if json.loads(rj.read_text(encoding="utf-8")).get("task_id") == task:
                        n_result += 1
                except Exception:
                    pass
            if n_result > 0:
                task_runs.append({"run_id": rdir.name, "result 数": n_result})

    if task_runs:
        st.dataframe(pd.DataFrame(task_runs), hide_index=True,
                      use_container_width=True)
    else:
        st.caption("(还没跑过)")


# ========================== Tab 6:改进(含自动应用)==========================
with tab6:
    from claw_eval.task_gen.apply_recommendation import (
        diff_stats, generate_prompt_patch, unified_diff,
    )
    from claw_eval.task_gen.versioning import save_version, list_versions

    st.subheader("改进建议 + 一键应用到 prompt")
    st.caption("**自改作用面**:只动 task.yaml 的 prompt;rubrics / personas / sampling 不动。"
                "接受 → 自动创建新版本(可在 Tab 📝 切回)。")

    rec_file = REPORTS_DIR / f"recommendations_{task}.json"

    c_top1, c_top2 = st.columns([1, 3])
    if c_top1.button("🔄 跑 recommend(LLM 3-5 分钟)", key=f"recompute_{task}"):
        cmd = [sys.executable, "-m", "claw_eval.cli", "recommend",
               "--task", task]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        with st.spinner("分析中(LLM 调用,可能 3-5 分钟)…"):
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                    env=env, cwd=str(ROOT))
        st.code(proc.stdout[-2000:], language="text")
        if proc.returncode == 0:
            st.success("✓ 完成。下面就显示新建议。"); st.rerun()

    if not rec_file.exists():
        st.info("还没有改进建议。先跑过 batch(产生 result.json)+ 上方按钮触发 recommend。")
    else:
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
            recs = data.get("recommendations", [])
        except Exception as e:
            st.error(f"解析失败:{e}"); recs = []

        st.caption(f"上次 recommend:{data.get('generated_at', '?')}({len(recs)} 条)")

        # 每条建议
        for i, r in enumerate(recs, 1):
            rid = r["rubric_id"]
            with st.expander(
                f"**[{i}] {rid}** · avg={r['avg_score']:.2f} · "
                f"severity={r.get('severity', 0):.2f}"
                + (f" · 预期 +{r['estimated_lift']:.2f}"
                   if r.get('estimated_lift') else ""),
                expanded=(i == 1),
            ):
                if r.get("suggested_prompt_change"):
                    st.markdown("**建议**:")
                    st.markdown(r["suggested_prompt_change"])
                if r.get("rationale"):
                    st.caption(f"**理由**:{r['rationale']}")
                if r.get("violation_samples"):
                    with st.expander(f"违规样本({len(r['violation_samples'])} 个)"):
                        for s in r["violation_samples"]:
                            st.markdown(
                                f"- `{s.get('case', '?')}` 第{s.get('turn', '?')}轮 "
                                f"(分 {s.get('score', '?')}):"
                                f"<br>&nbsp;&nbsp;<em>{s.get('evidence', '')[:120]}</em>",
                                unsafe_allow_html=True,
                            )

                # ---- 自动应用按钮 + diff ----
                st.markdown("---")
                patch_key = f"patch_{task}_{rid}"
                btn_key = f"applybtn_{task}_{rid}"

                if c_top2.empty() and False:
                    pass

                if st.button(f"🤖 自动应用建议(LLM 生成 patch)",
                             key=btn_key,
                             disabled=not r.get("suggested_prompt_change")):
                    with st.spinner("LLM 改写 prompt 中(~30s)…"):
                        cfg_path = ROOT / "configs" / "models.yaml"
                        with open(cfg_path) as f:
                            cfg = yaml.safe_load(f)
                        judge_model = cfg["judge"]["model"]
                        try:
                            # 读当前 task.yaml prompt
                            cur_data = yaml.safe_load(
                                (task_dir / "task.yaml").read_text(encoding="utf-8"))
                            old_prompt = cur_data.get("prompt", "")
                            new_prompt = generate_prompt_patch(
                                old_prompt, r, judge_model)
                            st.session_state[patch_key] = {
                                "old": old_prompt,
                                "new": new_prompt,
                            }
                        except Exception as exc:
                            st.error(f"LLM 调用失败:{exc}")

                if patch_key in st.session_state:
                    p = st.session_state[patch_key]
                    stats = diff_stats(p["old"], p["new"])
                    st.markdown(
                        f"**Diff**:加 {stats['added']} 行 / 删 {stats['removed']} 行")
                    diff_text = unified_diff(
                        p["old"], p["new"],
                        old_label="当前 prompt", new_label="LLM 改写后")
                    st.code(diff_text, language="diff")

                    cA, cR = st.columns([1, 1])
                    accept_key = f"accept_{task}_{rid}"
                    if cA.button(f"✓ 接受 → 备份 + 应用", type="primary",
                                  key=accept_key):
                        # 备份当前 + 写新 prompt
                        from datetime import datetime as _dt
                        new_label = f"vN_{_dt.now().strftime('%m%d_%H%M')}_{rid.replace('.','_')}"
                        versions = list_versions(task_dir)
                        prev_label = versions[-1].label if versions else "v1"
                        # 1) 先备份当前(应用前的状态)
                        if not versions:
                            save_version(task_dir, "v1",
                                          note="apply 前自动备份")
                        # 2) 写新 prompt
                        cur_data["prompt"] = p["new"]
                        (task_dir / "task.yaml").write_text(
                            yaml.safe_dump(cur_data, allow_unicode=True,
                                            sort_keys=False,
                                            default_flow_style=False),
                            encoding="utf-8")
                        # 3) 备份新版本
                        save_version(task_dir, new_label,
                                      based_on=prev_label,
                                      applied_recs=[rid],
                                      note=f"自动应用建议 {rid}")
                        st.success(
                            f"✓ 应用完成。新版本:{new_label}。"
                            f"建议:用「Tab 🏃 评测」跑 `--label {new_label}`,"
                            "然后在「📈 回归对比」页跟基线比对。")
                        st.session_state.pop(patch_key, None)
                        st.rerun()
                    if cR.button("✗ 拒绝(关闭 diff)",
                                  key=f"reject_{task}_{rid}"):
                        st.session_state.pop(patch_key, None)
                        st.rerun()
