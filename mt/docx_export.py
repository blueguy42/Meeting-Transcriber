"""Export the summary as a Word document with the used screenshots embedded at full quality."""

import json
import re
import shutil
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pypandoc

# No yaml_metadata_block (a stray leading '---' must stay a rule), no implicit_figures (no captions).
FORMAT = "markdown+pipe_tables-implicit_figures-yaml_metadata_block"
IMAGE_TYPES = {".jpg", ".jpeg", ".png"}


def _allowed_image(meeting_dir: Path, url: str) -> bool:
    """Only files directly inside <meeting>/shots. Summaries are written by an LLM that has read
    meeting content, so a planted '![x](http://...)' or '![x](shots/../../secret.jpg)' must not make
    pandoc fetch a URL or embed a file from elsewhere on disk."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) or url.startswith(("/", "~")):
        return False
    p = (meeting_dir / unquote(url)).resolve()
    return p.parent == (meeting_dir / "shots").resolve() and p.suffix.lower() in IMAGE_TYPES and p.is_file()


def _placeholder(alt: list) -> dict:
    return {"t": "Emph", "c": [{"t": "Str", "c": "[Screenshot:"}, {"t": "Space"}, *alt, {"t": "Str", "c": "]"}]}


def _sanitize(node, meeting_dir: Path):
    """Walk pandoc's document tree (covers inline, reference-style and every other image syntax)."""
    if isinstance(node, list):
        return [
            _placeholder(n["c"][1]) if isinstance(n, dict) and n.get("t") == "Image" and not _allowed_image(meeting_dir, n["c"][2][0])
            else _sanitize(n, meeting_dir)
            for n in node
        ]
    if isinstance(node, dict):
        return {k: _sanitize(v, meeting_dir) for k, v in node.items()}
    return node


def export_docx(meeting_dir: Path, summary_md: Path) -> Path:
    text = summary_md.read_text()
    # Pandoc needs a blank line before a list that directly follows a paragraph.
    text = re.sub(r"(?m)^(?![ \t]*([-*+]|\d+\.)\s)(\S.*)\n(?=[ \t]*([-*+]|\d+\.)\s)", r"\2\n\n", text)
    tree = json.loads(pypandoc.convert_text(text, "json", format=FORMAT))
    tree["blocks"] = _sanitize(tree["blocks"], meeting_dir)
    out = meeting_dir / "summary.docx"
    pypandoc.convert_text(
        json.dumps(tree), "docx", format="json", outputfile=str(out), extra_args=[f"--resource-path={meeting_dir}"]
    )
    _add_table_borders(out)
    return out


_BORDERS = (
    "<w:tblBorders>"
    + "".join(f'<w:{e} w:val="single" w:sz="4" w:space="0" w:color="999999"/>' for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + "</w:tblBorders>"
)


def _add_table_borders(docx: Path) -> None:
    """Pandoc's default table style has no borders; add thin grey ones."""
    tmp = docx.with_suffix(".tmp")
    with zipfile.ZipFile(docx) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = data.decode().replace("<w:tblLook", _BORDERS + "<w:tblLook").encode()
            zout.writestr(item, data)
    shutil.move(tmp, docx)
