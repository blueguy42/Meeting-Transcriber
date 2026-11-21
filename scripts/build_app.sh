#!/bin/bash
# Builds MeetingTranscriber.app (double-clickable app) in the project folder.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
APP="$ROOT/MeetingTranscriber.app"

fail() { echo; echo "✗ $1" >&2; shift; for line in "$@"; do echo "  $line" >&2; done; exit 1; }

echo "→ checking this Mac"
[ "$(uname -s)" = "Darwin" ] || fail "Meeting Transcriber only runs on a Mac."
[ "$(uname -m)" = "arm64" ] || fail "This Mac does not have Apple Silicon (M1 or newer)." "The speech model only runs on Apple Silicon."
if ! xcode-select -p >/dev/null 2>&1 || ! command -v swift >/dev/null 2>&1; then
  fail "Apple's command line tools are missing (they build the system-audio helper)." \
       "Run:  xcode-select --install" \
       "Click Install, wait for it to finish, then run this script again."
fi

# Python 3.11 or newer: the one the Mac ships with is too old, so look for the usual places.
python_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; }
PY=""
for c in python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && python_ok "$c"; then PY="$(command -v "$c")"; break; fi
done
[ -n "$PY" ] || fail "Python 3.11 or newer was not found (the Python that comes with macOS is too old)." \
  "Install it with Homebrew:  brew install python      (Homebrew: https://brew.sh)" \
  "or download it from https://www.python.org/downloads/macos/" \
  "Then open a NEW Terminal window and run this script again."

echo "→ building system-audio helper"
if ! (cd native/sck-audio && swift build -c release 2>&1 | grep -E "error|Compiling|Build complete" | tail -3) || [ ! -x native/sck-audio/.build/release/sck-audio ]; then
  fail "Building the system-audio helper failed." "Check that the command line tools are installed (xcode-select --install) and run this script again."
fi

echo "→ python environment ($("$PY" -V 2>&1))"
# A .venv left over from an attempt with an old Python would keep failing: start it again.
if [ -d .venv ] && ! python_ok .venv/bin/python; then rm -rf .venv; fi
[ -d .venv ] || "$PY" -m venv .venv
if ! .venv/bin/pip install -q -e .; then
  fail "Installing the Python packages failed." "Check your internet connection and run this script again."
fi

echo "→ assembling app bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O native/launcher/main.swift -o "$APP/Contents/MacOS/MeetingTranscriber"
cp native/sck-audio/.build/release/sck-audio "$APP/Contents/Resources/sck-audio"
cp resources/icons/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
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
