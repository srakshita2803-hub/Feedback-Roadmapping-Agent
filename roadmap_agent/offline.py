"""Keyword-rule baseline: runs with no API key.

Useful for demos, tests, and as a comparison point that shows what the LLM
agent adds (it handles paraphrases and new themes these rules cannot).
"""

from __future__ import annotations

import re

from .store import FeedbackStore

# (name, category, description, keyword regex) - first match wins, so order matters.
RULES = [
    ("Crashes & freezes", "bug", "App crashes or freezes.", r"crash|freez"),
    ("Billing & account questions", "not_a_request", "Billing/account support, not product asks.", r"charged|refund|billing"),
    ("Praise", "not_a_request", "Positive feedback with no request.", r"^(great|love the|your onboarding)|keep it up"),
    ("SCIM user provisioning", "security_compliance", "Automatic user provisioning from the IdP.", r"scim|provisioning"),
    ("Single sign-on (SAML / Okta / Azure AD / Google)", "security_compliance", "Enterprise SSO login.", r"\bsso\b|single sign-on|saml|okta"),
    ("Two-factor authentication", "security_compliance", "MFA / 2FA, enforceable by admins.", r"2fa|mfa|two-factor"),
    ("Audit logs", "security_compliance", "Exportable log of admin actions.", r"audit log"),
    ("Dark mode", "ux_improvement", "Dark / night theme.", r"dark|night mode|white background"),
    ("Slack & Microsoft Teams integration", "integration", "Notifications and actions in chat tools.", r"slack|teams"),
    ("Offline mode (mobile)", "feature_request", "Use the app without connectivity.", r"offline|on flights|without signal|subway"),
    ("Public API, webhooks & rate limits", "integration", "Programmatic access and automation.", r"\bapi\b|webhook|rate limit"),
    ("Custom dashboards & report builder", "feature_request", "User-defined dashboards and reports.", r"dashboard|report|custom charts"),
    ("Data export (CSV / Excel / PDF)", "feature_request", "Export data in common formats.", r"export|csv|excel|pdf"),
    ("Recurring tasks & templates", "feature_request", "Repeat tasks and reusable templates.", r"recurring|repeat|template|reminder"),
    ("Localization", "feature_request", "UI in more languages.", r"spanish|portuguese|german|french|locali|translat"),
    ("Speed & performance", "performance", "Slow load times and sluggish UI.", r"slow|sluggish|forever|faster|performance|seconds"),
]

URGENT = r"block|deal|dealbreaker|renewal|risk|lost the|must-have|requires|required|contingent|auditor|compliance|deleting|ciso"


def cluster_offline(store: FeedbackStore) -> dict:
    ids: dict[str, str] = {}
    other = None
    for f in store.feedback.values():
        text = f.text.lower()
        for name, cat, desc, pattern in RULES:
            if re.search(pattern, text):
                if name not in ids:
                    ids[name] = store.create_cluster(name, desc, cat).id
                urgent = [f.id] if re.search(URGENT, text) else []
                store.assign(ids[name], [f.id], urgent)
                break
        else:
            other = other or store.create_cluster("Other / uncategorized", "No rule matched.", "feature_request").id
            store.assign(other, [f.id])
    ranked = store.prioritize()
    top = ", ".join(s["name"] for s in ranked[:3])
    return {
        "executive_summary": f"(Offline keyword baseline - run with an API key for the Claude agent's analysis.) "
                             f"Highest-demand themes: {top}.",
        "rationales": {},
    }
