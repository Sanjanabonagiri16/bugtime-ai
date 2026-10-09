#!/usr/bin/env bash
# Restore demo-repo-v2 git history from the bundled mirror (required for P2 bisect).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="$ROOT/demo-repo-v2.bundle"
DEST="$ROOT/demo-repo-v2"

if [[ ! -f "$BUNDLE" ]]; then
  echo "Missing $BUNDLE — clone may be incomplete." >&2
  exit 1
fi

if [[ -d "$DEST/.git" ]]; then
  echo "demo-repo-v2 already has .git — leaving as-is."
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone "$BUNDLE" "$TMP/repo"
# Keep working tree files from the checkout; prefer bundle tip history.
rm -rf "$DEST"
mv "$TMP/repo" "$DEST"
echo "Restored demo-repo-v2 at $(git -C "$DEST" rev-parse --short HEAD)"
