# -*- coding: utf-8 -*-
"""Read-only Streamlit status page. It never starts a solver and never writes files."""
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ui.data import assemble_dashboard, display_path, scan_cases, scan_runs, suggest_rows  # noqa: E402

st.set_page_config(page_title="simufact-weld-pipeline 状态页", page_icon="🧭", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.2rem; max-width: 1500px;}
[data-testid="stMetricValue"] {font-size: 1.18rem;}
.readonly {color:#64748b; font-size:.9rem; margin-top:-.6rem;}
.status-ok {color:#047857;font-weight:700}.status-fail {color:#b91c1c;font-weight:700}.status-missing {color:#64748b;font-weight:700}
</style>
""", unsafe_allow_html=True)

st.title("simufact-weld-pipeline 状态页")
st.markdown('<div class="readonly">只读视图 · 不启动求解器 · 不修改 case、run 或参数库</div>', unsafe_allow_html=True)

cases = scan_cases()
if not cases:
    st.error("cases/*.json 中没有可显示的 case")
    st.stop()

with st.sidebar:
    st.header("浏览")
    labels = ["%s · %s" % (x["name"], x["file"]) for x in cases]
    requested = st.query_params.get("case")
    default_index = next((i for i, x in enumerate(cases) if x["name"] == requested), 0)
    selected = st.selectbox("Case", labels, index=default_index)
    case = cases[labels.index(selected)]
    runs = scan_runs(case["name"])
    if not runs:
        st.warning("该 case 没有已有运行")
        st.stop()
    run_dir = st.selectbox("已有运行", runs, format_func=lambda p: os.path.basename(p))
    st.caption(display_path(run_dir))

view = assemble_dashboard(case["name"], run_dir)
pf = view["preflight"]
line = "Preflight · block=%d · warn=%d · ok=%d · %s" % (pf["block"], pf["warn"], pf["ok"], pf["verdict"])
if pf["block"]:
    st.error(line)
elif pf["warn"]:
    st.warning(line)
else:
    st.success(line)
st.caption("来源：%s" % pf["source"])

titles = {"prep": "01 · Prep", "build": "02 · Build", "gate": "03 · Gate", "run": "04 · Run", "compare": "05 · Compare",
          "selfcheck": "06 · Selfcheck", "post": "07 · Post"}
for row in range((len(view["steps"]) + 2) // 3):
    cols = st.columns(3)
    for col, step in zip(cols, view["steps"][row * 3:(row + 1) * 3]):
        with col:
            with st.container(border=True):
                st.subheader(titles[step["key"]])
                css = "status-ok" if step["status"] == "ok" else "status-fail" if step["status"] == "fail" else "status-missing"
                st.markdown('<span class="%s">%s</span>' % (css, step["status"]), unsafe_allow_html=True)
                if step["duration_s"] is not None:
                    st.caption("耗时 %.2f s" % step["duration_s"])
                mcols = st.columns(2)
                for i, (label, value) in enumerate(step["metrics"][:5]):
                    mcols[i % 2].metric(label, value)
                with st.expander("日志原文"):
                    st.code(step["logs"], language="text")

st.divider()
st.subheader("参数库 Suggest")
with st.form("suggest_form"):
    a, b, c, d = st.columns(4)
    joint = a.selectbox("接头", ["fillet", "ring", "butt"])
    process = b.text_input("方法", "FCAW")
    material = c.text_input("材料", "Q420")
    thickness = d.text_input("板厚 mm", "6,10")
    submitted = st.form_submit_button("检索")
if submitted:
    try:
        warning, rows = suggest_rows(joint, process, material, thickness)
        if warning:
            st.warning(warning)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("同接头类型候选为 0")
    except ValueError:
        st.error("板厚请输入逗号分隔的数字，例如 6,10")
