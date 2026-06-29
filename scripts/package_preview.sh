#!/bin/bash
# Package the frozen WhisperQuiet.app as the public preview download.
#
# Usage:
#   bash scripts/build_app.sh
#   bash scripts/package_preview.sh
#
# Env overrides:
#   WQ_APP_PATH       app bundle to package (default: dist/WhisperQuiet.app)
#   WQ_RELEASE_DIR    output directory (default: release)
#   WQ_RELEASE_NAME   zip filename (default: WhisperQuiet.zip)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="${WQ_APP_PATH:-$REPO/dist/WhisperQuiet.app}"
OUT_DIR="${WQ_RELEASE_DIR:-$REPO/release}"
ZIP_NAME="${WQ_RELEASE_NAME:-WhisperQuiet.zip}"
ZIP="$OUT_DIR/$ZIP_NAME"

if [ ! -d "$APP" ]; then
  echo "FAILED: missing app bundle: $APP" >&2
  echo "Run: bash scripts/build_app.sh" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
rm -f "$ZIP" "$ZIP.sha256"

echo "==> verifying app signature"
codesign --verify --deep --strict --verbose=1 "$APP"

echo "==> packaging $ZIP_NAME"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

echo "==> writing checksum"
(
  cd "$OUT_DIR"
  shasum -a 256 "$ZIP_NAME" > "$ZIP_NAME.sha256"
)

echo "==> ready:"
du -sh "$ZIP"
cat "$ZIP.sha256"
