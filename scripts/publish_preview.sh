#!/bin/bash
# Publish the friends preview site + release assets to a public dist repo.
#
# Prereqs:
#   gh auth login -h github.com
#   bash scripts/build_app.sh
#   bash scripts/package_preview.sh
#
# Defaults publish to:
#   site:    https://aenicay1.github.io/whisperquiet-dist/
#   release: https://github.com/aenicay1/whisperquiet-dist/releases/tag/v0.1.0-preview
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIST_REPO="${WQ_DIST_REPO:-aenicay1/whisperquiet-dist}"
TAG="${WQ_PREVIEW_TAG:-v0.1.0-preview}"
SITE_DIR="${WQ_SITE_DIR:-$REPO/site}"
ZIP="${WQ_RELEASE_ZIP:-$REPO/release/WhisperQuiet.zip}"
SUM="$ZIP.sha256"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/wq-dist.XXXXXX")"

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

need_file() {
  if [ ! -e "$1" ]; then
    echo "FAILED: missing $1" >&2
    exit 1
  fi
}

need_file "$SITE_DIR/index.html"
need_file "$SITE_DIR/icon.png"
need_file "$SITE_DIR/og.png"
need_file "$ZIP"
need_file "$SUM"

echo "==> checking GitHub CLI auth"
gh auth status >/dev/null
gh auth setup-git >/dev/null

echo "==> staging generated public site"
cp -R "$SITE_DIR/." "$TMP/"
cat > "$TMP/README.md" <<EOF
# WhisperQuiet Preview

Public preview site and release downloads for WhisperQuiet.

- Site: https://aenicay1.github.io/whisperquiet-dist/
- Latest download: https://github.com/$DIST_REPO/releases/latest/download/WhisperQuiet.zip
EOF

git -C "$TMP" init -b main >/dev/null
git -C "$TMP" config user.name "Yacine Bouabida"
git -C "$TMP" config user.email "ybouabida11@gmail.com"
git -C "$TMP" add .
git -C "$TMP" commit -m "Publish WhisperQuiet preview site" >/dev/null

if gh repo view "$DIST_REPO" >/dev/null 2>&1; then
  echo "==> updating existing dist repo: $DIST_REPO"
  git -C "$TMP" remote add origin "https://github.com/$DIST_REPO.git"
  git -C "$TMP" push --force origin main
else
  echo "==> creating public dist repo: $DIST_REPO"
  gh repo create "$DIST_REPO" \
    --public \
    --disable-issues \
    --disable-wiki \
    --description "Public preview site and release downloads for WhisperQuiet" \
    --homepage "https://aenicay1.github.io/whisperquiet-dist/" \
    --source "$TMP" \
    --remote origin \
    --push
fi

echo "==> enabling GitHub Pages from main / (best effort)"
if ! gh api -X POST "repos/$DIST_REPO/pages" -f 'source[branch]=main' -f 'source[path]=/' >/dev/null 2>&1; then
  gh api -X PUT "repos/$DIST_REPO/pages" -f 'source[branch]=main' -f 'source[path]=/' >/dev/null 2>&1 || true
fi

NOTES="$TMP/release-notes.md"
cat > "$NOTES" <<EOF
Friends preview for WhisperQuiet.

Install:
1. Download \`WhisperQuiet.zip\`.
2. Unzip it and drag \`WhisperQuiet.app\` into Applications.
3. macOS 15 (Sequoia) or newer: open the app once, macOS will block it, then go to
   System Settings -> Privacy & Security, scroll down to the "WhisperQuiet was blocked"
   notice, click Open Anyway, and confirm (you may be asked to authenticate). Open the
   app again after that.
   macOS 13-14: right-click the app, choose Open, then choose Open again.
4. Grant Microphone, Accessibility, and Input Monitoring, then quit and reopen once.

SHA-256:
\`\`\`
$(cat "$SUM")
\`\`\`
EOF

echo "==> publishing release assets"
if gh release view "$TAG" -R "$DIST_REPO" >/dev/null 2>&1; then
  gh release upload "$TAG" "$ZIP" "$SUM" -R "$DIST_REPO" --clobber
else
  gh release create "$TAG" "$ZIP" "$SUM" \
    -R "$DIST_REPO" \
    --title "WhisperQuiet friends preview" \
    --notes-file "$NOTES" \
    --latest
fi

echo "==> done"
echo "Site:    https://aenicay1.github.io/whisperquiet-dist/"
echo "Release: https://github.com/$DIST_REPO/releases/tag/$TAG"
echo "Download: https://github.com/$DIST_REPO/releases/latest/download/WhisperQuiet.zip"
