"""CLI for testing / re-running without the app.

python -m meeting_transcriber.cli record            # Enter to stop, 's'+Enter for a screenshot
python -m meeting_transcriber.cli process <dir>     # transcribe + summarize an existing meeting folder
python -m meeting_transcriber.cli summarize <dir> [prompt]   # re-summarize with another prompt
"""

import sys

from . import log
from .settings import config

log.setup()
from .meetings.meeting import Meeting
from .processing.pipeline import finish, process
from .recording import Recorder
from .summary.summarize import summarize_meeting


def main():
    config.ensure_settings()
    cfg = config.load()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "record":
        r = Recorder(cfg)
        print("Recording to", r.start().folder, "— Enter = stop, s+Enter = screenshot")
        while True:
            line = sys.stdin.readline().strip()
            if line == "s":
                print("shot:", r.screenshot())
            else:
                break
        meeting = r.stop()
        print("Processing…")
        print("Done:", process(meeting, cfg)[1])
    elif cmd == "process":
        print("Done:", process(Meeting(sys.argv[2]), cfg)[1])
    elif cmd == "summarize":
        meeting = Meeting(sys.argv[2])
        print("Done:", finish(meeting, cfg, summarize_meeting(meeting, cfg, sys.argv[3] if len(sys.argv) > 3 else None))[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
