"""Render the roadmap as Markdown, JSON and a self-contained HTML dashboard."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .store import ROADMAP_CATEGORIES, FeedbackStore


def build(store: FeedbackStore, result: dict, weights: dict | None = None) -> dict:
    ranked = store.prioritize(weights)
    other = [store.cluster_stats(c) for c in store.clusters.values()
             if c.category not in ROADMAP_CATEGORIES and c.feedback_ids]
    for s in ranked:
        s["rationale"] = result.get("rationales", {}).get(s["id"], "")
    return {"summary": store.summary(), "executive_summary": result.get("executive_summary", ""),
            "roadmap": ranked, "excluded": other}


def write_all(report: dict, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "roadmap.json", out / "roadmap.md", out / "roadmap.html"]
    paths[0].write_text(json.dumps(report, indent=2))
    paths[1].write_text(to_markdown(report))
    paths[2].write_text(to_html(report))
    return paths


def money(x: float) -> str:
    return f"${x / 1000:,.0f}k" if x >= 1000 else f"${x:,.0f}"


def to_markdown(r: dict) -> str:
    s = r["summary"]
    lines = [
        "# Product Roadmap from Customer Feedback", "",
        f"_{s['total_feedback']} feedback items · {len(s['by_channel'])} channels · "
        f"{s['clusters']} clusters · generated {s['generated']}_", "",
        "## Executive summary", "", r["executive_summary"], "",
        "## Prioritized roadmap", "",
        "| # | Tier | Theme | Score | Requests | Accounts | ARR asking | Channels | Urgent |",
        "|---|------|-------|------:|---------:|---------:|-----------:|---------:|-------:|",
    ]
    for x in r["roadmap"]:
        lines.append(f"| {x['rank']} | {x['tier']} | {x['name']} | {x['score']} | {x['requests']} | "
                     f"{x['accounts']} | {money(x['arr'])} | {len(x['channels'])} | {x['urgent']} |")
    lines += ["", "## Theme details", ""]
    for x in r["roadmap"]:
        lines += [f"### {x['rank']}. {x['name']} — {x['tier']} (score {x['score']})", "",
                  f"*{x['category']}* · {x['description']}", ""]
        if x.get("rationale"):
            lines += [f"**Why:** {x['rationale']}", ""]
        lines += [f"> {q}" for q in x["quotes"]] + [""]
    if r["excluded"]:
        lines += ["## Not on the roadmap (bugs & non-requests)", ""]
        lines += [f"- **{x['name']}** ({x['category']}): {x['requests']} items" for x in r["excluded"]]
    return "\n".join(lines) + "\n"


def to_html(r: dict) -> str:
    s = r["summary"]
    top = max((x["score"] for x in r["roadmap"]), default=1) or 1
    rows = []
    for x in r["roadmap"]:
        quotes = "".join(f"<li>{escape(q)}</li>" for q in x["quotes"])
        why = f"<p class='why'>{escape(x['rationale'])}</p>" if x.get("rationale") else ""
        rows.append(f"""
<details class="item">
  <summary>
    <span class="rank">{x['rank']}</span>
    <span class="tier t-{x['tier'].lower()}">{x['tier']}</span>
    <span class="name">{escape(x['name'])}</span>
    <span class="bar"><span style="width:{100 * x['score'] / top:.0f}%"></span></span>
    <span class="score">{x['score']}</span>
  </summary>
  <div class="body">
    <p class="meta">{x['requests']} requests · {x['accounts']} accounts · {money(x['arr'])} ARR asking ·
      {x['urgent']} urgent · {escape(', '.join(x['channels']))}</p>
    {why}
    <ul class="quotes">{quotes}</ul>
  </div>
</details>""")
    excluded = "".join(f"<li>{escape(x['name'])} <span class='muted'>({x['category']}, {x['requests']})</span></li>"
                       for x in r["excluded"])
    channels = " · ".join(f"{escape(k)} {v}" for k, v in sorted(s["by_channel"].items(), key=lambda kv: -kv[1]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feedback Roadmap</title>
<style>
:root {{ --bg:#fafaf9; --card:#fff; --ink:#1c1917; --muted:#78716c; --line:#e7e5e4; --accent:#2563eb;
  --now:#dc2626; --next:#d97706; --later:#64748b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0c0a09; --card:#1c1917; --ink:#f5f5f4; --muted:#a8a29e;
  --line:#292524; --accent:#60a5fa; --now:#f87171; --next:#fbbf24; --later:#94a3b8; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,-apple-system,sans-serif }}
main {{ max-width:900px; margin:0 auto; padding:32px 16px }}
h1 {{ margin:0 0 4px; font-size:26px }} h2 {{ font-size:17px; margin:32px 0 12px }}
.muted, .meta {{ color:var(--muted) }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin:20px 0 }}
.kpi, .summary, .item {{ background:var(--card); border:1px solid var(--line); border-radius:10px }}
.kpi {{ padding:14px }} .kpi b {{ display:block; font-size:24px }}
.summary {{ padding:16px 18px }}
.item {{ margin-bottom:8px }}
.item summary {{ display:grid; grid-template-columns:28px 56px 1fr 140px 44px; gap:10px; align-items:center;
  padding:12px 14px; cursor:pointer; list-style:none }}
.rank {{ color:var(--muted); font-variant-numeric:tabular-nums }}
.tier {{ font-size:12px; font-weight:600; text-align:center; border-radius:99px; padding:2px 0; color:#fff }}
.t-now {{ background:var(--now) }} .t-next {{ background:var(--next) }} .t-later {{ background:var(--later) }}
.name {{ font-weight:600 }}
.bar {{ height:8px; background:var(--line); border-radius:99px; overflow:hidden }}
.bar span {{ display:block; height:100%; background:var(--accent) }}
.score {{ text-align:right; font-variant-numeric:tabular-nums; font-weight:600 }}
.body {{ padding:0 14px 14px 52px }} .why {{ margin:6px 0 }}
.quotes {{ margin:6px 0 0; padding-left:18px; color:var(--muted); font-size:14px }}
@media (max-width:600px) {{ .item summary {{ grid-template-columns:24px 52px 1fr 40px }} .bar {{ display:none }}
  .body {{ padding-left:14px }} }}
</style></head><body><main>
<h1>Product roadmap from customer feedback</h1>
<div class="muted">Generated {s['generated']} · {channels}</div>
<div class="kpis">
  <div class="kpi"><b>{s['total_feedback']}</b><span class="muted">feedback items</span></div>
  <div class="kpi"><b>{len(s['by_channel'])}</b><span class="muted">channels</span></div>
  <div class="kpi"><b>{s['clusters']}</b><span class="muted">clusters</span></div>
  <div class="kpi"><b>{sum(1 for x in r['roadmap'] if x['tier'] == 'Now')}</b><span class="muted">build-now themes</span></div>
</div>
<h2>Executive summary</h2>
<div class="summary">{escape(r['executive_summary'])}</div>
<h2>Prioritized themes <span class="muted">(click to see evidence)</span></h2>
{''.join(rows)}
<h2>Not on the roadmap</h2>
<ul>{excluded}</ul>
<p class="muted">Score = 30% request volume + 25% distinct accounts + 25% ARR + 10% channel breadth + 10% urgency.
Now ≥ 60 · Next ≥ 35 · Later &lt; 35.</p>
</main></body></html>
"""
