#!/bin/bash
# Build ~/Applications/WhisperQuiet.app — a wrapper bundle so macOS TCC
# attributes mic/camera/accessibility to "WhisperQuiet" (own identity,
# own usage descriptions) instead of whatever terminal spawned it.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/WhisperQuiet.app"

mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.yacine.whisperquiet</string>
    <key>CFBundleName</key>
    <string>WhisperQuiet</string>
    <key>CFBundleExecutable</key>
    <string>whisperquiet</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>WhisperQuiet transcribes your whispered speech on-device.</string>
    <key>NSCameraUsageDescription</key>
    <string>WhisperQuiet tracks your head and facial gestures on-device for hands-free control. No video ever leaves your Mac.</string>
</dict>
</plist>
PLIST

# Copy the real interpreter INTO the bundle so the running process lives at
# WhisperQuiet.app/Contents/MacOS/* — TCC prompts then say "WhisperQuiet",
# not "Python". (The framework is referenced by absolute path, so the copied
# binary still finds it.)
REAL_BIN="$(dirname "$(readlink -f "$REPO/.venv/bin/python")")/../Resources/Python.app/Contents/MacOS/Python"
cp -f "$REAL_BIN" "$APP/Contents/MacOS/whisperquiet-bin"

PYVER="$("$REPO/.venv/bin/python" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
cat > "$APP/Contents/MacOS/whisperquiet" <<LAUNCHER
#!/bin/bash
DIR="\$(cd "\$(dirname "\$0")" && pwd)"
exec >> "\$HOME/Library/Logs/whisperquiet.log" 2>&1
export PYTHONPATH="$REPO:$REPO/.venv/lib/python$PYVER/site-packages"
exec "\$DIR/whisperquiet-bin" -m whisperquiet.app
LAUNCHER
chmod +x "$APP/Contents/MacOS/whisperquiet"

# ad-hoc signature keeps TCC grants stable across launches
codesign --force --deep -s - "$APP"
echo "built: $APP"
