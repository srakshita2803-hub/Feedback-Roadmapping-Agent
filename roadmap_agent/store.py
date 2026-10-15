"""Feedback store: loads raw feedback, holds clusters, and computes demand scores.

The LLM decides *what* belongs together (semantic judgment); this module does
all the *counting and scoring* deterministically, so numbers in the roadmap are
never hallucinated.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CATEGORIES = [
    "feature_request",
    "integration",
    "security_compliance",
    "performance",
    "ux_improvement",
    "bug",
    "not_a_request",  # praise, billing questions, etc. - excluded from the roadmap
]

# Categories that represent product work worth putting on a roadmap.
ROADMAP_CATEGORIES = {"feature_request", "integration", "security_compliance", "performance", "ux_improvement"}

# Weights for the priority score (sum to 1.0). Tune these to your strategy.
WEIGHTS = {
    "requests": 0.30,        # raw volume of mentions
    "accounts": 0.25,        # distinct customers asking
    "arr": 0.25,             # revenue of customers asking
    "channels": 0.10,        # breadth: asked for across many channels
    "urgency": 0.10,         # share of mentions flagged as blocking / churn risk
}


@dataclass
class Feedback:
    id: str
    date: str
    channel: str
    customer: str
    plan: str
    arr: float
    text: str


@dataclass
class Cluster:
    id: str
    name: str
    description: str
    category: str
    feedback_ids: list[str] = field(default_factory=list)
    urgent_ids: set[str] = field(default_factory=set)


class FeedbackStore:
    def __init__(self, feedback: list[Feedback]):
        self.feedback = {f.id: f for f in feedback}
        self.clusters: dict[str, Cluster] = {}
        self.assignment: dict[str, str] = {}  # feedback_id -> cluster_id
        self._next = 1

    # ---------- loading ----------
    @classmethod
    def from_file(cls, path: str | Path) -> "FeedbackStore":
        path = Path(path)
        if path.suffix == ".json":
            rows = json.loads(path.read_text())
        else:
            with path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        items = []
        for i, r in enumerate(rows, 1):
            items.append(
                Feedback(
                    id=str(r.get("id") or f"F{i:04d}"),
                    date=r.get("date", ""),
                    channel=r.get("channel", "unknown"),
                    customer=r.get("customer", "unknown"),
                    plan=r.get("plan", ""),
                    arr=float(r.get("arr") or 0),
                    text=r["text"].strip(),
                )
            )
        return cls(items)

    # ---------- cluster operations (exposed to the agent as tools) ----------
    def create_cluster(self, name: str, description: str, category: str) -> Cluster:
        if category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        for c in self.clusters.values():
            if c.name.lower() == name.lower():
                raise ValueError(f"cluster '{name}' already exists as {c.id}")
        cid = f"C{self._next:02d}"
        self._next += 1
        self.clusters[cid] = Cluster(cid, name, description, category)
        return self.clusters[cid]

    def assign(self, cluster_id: str, feedback_ids: list[str], urgent_ids: list[str] | None = None) -> dict:
        if cluster_id not in self.clusters:
            raise ValueError(f"unknown cluster {cluster_id}")
        unknown = [f for f in feedback_ids if f not in self.feedback]
        moved = []
        for fid in feedback_ids:
            if fid not in self.feedback:
                continue
            prev = self.assignment.get(fid)
            if prev and prev != cluster_id:
                self.clusters[prev].feedback_ids.remove(fid)
                self.clusters[prev].urgent_ids.discard(fid)
                moved.append(fid)
            if fid not in self.clusters[cluster_id].feedback_ids:
                self.clusters[cluster_id].feedback_ids.append(fid)
            self.assignment[fid] = cluster_id
        for fid in urgent_ids or []:
            if self.assignment.get(fid) == cluster_id:
                self.clusters[cluster_id].urgent_ids.add(fid)
        return {"cluster": cluster_id, "size": len(self.clusters[cluster_id].feedback_ids),
                "moved_from_other_clusters": moved, "unknown_ids": unknown}

    def merge(self, source_id: str, target_id: str) -> dict:
        src, tgt = self.clusters.get(source_id), self.clusters.get(target_id)
        if not src or not tgt or source_id == target_id:
            raise ValueError("need two distinct, existing cluster ids")
        urgent = list(src.urgent_ids)
        self.assign(target_id, list(src.feedback_ids), urgent)
        del self.clusters[source_id]
        return {"merged_into": target_id, "size": len(tgt.feedback_ids)}

    def unassigned(self) -> list[str]:
        return [fid for fid in self.feedback if fid not in self.assignment]

    # ---------- analytics ----------
    def cluster_stats(self, c: Cluster) -> dict:
        items = [self.feedback[f] for f in c.feedback_ids]
        accounts = {i.customer for i in items}
        # ARR counted once per account, not once per mention.
        arr = sum({i.customer: i.arr for i in items}.values())
        dates = sorted(i.date for i in items if i.date)
        return {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "description": c.description,
            "requests": len(items),
            "accounts": len(accounts),
            "arr": arr,
            "channels": sorted({i.channel for i in items}),
            "urgent": len(c.urgent_ids),
            "first_seen": dates[0] if dates else "",
            "last_seen": dates[-1] if dates else "",
            "quotes": [f"[{i.id} · {i.channel} · {i.customer}] {i.text}" for i in items[:3]],
        }

    def prioritize(self) -> list[dict]:
        """Rank roadmap-worthy clusters with a transparent weighted score (0-100)."""
        stats = [self.cluster_stats(c) for c in self.clusters.values()
                 if c.category in ROADMAP_CATEGORIES and c.feedback_ids]
        if not stats:
            return []
        n_channels = len({f.channel for f in self.feedback.values()}) or 1
        mx = {k: max(s[k] for s in stats) or 1 for k in ("requests", "accounts", "arr")}
        for s in stats:
            parts = {
                "requests": s["requests"] / mx["requests"],
                "accounts": s["accounts"] / mx["accounts"],
                "arr": s["arr"] / mx["arr"],
                "channels": len(s["channels"]) / n_channels,
                "urgency": s["urgent"] / s["requests"],
            }
            s["score"] = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
        stats.sort(key=lambda s: s["score"], reverse=True)
        for rank, s in enumerate(stats, 1):
            s["rank"] = rank
            s["tier"] = "Now" if s["score"] >= 60 else "Next" if s["score"] >= 35 else "Later"
        return stats

    def summary(self) -> dict:
        by_channel: dict[str, int] = {}
        for f in self.feedback.values():
            by_channel[f.channel] = by_channel.get(f.channel, 0) + 1
        return {
            "total_feedback": len(self.feedback),
            "assigned": len(self.assignment),
            "unassigned": len(self.unassigned()),
            "clusters": len(self.clusters),
            "by_channel": by_channel,
            "generated": date.today().isoformat(),
        }
