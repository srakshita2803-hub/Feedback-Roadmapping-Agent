# Customer Feedback Clustering & Product Roadmap Agent

**Problem:** Product managers get hundreds of feature requests from app stores, support tickets, sales calls, NPS surveys, social media and forums, which makes patterns hard to see.

**Agent:** Claude (`claude-opus-5-5`) reads every item, **categorizes** it, and **clusters** it by the underlying need ("night mode", "dark theme" and "the white background is blinding" become one cluster). The code then **counts and scores** each cluster to rank the most-demanded updates as a Now / Next / Later roadmap.

## Features
- **Overview:** KPIs, the executive summary, a ranked chart, and the top-priority cards.
- **Roadmap board:** Now / Next / Later columns of theme cards.
- **Priority matrix:** impact vs. estimated effort, highlighting quick wins and big bets.
- **Insights:** demand momentum over time, who is asking (by plan), a channel × theme heatmap, and the top accounts.
- **Evidence:** every piece of feedback behind a theme, plus a one-click **PRD draft** from Claude.
- **Ask the agent:** chat with Claude about the results, with answers that cite feedback IDs.
- **Interactive scoring:** sliders for the score weights and filters by channel, plan and date. The roadmap re-ranks live.
- **Export:** Markdown, an HTML dashboard, JSON, and the clustered feedback as CSV.
- Light and dark themes that follow your system setting.

## Live demo
- **Streamlit app:** _link added after deployment_
- **Colab notebook:** _link added after deployment_

## Deploy on Streamlit Community Cloud (permanent link)
1. Put these files in a public GitHub repository.
2. At [share.streamlit.io](https://share.streamlit.io), click **Create app**, choose the repo and branch `main`, and set the main file to `app.py`.
3. Optional: under **App settings → Secrets**, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   Without it, visitors can paste their own key or use the offline baseline.

> ⚠️ With a server key set, anyone with the link can run the agent on your bill. Set a spending limit in the Anthropic Console. The key is used only on the server and is never sent to the browser.

## Run it on Google Colab
1. Open `Feedback_Roadmap_Agent.ipynb` in [Colab](https://colab.research.google.com): **File → Open notebook → GitHub** and paste the repo URL, or upload the file.
2. In the **🔑 Secrets** panel, add `ANTHROPIC_API_KEY` and turn on notebook access. Without a key, the offline baseline still works.
3. Choose **Runtime → Run all**. The last cell starts Streamlit and prints a public `https://….trycloudflare.com` link to the app. That link works only while the Colab runtime is running.

The notebook is self-contained: it writes every project file with `%%writefile`, so you don't need to upload anything else.

## Run it locally
```bash
pip install -r requirements.txt
```
```bash
streamlit run app.py
```
```bash
python main.py data/feedback.csv --offline
```

## How it works
| Step | Who does it | Tool |
|---|---|---|
| Read the feedback in batches | Claude | `get_overview`, `read_feedback` |
| Name clusters by user need and categorize them (feature, integration, security, performance, UX, bug, not a request) | Claude | `create_cluster` |
| Assign each item to a cluster and flag urgent ones (churn, blocked deal, compliance) | Claude | `assign_feedback` |
| Merge duplicates and make sure nothing is left over | Claude | `merge_clusters`, `get_unassigned` |
| Count requests, distinct accounts and ARR, then score | **Code** (deterministic) | `get_priorities` |
| Write the executive summary and a rationale for each theme | Claude | `submit_roadmap` |

**Score (0–100)** = 30% request volume + 25% distinct accounts + 25% ARR of asking accounts (counted once per account) + 10% channel breadth + 10% urgency share. Tiers: Now ≥ 60, Next ≥ 35, Later < 35. You can change the weights in `roadmap_agent/store.py`.

Bugs and non-requests (praise, billing questions) are kept out of the roadmap but listed separately.

## Files
- `app.py`: the Streamlit front-end ("Signal")
- `.streamlit/config.toml`: the app's light and dark theme
- `roadmap_agent/assistant.py`: Ask-the-agent chat and PRD drafting
- `roadmap_agent/agent.py`: the system prompt, tools, and agent loop (Anthropic SDK tool runner)
- `roadmap_agent/store.py`: the feedback store, clustering operations, and scoring
- `roadmap_agent/offline.py`: a keyword-rule baseline that needs no API key
- `roadmap_agent/report.py`: report rendering
- `main.py`: the command-line interface
- `data/feedback.csv`: 80 sample requests from 6 channels. Your own CSV needs a `text` column; `id, date, channel, customer, plan, arr` are optional.
- `Feedback_Roadmap_Agent.ipynb`: the Colab notebook
