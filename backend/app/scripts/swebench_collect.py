"""Collect SWE-bench dev cases into a ready-to-review P4A.1 experiment.

  python -m app.scripts.swebench_collect --target 20
  python -m app.scripts.swebench_collect --target 20 --limit 2   # dry smoke
"""

from __future__ import annotations

import argparse
import json

from app.services.swebench_import import run_collection


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=int, default=20)
    p.add_argument("--min-repos", type=int, default=4)
    p.add_argument("--candidate-limit", type=int, default=50)
    p.add_argument("--limit", type=int, default=None, help="Process only first N selected (debug)")
    p.add_argument("--data-dir", default=None)
    args = p.parse_args()
    result = run_collection(
        target_n=args.target,
        min_repos=args.min_repos,
        candidate_limit=args.candidate_limit,
        data_dir=args.data_dir,
        limit_cases=args.limit,
    )
    print(json.dumps(result, indent=2, default=str)[:8000])
    return 0 if result.get("imported", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
