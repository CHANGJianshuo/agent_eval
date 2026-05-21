"""新建任务 —— 贴 prompt 一键生成 task.yaml + flow + rubrics + personas + grader.py。

调底层 CLI `claw-eval generate-task` 跑;完成后跳「任务详情」页继续审核。
"""
from __future__ import annotations

import os
import subprocess
import sys
import re
from pathlib import Path

import streamlit as st

from claw_eval.editor._utils import ROOT, TASKS_DIR, inject_global_style, list_tasks

st.set_page_config(page_title="新建任务", page_icon="➕", layout="wide")
inject_global_style()

st.title("➕ 新建任务 · 一键生成")
st.caption("贴一段任务 prompt → 自动产 task.yaml + flow.yaml + rubrics 草稿 + personas 草稿 + grader.py。"
           "完成后跳「任务详情」页审核每一项。")


# ---------- 输入 ----------
c1, c2 = st.columns([1, 1])
task_id = c1.text_input(
    "任务 ID(英文小写下划线)", value="",
    placeholder="如:live_upgrade_v2",
    help="将作为 tasks/<id>/ 目录名")
task_name = c2.text_input("任务名(中文,可选)", value="",
                            placeholder="如:课程平台直播升级通知")


_EXAMPLE = """# Role: Customer Support Specialist for ...

## Task: ...

# Constraints:
- ...

# Opening Line: ...

# Conversation Flow:
## Step 1: ...
## Step 2: ...
"""

st.markdown("**任务 Prompt**(完整 SUT system prompt;粘进来即可)")
prompt = st.text_area("", value="",
                        height=380, label_visibility="collapsed",
                        placeholder=_EXAMPLE)

# 校验
ok_id = bool(task_id) and bool(re.fullmatch(r"[a-z][a-z0-9_]*", task_id))
ok_prompt = len(prompt.strip()) > 50
ok_new = bool(task_id) and not (TASKS_DIR / task_id).exists()

issues = []
if not task_id:
    issues.append("⚠ 还没填任务 ID")
elif not ok_id:
    issues.append("⚠ 任务 ID 不合法(英文小写下划线开头)")
elif not ok_new:
    issues.append(f"⚠ tasks/{task_id}/ 已存在,换个 ID 或先删除")
if not ok_prompt:
    issues.append("⚠ 任务 Prompt 太短(<50 字),贴完整描述")

ready = ok_id and ok_new and ok_prompt
for issue in issues:
    st.warning(issue)


# ---------- LLM 选项 ----------
st.markdown("---")
st.markdown("**LLM 设置**(用 configs/models.yaml 里的 judge 模型;默认 mimo-v2.5-pro)")
st.caption("整个生成 4-6 个 LLM 调用,预计 3-5 分钟。")


# ---------- 一键生成 ----------
if st.button("🚀 一键生成", type="primary", disabled=not ready):
    # 写 prompt 到临时文件
    tmp_prompt = ROOT / f"/tmp/_gen_prompt_{task_id}.md"
    tmp_prompt.parent.mkdir(parents=True, exist_ok=True)
    tmp_prompt = Path(f"/tmp/_gen_prompt_{task_id}.md")
    tmp_prompt.write_text(prompt, encoding="utf-8")

    cmd = [sys.executable, "-m", "claw_eval.cli", "generate-task",
           "--prompt", str(tmp_prompt), "--id", task_id]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    st.caption(f"命令:`{' '.join(cmd)}`")
    log_box = st.empty()
    log_box.info("⏳ 启动 LLM 调用…(预计 3-5 分钟,期间可去喝杯水)")

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=str(ROOT), bufsize=1, text=True)

    log_lines: list[str] = []
    log_pane = st.empty()
    while True:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line and proc.poll() is not None:
            break
        if line and not any(s in line for s in
                              ("WARNING", "botocore", "LiteLLM",
                               "use_container_width")):
            log_lines.append(line.rstrip())
            log_pane.code("\n".join(log_lines[-30:]), language="text")

    log_box.empty()
    if proc.returncode == 0:
        st.success(f"✓ 完成!任务 `{task_id}` 已生成到 tasks/{task_id}/")
        st.balloons()
        td = TASKS_DIR / task_id
        files = sorted(td.glob("*"))
        st.markdown("**产出文件**:")
        for f in files:
            if f.is_file():
                st.markdown(f"- `{f.relative_to(ROOT)}` ({f.stat().st_size // 1024} KB)")
            elif f.is_dir():
                subs = list(f.glob("*"))
                st.markdown(f"- `{f.relative_to(ROOT)}/` ({len(subs)} 个文件)")
        if st.button("→ 去任务详情页审核", type="primary"):
            st.session_state["current_task"] = task_id
            st.switch_page("pages/0_📋_任务详情.py")
    else:
        st.error(f"✗ 生成失败(exit={proc.returncode})。看上方日志。常见原因:LLM API key 没配 / 任务 prompt 太复杂 LLM YAML 输出格式错。")


st.markdown("---")
st.subheader("已有任务")
existing = list_tasks()
if existing:
    st.caption(f"{len(existing)} 个:{', '.join(existing)}")
else:
    st.caption("(无)")
