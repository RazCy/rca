"""
Agentic RCA Agent - Streamlit UI
Includes:
  - Classic RCA flow (Trace ID / Problem ID)
  - Natural Language → DQL (rule-based, no Davis scope needed)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Make sure we can import from src/
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / "config" / "settings.env")

from agent import RCAAgent  # noqa: E402
from nlp_to_dql import nlp_to_dql  # noqa: E402
from dql_client import create_dql_client  # noqa: E402


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Agentic RCA Agent",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🔍 Agentic RCA Agent")
st.caption("Dynatrace → OpenSearch → Root Cause Analysis  |  Natural Language → DQL")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Configuration")

    dt_url = os.getenv("DYNATRACE_URL", "")
    dt_token = os.getenv("DYNATRACE_API_TOKEN", "")
    os_hosts = os.getenv("OPENSEARCH_HOSTS", "")

    st.markdown("**Dynatrace**")
    if dt_url and dt_token:
        st.success("Connected")
        st.caption(dt_url)
    else:
        st.error("Not configured")
        st.caption("Set DYNATRACE_URL and DYNATRACE_API_TOKEN")

    st.markdown("**OpenSearch**")
    if os_hosts:
        st.success("Connected")
        st.caption(os_hosts)
    else:
        st.warning("Optional – only needed for span/log correlation")

    st.divider()
    st.markdown("### Tips")
    st.markdown(
        """
**RCA tab**
- Provide Trace ID or Problem ID
- Agent finds Culprit + Victims

**NL → DQL tab**
- Type naturally, press Enter
- Examples:
  - `error logs last 2 hours`
  - `active problems`
  - `logs for PaymentService`
  - `spans for trace 2d45e1...`
        """
    )

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_rca, tab_dql = st.tabs(["🔎 Root Cause Analysis", "💬 Natural Language → DQL"])

# ===========================================================================
# TAB 1 – Classic RCA
# ===========================================================================
with tab_rca:
    col1, col2, col3 = st.columns([3, 3, 1])

    with col1:
        trace_id = st.text_input(
            "Trace ID",
            placeholder="e.g. 2d45e1cde4f8645b36ab31c07e2e67d6",
            key="rca_trace",
        )
    with col2:
        problem_id = st.text_input(
            "Problem ID (optional)",
            placeholder="e.g. -3923117663478183459_1752459060000V2",
            key="rca_problem",
        )
    with col3:
        hours = st.number_input("Lookback (h)", min_value=1, max_value=72, value=4, key="rca_hours")

    st.write("")
    if st.button("🔎 Investigate", type="primary", use_container_width=True, key="rca_btn"):
        if not trace_id and not problem_id:
            st.warning("Please provide at least a Trace ID or a Problem ID.")
        else:
            with st.spinner("Running investigation (Dynatrace → OpenSearch → RCA)..."):
                try:
                    agent = RCAAgent()
                    result = agent.investigate(
                        trace_id=trace_id or None,
                        problem_id=problem_id or None,
                        lookback_hours=int(hours),
                    )
                except Exception as e:
                    st.error(f"Investigation failed: {e}")
                    st.exception(e)
                    st.stop()

            dt = result.get("dynatrace") or {}
            spans = result.get("spans") or []
            logs = result.get("logs") or []
            report = result.get("root_cause_report") or ""

            culprit = dt.get("culprit") or dt.get("root_cause_entity") or {}
            victims = dt.get("victims") or []

            st.subheader("Culprit & Victims")
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("#### 🎯 Culprit")
                if culprit.get("name"):
                    st.success(f"**{culprit.get('name')}**")
                    if culprit.get("id"):
                        st.caption(culprit.get("id"))
                else:
                    st.info("Not identified by Davis")
            with c2:
                st.markdown("#### 💥 Victims")
                if victims:
                    for v in victims:
                        st.markdown(f"- {v}")
                else:
                    st.info("No additional victims reported")

            st.divider()
            st.subheader("Full Root Cause Analysis")
            st.markdown(report)

            with st.expander("Raw Dynatrace context"):
                st.json(dt)
            with st.expander(f"Spans ({len(spans)})"):
                st.json(spans[:20] if spans else [])
            with st.expander(f"Logs ({len(logs)})"):
                st.json(logs[:20] if logs else [])

# ===========================================================================
# TAB 2 – Natural Language → DQL
# ===========================================================================
with tab_dql:
    st.markdown("Type a question in plain English and press **Enter**. The system converts it to DQL and runs it.")

    # Using a form so Enter triggers submission without an extra click
    with st.form(key="nl_form", clear_on_submit=False):
        nl_query = st.text_input(
            "Ask in natural language",
            placeholder="e.g. error logs for PaymentService last 2 hours",
            key="nl_input",
        )
        submitted = st.form_submit_button("Run", type="primary")

    if submitted and nl_query.strip():
        # 1. Convert NL → DQL
        nlp_result = nlp_to_dql(nl_query)

        st.markdown("#### Generated DQL")
        st.code(nlp_result.dql, language="sql")

        meta_cols = st.columns(4)
        meta_cols[0].metric("Intent", nlp_result.intent)
        meta_cols[1].metric("Confidence", f"{nlp_result.confidence:.0%}")
        meta_cols[2].metric("Fallback", "Yes" if nlp_result.fallback else "No")
        meta_cols[3].metric("Time range", nlp_result.time_range or "default")

        st.caption(nlp_result.explanation)

        # 2. Execute DQL
        with st.spinner("Executing DQL against Dynatrace Grail..."):
            try:
                client = create_dql_client()
                result = client.execute(nlp_result.dql)
            except Exception as e:
                st.error(f"DQL execution failed: {e}")
                st.stop()

        if result.get("error"):
            st.error(f"Query error: {result.get('message', 'unknown')}")
            if result.get("status_code"):
                st.caption(f"HTTP {result['status_code']}")
        else:
            records = result.get("records") or []
            st.success(f"Returned **{len(records)}** records")
            if records:
                st.dataframe(records, use_container_width=True)
            else:
                st.info("Query succeeded but returned no records.")

            with st.expander("Raw response"):
                st.json(result.get("raw") or result)

    elif submitted:
        st.warning("Please type a question first.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.divider()
st.caption("Agentic RCA Agent • Dynatrace + OpenSearch • Rule-based NLP→DQL (no Davis CoPilot scope required)")
