"""回归对比 —— 触发 regression CLI + 渲染 JSON 结果。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pandas as pd
import streamlit as st

from claw_eval.editor._utils import REPORTS_DIR, ROOT, list_runs, list_tasks

st.set_page_config(page_title="回归对比", page_icon="📈", layout="wide")
st.title("📈 回归对比")
st.caption("对比两个 run 的差异;改完 Prompt 跑 v2 → 跟 v1 比看实际提升。")

tasks = list_tasks()
runs = list_runs()
if not tasks or len(runs) < 2:
    st.warning(f"需要至少 2 个 run 才能对比。当前 runs:{runs}")
    if runs:
        st.caption("用「跑批评测」页跑批就会产生 run。")
    st.stop()

c1, c2, c3 = st.columns(3)
task = c1.selectbox("任务", tasks)
old_run = c2.selectbox("旧 run", runs, index=0)
new_run = c3.selectbox("新 run", runs,
                         index=(min(len(runs) - 1, 1)))

threshold = st.slider("显著性阈值", 0.01, 0.30, 0.05, 0.01,
                       help="|Δ| < 阈值视为「持平」")

if st.button("📈 跑回归对比", type="primary",
             disabled=(old_run == new_run)):
    cmd = [sys.executable, "-m", "claw_eval.cli", "regression",
           "--task", task, "--old", old_run, "--new", new_run,
           "--threshold", str(threshold)]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    with st.spinner("跑中..."):
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    st.code(result.stdout + result.stderr, language="text")

# 渲染最新的 regression JSON(若存在)
reg_file = REPORTS_DIR / f"regression_{task}.json"
if reg_file.exists():
    st.markdown("---")
    st.subheader("📊 最近一次 regression JSON 可视化")
    try:
        data = json.loads(reg_file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        st.error(f"JSON 解析失败:{e}")
        st.stop()

    st.caption(f"{data['old_label']} → {data['new_label']}    "
               f"显著阈值 ±{data['threshold']}    "
               f"{data.get('n_improvements', 0)} 改进 · "
               f"{data.get('n_regressions', 0)} 退化")

    # 总览
    overview = pd.DataFrame([
        {"指标": "case 数", "old": data['old_total'], "new": data['new_total']},
        {"指标": "task_score 平均",
         "old": f"{data['old_score_avg']:.3f}",
         "new": f"{data['new_score_avg']:.3f}",
         "Δ": f"{data['new_score_avg'] - data['old_score_avg']:+.3f}"},
        {"指标": "通过率",
         "old": f"{data['old_pass_rate']*100:.0f}%",
         "new": f"{data['new_pass_rate']*100:.0f}%",
         "Δ": f"{(data['new_pass_rate']-data['old_pass_rate'])*100:+.0f}pp"},
    ])
    st.dataframe(overview, hide_index=True, use_container_width=True)

    # 维度
    if data.get("by_dimension"):
        st.markdown("**按维度**")
        dim_df = pd.DataFrame(data["by_dimension"],
                                columns=["维度", "old", "new", "Δ"])
        st.dataframe(dim_df, hide_index=True, use_container_width=True)

    # Rubric 显著变化
    if data.get("by_rubric"):
        changes = [r for r in data["by_rubric"] if r.get("significance") != "flat"]
        if changes:
            st.markdown(f"**按 Rubric · 显著变化({len(changes)} 条)**")
            rdf = pd.DataFrame([
                {
                    "Rubric": r["rubric_id"],
                    "维度": r["dimension"],
                    "old": (f"{r['old_avg']:.2f}" if r.get("old_avg") is not None else "—"),
                    "new": (f"{r['new_avg']:.2f}" if r.get("new_avg") is not None else "—"),
                    "Δ": (f"{r['delta']:+.2f}" if r.get("delta") is not None else "—"),
                    "变化": {"improve": "↑ 改进",
                             "regress": "↓ 退化",
                             "added": "+ 新增",
                             "removed": "− 删除"}.get(r["significance"], r["significance"]),
                }
                for r in changes
            ])
            st.dataframe(rdf, hide_index=True, use_container_width=True)
