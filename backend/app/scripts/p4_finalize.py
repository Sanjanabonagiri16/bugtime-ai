"""Close P4: freeze, blind test labels, held-out compare, final report.

  DATA_DIR=../data python -m app.scripts.p4_finalize
"""

from __future__ import annotations

import json
import sys

from app.services.p4_finalize import finalize_p4


def main() -> int:
    try:
        result = finalize_p4()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "finalize_failed", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, default=str)[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
