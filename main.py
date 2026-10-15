"""CLI: python main.py data/feedback.csv [--offline] [--out output]"""

import argparse

from roadmap_agent import report
from roadmap_agent.store import FeedbackStore


def main():
    p = argparse.ArgumentParser(description="Cluster customer feedback and build a prioritized roadmap.")
    p.add_argument("input", help="CSV or JSON file with a 'text' column (optional: id, date, channel, customer, plan, arr)")
    p.add_argument("--out", default="output", help="output directory")
    p.add_argument("--offline", action="store_true", help="use the keyword baseline instead of an AI model")
    p.add_argument("--provider", choices=["claude", "gemini"], default="claude",
                   help="AI provider (needs ANTHROPIC_API_KEY or GEMINI_API_KEY)")
    args = p.parse_args()

    store = FeedbackStore.from_file(args.input)
    print(f"Loaded {len(store.feedback)} feedback items from {args.input}")

    if args.offline:
        from roadmap_agent.offline import cluster_offline
        result = cluster_offline(store)
    else:
        from roadmap_agent.agent import run_agent
        print(f"Running the {args.provider} agent…")
        result = run_agent(store, provider=args.provider)

    rep = report.build(store, result)
    for path in report.write_all(rep, args.out):
        print(f"Wrote {path}")
    print("\nTop themes:")
    for x in rep["roadmap"][:5]:
        print(f"  {x['rank']}. [{x['tier']:5}] {x['name']}  (score {x['score']}, "
              f"{x['requests']} requests, {x['accounts']} accounts)")


if __name__ == "__main__":
    main()
