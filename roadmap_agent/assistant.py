"""Follow-up Q&A and PRD drafting over a finished roadmap (streams text for the UI)."""

from __future__ import annotations

import json
from collections.abc import Iterator

from .agent import CLAUDE_MODEL, GEMINI_MODEL
from .store import FeedbackStore

ASK_SYSTEM = """You are a product-operations analyst answering a product manager's questions about a
customer-feedback analysis. You get the ranked roadmap (scores are computed in code from request
volume, distinct accounts, ARR, channel breadth and urgency) and every raw feedback item with its
cluster. Answer from this data only: cite numbers and feedback ids (e.g. F014), say when the data
can't answer something, and keep answers short and scannable."""

PRD_SYSTEM = """You are a senior product manager. Write a concise one-page PRD in Markdown for the
theme you are given, grounded only in the customer evidence provided. Sections: Problem, Who is
asking (with the numbers), Goals & non-goals, User stories (3-6, "As a ... I want ... so that ..."),
Requirements (must / should), Success metrics, Open questions. Quote customers with their feedback
ids. Do not invent customers, numbers or commitments."""


def _context(store: FeedbackStore, ranked: list[dict]) -> str:
    roadmap = [{k: s[k] for k in ("rank", "id", "name", "tier", "score", "requests", "accounts",
                                  "arr", "channels", "urgent", "effort", "category")} for s in ranked]
    items = [{"id": f.id, "date": f.date, "channel": f.channel, "customer": f.customer, "plan": f.plan,
              "arr": f.arr, "cluster": store.clusters[store.assignment[f.id]].name
              if f.id in store.assignment else None, "text": f.text} for f in store.feedback.values()]
    return f"<roadmap>\n{json.dumps(roadmap)}\n</roadmap>\n<feedback>\n{json.dumps(items)}\n</feedback>"


def _stream(provider: str, api_key: str, system: str, messages: list[dict]) -> Iterator[str]:
    """Stream text from Claude or Gemini. `messages` is [{"role": "user"|"assistant", "content": str}]."""
    if provider == "gemini":
        yield from _stream_gemini(api_key, system, messages)
    else:
        yield from _stream_claude(api_key, system, messages)


def _stream_claude(api_key: str, system: str, messages: list[dict]) -> Iterator[str]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    with client.beta.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=messages,
    ) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        yield "\n\n_The model declined to answer this request._"
    elif final.stop_reason == "max_tokens":
        yield "\n\n_(Answer cut off at the length limit.)_"


def _stream_gemini(api_key: str, system: str, messages: list[dict]) -> Iterator[str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                              parts=[types.Part(text=m["content"])]) for m in messages]
    for chunk in client.models.generate_content_stream(
            model=GEMINI_MODEL, contents=contents,
            config=types.GenerateContentConfig(system_instruction=system)):
        if chunk.text:
            yield chunk.text


def ask(provider: str, api_key: str, store: FeedbackStore, ranked: list[dict], history: list[dict]) -> Iterator[str]:
    """Stream an answer. `history` is the chat so far as [{"role", "content"}], ending with the user turn."""
    # The data goes in the first user turn so it is identical (and cacheable) across follow-ups.
    first = {"role": "user", "content": _context(store, ranked) + "\n\n" + history[0]["content"]}
    return _stream(provider, api_key, ASK_SYSTEM, [first, *history[1:]])


def draft_prd(provider: str, api_key: str, store: FeedbackStore, theme: dict) -> Iterator[str]:
    cluster = store.clusters[theme["id"]]
    evidence = [{"id": f.id, "channel": f.channel, "customer": f.customer, "plan": f.plan,
                 "urgent": f.id in cluster.urgent_ids, "text": f.text}
                for f in (store.feedback[i] for i in cluster.feedback_ids)]
    stats = {k: theme[k] for k in ("name", "description", "tier", "score", "requests", "accounts",
                                   "arr", "channels", "urgent", "effort")}
    prompt = f"Theme:\n{json.dumps(stats)}\n\nCustomer evidence:\n{json.dumps(evidence)}\n\nWrite the PRD."
    return _stream(provider, api_key, PRD_SYSTEM, [{"role": "user", "content": prompt}])
