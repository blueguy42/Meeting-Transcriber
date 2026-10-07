"""Optional MCP server so Claude Desktop can browse meetings, e.g. "what did we decide last Tuesday?"

Claude Desktop config:
  "meetings": {"command": "<project>/.venv/bin/python", "args": ["-m", "mt.mcp_server"],
               "cwd": "<project>"}
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from . import config
from .transcribe import transcript_file

mcp = FastMCP("meetings")
CFG = config.load()
ROOT: Path = CFG["meetings_dir"]


def _summaries(d: Path) -> list[Path]:
    """summary-<style>.md files, newest first."""
    return sorted(d.glob("summary-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)


def _dir(meeting_id: str) -> Path:
    d = (ROOT / meeting_id).resolve()
    if ROOT.resolve() not in d.parents or not d.is_dir():
        raise ValueError(f"unknown meeting {meeting_id!r}")
    return d


@mcp.tool()
def list_meetings() -> list[dict]:
    """List recorded meetings, newest first, with whether a transcript/summary exists."""
    out = []
    for d in sorted((p for p in ROOT.iterdir() if p.is_dir()) if ROOT.exists() else [], reverse=True):
        out.append(
            {
                "id": d.name,
                "transcript": transcript_file(d).exists(),
                "summary": bool(_summaries(d)),
                "screenshots": len(list((d / "shots").glob("*.jpg"))),
            }
        )
    return out


@mcp.tool()
def get_transcript(meeting_id: str) -> str:
    """Full timestamped transcript (Me = mic, Others = system audio, with screenshot markers)."""
    return transcript_file(_dir(meeting_id)).read_text()


@mcp.tool()
def get_summary(meeting_id: str, style: str | None = None) -> str:
    """A generated summary for a meeting: the latest one, or the one in a given style (e.g. "default")."""
    d = _dir(meeting_id)
    if style:
        p = d / f"summary-{Path(style).name}.md"
    else:
        found = _summaries(d)
        if not found:
            raise ValueError("no summary yet")
        p = found[0]
    return p.read_text()


@mcp.tool()
def get_screenshot(meeting_id: str, name: str) -> Image:
    """A screenshot by file name, e.g. shot_00754.jpg (names appear in the transcript)."""
    p = (_dir(meeting_id) / "shots" / Path(name).name)
    return Image(path=str(p))


@mcp.tool()
def list_summary_prompts() -> dict[str, str]:
    """The user's editable summary templates (name -> text). Use one to re-summarize a meeting."""
    return {p.stem: p.read_text() for p in config.prompts_dir().glob("*.md")}


if __name__ == "__main__":
    mcp.run()
