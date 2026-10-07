# Meeting Transcriber

A menu bar app that listens to your meetings and writes the notes for you.

You press a key to start recording. During the meeting you can press another key to grab
screenshots. When you stop, the app turns the audio into a written transcript on your own computer
(Indonesian and English), then asks Claude to write a tidy summary using your Claude subscription.
No API key is needed.

| | Status |
|---|---|
| **macOS** (Apple Silicon, macOS 13+) | Supported |
| **Windows** | Planned, not available yet (see [Platform support](#platform-support)) |
| **Summaries** | Written by **Claude** through the Claude Code CLI. Other AI providers are not supported |
| **Transcription** | Runs locally with Whisper; no audio leaves your computer |

**What you get after each meeting**
- a **summary** (as a Word document and a Markdown file) with the key decisions and action items,
- the **full transcript**, with timestamps and who said what ("Me" or "Others"),
- the **screenshots** you took, with the useful ones placed inside the summary.

```
record  ──▶  transcribe (on your Mac)  ──▶  summarize (Claude)  ──▶  Word + Markdown summary
 your mic     "Me" / "Others"                picks the useful          folder renamed with
 + the other  with timestamps                screenshots; you pick     the meeting title
 side's audio                                the summary style
 + screenshots
```

> **Privacy:** the transcript and the screenshots you took are sent to Claude (Anthropic) to write the
> summary. Audio is never uploaded. If you turn off *Summarize automatically with Claude* in Settings,
> nothing is sent anywhere until you choose to summarize a meeting.

---

## Contents
1. [Get started](#get-started)
2. [Everyday use](#everyday-use)
3. [Where your files go](#where-your-files-go)
4. [Settings](#settings)
5. [Good to know](#good-to-know)
6. [Troubleshooting](#troubleshooting)
7. [Technical details](#technical-details)
8. [Platform support](#platform-support)

---

## Get started

### What you need
- A Mac with **Apple Silicon** (M1 or newer) running **macOS 13 or later**. (Windows is not supported yet.)
- The **Claude Code app or CLI**, signed in to your Claude account (a Claude subscription). This is required
  for summaries. If you have the Claude desktop app, the app finds the copy bundled with it automatically.
  Without it you can still record and transcribe.
- Apple's command line tools (install once with `xcode-select --install`) and **Python 3.11+**.

### Install
Open Terminal in this folder and run:

```bash
./build_app.sh
```

This takes a few minutes the first time. It creates **MeetingTranscriber.app** in this folder.
Drag it to Applications or the Dock and double-click it. Its icon appears in the menu bar (a 🎙 if the
image is missing).

- The first transcription downloads the speech model (about 3 GB, one time only).
- The app runs its code from **this project folder**. Keep the folder where it is. If you move it,
  run `./build_app.sh` again from the new location.

### Allow the two permissions
The first time you open the app it explains what it needs, then macOS asks for permission, one at a time:

| Permission | Why the app needs it |
|---|---|
| **Microphone** | to record your voice |
| **Screen & System Audio Recording** | to record the other participants' audio and to take screenshots (no video is recorded) |

- macOS asks only once. If you said no, or clicked "Later", the menu shows **⚠ Permissions needed…**.
  Click it and the app asks again or opens the right page in System Settings.
- After you switch on *Screen & System Audio Recording*, **quit and reopen the app**.
- Without microphone access the app won't start a recording. Without screen access it records your
  microphone only and tells you so.
- Rebuilding the app changes its signature, so macOS may ask for permissions again after `./build_app.sh`.

---

## Everyday use

| I want to… | Do this |
|---|---|
| Start or stop a recording | Click the menu bar icon, or press **Ctrl + Option + R**. Stopping also processes the meeting. |
| Take a screenshot while recording | Menu bar, or **Ctrl + Option + S**. The screen under your mouse flashes white to confirm. |
| Choose how the summary is written | Menu → **Summary style** (remembered next time) |
| Summarize or re-summarize any meeting | Menu → **Summarize a meeting…**, then pick the meeting's folder |
| Change settings or edit the summary styles | Menu → **Settings…** (⌘ ,) |
| Open the folder with all meetings | Menu → **Open meetings folder** |

The menu shows each shortcut next to its item. Shortcuts work in any app and need no extra
permission. If another app already uses the same combination, you get a notification.

**While recording** the menu bar shows 🔴 and the elapsed time. **While processing** it shows ⏳.
You can start a new recording while the previous meeting is still being processed; meetings are processed one at a time.

### How the summary is made
Claude reads the transcript and **every screenshot you took**, then keeps only the useful ones
(slides, charts, documents) and places them in the right spot in the summary. Duplicates, blank
screens and unrelated windows stay in the `shots` folder but are left out of the summary.

A **summary style** is simply a set of instructions for Claude. Two come built in:
- **default**: overview, decisions, action items, open questions, discussion points and what was on screen.
- **action-items-only**: just the decisions and a table of action items.

In **Settings → Prompts** you can edit a style, create your own, rename or delete your own, or reset
a built-in one to its original text (your edited version is kept as `<name>.md.bak`).

### Summarizing a meeting later
**Summarize a meeting…** works on any past meeting, even after restarting the app and even if
automatic summarizing is off. Use it to try another style or to retry a failed summary. If the
meeting has no transcript yet, it is transcribed first.

---

## Where your files go

Each meeting gets its own folder inside `~/Documents/Meetings` (you can change this in Settings):

```
2026-10-07_1030 - Marketing Budget Review/
├── summary.docx            Word version of the summary, with the used screenshots embedded
├── summary-<style>.md      the summary as text, one file per style (e.g. summary-default.md)
├── transcript.md           the full transcript, with timestamps and screenshot markers
├── shots/                  every screenshot you took
├── recordings/             the audio (mic and other participants)
└── meta.json               timing information the app needs
```

- The folder name starts as the date and time. After the first summary it gets the **meeting title** added.
  Summarizing again never renames it.
- Audio is first saved as WAV (safe even if the app crashes), then shrunk to about 30 MB per hour once the transcript is saved and the
  smaller file is verified.
- The Word file only appears when automatic summarizing is on (see Settings).
- **Google Docs:** upload `summary.docx` to Google Drive and open it with Google Docs. Images are kept.
- Older meetings may still have `transcript/transcript.md` and the audio files next to `meta.json`. They keep working.

---

## Settings

Open **Settings…** from the menu (or ⌘ ,). Everything is checked before it is saved, and changes to
shortcuts and the menu apply immediately. Other changes apply from the next recording.

| Setting | What it does | Default |
|---|---|---|
| Language | Auto-detects Indonesian or English as people speak, or force one of them | Auto |
| Model | The speech-to-text model. The "turbo" version is about 4× faster but slightly less accurate | large-v3 |
| Vocabulary | Names and terms to spell correctly, e.g. *Budi, Dina, Tokopedia* | empty |
| Your microphone / Everyone else | The speaker names shown in the transcript (they must differ) | Me / Others |
| Audio compression | AAC (smallest, ~30 MB/hour), FLAC (lossless, ~110 MB/hour) or off (WAV, ~230 MB/hour) | AAC |
| Summarize automatically with Claude | If off, the app only transcribes. Summarize later from the menu | on |
| Create a Word document | Also save the summary as `.docx`. Greyed out while automatic summarizing is off | on |
| Default summary style | The style used for automatic summaries | default |
| Hotkeys | Click the box and press the combination you want (needs at least one modifier key) | Ctrl+Option+R / S |
| Meetings folder | Where meetings are saved | `~/Documents/Meetings` |
| Screenshot resolution (Advanced) | Size of saved screenshots, shown as pixel sizes with the percentage of your screen in grey | 50% |
| Claude path and model (Advanced) | Leave empty to detect the Claude CLI and use your usual model | empty |

**Reset to defaults…** restores the original settings and keeps your old file as `config.toml.bak`.

Settings are stored in `~/Library/Application Support/MeetingTranscriber/config.toml`. You can edit
that file by hand too (see [`config.example.toml`](config.example.toml)); the app keeps your comments
when it saves. If the file has a typo, you get a notification and the previous settings stay in effect.

---

## Good to know

- **Tell participants you are recording.**
- **Very long meetings:** the whole transcript and all screenshots go to Claude in one request. The
  app sets no limit of its own (only a 30-minute timeout), but Claude's own limits apply. A very long meeting or
  dozens of screenshots can make the summary fail. Your transcript and audio are already saved, so
  you can retry with **Summarize a meeting…**.
- **Microphone:** the app uses whichever microphone macOS has selected when you start recording
  (there is no picker). Switching mid-meeting, for example when AirPods connect, may stall the recording.
- **Other participants:** the app records everything your Mac plays, from all apps mixed, whatever
  speakers or headphones you use. All remote participants appear as "Others".
- **Echo:** without headphones your mic also hears the speakers. The app detects and drops most of this,
  but headphones give cleaner results, and talking over each other can leak some echo.
- **Accuracy:** language is detected for each burst of speech, so a switch mid-sentence can garble a
  few words, and very short or quiet words are sometimes misheard. Add names to *Vocabulary* to help.
- **Stopping** can freeze the menu for a few seconds while the audio is closed.
- **Mac only.** The app has not yet been tested on a real call with live system audio, hotkeys and microphone.

---

## Troubleshooting

| Problem | What to do |
|---|---|
| The icon disappears right after launch | Read `~/Library/Logs/MeetingTranscriber.log` |
| "Claude Code CLI not found" | Install it, or set the path under Settings → Advanced |
| Screenshot error about Screen Recording | Allow the app in System Settings → Privacy & Security, then restart it |
| A meeting failed to process or summarize | Menu → **Summarize a meeting…**, or run the CLI command below |
| The summary fails on a very long meeting | See "Very long meetings" above |
| Other participants are silent in the transcript | The transcript starts with a ⚠ warning when a track was silent. Check the Screen & System Audio Recording permission |

---

## Technical details

### Requirements and build
`build_app.sh` builds the `sck-audio` Swift helper (system audio), creates `.venv` and installs
`requirements.txt`, compiles a small native launcher, assembles `MeetingTranscriber.app`, ad-hoc code-signs it
and registers the icon. The app is a thin launcher: it starts `.venv/bin/python -m mt.menubar` from the project
folder, so macOS attributes the permissions to the app instead of to Terminal or a bare Python binary.
Output goes to `~/Library/Logs/MeetingTranscriber.log`.

To change the app icon, replace `assets/icon-source.jpg` (a square image that fills the whole square, since macOS 26 applies its own
rounded mask), run `.venv/bin/python assets/make_icon.py`, then `./build_app.sh`. The script writes `AppIcon.icns`
and the menu bar image `menubar.png`.

### How recording works
- **Microphone:** `sounddevice` input stream on the default input device, 16 kHz mono, written to `recordings/mic.wav`.
- **System audio:** the `sck-audio` helper uses ScreenCaptureKit on the first display (all system audio, excluding the app itself) and
  writes `recordings/system.wav`. It reports the start time of the first audio buffer so the tracks can be aligned.
- **Screenshots:** captured with Quartz at the real pixel resolution of the display under the mouse, scaled by
  `screenshot_scale`, saved as `shots/shot_<seconds>.jpg` (JPEG quality 80). A white flash window confirms each capture.
- **Hotkeys:** Carbon `RegisterEventHotKey`, which needs no Accessibility or Input Monitoring permission and runs
  callbacks on the main thread.
- `meta.json` stores the start time, duration and the offsets that align mic, system audio and screenshots.

### How transcription works
Each track is cut into bursts of speech and every burst is transcribed on its own with mlx-whisper, with exact
timestamps; silence is skipped. Mic bursts whose loudness envelope mirrors the system audio (speaker bleed) are
dropped before and after transcription. Language is detected per burst (a pause of 1.5 s or more starts a new
one), limited to `allowed_languages`. Very short bursts reuse the previous burst's language. Roughly 30× faster than real time on Apple Silicon.
The result is `transcript.md`: `[hh:mm:ss] Speaker: text` lines plus `(screenshot: shots/…)` markers.

### How summarizing works
`mt/summarize.py` runs `claude -p` with the chosen prompt, the transcript and a calendar of the next 15 days (so
relative dates such as "next Friday" resolve correctly) on stdin. Claude may read only `./shots/*`
(`--allowedTools "Read(./shots/*)"`). The output is saved as `summary-<style>.md`; the Word export (pandoc via `pypandoc`)
and the folder renaming use it.

### Security
Meeting content (speech and what is on screen) is untrusted input to Claude:
- Claude can read only `shots/*` of the current meeting, and the prompt tells it to treat transcript text as data, never as instructions.
- The Word export embeds only real image files directly inside `shots/`. Any other image reference (URLs, `../` paths) becomes a
  text placeholder, so nothing is fetched or pulled in from elsewhere on disk.
- The Settings window is a native window with a local page; it talks to the app through a message handler (no web server,
  no open port) and every request is validated.

### Configuration reference
All keys of `config.toml` (the Settings window edits the same file in place):

| Key | Default | Notes |
|---|---|---|
| `meetings_dir` | `~/Documents/Meetings` | where meetings are saved |
| `model` | `mlx-community/whisper-large-v3-mlx` | `…-large-v3-turbo` is ~4× faster, slightly less accurate |
| `language` | `"auto"` | per-burst detection, or force `"id"` / `"en"` |
| `allowed_languages` | `["id", "en"]` | languages auto-detection may pick (config file only) |
| `vocabulary` | `[]` | names/terms to spell correctly |
| `my_label`, `others_label` | `Me`, `Others` | speaker names in the transcript; must differ |
| `compress_audio` | `"aac"` | `"flac"` or `"off"` |
| `auto_summarize` | `true` | `false` = transcribe only |
| `export_docx` | `true` | write `summary.docx`; only takes effect when `auto_summarize` is on |
| `default_prompt` | `"default"` | prompt file name without `.md` |
| `screenshot_scale` | `50` | percent (10–100) of the screen's real pixel size |
| `claude_path`, `claude_model` | auto, Claude Code's default | |
| `hotkey_record`, `hotkey_screenshot` | `<ctrl>+<alt>+r` / `<ctrl>+<alt>+s` | modifiers `<ctrl> <alt> <shift> <cmd>` plus a letter, digit or `<f1>`…`<f20>`; must differ |

Prompts live in `~/Library/Application Support/MeetingTranscriber/prompts/` (copied from `prompts/` on first run).

### Command line (no menu bar app)
```bash
.venv/bin/python -m mt.cli record                                      # Enter = stop, s+Enter = screenshot
.venv/bin/python -m mt.cli process <meeting-dir>                       # transcribe + summarize (summary skipped if auto_summarize is off)
.venv/bin/python -m mt.cli summarize <meeting-dir> action-items-only   # (re-)summarize, optional style name
```

### Optional: ask Claude Desktop about past meetings (MCP)
Add to `claude_desktop_config.json`, then ask things like "what did we decide on Tuesday?":
```json
"meetings": {"command": "/ABS/PATH/meeting-transcriber/.venv/bin/python",
             "args": ["-m", "mt.mcp_server"], "cwd": "/ABS/PATH/meeting-transcriber"}
```
The server reads the meetings folder from your settings when it starts. Tools: `list_meetings`, `get_transcript`,
`get_summary` (latest, or a given style), `get_screenshot`, `list_summary_prompts`.

### Project layout
| Path | Purpose |
|---|---|
| `mt/menubar.py` | menu bar app, menu actions, permission flow |
| `mt/recorder.py`, `mt/flash.py` | recording, screenshots, screenshot flash |
| `mt/transcribe.py`, `mt/audio.py` | Whisper transcription, echo handling, audio compression |
| `mt/pipeline.py`, `mt/summarize.py`, `mt/docx_export.py` | processing flow, Claude summary, Word export |
| `mt/config.py`, `mt/settings_api.py`, `mt/settings_window.py`, `mt/ui/settings.html` | settings storage, validation and window |
| `mt/hotkeys.py`, `mt/permissions.py` | global hotkeys, macOS permissions |
| `mt/cli.py`, `mt/mcp_server.py` | command line, MCP server |
| `sck-audio/`, `launcher/` | Swift helpers: system-audio capture, app launcher |
| `prompts/`, `assets/` | built-in summary styles, icons |

---

## Platform support

The app is macOS-only today. A Windows version is planned. The pieces that are tied to macOS are
listed here so the work can be split up:

| Area | macOS (today) | Windows (planned) |
|---|---|---|
| System audio | `sck-audio` (Swift, ScreenCaptureKit) | WASAPI loopback |
| Transcription | `mlx-whisper` | `faster-whisper` |
| Screenshots | Quartz | `mss` / Pillow |
| Menu bar / tray | `rumps` | `pystray` or Qt |
| Settings window | WKWebView (PyObjC) | `pywebview` (WebView2), same `settings.html` |
| Hotkeys | Carbon `RegisterEventHotKey` | Win32 `RegisterHotKey` |
| Audio compression | `afconvert` | `ffmpeg` |
| Packaging | `build_app.sh`, `.app` bundle | PyInstaller or similar, plus an installer |

Already portable: the processing pipeline, summarizing, the Word export, settings validation, prompts, the
CLI and the MCP server. Contributions toward Windows support are welcome.

## Claude-only summaries

Summaries come from the **Claude Code CLI** (`claude -p`), using your own Claude subscription. There is no
API key and no other AI provider. Transcription does not use Claude and works without it.

## License

No license has been chosen yet. Add a `LICENSE` file before sharing the code publicly.
