"""任务详情 主入口 = 路由器。

视图:
  list   —— 任务列表(默认登录页)
  detail —— 单任务管理页

切换:session_state["view"] = "list" / "detail"

Sidebar 只有 2 项:
  📋 任务详情(= 本 app.py,默认 list 视图)
  ⚙️ 全局配置(pages/⚙️_全局配置.py)
"""
from __future__ import annotations

import streamlit as st

from claw_eval.editor._utils import inject_global_style
from claw_eval.editor.views.task_detail import render_task_detail
from claw_eval.editor.views.task_list import render_task_list


st.set_page_config(
    page_title="任务详情",
    page_icon="📋",
    layout="wide",
)
inject_global_style()

# 默认 view = list
st.session_state.setdefault("view", "list")

if st.session_state["view"] == "list":
    render_task_list()
elif st.session_state["view"] == "detail":
    render_task_detail()
else:
    st.session_state["view"] = "list"; st.rerun()
