#!/bin/bash
# Build dist/WhisperQuiet.app — a SELF-CONTAINED, distributable bundle.
#
# Unlike scripts/make_app.sh (a dev wrapper that pointed a copied interpreter at
# the live repo + .venv and only ran on THIS machine), this freezes the
# interpreter + every runtime dependency + the whisperquiet source into the
# bundle with PyInstaller, so it runs on a Mac that has never seen the repo.
#
# (Why PyInstaller and not py2app: mlx is a PEP-420 namespace package shipping a
# compiled extension + a sibling Metal lib; py2app's legacy imp.find_module shim
# can't locate it on Python 3.12+, and it doesn't do the Mach-O dependency
# analysis these native libs need. See packaging/whisperquiet.spec.)
#
# Usage:
#   bash scripts/build_app.sh
#
# Env overrides:
#   WQ_BUILD_VENV   path to the build venv (default: $REPO/.buildvenv)
#   WQ_PYTHON       python used to create the build venv (default: python3.14, then python3)
#   WQ_SIGN_ID      Developer ID Application identity for distribution signing
#                   (default: ad-hoc "-", which runs locally but Gatekeeper will
#                   warn on for downloaders — set this once an Apple Developer
#                   account + cert exist to make the download open cleanly).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_VENV="${WQ_BUILD_VENV:-$REPO/.buildvenv}"
SIGN_ID="${WQ_SIGN_ID:--}"
ENTITLEMENTS="$REPO/packaging/entitlements.plist"

# Dictation-only runtime deps. Deliberately EXCLUDES mediapipe/opencv (camera is
# frozen) and the optional parakeet/rescore/denoise extras, so the bundle stays
# as lean as a native-ML app gets. ApplicationServices is required: app.py's
# warm-up imports AXIsProcessTrusted from it for the Accessibility prompt — it is
# NOT pulled in transitively by the other pyobjc frameworks.
RUNTIME_DEPS=(
  mlx-whisper numpy sounddevice rumps
  pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-AVFoundation
  pyobjc-framework-ApplicationServices
)

if [ ! -x "$BUILD_VENV/bin/python" ]; then
  PY="${WQ_PYTHON:-}"
  if [ -z "$PY" ]; then
    if command -v python3.14 >/dev/null 2>&1; then PY=python3.14; else PY=python3; fi
  fi
  echo "==> creating build venv ($PY) at $BUILD_VENV"
  "$PY" -m venv "$BUILD_VENV"
  "$BUILD_VENV/bin/pip" install -q --upgrade pip wheel setuptools
  echo "==> installing dictation-only runtime deps + PyInstaller"
  "$BUILD_VENV/bin/pip" install -q "${RUNTIME_DEPS[@]}" pyinstaller
fi

cd "$REPO"
echo "==> cleaning previous build"
rm -rf build dist

echo "==> freezing with PyInstaller"
"$BUILD_VENV/bin/pyinstaller" packaging/whisperquiet.spec \
  --noconfirm --distpath "$REPO/dist" --workpath "$REPO/build"

APP="$REPO/dist/WhisperQuiet.app"
[ -d "$APP" ] || { echo "FAILED: $APP not produced"; exit 1; }

# Sign INSIDE-OUT: every nested Mach-O must be signed before the outer .app, or
# notarization rejects the bundle. Detect Mach-O by CONTENT (`file`), not by
# extension — PyInstaller embeds Python.framework/Versions/X/Python, a dylib with
# NO extension that an extension glob skips, and the outer sign (no --deep) won't
# recurse into the nested framework, so it would keep PyInstaller's ad-hoc
# signature and fail notarization. --deep is intentionally NOT used. .metallib and
# other data files are sealed into the app's CodeResources by the final sign.
echo "==> signing ($SIGN_ID)"
if [ "$SIGN_ID" = "-" ]; then
  # Ad-hoc: fine for local runs (Gatekeeper still warns downloaders).
  SIGN_FLAGS=(--force --timestamp=none)
else
  # Distribution: hardened runtime + entitlements on every binary.
  SIGN_FLAGS=(--force --options runtime --timestamp --entitlements "$ENTITLEMENTS")
fi
while IFS= read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign "${SIGN_FLAGS[@]}" -s "$SIGN_ID" "$f"
  fi
done < <(find "$APP/Contents" -type f)
codesign "${SIGN_FLAGS[@]}" -s "$SIGN_ID" "$APP"  # outer app sealed last

echo "==> verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> built: $APP"
du -sh "$APP"

# Optional smoke test (WQ_SMOKE=1). A green PyInstaller build can still produce a
# SILENTLY DEAD app — a missing lazily-imported dep, an unresolved native lib, or
# a multiprocessing fork-bomb. Both happened during bring-up and only a real
# launch caught them. This launches the frozen binary, confirms it warms the
# model + installs the hotkey ("PTT tap installed", printed only after the mlx
# Metal warm-up decode), and asserts it started exactly ONCE (no fork-bomb).
# Opt-in because it pops the menu-bar app + TCC prompts. Run it before releasing.
if [ "${WQ_SMOKE:-0}" = "1" ]; then
  echo "==> audio child smoke test (spawn + real mic)"
  AUDIO_SMOKE_LOG="$(mktemp -t wq-audio-smoke)"
  if WQ_NO_LOG_REDIRECT=1 WQ_AUDIO_PROBE=1 \
    "$APP/Contents/MacOS/WhisperQuiet" >"$AUDIO_SMOKE_LOG" 2>&1 && \
    grep -q "audio child smoke PASS" "$AUDIO_SMOKE_LOG"; then
    grep "audio child smoke PASS" "$AUDIO_SMOKE_LOG"
  else
    echo "    audio child smoke FAIL:"
    cat "$AUDIO_SMOKE_LOG"
    exit 1
  fi

  echo "==> smoke test (launching frozen app)"
  SMOKE_LOG="$(mktemp -t wq-smoke)"
  # WQ_NO_LOG_REDIRECT so the app leaves stdout alone and we can read the banner
  # (otherwise launch.py dup2's its output to ~/Library/Logs/whisperquiet.log).
  WQ_NO_LOG_REDIRECT=1 "$APP/Contents/MacOS/WhisperQuiet" >"$SMOKE_LOG" 2>&1 &
  SMOKE_PID=$!
  for _ in $(seq 1 75); do
    grep -qE "PTT tap installed|Traceback|ModuleNotFoundError|metallib|Abort|Fatal|ImportError" "$SMOKE_LOG" && break
    sleep 1
  done
  if grep -q "PTT tap installed" "$SMOKE_LOG"; then
    echo "==> event tap memory smoke test (4,000 tagged events)"
    "$BUILD_VENV/bin/python" "$REPO/scripts/probe_event_tap_memory.py" \
      --pid "$SMOKE_PID" \
      --pairs 2000
  fi
  sleep 5  # let any relaunch/fork-bomb manifest
  kill -9 "$SMOKE_PID" 2>/dev/null || true
  pkill -9 -f "$APP" 2>/dev/null || true
  banners=$(grep -c "settings bridge on port" "$SMOKE_LOG" 2>/dev/null || echo 0)
  if grep -q "PTT tap installed" "$SMOKE_LOG" && [ "$banners" = "1" ]; then
    echo "    smoke PASS: warmed model + installed hotkey, single startup"
  else
    echo "    smoke FAIL: marker missing or app relaunched ($banners startups). Log:"
    cat "$SMOKE_LOG"
    exit 1
  fi
fi
