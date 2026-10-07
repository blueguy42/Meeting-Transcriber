#!/bin/bash
# Builds MeetingTranscriber.app (double-clickable menu bar app) next to this script.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
APP="$ROOT/MeetingTranscriber.app"

echo "→ building system-audio helper"
(cd sck-audio && swift build -c release 2>&1 | grep -E "error|Compiling|Build complete" | tail -3)

echo "→ python environment"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "→ assembling app bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O launcher/main.swift -o "$APP/Contents/MacOS/MeetingTranscriber"
cp sck-audio/.build/release/sck-audio "$APP/Contents/Resources/sck-audio"
cp assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
echo "$ROOT" > "$APP/Contents/Resources/project_path.txt"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Meeting Transcriber</string>
  <key>CFBundleDisplayName</key><string>Meeting Transcriber</string>
  <key>CFBundleIdentifier</key><string>com.meetingtranscriber.app</string>
  <key>CFBundleExecutable</key><string>MeetingTranscriber</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Records your microphone for meeting transcripts.</string>
  <key>NSScreenCaptureUsageDescription</key><string>Captures system audio and screenshots during meetings.</string>
</dict></plist>
PLIST

codesign --force --deep -s - "$APP"

# macOS caches app icons by path; rebuilding in place can leave a stale (or generic) icon showing.
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREGISTER" -f "$APP" >/dev/null 2>&1 || true
touch "$APP"
echo "✓ built $APP"
echo "  Drag it to /Applications (or the Dock), then double-click."
echo "  If Finder still shows an old icon: killall Finder"
