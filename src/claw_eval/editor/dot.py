"""Persona 状态机 → Graphviz DOT 字符串(供 Streamlit st.graphviz_chart 渲染)。"""
from __future__ import annotations


def build_dot(states: list[str],
              transitions: list[tuple[str, str]],
              initial_state: str) -> str:
    """生成 DOT。

    样式约定:
    - 普通状态:浅灰圆角矩形
    - 初始状态:蓝色填充
    - END:绿色椭圆(终态)
    """
    lines = [
        "digraph G {",
        "  rankdir=LR;",
        "  bgcolor=\"transparent\";",
        '  node [shape=box, style="rounded,filled", fillcolor="#f1f5f9", '
        'color="#cbd5e1", fontname="PingFang SC, Microsoft YaHei, sans-serif", '
        'fontsize=12];',
        '  edge [color="#94a3b8", fontname="PingFang SC, Microsoft YaHei, sans-serif"];',
    ]

    seen_states = set(states)
    # 起始节点高亮
    if initial_state in seen_states:
        lines.append(
            f'  "{initial_state}" [fillcolor="#3370ff", '
            f'fontcolor="white", color="#3370ff"];'
        )

    # 如果转移里出现了 END,显示一个绿色椭圆终态
    if any(t == "END" for _, t in transitions):
        lines.append(
            '  "END" [shape=ellipse, fillcolor="#22c55e", '
            'fontcolor="white", color="#22c55e"];'
        )

    # 转移
    for fr, to in transitions:
        if not fr or not to:
            continue
        lines.append(f'  "{fr}" -> "{to}";')

    lines.append("}")
    return "\n".join(lines)
