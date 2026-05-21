"""嵌入查看 dashboard HTML 报告。"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from claw_eval.editor._utils import REPORTS_DIR, list_tasks

st.set_page_config(page_title="报告查看", page_icon="📊", layout="wide")
inject_global_style()
st.title("📊 报告查看")
st.caption("嵌入查看 dashboard 多页 HTML。先 `claw-eval dashboard` 或在「跑批评测」页跑批,这里就有内容。")

if not REPORTS_DIR.exists():
    st.warning("`reports/` 目录不存在 —— 先跑一次 batch + dashboard。")
    st.stop()

html_files = sorted(REPORTS_DIR.glob("*.html"))
if not html_files:
    st.warning("没有 HTML 报告。"); st.stop()

# 列表 + 选择
options = {f.name: f for f in html_files}
default = "index.html" if "index.html" in options else next(iter(options))
chosen = st.selectbox("选择页面", list(options.keys()),
                       index=list(options.keys()).index(default))

target = options[chosen]
st.caption(f"文件:`reports/{chosen}` · 大小 {target.stat().st_size // 1024} KB")

# 给个外部打开的链接(用户可能想全屏看)
col_a, col_b = st.columns([3, 1])
with col_a:
    st.markdown(f"原始链接:`file://{target}`")
with col_b:
    if st.button("🔄 重载"):
        st.rerun()

# 嵌入 iframe(读 HTML 文本直接渲染)
html = target.read_text(encoding="utf-8")
components.html(html, height=900, scrolling=True)
