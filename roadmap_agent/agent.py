"""The clustering & roadmap agent, runnable on Claude or Gemini.

The model drives the loop: it reads feedback in batches, creates/merges clusters,
assigns every item, asks for the deterministic priority ranking, records a
rationale and effort estimate per theme, and finally submits a summary. The same
plain-Python tools serve both providers; only the loop driver differs.
"""

from __future__ import annotations

import functools
import json
import os

from .store import CATEGORIES, FeedbackStore

CLAUDE_MODEL = "claude-opus-5-5"
# Google's alias for the newest stable Flash model; override with the GEMINI_MODEL env var / secret.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
PROVIDERS = {"claude": "Claude", "gemini": "Gemini"}

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
6. For every roadmap cluster, call set_theme_details with a 1-2 sentence rationale grounded in the
   numbers and quotes, and a rough build effort (S = days, M = a few weeks, L = a quarter or more)
   based on typical SaaS engineering cost. Note where the score alone is misleading (e.g. one huge
   account, or a cheap quick win).
7. Finish by calling submit_roadmap once with the executive summary."""


def build_tools(store: FeedbackStore, result: dict) -> list:
    """Plain functions with typed signatures and docstrings; each SDK derives its tool schema from them."""

    def get_overview() -> str:
        """Get totals: number of feedback items, how many are clustered, and counts per channel."""
        return json.dumps(store.summary())

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

    def list_clusters() -> str:
        """List current clusters with their size and a few member ids."""
        return json.dumps([{"id": c.id, "name": c.name, "category": c.category,
                            "description": c.description, "size": len(c.feedback_ids),
                            "sample_ids": c.feedback_ids[:5]} for c in store.clusters.values()])

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

    def get_unassigned() -> str:
        """Return the full text of any feedback items not yet assigned to a cluster."""
        ids = store.unassigned()
        return json.dumps({"count": len(ids), "items": [
            {"id": i, "text": store.feedback[i].text} for i in ids]})

    def get_priorities() -> str:
        """Compute the ranked roadmap: score (0-100), tier (Now/Next/Later), requests, accounts, ARR, channels."""
        ranked = store.prioritize()
        return json.dumps([{k: s[k] for k in ("rank", "id", "name", "tier", "score", "requests",
                                                "accounts", "arr", "channels", "urgent")} for s in ranked])

    def set_theme_details(cluster_id: str, rationale: str, effort: str) -> str:
        """Record the rationale and estimated build effort for one roadmap cluster.

        Args:
            cluster_id: Cluster id, e.g. "C03".
            rationale: 1-2 sentences on why it ranks where it does, grounded in the numbers and quotes.
            effort: Estimated build effort: "S" (days), "M" (a few weeks) or "L" (a quarter or more).
        """
        if cluster_id not in store.clusters:
            return f"ERROR: unknown cluster {cluster_id}"
        result.setdefault("rationales", {})[cluster_id] = rationale
        store.set_effort(cluster_id, effort.strip().upper()[:1])
        return "Saved."

    def submit_roadmap(executive_summary: str) -> str:
        """Submit the final roadmap. Call exactly once, after set_theme_details for every roadmap cluster.

        Args:
            executive_summary: 3-5 sentences for a product leader: the top themes, what to build now, and key risks.
        """
        if store.unassigned():
            return f"ERROR: {len(store.unassigned())} items are still unassigned; call get_unassigned."
        result["executive_summary"] = executive_summary
        return "Roadmap saved. You are done."

    return [get_overview, read_feedback, list_clusters, create_cluster, assign_feedback,
            merge_clusters, get_unassigned, get_priorities, set_theme_details, submit_roadmap]


def _short(args: dict) -> str:
    s = json.dumps(args)
    return s[:100] + ("…" if len(s) > 100 else "")


def _run_claude(store: FeedbackStore, result: dict, on_event, api_key: str | None) -> None:
    import anthropic
    from anthropic import beta_tool

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # Server-side fallback if a request is ever declined by safety classifiers.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=80,
        tools=[beta_tool(fn) for fn in build_tools(store, result)],
        messages=[{"role": "user", "content":
                   "Cluster all of the customer feedback and produce a prioritized product roadmap."}],
    )
    for message in runner:
        if message.stop_reason == "refusal":
            raise RuntimeError(f"Request declined: {message.stop_details}")
        for block in message.content:
            if block.type == "tool_use":
                on_event(f"→ {block.name}({_short(block.input)})")
            elif block.type == "text" and block.text.strip():
                on_event(block.text.strip()[:300])


def _run_gemini(store: FeedbackStore, result: dict, on_event, api_key: str | None) -> None:
    from google import genai
    from google.genai import types

    def logged(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            on_event(f"→ {fn.__name__}({_short(kwargs)})")
            return fn(*args, **kwargs)
        return wrapper

    client = genai.Client(api_key=api_key) if api_key else genai.Client()
    # The SDK's automatic function calling runs the tool loop for us.
    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[logged(fn) for fn in build_tools(store, result)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=120),
        ),
    )
    response = chat.send_message("Cluster all of the customer feedback and produce a prioritized product roadmap.")
    # Gemini sometimes pauses to report progress before finishing; nudge it to complete the steps.
    for _ in range(3):
        if "executive_summary" in result:
            break
        if response.text:
            on_event(response.text.strip()[:300])
        response = chat.send_message("Continue with the remaining steps until you have called submit_roadmap.")


def run_agent(store: FeedbackStore, on_event=print, api_key: str | None = None, provider: str = "claude") -> dict:
    """Run the agent to completion. `on_event(str)` receives a log line per step (for CLI or UI)."""
    result: dict = {}
    if provider == "gemini":
        _run_gemini(store, result, on_event, api_key)
    else:
        _run_claude(store, result, on_event, api_key)
    if "executive_summary" not in result:
        raise RuntimeError("The agent stopped before submitting a roadmap. Try running it again.")
    return result
