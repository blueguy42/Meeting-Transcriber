# Meeting Transcriber

An app with a small floating control bar that listens to your meetings and writes the notes for you.

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
Drag it to Applications or the Dock and double-click it. A small control bar appears on the edge of your screen.
The power button at the bottom of the bar closes the app (it asks first if a recording or processing is running).

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
| Start or stop a recording | Click the record button on the bar, or press **Ctrl + Option + R**. Stopping also processes the meeting. |
| Take a screenshot while recording | The camera button on the bar, or **Ctrl + Option + S**. The screen under your mouse flashes white to confirm. |
| Mute your microphone / the system audio | The mic and speaker buttons on the bar, or **Ctrl + Option + M** (microphone) and **Ctrl + Option + N** (system audio). Press again to unmute. This works any time, also before you start recording, and the state carries over to the next recording. A muted button gets a red line across it (also on the hidden tab). Muted audio is replaced with silence before it is saved, so it cannot be recovered. The transcript marks the stretch as `(Me muted)` / `(Me unmuted)`. |
| Choose how the summary is written | **⋮ → Summary style** (remembered next time) |
| Summarize or re-summarize any meeting | **⋮ → Summarize a meeting…**, then pick the meeting's folder |
| Transcribe a meeting (again, after changing the model, language or vocabulary) | **⋮ → Transcribe a meeting…**, then pick its folder. The old `transcript.md` stays as it is until the new transcription and speaker separation have finished, and is then replaced; the meeting is summarized again if that is on. |
| Change settings or edit the summary styles | **⋮ → Settings…** (⌘ ,) |
| Open the folder with all meetings | **⋮ → Open meetings folder** |

**Floating bar.** A small bar sits on the right edge of your screen with the same controls: record / stop
(with the elapsed time), screenshot, mute microphone, mute system audio, and **⋮** for the rest of the menu
(summary style, transcribe a meeting, summarize a meeting, settings, meetings folder). Hover a button for its name
and shortcut. A red line across an icon means it is muted.
- **Move it:** drag it by its grip or any empty part, up, down or across. A dashed preview shows the screen edge it will snap to when you let go (it jumps to the other edge once you pass the middle of the screen). It remembers where you left it.
  Menus and tooltips open on the side facing the screen centre.
- **Hide it:** the arrow at the bottom of the bar collapses it to a slim tab on the edge (still showing the timer, progress and mutes). Click the tab to bring the bar back, or drag it up, down or to the other edge (a dashed preview shows where it lands).
- **After you stop:** the record button turns into a progress ring and a panel lists the steps (transcribing your mic and the
  system audio, summarizing, saving). When it is done you get **Summary** and **Folder** buttons. You can start a new
  recording meanwhile; a blue badge counts the meetings still being processed.
- **Permissions:** if Microphone or Screen & System Audio Recording is missing, an amber warning button appears on the bar (and at the top of **⋮**). Click it to be asked, or taken to the right System Settings pane. macOS only lets a running app use Screen Recording after a restart, so once you have sent yourself to System Settings the button turns into a **restart** button: switch the permission on, then click it and the app restarts by itself (it asks you to stop a recording first).
- **Private:** it never takes focus from the app you are in, and by default it is kept out of screen shares and out of your screenshots. Turn on **Settings → Floating bar → Show the bar in screenshots and screen shares** (`overlay_in_captures = true`) if you want it visible in them. It applies right away.
- If a display change leaves the bar off-screen, it moves back onto a visible screen.

Hover a button on the bar to see its shortcut. Shortcuts work in any app and need no extra
permission. If another app already uses the same combination, you get a notification.

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
- The Word file is made for every summary (automatic or from the menu) when *Also create a Word document* is on.
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
| Other people: tell them apart | Numbers the other people on the call in the transcript (Others 1, Others 2…), by how much they talk. Downloads about 35 MB the first time and runs on your Mac | on |
| Other people: how many | Work it out automatically, or set the number of other people if it merges or splits someone | automatic |
| Your microphone / Everyone else | The speaker names shown in the transcript (they must differ) | Me / Others |
| Audio compression | AAC (smallest, ~30 MB/hour), FLAC (lossless, ~110 MB/hour) or off (WAV, ~230 MB/hour) | AAC |
| When a recording stops: summarize automatically | If off, the app only transcribes. Summarize later from the menu | on |
| Every summary: also create a Word document | Also save each summary as `.docx`, automatic or manual | on |
| Default summary style | The style used for automatic summaries | default |
| Muting: when the app opens | Start with the microphone and/or system audio muted | both off |
| Hotkeys | Click the box and press the combination you want (needs at least one modifier key) | Ctrl+Option+R / S / M / N |
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
  speakers or headphones you use. With *Tell them apart* on (the default) the other people are numbered by voice
  (Others 1, Others 2…, most talkative first); otherwise they all appear as "Others".
- **Telling people apart** is by voice only, so it can't know names, and it makes mistakes: two similar voices may be
  merged, one person with a very different tone may be split, and a short remark can land on the wrong person. If the
  count looks wrong, set *How many* other people in Settings. Overlapping speech goes to whoever talks most in that line.
- **Echo:** without headphones your mic also hears the speakers. The app detects and drops most of this,
  but headphones give cleaner results, and talking over each other can leak some echo.
- **Accuracy:** language is detected for each burst of speech, so a switch mid-sentence can garble a
  few words, and very short or quiet words are sometimes misheard. Add names to *Vocabulary* to help; words from the
  meeting folder's title (the part after " - ") are added automatically.
- **Made-up text:** Whisper sometimes invents "Terima kasih." or "Thank you." out of silence or noise. Segments that are
  mostly silent, repeat themselves, or are a lone outro phrase with low confidence are dropped, so a real one-word
  "thank you" said very quietly could occasionally go missing.
- **Stopping** can freeze the menu for a few seconds while the audio is closed.
- **Mac only.** The app has not yet been tested on a real call with live system audio, hotkeys and microphone.

---

## Troubleshooting

| Problem | What to do |
|---|---|
| The icon disappears right after launch | Read `~/Library/Logs/MeetingTranscriber.log` |
| "Claude Code CLI not found" | Install it, or set the path under Settings → Advanced |
| Screenshot error about Screen Recording | Allow the app in System Settings → Privacy & Security, then restart it |
| A meeting failed to process or summarize | **⋮ → Summarize a meeting…**, or run the CLI command below |
| The summary fails on a very long meeting | See "Very long meetings" above |
| Other participants are silent in the transcript | The transcript starts with a ⚠ warning when a track was silent (stretches you muted yourself don't count). Check the Screen & System Audio Recording permission |

---

## Technical details

### Requirements and build
`build_app.sh` builds the `sck-audio` Swift helper (system audio), creates `.venv` and installs
`requirements.txt`, compiles a small native launcher, assembles `MeetingTranscriber.app`, ad-hoc code-signs it
and registers the icon. The app is a thin launcher: it starts `.venv/bin/python -m mt.app` from the project
folder, so macOS attributes the permissions to the app instead of to Terminal or a bare Python binary.
Output goes to `~/Library/Logs/MeetingTranscriber.log`.

### How recording works
- **Microphone:** `sounddevice` input stream on the default input device, 16 kHz mono, written to `recordings/mic.wav`.
- **System audio:** the `sck-audio` helper uses ScreenCaptureKit on the first display (all system audio, excluding the app itself) and
  writes `recordings/system.wav`. It reports the start time of the first audio buffer so the tracks can be aligned.
- **Screenshots:** captured with Quartz at the real pixel resolution of the display under the mouse, scaled by
  `screenshot_scale`, saved as `shots/shot_<seconds>.jpg` (JPEG quality 80). A white flash window confirms each capture.
- **Hotkeys:** Carbon `RegisterEventHotKey`, which needs no Accessibility or Input Monitoring permission and runs
  callbacks on the main thread.
- `meta.json` stores the start time, duration and the offsets that align mic, system audio and screenshots, plus
  `mic_muted` / `system_muted` (muted spans, in seconds from the recording start).
- **Muting** replaces the audio with silence at the source (zeros in the mic callback; a `--muted` launch flag plus `MUTE`/`UNMUTE` commands on the
  `sck-audio` helper's stdin). The files keep running, so they stay aligned with the clock and the screenshots.

### How transcription works
Each track is cut into bursts of speech and every burst is transcribed on its own with mlx-whisper, with exact
timestamps; silence is skipped. Mic bursts whose loudness envelope mirrors the system audio (speaker bleed) are
dropped before and after transcription. Language is detected per burst (a pause of 1.5 s or more starts a new
one), limited to `allowed_languages`. Very short bursts reuse the previous burst's language. Roughly 30× faster than real time on Apple Silicon.
If speaker separation is on, the system track is also run through sherpa-onnx (`mt/diarize.py`): pyannote
segmentation cuts it into speech turns, which are grouped by voice (3D-Speaker embeddings, CPU, about 25× faster than real
time). The groups are deliberately too fine; each is averaged into one voiceprint and groups whose voiceprints are
close (cosine distance below 0.35) are merged, tiny groups first. Each Whisper segment then gets the speaker who talks
most during it. The two models (about 35 MB) are downloaded to `~/Library/Application Support/MeetingTranscriber/models`
the first time. If that fails, the transcript is made without numbering and says so at the top.
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
| `diarize`, `diarize_speakers` | `true`, `0` | tell the other people apart (Others 1, 2…); `0` = work out how many, or a number up to 12 |
| `my_label`, `others_label` | `Me`, `Others` | speaker names in the transcript; must differ |
| `compress_audio` | `"aac"` | `"flac"` or `"off"` |
| `auto_summarize` | `true` | `false` = transcribe only |
| `export_docx` | `true` | also write `summary.docx` with every summary, automatic or manual |
| `default_prompt` | `"default"` | prompt file name without `.md` |
| `screenshot_scale` | `50` | percent (10–100) of the screen's real pixel size |
| `claude_path`, `claude_model` | auto, Claude Code's default | |
| `start_muted_mic`, `start_muted_system` | `false` | start the app with that side muted |
| `overlay` | `true` | show the floating control bar |
| `overlay_in_captures` | `false` | show the floating bar in screenshots and screen shares |
| `hotkey_record`, `hotkey_screenshot`, `hotkey_mute_mic`, `hotkey_mute_system` | `<ctrl>+<alt>+r` / `s` / `m` / `n` | modifiers `<ctrl> <alt> <shift> <cmd>` plus a letter, digit or `<f1>`…`<f20>`; must differ |

Prompts live in `~/Library/Application Support/MeetingTranscriber/prompts/` (copied from `prompts/` on first run).

### Command line (no app)
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
| `mt/app.py` | the app: actions, permission flow, glue to the bar |
| `mt/overlay.py`, `mt/ui/overlay.html`, `mt/ui/popup.html` | the floating bar, its menu, tooltips and status panel |
| `mt/jobs.py`, `mt/progress.py` | progress of the meetings being processed |
| `mt/recorder.py`, `mt/flash.py` | recording, screenshots, screenshot flash |
| `mt/transcribe.py`, `mt/audio.py` | Whisper transcription, echo handling, audio compression |
| `mt/diarize.py` | telling the other people apart (sherpa-onnx) |
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
| Floating bar | `NSPanel` + WKWebView (PyObjC) | a topmost frameless window |
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
