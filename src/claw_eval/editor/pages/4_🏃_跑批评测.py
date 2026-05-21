"""跑批评测 —— 后台 subprocess 调 claw-eval batch,实时显示进度。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from claw_eval.editor._utils import ROOT, list_personas, list_tasks

st.set_page_config(page_title="跑批评测", page_icon="🏃", layout="wide")
inject_global_style()
st.title("🏃 跑批评测")
st.caption("触发 `claw-eval batch`,日志在下方实时显示。后台 subprocess,关闭页面不影响后台运行。")

tasks = list_tasks()
if not tasks:
    st.error("没有任务"); st.stop()

c1, c2 = st.columns(2)
task = c1.selectbox("任务", tasks, key="batch_task")
mode = c2.radio("模式", ["uniform (--trials)", "比例分配 (--total)"], horizontal=True)

c3, c4, c5 = st.columns([1, 1, 2])
if mode.startswith("uniform"):
    trials = c3.number_input("--trials", 1, 30, 1)
    total = 0
else:
    total = c3.number_input("--total", 1, 500, 30)
    trials = 1

default_label = f"v_{datetime.now().strftime('%m%d_%H%M')}"
label = c4.text_input("--label", default_label,
                       help="run_id 标签;用于回归对比")

no_judge = c5.checkbox("--no-judge(只跑对话不评分)", value=False)

concurrency = st.slider("并发数 --concurrency", 1, 16, 4)

# ---- 启动 ----
log_placeholder = st.empty()
status_placeholder = st.empty()


def _build_cmd() -> list[str]:
    cmd = [sys.executable, "-m", "claw_eval.cli", "batch",
           "--task", task, "--label", label,
           "--concurrency", str(concurrency)]
    if total > 0:
        cmd += ["--total", str(total)]
    else:
        cmd += ["--trials", str(trials)]
    if no_judge:
        cmd.append("--no-judge")
    return cmd


if st.button("🏃 开跑", type="primary"):
    cmd = _build_cmd()
    st.caption(f"命令:`{' '.join(cmd)}`")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    log_file = ROOT / "reports" / f"_batch_{label}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("", encoding="utf-8")

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=str(ROOT), bufsize=1, text=True,
    )

    log_lines: list[str] = []
    spinner = st.empty()
    spinner.info(f"⏳ 正在运行(PID {proc.pid})…")

    while True:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line and proc.poll() is not None:
            break
        if line:
            log_lines.append(line.rstrip())
            # 刷新最新 30 行(避免过长)
            tail = "\n".join(log_lines[-30:])
            log_placeholder.code(tail, language="text")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)

    code = proc.returncode
    spinner.empty()
    if code == 0:
        status_placeholder.success(f"✓ 完成(exit=0)。结果在 traces/{label}/")
        st.balloons()
    else:
        status_placeholder.error(f"✗ 失败(exit={code})。看日志或 reports/_batch_{label}.log")

    # 列出新增 trace
    out_dir = ROOT / "traces" / label
    if out_dir.exists():
        n_jsonl = len(list(out_dir.glob("*.jsonl")))
        n_result = len(list(out_dir.glob("*.result.json")))
        st.caption(f"产物:{n_jsonl} trace + {n_result} result")
        if n_result > 0:
            st.markdown(f"下一步:**跑 `dashboard` 出报告** 或 **进「回归对比」页跟另一 run 对比**")
