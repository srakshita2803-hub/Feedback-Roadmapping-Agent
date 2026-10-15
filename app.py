"""Signal: Streamlit front-end for the Customer Feedback Clustering & Roadmap Agent.

Run:  streamlit run app.py
"""

import json
import os
import tempfile
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from roadmap_agent import report
from roadmap_agent.agent import PROVIDERS
from roadmap_agent.offline import cluster_offline
from roadmap_agent.store import EFFORT_POINTS, WEIGHTS, FeedbackStore

SAMPLE = Path(__file__).parent / "data" / "feedback.csv"
TIERS = {"Now": "#4F46E5", "Next": "#0EA5E9", "Later": "#94A3B8"}
EFFORT_LABEL = {"S": "Small · days", "M": "Medium · weeks", "L": "Large · quarter+"}
WEIGHT_LABELS = {"requests": "Request volume", "accounts": "Distinct accounts", "arr": "Revenue (ARR)",
                 "channels": "Channel breadth", "urgency": "Urgency"}
SUGGESTIONS = ["Why is the #1 theme ranked first?", "What do enterprise customers want most?",
               "Which themes are quick wins?", "Which requests signal churn risk?"]

st.set_page_config(page_title="Signal · Feedback → Roadmap", page_icon="🧭", layout="wide",
                   initial_sidebar_state="expanded")

# ---------------------------------------------------------------- styling
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], button, input, textarea {
  font-family: 'Inter', system-ui, -apple-system, sans-serif !important; }
[data-testid="stAppViewContainer"] .block-container { padding-top: 2rem; max-width: 1280px; }
h1, h2, h3 { letter-spacing: -0.02em; }
.hero { padding: 28px 32px; border-radius: 16px; margin-bottom: 20px;
  background: linear-gradient(135deg, rgba(79,70,229,.12), rgba(14,165,233,.08));
  border: 1px solid rgba(99,102,241,.25); }
.hero h1 { font-size: 2.1rem; font-weight: 800; margin: 0 0 6px; padding: 0; }
.hero p { margin: 0; opacity: .75; font-size: 1.02rem; max-width: 760px; }
.pills { margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap; }
.pill { font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 999px;
  background: rgba(127,127,127,.12); }
.pill.live { background: rgba(79,70,229,.15); color: #6366F1; }
.card { padding: 16px 18px; border-radius: 12px; border: 1px solid rgba(127,127,127,.2);
  background: rgba(127,127,127,.04); height: 100%; }
.kpi .label { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; opacity: .6; }
.kpi .value { font-size: 1.9rem; font-weight: 800; margin-top: 2px; }
.kpi .sub { font-size: 12.5px; opacity: .6; }
.step .num { width: 28px; height: 28px; border-radius: 8px; display: inline-flex; align-items: center;
  justify-content: center; background: rgba(79,70,229,.15); color: #6366F1; font-weight: 700; margin-bottom: 8px; }
.step h4 { margin: 0 0 4px; font-size: 1rem; } .step p { margin: 0; opacity: .7; font-size: .92rem; }
.theme { padding: 14px 16px; border-radius: 12px; border: 1px solid rgba(127,127,127,.2);
  background: rgba(127,127,127,.04); margin-bottom: 10px; border-left: 4px solid var(--tier); }
.theme .top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.theme .name { font-weight: 700; font-size: .98rem; line-height: 1.3; }
.theme .score { font-weight: 800; color: var(--tier); font-size: 1.1rem; }
.theme .meta { font-size: 12.5px; opacity: .65; margin-top: 6px; }
.chip { display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 6px;
  background: rgba(127,127,127,.13); margin: 6px 4px 0 0; }
.col-head { display: flex; align-items: center; gap: 8px; font-weight: 700; margin-bottom: 10px; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--tier); }
.summary { padding: 18px 20px; border-radius: 12px; border: 1px solid rgba(99,102,241,.3);
  background: rgba(79,70,229,.06); line-height: 1.6; }
.quote { padding: 10px 14px; border-left: 3px solid rgba(127,127,127,.35); margin: 6px 0;
  font-size: .93rem; opacity: .85; }
.quote b { font-weight: 600; opacity: .7; font-size: 12px; }
div[data-testid="stTabs"] button p { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

ss = st.session_state


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def money(x: float) -> str:
    return f"${x / 1e6:,.1f}M" if x >= 1e6 else f"${x / 1000:,.0f}k" if x >= 1000 else f"${x:,.0f}"


def nice(s: str) -> str:
    return s.replace("_", " ").title()


def kpi(col, label: str, value, sub: str = "") -> None:
    col.markdown(f"<div class='card kpi'><div class='label'>{label}</div><div class='value'>{value}</div>"
                 f"<div class='sub'>{sub}</div></div>", unsafe_allow_html=True)


def theme_card(x: dict, show_rank: bool = True) -> None:
    rank = f"#{x['rank']} · " if show_rank else ""
    chips = f"<span class='chip'>{EFFORT_LABEL.get(x['effort'], x['effort'])}</span>"
    if x["urgent"]:
        chips += f"<span class='chip'>⚡ {x['urgent']} urgent</span>"
    html(f"""<div class='theme' style='--tier:{TIERS[x["tier"]]}'>
      <div class='top'><span class='name'>{rank}{escape(x['name'])}</span><span class='score'>{x['score']:.0f}</span></div>
      <div class='meta'>{x['requests']} requests · {x['accounts']} accounts · {money(x['arr'])} ARR · {len(x['channels'])} channels</div>
      {chips}</div>""")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    html("<div style='font-size:1.35rem;font-weight:800;letter-spacing:-.02em'>🧭 Signal</div>"
         "<div style='opacity:.6;font-size:.85rem;margin-bottom:8px'>Feedback → Roadmap agent</div>")

    st.subheader("Data", divider="gray")
    upload = st.file_uploader("Upload feedback (CSV or JSON)", type=["csv", "json"],
                              help="Needs a `text` column. Optional: id, date, channel, customer, plan, arr.")
    st.caption("No file? The sample dataset is used (80 requests from 6 channels).")
    st.download_button("Download CSV template", SAMPLE.read_bytes(), "feedback_template.csv", "text/csv",
                       width="stretch")

    st.subheader("AI provider", divider="gray")
    provider_name = st.segmented_control("AI provider", list(PROVIDERS.values()), default="Claude",
                                         label_visibility="collapsed",
                                         help="Used by the AI agent, Ask the agent, and PRD drafts.")
    provider = "gemini" if provider_name == "Gemini" else "claude"
    if provider == "gemini":
        server_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        key_label, key_hint = "Gemini API key", "AIza…"
        st.caption("Get a free key at [aistudio.google.com](https://aistudio.google.com/app/apikey).")
    else:
        server_key = os.environ.get("ANTHROPIC_API_KEY", "")
        key_label, key_hint = "Anthropic API key", "sk-ant-…"
    user_key = st.text_input(key_label, type="password", key=f"key_{provider}",
                             placeholder="Using the server's key" if server_key else key_hint,
                             help="A key set on the server is never sent to your browser.")
    api_key = user_key or server_key
    ai = PROVIDERS[provider]

    st.subheader("Clustering engine", divider="gray")
    engine = st.segmented_control("Clustering engine", ["AI agent", "Offline baseline"],
                                  default="AI agent", label_visibility="collapsed",
                                  help="The offline baseline uses keyword rules and needs no API key.")
    run = st.button("Build roadmap", type="primary", width="stretch", icon="✨")


def load_store() -> FeedbackStore:
    if upload is None:
        return FeedbackStore.from_file(SAMPLE)
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(upload.name).suffix) as tmp:
        tmp.write(upload.getvalue())
    return FeedbackStore.from_file(tmp.name)


try:
    raw_store = load_store()
except Exception as e:
    st.error(f"Couldn't read that file: {e}")
    st.stop()

# ---------------------------------------------------------------- run the agent
if run:
    if engine == "AI agent" and not api_key:
        st.sidebar.error("Add an API key, or switch to the offline baseline.")
    else:
        try:
            if engine == "AI agent":
                from roadmap_agent.agent import run_agent
                with st.status(f"{ai} is reading and clustering your feedback…", expanded=True) as status:
                    result = run_agent(raw_store, on_event=lambda line: st.caption(line), api_key=api_key,
                                       provider=provider)
                    status.update(label="Roadmap ready", state="complete", expanded=False)
            else:
                result = cluster_offline(raw_store)
            ss.update(store=raw_store, result=result, chat=[], prds={},
                      engine=f"{ai} agent" if engine == "AI agent" else "Offline baseline")
        except Exception as e:
            st.error(f"Run failed: {e}")

# ---------------------------------------------------------------- landing page
if "store" not in ss:
    html("""<div class='hero'><h1>Turn feedback noise into a roadmap.</h1>
      <p>Signal reads every feature request from app reviews, support tickets, sales calls, surveys and social,
      clusters them by the underlying need, and ranks what customers want most, backed by the evidence.</p>
      <div class='pills'><span class='pill'>Claude or Gemini clustering</span><span class='pill'>Transparent scoring</span>
      <span class='pill'>Now / Next / Later roadmap</span><span class='pill'>PRD drafts</span></div></div>""")
    steps = [("Ingest", "Upload a CSV/JSON export from any channel, or use the sample data."),
             ("Cluster", "The agent groups requests by user need and flags churn and deal risks."),
             ("Prioritize", "Code scores each theme on volume, accounts, revenue, breadth and urgency."),
             ("Act", "Explore the board, the matrix and trends; ask follow-ups; draft PRDs.")]
    for col, (i, (title, text)) in zip(st.columns(4), enumerate(steps, 1)):
        col.markdown(f"<div class='card step'><div class='num'>{i}</div><h4>{title}</h4><p>{text}</p></div>",
                     unsafe_allow_html=True)
    st.write("")
    st.info("Choose an engine in the sidebar and click **Build roadmap**.", icon="👈")
    st.markdown(f"##### Preview · {len(raw_store.feedback)} feedback items")
    st.dataframe(pd.DataFrame([vars(f) for f in raw_store.feedback.values()]), width="stretch",
                 hide_index=True, height=320,
                 column_config={"arr": st.column_config.NumberColumn("ARR", format="$%d"),
                                "text": st.column_config.TextColumn("Feedback", width="large")})
    st.stop()

# ---------------------------------------------------------------- weights & filters (after a run)
store: FeedbackStore = ss.store
all_fb = list(store.feedback.values())


def reset_weights() -> None:
    for k, v in WEIGHTS.items():
        ss[f"w_{k}"] = int(v * 100)


with st.sidebar:
    st.subheader("Scoring weights", divider="gray")
    st.caption("Drag to re-rank the roadmap live.")
    weights = {k: st.slider(WEIGHT_LABELS[k], 0, 100, int(v * 100), 5, key=f"w_{k}") for k, v in WEIGHTS.items()}
    st.button("Reset weights", on_click=reset_weights, width="stretch")

    st.subheader("Filters", divider="gray")
    channels = sorted({f.channel for f in all_fb})
    plans = sorted({f.plan for f in all_fb if f.plan})
    f_channels = st.multiselect("Channels", channels, default=channels, format_func=nice)
    f_plans = st.multiselect("Plans", plans, default=plans, format_func=nice)
    dates = sorted(f.date for f in all_fb if f.date)
    f_dates = None
    if dates:
        d0, d1 = pd.to_datetime(dates[0]).date(), pd.to_datetime(dates[-1]).date()
        if d0 < d1:
            f_dates = st.slider("Date range", d0, d1, (d0, d1))

keep = {f.id for f in all_fb
        if f.channel in f_channels and (not plans or f.plan in f_plans)
        and (not f_dates or not f.date or f_dates[0] <= pd.to_datetime(f.date).date() <= f_dates[1])}
view = store.subset(keep)
rep = report.build(view, ss.result, weights)
ranked, summary = rep["roadmap"], rep["summary"]
filtered = len(keep) < len(all_fb)

# ---------------------------------------------------------------- header
engine_pill = ss.engine
html(f"""<div class='hero'><h1>Product roadmap</h1>
  <p>{summary['total_feedback']} feedback items from {len(summary['by_channel'])} channels, grouped into
  {len(ranked)} roadmap themes.</p>
  <div class='pills'><span class='pill live'>● {engine_pill}</span><span class='pill'>Generated {summary['generated']}</span>
  {"<span class='pill'>Filtered view</span>" if filtered else ""}
  {"<span class='pill'>Custom weights</span>" if any(weights[k] != int(WEIGHTS[k] * 100) for k in WEIGHTS) else ""}</div></div>""")

if not ranked:
    st.warning("No roadmap themes match the current filters.")
    st.stop()

tabs = st.tabs(["Overview", "Roadmap board", "Priority matrix", "Insights", "Evidence", "Ask the agent", "Export"])

# ---------------------------------------------------------------- overview
with tabs[0]:
    now = [x for x in ranked if x["tier"] == "Now"]
    urgent = sum(x["urgent"] for x in ranked)
    asking_arr = sum({f.customer: f.arr for f in view.feedback.values()
                      if f.id in view.assignment and view.clusters[view.assignment[f.id]].category
                      in report.ROADMAP_CATEGORIES}.values())
    c = st.columns(5)
    kpi(c[0], "Feedback analysed", summary["total_feedback"], f"{len(summary['by_channel'])} channels")
    kpi(c[1], "Roadmap themes", len(ranked), f"{len(rep['excluded'])} non-roadmap groups")
    kpi(c[2], "Build now", len(now), "score ≥ 60")
    kpi(c[3], "Urgent signals", urgent, "churn, blocked deals, compliance")
    kpi(c[4], "ARR asking", money(asking_arr), "distinct accounts")
    st.write("")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("##### Executive summary")
        html(f"<div class='summary'>{escape(rep['executive_summary'] or '—')}</div>")
        st.write("")
        st.markdown("##### Ranking")
        df = pd.DataFrame([{"Theme": x["name"], "Score": x["score"], "Tier": x["tier"], "Requests": x["requests"],
                            "Accounts": x["accounts"], "ARR": x["arr"]} for x in ranked])
        bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4, height=18).encode(
            x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 100]), title=None),
            y=alt.Y("Theme:N", sort=list(df["Theme"]), title=None, axis=alt.Axis(labelLimit=300)),
            color=alt.Color("Tier:N", scale=alt.Scale(domain=list(TIERS), range=list(TIERS.values())),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Theme", "Score", "Tier", "Requests", "Accounts", alt.Tooltip("ARR:Q", format="$,.0f")])
        labels = bars.mark_text(align="left", dx=4, fontWeight=600).encode(text=alt.Text("Score:Q", format=".0f"),
                                                                             color=alt.value("#94A3B8"))
        st.altair_chart((bars + labels).properties(height=30 * len(df) + 30), width="stretch")
    with right:
        st.markdown("##### Top priorities")
        for x in ranked[:4]:
            theme_card(x)
            if x.get("rationale"):
                st.caption(x["rationale"])

# ---------------------------------------------------------------- roadmap board
with tabs[1]:
    st.caption("Themes by tier. Tiers come from the score: Now ≥ 60, Next ≥ 35, Later < 35.")
    for col, tier in zip(st.columns(3, gap="medium"), TIERS):
        items = [x for x in ranked if x["tier"] == tier]
        with col:
            html(f"<div class='col-head' style='--tier:{TIERS[tier]}'><span class='dot'></span>{tier}"
                 f"<span class='pill'>{len(items)}</span></div>")
            for x in items:
                theme_card(x)
            if not items:
                st.caption("Nothing here with the current weights and filters.")

# ---------------------------------------------------------------- priority matrix
with tabs[2]:
    st.caption("Impact (score) against estimated build effort. Bubble size = ARR asking.")
    mdf = pd.DataFrame([{"Theme": x["name"], "Score": x["score"], "Tier": x["tier"], "Effort": x["effort"],
                         "x": EFFORT_POINTS.get(x["effort"], 2) + ((x["rank"] % 5) - 2) * 0.09,
                         "ARR": max(x["arr"], 1), "Requests": x["requests"]} for x in ranked])
    quad = pd.DataFrame([{"x": 1.0, "y": 97, "t": "QUICK WINS"}, {"x": 3.0, "y": 97, "t": "BIG BETS"},
                         {"x": 1.0, "y": 3, "t": "FILL-INS"}, {"x": 3.0, "y": 3, "t": "MONEY PITS"}])
    base_x = alt.X("x:Q", scale=alt.Scale(domain=[0.5, 4.4]), title="Estimated effort",
                   axis=alt.Axis(values=[1, 2, 3], grid=False, labelExpr="datum.value == 1 ? 'Small' : datum.value == 2 ? 'Medium' : 'Large'"))
    points = alt.Chart(mdf).mark_circle(opacity=.85, stroke="white", strokeWidth=1).encode(
        x=base_x, y=alt.Y("Score:Q", scale=alt.Scale(domain=[0, 100]), title="Impact score", axis=alt.Axis(grid=False)),
        size=alt.Size("ARR:Q", scale=alt.Scale(range=[120, 1800]), legend=None),
        color=alt.Color("Tier:N", scale=alt.Scale(domain=list(TIERS), range=list(TIERS.values())),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Theme", "Score", "Tier", "Effort", "Requests", alt.Tooltip("ARR:Q", format="$,.0f")])
    text = alt.Chart(mdf).mark_text(align="left", dx=12, fontSize=11).encode(x=base_x, y="Score:Q", text="Theme:N")
    rules = (alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(strokeDash=[4, 4], opacity=.4).encode(y="y:Q") +
             alt.Chart(pd.DataFrame({"x": [2.0]})).mark_rule(strokeDash=[4, 4], opacity=.4).encode(x="x:Q"))
    qlabels = alt.Chart(quad).mark_text(fontWeight=700, fontSize=11, opacity=.4).encode(x="x:Q", y="y:Q", text="t:N")
    st.altair_chart((rules + qlabels + points + text).properties(height=520), width="stretch")

    wins = [x for x in ranked if x["effort"] == "S" and x["score"] >= 35]
    bets = [x for x in ranked if x["effort"] == "L" and x["score"] >= 50]
    a, b = st.columns(2, gap="large")
    with a:
        st.markdown("##### ⚡ Quick wins")
        st.caption("High enough demand, small effort: ship these early.")
        for x in wins or []:
            theme_card(x)
        if not wins:
            st.caption("None right now.")
    with b:
        st.markdown("##### 🎯 Big bets")
        st.caption("High demand, large effort: plan and staff deliberately.")
        for x in bets:
            theme_card(x)
        if not bets:
            st.caption("None right now.")

# ---------------------------------------------------------------- insights
with tabs[3]:
    rows = [{"id": f.id, "date": f.date, "channel": nice(f.channel), "customer": f.customer, "plan": nice(f.plan or "unknown"),
             "arr": f.arr, "theme": view.clusters[view.assignment[f.id]].name}
            for f in view.feedback.values() if f.id in view.assignment]
    idf = pd.DataFrame(rows)
    names = [x["name"] for x in ranked]
    idf = idf[idf["theme"].isin(names)]
    top = names[:6]

    a, b = st.columns(2, gap="large")
    with a:
        st.markdown("##### Momentum")
        st.caption("Cumulative requests over time for the top 6 themes. Steeper = growing demand.")
        tdf = idf[idf["theme"].isin(top) & (idf["date"] != "")].copy()
        if not tdf.empty:
            tdf["week"] = pd.to_datetime(tdf["date"]).dt.to_period("W").dt.start_time
            tdf = (tdf.groupby(["week", "theme"]).size().unstack(fill_value=0).cumsum()
                   .stack().reset_index(name="requests"))
            st.altair_chart(alt.Chart(tdf).mark_line(strokeWidth=2.5, interpolate="monotone").encode(
                x=alt.X("week:T", title=None), y=alt.Y("requests:Q", title=None),
                color=alt.Color("theme:N", sort=top, legend=alt.Legend(orient="bottom", title=None, columns=2,
                                                                       labelLimit=260)),
                tooltip=["week:T", "theme", "requests"]).properties(height=330), width="stretch")
        else:
            st.caption("No dates in this dataset.")
    with b:
        st.markdown("##### Who is asking")
        st.caption("Mentions per theme by customer plan.")
        st.altair_chart(alt.Chart(idf).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("count():Q", title=None, stack="normalize", axis=alt.Axis(format="%")),
            y=alt.Y("theme:N", sort=names, title=None, axis=alt.Axis(labelLimit=240)),
            color=alt.Color("plan:N", legend=alt.Legend(orient="bottom", title=None)),
            tooltip=["theme", "plan", "count()"]).properties(height=30 * len(names) + 20), width="stretch")

    st.markdown("##### Channel × theme")
    st.caption("Where each request comes from. Themes seen across many channels are broad, real needs.")
    heat = idf.groupby(["theme", "channel"]).size().reset_index(name="requests")
    st.altair_chart(alt.Chart(heat).mark_rect(cornerRadius=4).encode(
        x=alt.X("channel:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("theme:N", sort=names, title=None, axis=alt.Axis(labelLimit=300)),
        color=alt.Color("requests:Q", scale=alt.Scale(scheme="purples"), legend=None),
        tooltip=["theme", "channel", "requests"]).properties(height=30 * len(names) + 20)
        + alt.Chart(heat).mark_text(fontWeight=600).encode(x="channel:N", y=alt.Y("theme:N", sort=names),
                                                            text="requests:Q"), width="stretch")

    st.markdown("##### Top accounts asking")
    acc = (idf.groupby("customer").agg(ARR=("arr", "max"), Requests=("id", "count"), Plan=("plan", "first"),
                                       Themes=("theme", lambda s: ", ".join(sorted(set(s)))))
           .sort_values(["ARR", "Requests"], ascending=False).reset_index().rename(columns={"customer": "Account"}))
    st.dataframe(acc.head(15), width="stretch", hide_index=True,
                 column_config={"ARR": st.column_config.NumberColumn(format="$%d"),
                                "Themes": st.column_config.TextColumn(width="large")})

# ---------------------------------------------------------------- evidence + PRD
with tabs[4]:
    pick = st.selectbox("Theme", ranked, format_func=lambda x: f"#{x['rank']}  {x['name']}  ·  {x['tier']}  ·  {x['score']:.0f}")
    a, b = st.columns([3, 2], gap="large")
    with a:
        st.markdown(f"### {pick['name']}")
        st.caption(f"{nice(pick['category'])} · {pick['description']}")
        c = st.columns(4)
        kpi(c[0], "Score", f"{pick['score']:.0f}", pick["tier"])
        kpi(c[1], "Requests", pick["requests"], f"{pick['urgent']} urgent")
        kpi(c[2], "Accounts", pick["accounts"], money(pick["arr"]) + " ARR")
        kpi(c[3], "Effort", pick["effort"], EFFORT_LABEL.get(pick["effort"], ""))
        if pick.get("rationale"):
            st.write("")
            html(f"<div class='summary'><b>Why it ranks here:</b> {escape(pick['rationale'])}</div>")
        st.markdown("##### All feedback in this theme")
        cl = view.clusters[pick["id"]]
        st.dataframe(pd.DataFrame([{"⚡": "⚡" if f.id in cl.urgent_ids else "", "ID": f.id, "Date": f.date,
                                    "Channel": nice(f.channel), "Customer": f.customer, "Feedback": f.text}
                                   for f in (view.feedback[i] for i in cl.feedback_ids)]),
                     width="stretch", hide_index=True,
                     column_config={"Feedback": st.column_config.TextColumn(width="large")})
    with b:
        st.markdown("##### Draft a PRD")
        st.caption(f"{ai} writes a one-page PRD from this theme's customer evidence.")
        prds = ss.setdefault("prds", {})
        if st.button(f"✍️ Draft PRD with {ai}", width="stretch", disabled=not api_key,
                     help=None if api_key else "Add an API key in the sidebar"):
            from roadmap_agent.assistant import draft_prd
            try:
                with st.container(border=True):
                    prds[pick["id"]] = st.write_stream(draft_prd(provider, api_key, view, pick))
            except Exception as e:
                st.error(f"PRD failed: {e}")
        elif pick["id"] in prds:
            with st.container(border=True):
                st.markdown(prds[pick["id"]])
        if pick["id"] in prds:
            st.download_button("Download PRD (.md)", prds[pick["id"]],
                               f"PRD-{pick['name'][:40].replace(' ', '-').replace('/', '-')}.md", width="stretch")
        if not api_key:
            st.info(f"Add a {ai} API key in the sidebar to draft PRDs.", icon="🔑")

# ---------------------------------------------------------------- ask the agent
with tabs[5]:
    st.caption(f"Ask follow-up questions. {ai} answers from this analysis only, citing feedback ids.")
    if not api_key:
        st.info(f"Add a {ai} API key in the sidebar to chat with the agent.", icon="🔑")
    chat = ss.setdefault("chat", [])
    cols = st.columns(len(SUGGESTIONS))
    clicked = next((q for col, q in zip(cols, SUGGESTIONS)
                    if col.button(q, width="stretch", disabled=not api_key)), None)
    box = st.container(height=480, border=True)
    with box:
        if not chat:
            st.caption("No questions yet. Try a suggestion above.")
        for m in chat:
            with st.chat_message(m["role"], avatar="🧭" if m["role"] == "assistant" else None):
                st.markdown(m["content"])
    question = st.chat_input("Ask about the roadmap…", disabled=not api_key) or clicked
    if question:
        chat.append({"role": "user", "content": question})
        from roadmap_agent.assistant import ask
        with box:
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant", avatar="🧭"):
                try:
                    answer = st.write_stream(ask(provider, api_key, view, ranked, chat))
                    chat.append({"role": "assistant", "content": answer})
                except Exception as e:
                    chat.pop()
                    st.error(f"Couldn't get an answer: {e}")
    if chat and st.button("Clear conversation"):
        ss.chat = []
        st.rerun()

# ---------------------------------------------------------------- export
with tabs[6]:
    st.caption("Exports reflect the current weights and filters.")
    clustered = pd.DataFrame([{"id": f.id, "date": f.date, "channel": f.channel, "customer": f.customer,
                               "plan": f.plan, "arr": f.arr, "text": f.text,
                               "cluster": view.clusters[view.assignment[f.id]].name if f.id in view.assignment else "",
                               "category": view.clusters[view.assignment[f.id]].category if f.id in view.assignment else "",
                               "urgent": f.id in view.clusters[view.assignment[f.id]].urgent_ids if f.id in view.assignment else False}
                              for f in view.feedback.values()])
    c = st.columns(4)
    c[0].download_button("📄 Roadmap (Markdown)", report.to_markdown(rep), "roadmap.md", "text/markdown", width="stretch")
    c[1].download_button("🌐 Dashboard (HTML)", report.to_html(rep), "roadmap.html", "text/html", width="stretch")
    c[2].download_button("🧾 Roadmap (JSON)", json.dumps(rep, indent=2), "roadmap.json", "application/json", width="stretch")
    c[3].download_button("📊 Clustered feedback (CSV)", clustered.to_csv(index=False), "clustered_feedback.csv",
                         "text/csv", width="stretch")
    st.markdown("##### Preview")
    st.markdown(report.to_markdown(rep))
