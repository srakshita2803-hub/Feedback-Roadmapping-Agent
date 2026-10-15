"""Streamlit front-end for the Customer Feedback Clustering & Roadmap Agent.

Run:  streamlit run app.py
"""

import json
import os
import tempfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from roadmap_agent import report
from roadmap_agent.offline import cluster_offline
from roadmap_agent.store import WEIGHTS, FeedbackStore

SAMPLE = Path(__file__).parent / "data" / "feedback.csv"
TIER_COLORS = {"Now": "#dc2626", "Next": "#d97706", "Later": "#64748b"}

st.set_page_config(page_title="Feedback → Roadmap Agent", page_icon="🧭", layout="wide")

# ---------------- sidebar: inputs ----------------
with st.sidebar:
    st.header("1 · Feedback source")
    upload = st.file_uploader("Upload CSV or JSON", type=["csv", "json"],
                              help="Needs a `text` column. Optional: id, date, channel, customer, plan, arr.")
    st.caption("No file? The bundled sample (80 items, 6 channels) is used.")

    st.header("2 · Engine")
    mode = st.radio("Clustering engine", ["Claude agent", "Offline keyword baseline"],
                    help="The baseline needs no API key; it's useful for demos and comparison.")
    api_key = ""
    if mode == "Claude agent":
        # A server-side key (Colab secret / Streamlit Cloud secret) is used on the server and never
        # sent to the browser; visitors can still paste their own key instead.
        server_key = os.environ.get("ANTHROPIC_API_KEY", "")
        user_key = st.text_input("Anthropic API key", type="password",
                                 placeholder="Using the server's key" if server_key else "sk-ant-…",
                                 help="Optional when the app owner has configured a key.")
        api_key = user_key or server_key

    run = st.button("Build roadmap", type="primary", use_container_width=True)

    with st.expander("How scores work"):
        st.markdown("\n".join(f"- **{k}**: {int(v * 100)}%" for k, v in WEIGHTS.items()))
        st.caption("Tiers: Now ≥ 60 · Next ≥ 35 · Later < 35. Scores are computed in code, not by the LLM.")


def load_store() -> FeedbackStore:
    if upload is None:
        return FeedbackStore.from_file(SAMPLE)
    suffix = Path(upload.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(upload.getvalue())
    return FeedbackStore.from_file(tmp.name)


# ---------------- header + raw data preview ----------------
st.title("🧭 Customer Feedback → Product Roadmap")
st.write("An AI agent that categorizes, clusters and counts feature requests from every channel, "
         "then ranks the most-demanded updates.")

try:
    store = load_store()
except Exception as e:  # malformed upload
    st.error(f"Could not read the file: {e}")
    st.stop()

with st.expander(f"Raw feedback ({len(store.feedback)} items)"):
    st.dataframe(pd.DataFrame([vars(f) for f in store.feedback.values()]), use_container_width=True, hide_index=True)

# ---------------- run ----------------
if run:
    if mode == "Claude agent" and not api_key:
        st.sidebar.error("Enter an API key, or switch to the offline baseline.")
        st.stop()
    try:
        if mode == "Claude agent":
            from roadmap_agent.agent import run_agent
            with st.status("Agent is reading and clustering feedback…", expanded=True) as status:
                result = run_agent(store, on_event=lambda line: st.write(line), api_key=api_key)
                status.update(label="Roadmap ready", state="complete", expanded=False)
        else:
            result = cluster_offline(store)
    except Exception as e:
        st.error(f"Run failed: {e}")
        st.stop()
    st.session_state["report"] = report.build(store, result)

rep = st.session_state.get("report")
if not rep:
    st.info("Choose a source and engine in the sidebar, then click **Build roadmap**.")
    st.stop()

# ---------------- results ----------------
s, ranked = rep["summary"], rep["roadmap"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Feedback items", s["total_feedback"])
c2.metric("Channels", len(s["by_channel"]))
c3.metric("Clusters", s["clusters"])
c4.metric("Build-now themes", sum(1 for x in ranked if x["tier"] == "Now"))

st.subheader("Executive summary")
st.info(rep["executive_summary"] or "—")

tab_rank, tab_detail, tab_channels, tab_export = st.tabs(["Ranking", "Theme evidence", "Channels", "Export"])

with tab_rank:
    df = pd.DataFrame([{
        "Rank": x["rank"], "Tier": x["tier"], "Theme": x["name"], "Score": x["score"],
        "Requests": x["requests"], "Accounts": x["accounts"], "ARR asking": x["arr"],
        "Channels": len(x["channels"]), "Urgent": x["urgent"], "Category": x["category"],
    } for x in ranked])
    chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 100])),
        y=alt.Y("Theme:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320)),
        color=alt.Color("Tier:N", scale=alt.Scale(domain=list(TIER_COLORS), range=list(TIER_COLORS.values()))),
        tooltip=list(df.columns),
    ).properties(height=max(240, 34 * len(df)))
    st.altair_chart(chart, use_container_width=True)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={"ARR asking": st.column_config.NumberColumn(format="$%d"),
                                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")})

with tab_detail:
    for x in ranked:
        with st.expander(f"{x['rank']}. [{x['tier']}] {x['name']} — score {x['score']}"):
            st.caption(f"{x['category']} · {x['requests']} requests · {x['accounts']} accounts · "
                       f"${x['arr']:,.0f} ARR · {x['urgent']} urgent · {', '.join(x['channels'])}")
            st.write(x["description"])
            if x.get("rationale"):
                st.markdown(f"**Why:** {x['rationale']}")
            for q in x["quotes"]:
                st.markdown(f"> {q}")
    if rep["excluded"]:
        st.markdown("**Not on the roadmap (bugs & non-requests)**")
        for x in rep["excluded"]:
            st.markdown(f"- {x['name']} ({x['category']}, {x['requests']} items)")

with tab_channels:
    ch = pd.DataFrame(sorted(s["by_channel"].items(), key=lambda kv: -kv[1]), columns=["Channel", "Items"])
    st.altair_chart(alt.Chart(ch).mark_bar().encode(x="Items:Q", y=alt.Y("Channel:N", sort="-x", title=None)),
                    use_container_width=True)

with tab_export:
    st.download_button("Roadmap (Markdown)", report.to_markdown(rep), "roadmap.md", "text/markdown")
    st.download_button("Roadmap (HTML dashboard)", report.to_html(rep), "roadmap.html", "text/html")
    st.download_button("Roadmap (JSON)", json.dumps(rep, indent=2), "roadmap.json", "application/json")
