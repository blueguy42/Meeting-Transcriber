# Meeting Transcriber

**A small floating bar for your Mac that listens to your meetings and writes the notes for you.**

Press a shortcut to start recording. While the meeting runs you can grab a screenshot of a slide, type your own notes
and add documents (an agenda, a report, a spreadsheet) for background. When you stop, the app turns the conversation
into a written transcript (Indonesian and English) and asks Claude to write a tidy summary with the decisions and action
items. You get a Word document and a text file, ready to share.

<p align="center">
  <img src="docs/screenshots/bar-states.png" alt="The floating bar: idle, recording, muted, the More menu, and with notes and documents" width="900">
</p>

| | |
|---|---|
| **Works on** | macOS 13 or newer, Apple Silicon (M1 or later). Windows is planned, not available yet |
| **Transcription** | Done on your own Mac with Whisper. Your audio never leaves your computer |
| **Summaries** | Written by Claude, using your own Claude subscription. No API key needed |
| **Cost** | Free app. Summaries use your existing Claude plan |

> **Privacy.** To write the summary, the app sends Claude the *transcript*, the *screenshots you took*, *your notes*
> and the *documents you added* (Claude only opens the ones it finds relevant). **Audio is never uploaded**, and reading
> text inside documents and pictures (OCR) happens on your Mac. If you turn off *Summarize automatically with Claude* in
> Settings, nothing is sent anywhere until you choose to summarize a meeting yourself.
> Please tell the people in your meeting that you are recording.

---

## Contents
1. [What you get](#what-you-get)
2. [Get started](#get-started)
3. [Everyday use](#everyday-use)
   · [The bar](#the-bar) · [Screenshots](#taking-screenshots) · [Notes](#taking-notes) · [Documents](#adding-documents) · [After you stop](#after-you-stop) · [Summary styles](#summary-styles)
4. [Where your files go](#where-your-files-go)
5. [Settings](#settings)
6. [Privacy and permissions](#privacy-and-permissions)
7. [Good to know](#good-to-know)
8. [Troubleshooting](#troubleshooting)
9. [For developers](#for-developers)
10. [Platform support](#platform-support)

### A few words used in this guide
- **Transcript**: the conversation written out as text, with times and who said what.
- **Summary**: the short write-up Claude makes from the transcript (decisions, action items, open questions…).
- **Summary style**: the set of instructions that tells Claude how to write the summary.
- **OCR** (*optical character recognition*): reading the text that is only a picture, such as a scanned page.
- **Shortcut** (or *hotkey*): a key combination that works in any app, such as **Ctrl + Option + R**.

---

## What you get

After every meeting, the app makes a folder containing:

- a **summary** (Word document and Markdown file) with the key decisions and action items,
- the **full transcript**, with timestamps and who said what ("Me", "Others", or "Others 1", "Others 2"… when it can tell people apart),
- the **screenshots** you took. Claude picks the useful ones (slides, charts, documents) and places them in the right spot,
- the **notes you typed** (`notes.txt`), which Claude also reads when writing the summary,
- the **documents you added**, copied into the folder and used by Claude as background.

```
record  ──▶  transcribe (on your Mac)  ──▶  summarize (Claude)  ──▶  Word + Markdown summary
 your mic     "Me" / "Others"                reads the transcript,      folder renamed with
 + the other  with timestamps                your notes, the useful     the meeting title
 side's audio                                screenshots and the
 + screenshots, notes, documents             documents you added
```

---

## Get started

### What you need
- A Mac with **Apple Silicon** (M1 or newer) running **macOS 13 or later**.
- **Claude Code** (the app or the command-line tool), signed in to your Claude account. It is only needed for summaries;
  without it you can still record and transcribe. If you have the Claude desktop app, Meeting Transcriber finds the copy
  bundled with it automatically.
- **Apple's command line tools**, a free developer add-on from Apple that the installer needs. Open **Terminal** (press
  ⌘ Space, type "Terminal", press Return) and run `xcode-select --install` once.
- **Python 3.11 or newer.**
- About **3 GB of free space** for the speech model (downloaded the first time you transcribe), plus room for your recordings.

### Install
Open Terminal in this project folder (in Finder, right-click the folder → *New Terminal at Folder*) and run:

```bash
./build_app.sh
```

This takes a few minutes the first time and creates **MeetingTranscriber.app** in this folder. Drag it to Applications or
the Dock, then double-click it. A small control bar appears on the edge of your screen.

- The first transcription downloads the speech model (about 3 GB, one time only). Telling people apart downloads two small
  models (about 35 MB) the first time it is used.
- The app runs its code from **this project folder**. Keep the folder where it is; if you move it, run `./build_app.sh`
  again from the new location.
- Rebuilding changes the app's signature, so macOS may ask for permissions again afterwards.

### Allow two permissions
The first time you open the app it explains what it needs, and macOS then asks for each permission:

<p align="center">
  <img src="docs/screenshots/permissions.png" alt="The amber permission button, its tooltip and the restart button" width="820">
</p>

| Permission | Why the app needs it |
|---|---|
| **Microphone** | to record your voice |
| **Screen & System Audio Recording** | to record the other participants' audio and to take screenshots (no video is recorded) |

- macOS asks only once. If you said no, an amber **⚠** button appears on the bar, just above **⋮ More**. Click it and the app
  asks again or opens the right page in System Settings.
- After you switch on *Screen & System Audio Recording*, that button becomes a **restart** button. Click it and the app
  restarts itself (macOS only applies this permission after a restart).
- Without microphone access the app won't record. Without screen access it records your microphone only and tells you.

---

## Everyday use

### The bar

Everything is controlled from a small bar on the edge of your screen. Hover a button to see its name and shortcut.

<p align="center">
  <img src="docs/screenshots/button-map.png" alt="Map of every button on the bar" width="640">
</p>

From top to bottom: the **record** button with a timer, the two **mute** buttons, four buttons for adding context while you
record (**screenshot, notes, add documents, documents list**; greyed out until you record), then **⋮ More**, **Hide** and **Quit**.

| I want to… | Do this |
|---|---|
| Start or stop a recording | The record button, or **Ctrl + Option + R**. Stopping also processes the meeting |
| Mute my microphone | The mic button, or **Ctrl + Option + M** |
| Mute the other people (system audio) | The speaker button, or **Ctrl + Option + N** |
| Take a screenshot | The camera button, or **Ctrl + Option + S**: the screen freezes and you choose the area. See [Taking screenshots](#taking-screenshots) |
| Take notes | The notes button, or **Ctrl + Option + T**. See [Taking notes](#taking-notes) |
| Add documents for context | The paperclip button, or **Ctrl + Option + D**. See [Adding documents](#adding-documents) |
| See or remove the added documents | The documents button (a page with a corner) |
| Pick how the summary is written | **⋮ → Summary style** |
| Summarize or re-summarize a past meeting | **⋮ → Summarize a meeting…**, then pick its folder |
| Transcribe a meeting again (new model, language or names) | **⋮ → Transcribe a meeting…**, then pick its folder |
| Change settings | **⋮ → Settings…** (⌘ ,) |
| Open the folder with all meetings | **⋮ → Open meetings folder** |

Shortcuts work in any app and need no extra permission. You can change them all in Settings → Shortcuts. If another app
already uses a combination, you get a notification.

**Tooltips.** Hovering a button shows what it does, beside the bar. While you are not recording, the screenshot, notes and
documents tooltips say "(while recording)" and show no shortcut. The notes tooltip shows how many lines you have, and the
documents tooltip how many documents are read. Tooltips float above every popup.

<p align="center">
  <img src="docs/screenshots/tooltips.png" alt="Every tooltip: idle, recording, and other states" width="800">
</p>

**Muting.** Works any time, even before you start recording, and carries over to the next recording. A muted button gets a
red line across it. Muted audio is replaced with silence before it is saved, so it cannot be recovered. The transcript marks
the stretch as `(Me muted)` / `(Me unmuted)`.

**Move it.** Drag the bar by its grip or any empty part. A dashed preview shows which screen edge it will snap to. It
remembers where you left it. Menus and popups open on the side facing the middle of the screen.

**Hide it.** The arrow near the bottom collapses the bar into a slim tab that still shows the timer, progress and mute
state. Click the tab to bring the bar back, or drag it elsewhere.

<p align="center">
  <img src="docs/screenshots/hidden.png" alt="The collapsed tab on the left and right edges" width="600">
</p>

**Private by design.** The bar never takes the keyboard from the app you are in (so it never interrupts a call), and by
default it is kept out of your screenshots and screen shares. See [Privacy and permissions](#privacy-and-permissions).

### Taking screenshots

Press the camera button (or **Ctrl + Option + S** from any app) while recording. The screen under your mouse **freezes**
into a still picture, dimmed a little, so what you see is exactly what gets saved, even if a video keeps playing. Then:

1. **Drag** to draw the area you want. Its edge is a solid white line and everything outside is dimmed. A small label shows its
   size in real screen pixels.
2. **Adjust it**: drag inside to move it, or pull a corner or any part of an edge to resize it (the pointer shows the matching
   resize arrow).
3. Click **✓** (bottom right, inside the frame) or press **Return** to take the screenshot; **✕** or **Esc** cancels and saves nothing.
   **⌘ A** selects the whole screen. Point at a button and it lights up (✕ turns red, ✓ a lighter blue) with a small label
   naming it and its key.

<p align="center">
  <img src="docs/screenshots/screenshot-select.png" alt="Choosing the screenshot area: frozen screen, white selection, cancel and screenshot buttons" width="900">
</p>

- Only the area you chose is saved, as `shots/shot_<seconds>.jpg`, scaled by *Settings → Recording → Screenshots → Screen
  resolution*. The screen flashes white, and the transcript gets a `(screenshot: …)` marker at the moment
  the screen froze.
- If the area is too small to hold the two buttons, they sit just outside it, below or above.
- With several screens, the one under your mouse freezes. The bar and its popups close while you choose, and the choosing
  window is not visible to people you share your screen with.
- If the recording stops while you are choosing, the choice is cancelled.

### Taking notes

Press the notes button (or **Ctrl + Option + T** from any app) while recording. A small panel opens beside the bar with a
plain text box, and you can start typing right away. The meeting window you are in stays in front, so you can keep an eye
on the call. Press the shortcut again, or the × on the panel, to close it. Nothing is lost when you close it.

<p align="center">
  <img src="docs/screenshots/notes.png" alt="The notes button and the notes panel" width="900">
</p>

- Notes are saved to `notes.txt` in the meeting's folder, a normal text file you can open anywhere. Saving happens when you
  stop typing for **1 second**, when you close the panel and when the recording stops (and when you quit). The panel shows
  *Saved* or *Not saved yet*.
- **Add time** (or **⌘ T** inside the panel) puts the recording time, like `[12:34]`, at the cursor, so a note can be matched
  to the moment in the transcript. A time and the space after it behave as one piece: one Backspace or Delete removes both.
- The usual editing shortcuts work: **⌘ A** (select all), **⌘ C**, **⌘ X**, **⌘ V** (pastes plain text only) and **⌘ Z** (undo).
- A blue dot on the notes button tells you this meeting already has notes.
- **Resize the panel** with the small grip in its top corner (on the side away from the bar), or by pulling its whole top
  edge or outer side edge. Drag up and outwards: it grows upwards, up to the top of the bar, and sideways up to 1.5 times its
  original width. It never gets smaller than the original size. The size is remembered when you move the bar to the other
  edge and the next time you open the app.

<p align="center">
  <img src="docs/screenshots/notes-resize.png" alt="The notes panel at its original size, enlarged, and with the bar on the other side" width="900">
</p>

- **Claude reads your notes** together with the transcript, with every summary style (also your own), and makes sure what you
  wrote down is covered: decisions, names, numbers, action items and questions. The summary does not label things as "from
  your notes"; it is one document. Summarizing a meeting again uses its notes too.
- Notes that read like to-dos for you ("try…", "TODO") become action items. The times you type can be a few minutes off, so
  Claude matches each note to the right moment by what was said.
- Don't want that? Turn off *Use my notes when summarizing* in Settings → Summaries. Notes are still saved.

<p align="center">
  <img src="docs/screenshots/notes-summary.png" alt="notes.txt in the meeting folder and how the processing panel mentions it" width="900">
</p>

### Adding documents

Press the **paperclip** button (or **Ctrl + Option + D**) while recording to give Claude background for the summary: an
agenda, slides, a report, a spreadsheet, a photo of a whiteboard.

<p align="center">
  <img src="docs/screenshots/documents-add.png" alt="The two document buttons and the file chooser with the OCR box" width="900">
</p>

- The bar's popups close and a normal Mac file chooser opens. Pick **one or more files**: PDF, Word (`.docx`), PowerPoint
  (`.pptx`), Excel (`.xlsx`), text or Markdown, and images (PNG, JPEG, HEIC, TIFF…). Old `.doc`, `.ppt` and `.xls` files are not supported.
- Under the list is **Read text in images (OCR)**. It starts ticked and remembers your last choice. It is how the app reads
  scanned pages, screenshots inside a document, or a photo of a whiteboard. It uses Apple's text recognition **on your Mac**,
  which reads Indonesian and English well.
- The files are **copied into the meeting folder straight away** (your originals are never changed or moved). Their text is
  read at once; OCR carries on in the background, one file at a time, so recording is not slowed down.
- A different file with the same name is kept too, as `name (2).pdf`. Adding the very same file twice is skipped (you get a
  notification). A meeting can have up to **30 documents**, each up to **100 MB**; OCR reads up to 300 pages or pictures per file.
- The **documents button** carries a badge: a **blue number** (for example `3`) when every document has been read, or an
  **amber `2/3`** (read so far, out of all) while some are still being read or could not be read.
- Click it for the **list**: each file's type, when it was added, and its status (*Reading… 40%*, *Ready*, *No text found*,
  *Couldn't read*). The **trash button** on a row deletes the document at once, with no questions: its copy, its text and its
  timestamp are removed from the meeting folder. The list opens beside the bar and never grows taller than the bar; with
  many documents it scrolls.

<p align="center">
  <img src="docs/screenshots/documents-list.png" alt="The documents list: reading, delete, empty, many documents, bar on the right" width="1000">
</p>

**How Claude uses them.** The documents are not pasted into the request. Claude gets a list with each document's name, type,
when it was added (for example `[03:12]`) and a short preview, and the transcript has a marker `(document added: …)` at the
moment each one was added. Claude then opens the ones that matter, by file path, to get names, terms, numbers and the agenda
right. It does not summarize the documents themselves. If you stop recording while files are still being read, the progress
panel shows a **Reading documents** step first and the summary waits for it. You can turn this off in Settings → Summaries.
Summarizing a meeting again uses its documents too.

<p align="center">
  <img src="docs/screenshots/documents-summary.png" alt="The documents folder, the processing panel and what Claude receives" width="900">
</p>

### After you stop

The record button turns into a progress ring and a panel lists the steps. *Transcribing* has parts of its own: your
microphone, the system audio (the other side), and, if you left it on in Settings, **Speakers**, where the app works out who
said what among the other people. Then come summarizing (it says what it uses, for example *with your notes (4 lines) and 3
documents*) and saving. When it is done you get **Summary** and **Folder** buttons. If something goes wrong, the panel says why.

<p align="center">
  <img src="docs/screenshots/processing.png" alt="Progress panel: transcribing, telling speakers apart, summarizing, done, failed" width="800">
</p>

You can start a new recording right away; a blue badge counts the meetings still being processed, and they are handled **one
at a time**. The panel also shows meetings you re-process from the menu (**Transcribe a meeting…** has no "Recorded" step).

The very first time (or if a half-finished download is found), the app first checks that the speech model is on your Mac and
downloads it if not. The panel shows a **Downloading model** line with how many MB have arrived, and transcribing starts when it
is done. If nothing arrives for 5 minutes you are told to check your internet connection.

<p align="center">
  <img src="docs/screenshots/processing-queue.png" alt="Two meetings in the queue, transcribing an existing meeting, summarizing an existing meeting, downloading the speech model" width="800">
</p>

### Summary styles

A **summary style** is simply a set of instructions for Claude. Two come built in:
- **default**: overview, decisions, action items, open questions, discussion points and what was on screen. Decisions say how
  firm they were (*decided*, *leaning*, *proposed*); action items are a table (owner, task, due, status, time) with relative
  dates like "Friday" turned into real dates. Names are taken from what is visible on screen, and anything unclear in the
  recording is marked instead of guessed.
- **action-items-only**: just the decisions and a table of action items.

You can edit a style, create your own, rename or delete your own, or reset a built-in one in **Settings → Summary styles**.
Summarizing a past meeting again with another style (or to retry after an error) works any time, even after restarting the
app. Your notes and documents are added for every style, so you never have to mention them in a style.

---

## Where your files go

Each meeting gets its own folder inside `~/Documents/Meetings` (changeable in Settings):

```
2026-10-07_1030 - Marketing Budget Review/
├── summary.docx            Word version of the summary, with the used screenshots embedded
├── summary-<style>.md      the summary as text, one file per style (e.g. summary-default.md)
├── transcript.md           the full transcript, with timestamps and screenshot markers
├── notes.txt               the notes you typed (only if you took notes)
├── documents/              the documents you added (only if you added some)
│   ├── Anggaran-Q4.pdf     the copy of each file
│   ├── documents.json      what was added, and when
│   └── extracted/          the text read from each file, for Claude
├── shots/                  every screenshot you took
├── recordings/             the audio (your mic and the other participants)
└── meta.json               timing information the app needs
```

- The folder name starts as the date and time. After the first summary, the **meeting title** is added. Summarizing again
  never renames it.
- Audio is first saved as WAV (safe even if the app crashes), then shrunk to about 30 MB per hour once the transcript is
  saved and the smaller file is checked.
- **Google Docs:** upload `summary.docx` to Google Drive and open it with Google Docs. Images are kept.
- Older meetings may have `transcript/transcript.md` and the audio files next to `meta.json`. They keep working.

---

## Settings

Open **Settings…** from the ⋮ menu (or ⌘ ,). Everything is checked before it is saved. Shortcuts and the bar update
immediately; other changes apply from the next recording.

### Recording

<p align="center">
  <img src="docs/screenshots/settings_recording.png" alt="Settings: Recording tab" width="560">
</p>

| Setting | What it does | Default |
|---|---|---|
| Meetings → Folder | Where meetings are saved | `~/Documents/Meetings` |
| Meetings → Audio files | AAC (smallest, ~30 MB/hour), FLAC (lossless, ~110 MB/hour) or keep the WAV (~230 MB/hour) | AAC |
| Microphone and system audio → When the app starts | Start with the microphone and/or system audio muted | both off |
| Screenshots → Screen resolution | How sharp the saved area is: a share of your screen's real resolution, shown as pixel sizes for your main display | 50% |
| Floating bar → Screen sharing | Show the bar in screenshots and screen shares | off |

### Transcription

<p align="center">
  <img src="docs/screenshots/settings_transcription.png" alt="Settings: Transcription tab" width="560">
</p>

| Setting | What it does | Default |
|---|---|---|
| Speech recognition → Language | Detects Indonesian or English as people speak, or forces one | automatic |
| Speech recognition → Model | "Large v3" is the most accurate; "turbo" is about 4× faster but slightly less accurate | large v3 |
| Speech recognition → Names and terms | Names and jargon to spell correctly, e.g. *Budi, Dina, Tokopedia*. Also given to Claude so the summary uses the same spellings | empty |
| Speakers → Other people | Numbers the other people on the call (Others 1, Others 2…) by voice. Runs on your Mac | on |
| Speakers → Number of people | Work it out automatically, or set it if the app merges or splits someone | automatic |
| Labels | The names shown in the transcript for you and for everyone else (must differ) | Me / Others |

### Summaries

<p align="center">
  <img src="docs/screenshots/settings_summaries.png" alt="Settings: Summaries tab" width="560">
</p>

| Setting | What it does | Default |
|---|---|---|
| After a recording → Summary | Summarize automatically with Claude. If off, the app only transcribes | on |
| After a recording → Word document | Also save each summary as `.docx` | on |
| After a recording → Meeting notes | Send the notes you took to Claude with the transcript | on |
| After a recording → Documents | Give Claude the documents you added (a list with previews; it opens the ones that matter) | on |
| After a recording → Reading images | Whether the file chooser's *Read text in images (OCR)* box starts ticked | on |
| Style → Default style | The style used for summaries | default |
| Claude → CLI and model | Leave empty to find Claude Code and use your usual model | empty |

### Summary styles and Shortcuts

**Summary styles** is where you edit, create, rename or delete styles (a built-in style can be reset; your own can be renamed
or deleted). **Shortcuts** holds the six global shortcuts (record, mute microphone, mute system audio, screenshot, notes, add
documents) and the one used inside the notes panel (insert the time). Click a box and press the combination you want; it
needs at least one modifier key, and the global ones must all differ.

<p align="center">
  <img src="docs/screenshots/settings_styles.png" alt="Settings: Summary styles tab, a built-in style" width="400">
  <img src="docs/screenshots/settings_styles_custom.png" alt="Settings: your own style, with the New style dialog" width="400">
</p>
<p align="center">
  <img src="docs/screenshots/settings_shortcuts.png" alt="Settings: Shortcuts tab" width="560">
</p>

**Reset to defaults…** restores the original settings and keeps your old file as `config.toml.bak`.

Settings are stored in `~/Library/Application Support/MeetingTranscriber/config.toml`. You can edit that file by hand too
(see [`config.example.toml`](config.example.toml)); the app keeps your comments when it saves. If the file has a typo, you get
a notification and the previous settings stay in effect.

---

## Privacy and permissions

- **What leaves your Mac:** only what Claude needs for the summary: the transcript, the screenshots you took, your notes
  and the documents Claude opens. Audio never does. Nothing is sent if automatic summaries are off, until you ask for one.
- **The bar in screenshots and screen shares.** By default the bar is hidden from screenshots, screen recordings and screen
  shares, and from the app's own screenshots, but you still see and use it. To show it there, turn on *Settings → Recording →
  Floating bar → Show the bar in screenshots and screen shares*. It applies right away.

<p align="center">
  <img src="docs/screenshots/capture.png" alt="The screen-sharing setting, off and on, and what others see" width="620">
</p>

- **Dialogs and notifications.** The app asks before it does something you can't easily undo, and tells you about results
  with normal macOS notifications.

<p align="center">
  <img src="docs/screenshots/dialogs.png" alt="The welcome dialog, the quit questions, a refused permission, and notifications" width="900">
</p>

---

## Good to know

- **Very long meetings:** the whole transcript and all screenshots go to Claude in one request. The app sets no limit of its
  own (only a 30-minute timeout), but Claude's limits apply. A very long meeting or dozens of screenshots can make the summary
  fail. Your transcript and audio are already saved, so retry with **Summarize a meeting…**.
- **Microphone:** the app uses whichever microphone macOS has selected. If you switch mid-meeting (AirPods connect, a mic is
  unplugged), it follows the new default within a few seconds and fills the gap with silence. With no microphone available it keeps retrying.
- **Other participants:** the app records everything your Mac plays, from all apps mixed, whatever speakers or headphones you use.
- **Telling people apart** is by voice only, so it can't know names and it makes mistakes: two similar voices may be merged,
  one person with a varying tone may be split, and a short remark can land on the wrong person. If the count looks wrong, set
  *Number of people* under Settings → Transcription → Speakers.
- **Echo:** without headphones your mic also hears the speakers. The app drops most of this, but headphones give cleaner results.
- **Accuracy:** language is detected for each burst of speech, so a switch mid-sentence can garble a few words, and very
  short or quiet words are sometimes misheard. Add names to *Names and terms* to help; words from the meeting folder's title
  (after the " - ") are added automatically.
- **Made-up text:** Whisper sometimes invents "Terima kasih." or "Thank you." out of silence. Such segments are dropped, so a
  real, very quiet one-word "thank you" could occasionally go missing.
- **Reading pictures (OCR)** works well on clean scans and screenshots, in Indonesian and English. It cannot read very
  blurry or very low-resolution pictures. Documents that are only pictures need the OCR box ticked.
- **Stopping** can make the bar pause for a few seconds while the audio is closed.
- **Not yet tested** on a long real call with live system audio, hotkeys and microphone together.

---

## Troubleshooting

| Problem | What to do |
|---|---|
| "Downloading model" stays at the same number of MB | The app stops after 5 minutes without progress and says so. Check your internet connection, delete the model folder it names (under `~/.cache/huggingface`), and transcribe again |
| The bar does not appear after launch | Read `~/Library/Logs/MeetingTranscriber.log` |
| "Claude Code CLI not found" | Install Claude Code, or set its path under Settings → Summaries → Claude |
| The notes, screenshot or document buttons are greyed out | They only work while recording. Start a recording first |
| A document says *No text found* | It is probably scanned or all pictures: add it again with *Read text in images (OCR)* ticked. Very blurry or low-resolution pictures cannot be read |
| A document was not added | A notification says why: already added, too big (over 100 MB), not a supported kind of file, or the limit of 30 documents |
| Screenshot error about Screen Recording | Allow the app in System Settings → Privacy & Security, then restart it |
| A meeting failed to process or summarize | **⋮ → Summarize a meeting…**, or use the command line below |
| The summary fails on a very long meeting | See "Very long meetings" above |
| Other participants are silent in the transcript | The transcript starts with a ⚠ warning when a track was silent (stretches you muted yourself don't count). Check the Screen & System Audio Recording permission |

---

## For developers

### Dependencies

| Part | What it uses |
|---|---|
| Language | Python 3.11+, plus Swift (command line tools) for two small helpers |
| Transcription | [`mlx-whisper`](https://github.com/ml-explore/mlx-examples) (Apple Silicon), `numpy` |
| Speaker separation | `sherpa-onnx` (pyannote segmentation + 3D-Speaker embeddings, on CPU) |
| Documents and OCR | `pyobjc-framework-Vision` (Apple Vision text recognition), PDFKit through `pyobjc-framework-Quartz` (PDF text and page rendering), `python-pptx`, `openpyxl`; Word files go through pandoc (`pypandoc_binary`) |
| Audio capture | `sounddevice` (mic), the `sck-audio` Swift helper (ScreenCaptureKit, system audio) |
| macOS integration | `pyobjc-framework-Quartz`, `-WebKit`, `-AVFoundation`, `-Vision`; `Pillow` for screenshots |
| Word export | `pypandoc_binary` (bundles pandoc) |
| MCP server | `mcp` |
| Summaries | the Claude Code CLI (`claude -p`), not a Python package |

Python packages are listed in [`requirements.txt`](requirements.txt).

The Whisper model is fetched from Hugging Face the first time. `transcribe.ensure_model` checks that the weights file is present and not empty (an
interrupted download used to make transcribing hang), downloads it with progress shown in the panel, and gives up after 5 minutes
without new bytes. Downloads use plain HTTP (`HF_HUB_DISABLE_XET=1`, set in `mt/log.py`) because they resume reliably.

### Build and test
`build_app.sh` builds the `sck-audio` Swift helper, creates `.venv` and installs `requirements.txt`, compiles a small native
launcher, assembles `MeetingTranscriber.app`, ad-hoc code-signs it and registers the icon. The app is a thin launcher that
starts `.venv/bin/python -m mt.app` from the project folder, so macOS attributes permissions to the app instead of to Terminal
or a bare Python binary. Logs go to `~/Library/Logs/MeetingTranscriber.log` (every line has a timestamp, level and source; the file rotates to `.log.1` at 5 MB).

Run the tests with `.venv/bin/python -m unittest discover tests`. (The OCR tests need macOS and skip themselves elsewhere.)

### How the interface works
- The app is an `NSApplication` with no Dock icon and no menu bar item. Everything is the **floating bar** (`mt/overlay.py`):
  a non-activating `NSPanel` hosting `mt/ui/overlay.html` in a `WKWebView`. The tooltip, More menu, processing panel and
  documents list are four more panels (`mt/ui/popup.html`), and the notes panel (`mt/ui/notes.html`) is the one that can take
  the keyboard (key-capable but non-activating, so the meeting app stays in front).
- Pages talk to Python through a `WKScriptMessageHandler` (no web server, no open port). The app pushes state to the pages as
  JSON. Because these panels are never the active window, WebKit does not report the mouse, so Python reads the pointer
  position (global and local mouse monitors, plus a 60 Hz poll while it is over a panel), tells the pages what is hovered and
  sets the pointing-hand cursor itself. Lists are updated in place, never redrawn under the pointer, so clicks are not lost.
- Panels are hidden from screenshots with `NSWindowSharingNone` (the *Show the bar in screenshots* setting rebuilds them).
  Tooltips float one level above popups. The bar's position, hidden state and the notes panel size are kept in `overlay.json`
  next to `config.toml`.
- Because the app has no menu bar, it installs a small hidden Edit menu at start-up so ⌘A/C/V/X/Z reach text boxes.
- Global hotkeys use Carbon `RegisterEventHotKey`, which needs no Accessibility or Input Monitoring permission.

### How recording works
- **Microphone:** `sounddevice` input stream on the default input device, 16 kHz mono, written to `recordings/mic.wav`.
  A watchdog reopens the stream when the default input changes, disappears or stops delivering audio (gap padded with silence).
- **System audio:** the `sck-audio` helper uses ScreenCaptureKit on the first display (all system audio, excluding the app
  itself) and writes `recordings/system.wav`. It reports the start time of the first buffer so tracks can be aligned.
- **Screenshots:** the display under the mouse is captured with Quartz at its real pixel resolution at the moment you press the
  button (`mt/screenshot.py`), shown full screen in a borderless window (`AreaSelector`) where you choose the area, and only the
  chosen crop is scaled by `screenshot_scale` and saved as `shots/shot_<seconds>.jpg` (JPEG quality 80); the number is the time
  the screen froze. The window is hidden from screen sharing (`NSWindowSharingNone`), sits above the bar, and hands the keyboard
  back to the meeting app when it closes. A white flash confirms each added screenshot.
- **`meta.json`** stores the start time, duration and the offsets that align mic, system audio and screenshots, plus
  `mic_muted` / `system_muted` (muted spans, in seconds from the start) and `mic_device_events` when the microphone changed
  mid-recording (`changed` / `stalled`, `reopened` with the new device name).
- **Muting** replaces audio with silence at the source (zeros in the mic callback; `--muted` flag plus `MUTE`/`UNMUTE`
  commands on the `sck-audio` helper's stdin). Files keep running, so they stay aligned with the clock and screenshots.
- **Notes:** the page is a `contenteditable` box; a timestamp is a non-editable chip written out as `[mm:ss]` (`[h:mm:ss]` from
  the first hour on). The page sends plain text after 1 s without typing (`SAVE_AFTER` in `notes.html`) and on close, stop and quit;
  the app writes `notes.txt` atomically (`mt/notes.py`). The panel is at least its natural size, at most 1.5× as wide, and as
  tall as the top of the bar allows; its bottom edge stays level with the notes button and it opens on the screen side of the bar.

### How transcription works
Each track is cut into bursts of speech and each burst is transcribed on its own with mlx-whisper, with exact timestamps;
silence is skipped. Mic bursts whose loudness envelope mirrors the system audio (speaker bleed) are dropped before and after
transcription. Language is detected per burst (a pause of 1.5 s or more starts a new one), limited to `allowed_languages`;
very short bursts reuse the previous burst's language. Roughly 30× faster than real time on Apple Silicon.

If speaker separation is on, the system track is also run through sherpa-onnx (`mt/diarize.py`). It is the third part of the
*Transcribing* step in the progress panel: `diarize.py` reports progress through helpers in `mt/progress.py`
(`diarizing(percent)`, `MATCHING_VOICES`, `downloading_model(n)`, `speakers_found(...)`), which `mt/jobs.py` turns into the
"Speakers" line (listed only when `diarize` is on; *Skipped* if the models could not be loaded). Pyannote segmentation cuts
the track into speech turns, grouped by voice (3D-Speaker embeddings, CPU, ~25× real time). The groups are deliberately too
fine; each is averaged into one voiceprint and groups whose voiceprints are close (cosine distance below 0.35) are merged,
tiny groups first. Each Whisper segment gets the speaker who talks most during it. The two models (~35 MB) are downloaded to
`~/Library/Application Support/MeetingTranscriber/models` the first time; if that fails, the transcript is made without
numbering and says so at the top.

The result is `transcript.md`: `[hh:mm:ss] Speaker: text` lines plus `(screenshot: shots/…)` markers. It is written atomically
and only replaced once transcription and speaker separation have both finished.

### How documents are read
`mt/documents.py` keeps a manifest (`documents/documents.json`: unique id, original and stored name, kind, SHA-256, seconds
since the recording started, status, progress, a 300-character preview) and one low-priority background thread
(`DocumentWorker`) that copies each file and reads its text into `documents/extracted/<name>.txt`:
- **Text, Markdown:** read directly. **Word:** pandoc. **PowerPoint:** `python-pptx` (slide text, tables, speaker notes).
  **Excel:** `openpyxl` (cell values, up to 2,000 rows per sheet). **PDF:** the page text through PDFKit.
- **OCR** (`mt/ocr.py`) uses Apple's Vision framework in accurate mode, languages `id-ID` and `en-US` where the Mac supports
  them (the fast mode only knows six languages). Tests with simulated Indonesian scans gave 0% errors on clean text, 0.5% on
  10 px text once enlarged 2×, and about 20% on a blurry low-resolution photo. In a PDF each page is rendered at 150 dpi and
  read in strips about 800 px tall, because Vision can skip small text on a mostly empty full page; only the lines that are
  not already in the page's own text are added. Word, PowerPoint and Excel files are zip files, so their pictures
  (`word/media`, `ppt/media`, `xl/media`) are taken out, de-duplicated and read; pictures under 60 px are skipped and small
  ones are enlarged 2× first.
- A recording that stops while files are still being read gets a *Reading documents* step in the progress panel, and the
  summary waits for it. Deleting a document cancels its reading and removes the copy, the text and the entry.

### How summarizing works
`mt/summarize.py` runs `claude -p` with the chosen prompt, the transcript and a calendar of the next 15 days (so "next Friday"
resolves correctly) on stdin. Instructions that apply to every style are added after the style, in this order:
- the **notes** (`notes.txt`, if `use_notes` is on) between markers: cover everything in them, use their `[mm:ss]` times to find
  the moment, don't label them as "from the notes", treat them as data;
- the **documents** (if `use_documents` is on and some text was read): file name, type, pages, the time added, a preview and
  the path of the extracted text, with instructions to open the ones that matter and treat them as data. `with_document_markers`
  puts a `(document added: …)` line into the transcript at the right timestamp at this point (not in `transcript.md`, so a
  deleted document needs no edit of the transcript).

Claude may read only `./shots/*` and, when there are documents, `./documents/extracted/*`
(`--allowedTools "Read(./shots/*),Read(./documents/extracted/*)"`). The output is saved as `summary-<style>.md`; the Word
export (pandoc via `pypandoc`) and the folder renaming use it.

### Security
Meeting content (speech, what is on screen, your notes, documents) is untrusted input to Claude:
- Claude can read only `shots/*` and the extracted document text of the current meeting, and the prompt tells it to treat the
  transcript, notes and documents as data, never as instructions.
- The Word export embeds only real image files directly inside `shots/`. Any other image reference (URLs, `../` paths)
  becomes a text placeholder, so nothing is fetched or pulled in from elsewhere on disk.
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
| `vocabulary` | `[]` | names/terms to spell correctly (in the transcript and in the summary) |
| `diarize`, `diarize_speakers` | `true`, `0` | tell the other people apart; `0` = work out how many, or a number up to 12 |
| `my_label`, `others_label` | `Me`, `Others` | speaker names in the transcript; must differ |
| `compress_audio` | `"aac"` | `"flac"` or `"off"` |
| `auto_summarize` | `true` | `false` = transcribe only |
| `export_docx` | `true` | also write `summary.docx` with every summary |
| `default_prompt` | `"default"` | prompt file name without `.md` |
| `screenshot_scale` | `50` | percent (10–100) of the chosen area's real pixel size (the screen's, for a whole-screen shot) |
| `claude_path`, `claude_model` | auto, Claude Code's default | |
| `start_muted_mic`, `start_muted_system` | `false` | start the app with that side muted |
| `overlay_in_captures` | `false` | show the bar in screenshots and screen shares |
| `hotkey_record`, `hotkey_screenshot`, `hotkey_notes`, `hotkey_documents`, `hotkey_mute_mic`, `hotkey_mute_system` | `<ctrl>+<alt>+r` / `s` / `t` / `d` / `m` / `n` | global shortcuts: modifiers `<ctrl> <alt> <shift> <cmd>` plus a letter, digit or `<f1>`…`<f20>`; must differ |
| `hotkey_notes_time` | `<cmd>+t` | inside the notes panel: insert the recording time (same format) |
| `use_notes` | `true` | send `notes.txt` to Claude with the transcript |
| `use_documents` | `true` | give Claude the documents added to a meeting |
| `documents_ocr` | `true` | whether the file chooser's OCR box starts ticked |

Prompts live in `~/Library/Application Support/MeetingTranscriber/prompts/` (copied from `prompts/` on first run).

### Command line (no app)
```bash
.venv/bin/python -m mt.cli record                                      # Enter = stop, s+Enter = screenshot
.venv/bin/python -m mt.cli process <meeting-dir>                       # transcribe + summarize
.venv/bin/python -m mt.cli summarize <meeting-dir> action-items-only   # (re-)summarize, optional style name
```

### Optional: ask Claude Desktop about past meetings (MCP)
Add this to `claude_desktop_config.json`, then ask things like "what did we decide on Tuesday?":
```json
"meetings": {"command": "/ABS/PATH/meeting-transcriber/.venv/bin/python",
             "args": ["-m", "mt.mcp_server"], "cwd": "/ABS/PATH/meeting-transcriber"}
```
The server reads the meetings folder from your settings when it starts. Tools: `list_meetings`, `get_transcript`,
`get_summary` (latest, or a given style), `get_screenshot`, `list_summary_prompts`.

### Project layout
| Path | Purpose |
|---|---|
| `mt/app.py` | the app: actions, permission flow, dialogs, glue to the bar |
| `mt/overlay.py`, `mt/ui/overlay.html`, `mt/ui/popup.html` | the floating bar, its tooltips, menu, processing panel and documents list |
| `mt/notes.py`, `mt/ui/notes.html` | meeting notes: `notes.txt` storage, and the notes panel (a text box with timestamp chips) |
| `mt/documents.py`, `mt/ocr.py` | meeting documents: copying, the manifest, reading each file type, the background reader, and Apple Vision OCR |
| `mt/jobs.py`, `mt/progress.py` | progress of the meetings being processed |
| `mt/recorder.py`, `mt/screenshot.py`, `mt/flash.py` | recording, choosing and saving screenshots, screenshot flash |
| `mt/transcribe.py`, `mt/audio.py` | Whisper transcription (including `ensure_model`, which checks the model is complete and downloads it with progress), echo handling, audio compression |
| `mt/diarize.py` | telling the other people apart (sherpa-onnx) |
| `mt/pipeline.py`, `mt/summarize.py`, `mt/docx_export.py` | processing flow, Claude summary, Word export |
| `mt/config.py`, `mt/settings_api.py`, `mt/settings_window.py`, `mt/ui/settings.html` | settings storage, validation and window |
| `mt/hotkeys.py`, `mt/permissions.py` | global hotkeys, macOS permissions |
| `mt/cli.py`, `mt/mcp_server.py` | command line, MCP server |
| `sck-audio/`, `launcher/` | Swift helpers: system-audio capture, app launcher |
| `prompts/`, `assets/` | built-in summary styles, icons |
| `docs/screenshots/` | the design mockups used in this README |
| `tests/` | unit tests |

---

## Platform support

The app is macOS-only today. A Windows version is planned; the macOS-specific pieces are listed so the work can be split up:

| Area | macOS (today) | Windows (planned) |
|---|---|---|
| System audio | `sck-audio` (Swift, ScreenCaptureKit) | WASAPI loopback |
| Transcription | `mlx-whisper` | `faster-whisper` |
| Screenshots | Quartz | `mss` / Pillow |
| Text recognition (OCR) | Apple Vision | Windows OCR or Tesseract |
| Floating bar | `NSPanel` + WKWebView (PyObjC) | a topmost frameless window |
| Settings window | WKWebView (PyObjC) | `pywebview` (WebView2), same `settings.html` |
| Hotkeys | Carbon `RegisterEventHotKey` | Win32 `RegisterHotKey` |
| Audio compression | `afconvert` | `ffmpeg` |
| Packaging | `build_app.sh`, `.app` bundle | PyInstaller or similar, plus an installer |

Already portable: the processing pipeline, summarizing, the Word export, settings validation, prompts, the CLI and the MCP
server. Contributions toward Windows support are welcome.

Summaries come only from the **Claude Code CLI**, using your own Claude subscription. There is no API key and no other AI
provider. Transcription does not use Claude and works without it.
