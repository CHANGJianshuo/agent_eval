"""任务详情 主入口 = 路由器。

3 层视图:
  list           —— 任务列表(默认登录页)
  task_overview  —— 单任务概览(测试列表 + 新建测试 + 任务级配置)
  test_detail    —— 单次测试详情(进度 + 报告 + 建议 + 对比)
"""
from __future__ import annotations

import streamlit as st

from claw_eval.editor._utils import inject_global_style
from claw_eval.editor.views.task_list import render_task_list
from claw_eval.editor.views.task_overview import render_task_overview
from claw_eval.editor.views.test_detail import render_test_detail


st.set_page_config(
    page_title="任务详情",
    page_icon="📋",
    layout="wide",
)
inject_global_style()

st.session_state.setdefault("view", "list")

view = st.session_state["view"]

if view == "list":
    render_task_list()
elif view == "task_overview":
    render_task_overview()
elif view == "test_detail":
    render_test_detail()
else:
    st.session_state["view"] = "list"; st.rerun()
