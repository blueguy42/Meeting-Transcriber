"""CLI for testing / re-running without the menu bar app.

python -m mt.cli record            # Enter to stop, 's'+Enter for a screenshot
python -m mt.cli process <dir>     # transcribe + summarize an existing meeting folder
python -m mt.cli summarize <dir> [prompt]   # re-summarize with another prompt
"""

import sys
from pathlib import Path

from . import config
from .pipeline import finish, process
from .recorder import Recorder
from .summarize import summarize_meeting


def main():
    config.ensure_settings()
    cfg = config.load()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "record":
        r = Recorder(cfg)
        print("Recording to", r.start(), "— Enter = stop, s+Enter = screenshot")
        while True:
            line = sys.stdin.readline().strip()
            if line == "s":
                print("shot:", r.screenshot())
            else:
                break
        d = r.stop()
        print("Processing…")
        print("Done:", process(d, cfg)[1])
    elif cmd == "process":
        print("Done:", process(Path(sys.argv[2]), cfg)[1])
    elif cmd == "summarize":
        d = Path(sys.argv[2])
        print("Done:", finish(d, cfg, summarize_meeting(d, cfg, sys.argv[3] if len(sys.argv) > 3 else None))[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
