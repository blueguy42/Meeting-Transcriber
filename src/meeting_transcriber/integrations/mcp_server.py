"""Optional MCP server so Claude Desktop can browse meetings, e.g. "what did we decide last Tuesday?"

Claude Desktop config:
  "meetings": {"command": "<project>/.venv/bin/python", "args": ["-m", "meeting_transcriber.integrations.mcp_server"],
               "cwd": "<project>"}
"""

from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from ..settings import config
from ..meetings.meeting import Meeting

mcp = MCPServer("meetings")
CFG = config.load()
ROOT: Path = CFG.meetings_dir


def _meeting(meeting_id: str) -> Meeting:
    d = (ROOT / meeting_id).resolve()
    if ROOT.resolve() not in d.parents or not d.is_dir():
        raise ValueError(f"unknown meeting {meeting_id!r}")
    return Meeting(d)


@mcp.tool()
def list_meetings() -> list[dict]:
    """List recorded meetings, newest first, with whether a transcript/summary exists."""
    out = []
    for d in sorted((p for p in ROOT.iterdir() if p.is_dir()) if ROOT.exists() else [], reverse=True):
        meeting = Meeting(d)
        out.append(
            {
                "id": meeting.name,
                "transcript": meeting.has_transcript,
                "summary": bool(meeting.summaries()),
                "screenshots": len(meeting.shot_files()),
            }
        )
    return out


@mcp.tool()
def get_transcript(meeting_id: str) -> str:
    """Full timestamped transcript (Me = mic, Others = system audio, with screenshot markers)."""
    return _meeting(meeting_id).transcript_file.read_text()


@mcp.tool()
def get_summary(meeting_id: str, style: str | None = None) -> str:
    """A generated summary for a meeting: the latest one, or the one in a given style (e.g. "default")."""
    meeting = _meeting(meeting_id)
    if style:
        p = meeting.summary_file(style)
    else:
        found = meeting.summaries()
        if not found:
            raise ValueError("no summary yet")
        p = found[0]
    return p.read_text()


@mcp.tool()
def get_screenshot(meeting_id: str, name: str) -> Image:
    """A screenshot by file name, e.g. shot_00754.jpg (names appear in the transcript)."""
    p = _meeting(meeting_id).shots_dir / Path(name).name
    return Image(path=str(p))


@mcp.tool()
def list_summary_prompts() -> dict[str, str]:
    """The user's editable summary templates (name -> text). Use one to re-summarize a meeting."""
    return {p.stem: p.read_text() for p in config.prompts_dir().glob("*.md")}


if __name__ == "__main__":
    mcp.run()
