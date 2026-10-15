"""The Claude-powered clustering & roadmap agent.

Claude drives the loop (via the SDK tool runner): it reads feedback in batches,
creates/merges clusters, assigns every item, asks for the deterministic
priority ranking, and finally submits a roadmap with its rationale.
"""

from __future__ import annotations

import json

import anthropic
from anthropic import beta_tool

from .store import CATEGORIES, FeedbackStore

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = f"""You are a senior product-operations analyst. Product managers are flooded with
feature requests from many channels (app store reviews, support tickets, sales calls, NPS surveys,
social media, community forums). Your job is to turn that raw feedback into a clear, evidence-backed
product roadmap.

Work through it with your tools:
1. Call get_overview, then read_feedback in batches until you have seen every item.
2. Group items by the underlying user NEED, not surface wording ("night mode", "dark theme" and
   "white background is blinding" are one cluster; "SSO" and "SCIM provisioning" are related but
   distinct asks). Prefer specific, actionable clusters over vague buckets like "general requests".
   Categories: {", ".join(CATEGORIES)}. Use `bug` for defects and crashes, and `not_a_request` for
   praise, billing questions and similar items, so they stay out of the roadmap.
3. When assigning, pass urgent_ids for items that signal churn, a blocked deal or renewal, a
   compliance requirement, or a customer abandoning the product.
4. Merge clusters that turn out to be duplicates. Keep going until get_unassigned returns nothing.
5. Call get_priorities. The scores are computed in code from request volume, distinct accounts,
   revenue, channel breadth and urgency. Do not invent numbers; cite the ones the tool returns.
6. Call submit_roadmap once with an executive summary and one rationale per roadmap cluster.
   Note where the score alone is misleading (e.g. one huge account, or a cheap quick win)."""


def build_tools(store: FeedbackStore, result: dict):
    @beta_tool
    def get_overview() -> str:
        """Get totals: number of feedback items, how many are clustered, and counts per channel."""
        return json.dumps(store.summary())

    @beta_tool
    def read_feedback(offset: int = 0, limit: int = 40) -> str:
        """Read raw feedback items in a batch.

        Args:
            offset: Index of the first item to return.
            limit: Maximum number of items to return (max 60).
        """
        items = list(store.feedback.values())[offset: offset + min(limit, 60)]
        rows = [{"id": f.id, "channel": f.channel, "customer": f.customer, "plan": f.plan,
                 "text": f.text} for f in items]
        return json.dumps({"offset": offset, "returned": len(rows),
                           "total": len(store.feedback), "items": rows})

    @beta_tool
    def list_clusters() -> str:
        """List current clusters with their size and a few member ids."""
        return json.dumps([{"id": c.id, "name": c.name, "category": c.category,
                            "description": c.description, "size": len(c.feedback_ids),
                            "sample_ids": c.feedback_ids[:5]} for c in store.clusters.values()])

    @beta_tool
    def create_cluster(name: str, description: str, category: str) -> str:
        """Create a new cluster representing one underlying user need.

        Args:
            name: Short, specific name, e.g. "SAML SSO (Okta / Azure AD)".
            description: One sentence describing the need.
            category: One of feature_request, integration, security_compliance, performance,
                ux_improvement, bug, not_a_request.
        """
        try:
            c = store.create_cluster(name, description, category)
        except ValueError as e:
            return f"ERROR: {e}"
        return json.dumps({"id": c.id, "name": c.name})

    @beta_tool
    def assign_feedback(cluster_id: str, feedback_ids: list[str], urgent_ids: list[str] | None = None) -> str:
        """Assign feedback items to a cluster. Re-assigning an item moves it.

        Args:
            cluster_id: Target cluster id, e.g. "C03".
            feedback_ids: Feedback ids to put in this cluster.
            urgent_ids: Subset of feedback_ids signalling churn risk, a blocked deal, or a compliance need.
        """
        try:
            return json.dumps(store.assign(cluster_id, feedback_ids, urgent_ids))
        except ValueError as e:
            return f"ERROR: {e}"

    @beta_tool
    def merge_clusters(source_id: str, target_id: str) -> str:
        """Merge a duplicate cluster into another one; the source cluster is deleted.

        Args:
            source_id: Cluster to fold in and remove.
            target_id: Cluster that keeps all items.
        """
        try:
            return json.dumps(store.merge(source_id, target_id))
        except ValueError as e:
            return f"ERROR: {e}"

    @beta_tool
    def get_unassigned() -> str:
        """Return the full text of any feedback items not yet assigned to a cluster."""
        ids = store.unassigned()
        return json.dumps({"count": len(ids), "items": [
            {"id": i, "text": store.feedback[i].text} for i in ids]})

    @beta_tool
    def get_priorities() -> str:
        """Compute the ranked roadmap: score (0-100), tier (Now/Next/Later), requests, accounts, ARR, channels."""
        ranked = store.prioritize()
        return json.dumps([{k: s[k] for k in ("rank", "id", "name", "tier", "score", "requests",
                                                "accounts", "arr", "channels", "urgent")} for s in ranked])

    @beta_tool
    def submit_roadmap(executive_summary: str, rationales: dict[str, str]) -> str:
        """Submit the final roadmap. Call exactly once, after get_priorities.

        Args:
            executive_summary: 3-5 sentences for a product leader: the top themes, what to build now, and key risks.
            rationales: Map of cluster id -> 1-2 sentence rationale grounded in the numbers and quotes.
        """
        if store.unassigned():
            return f"ERROR: {len(store.unassigned())} items are still unassigned; call get_unassigned."
        result["executive_summary"] = executive_summary
        result["rationales"] = rationales
        return "Roadmap saved."

    return [get_overview, read_feedback, list_clusters, create_cluster, assign_feedback,
            merge_clusters, get_unassigned, get_priorities, submit_roadmap]


def run_agent(store: FeedbackStore, on_event=print, api_key: str | None = None) -> dict:
    """Run the agent to completion. `on_event(str)` receives a log line per step (for CLI or UI)."""
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    result: dict = {}
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # Server-side fallback if a request is ever declined by safety classifiers.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=60,
        tools=build_tools(store, result),
        messages=[{"role": "user", "content":
                   "Cluster all of the customer feedback and produce a prioritized product roadmap."}],
    )
    for message in runner:
        if message.stop_reason == "refusal":
            raise RuntimeError(f"Request declined: {message.stop_details}")
        for block in message.content:
            if block.type == "tool_use":
                args = json.dumps(block.input)
                on_event(f"→ {block.name}({args[:100]}{'…' if len(args) > 100 else ''})")
            elif block.type == "text" and block.text.strip():
                on_event(block.text.strip()[:300])
    if "executive_summary" not in result:
        raise RuntimeError("Agent finished without submitting a roadmap.")
    return result
