"""Run the P4B weak-supervision fine-tuning pilot.

  DATA_DIR=../data python -m app.scripts.p4b_pilot
"""

from __future__ import annotations

import json
import sys

from app.services.p4b_pilot import run_p4b_pilot


def main() -> int:
    try:
        result = run_p4b_pilot()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "training_failed", "error": str(exc)}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": result["status"],
                "checkpoint_hash": result["checkpoint_hash"],
                "experimental": True,
                "activated": False,
                "report_path": result["report_path"],
                "exact_counts": result["audit"]["exact_counts"],
                "annotation_provenance": result["audit"]["annotation_provenance"],
                "selected_epoch": result["training"]["selected_epoch"],
                "verify": result["verify"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
