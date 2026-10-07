"""Builds assets/AppIcon.icns from assets/icon-source.jpg.

macOS 26 masks app icons itself (and puts older pre-rounded icons on a gray plate), so the icon is a
full-bleed opaque square with no baked-in corners or shadow.
Run:  .venv/bin/python assets/make_icon.py   (needs macOS `iconutil`)"""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
CANVAS = 1024
INSET = 14  # the source has white pixels in its rounded corners; crop them off


def build_master() -> Image.Image:
    src = Image.open(HERE / "icon-source.jpg").convert("RGB")
    return src.crop((INSET, INSET, src.width - INSET, src.height - INSET)).resize((CANVAS, CANVAS), Image.LANCZOS)


def build_menubar(master: Image.Image, size: int = 80) -> Image.Image:
    """The same artwork as a small rounded square for the menu bar (shown ~20 pt tall)."""
    big = master.resize((size * 4, size * 4), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", big.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big.width - 1, big.height - 1), int(big.width * 0.22), fill=255)
    big.putalpha(mask)
    return big.resize((size, size), Image.LANCZOS)


def main():
    master = build_master()
    master.save(HERE / "AppIcon-1024.png")
    build_menubar(master).save(HERE / "menubar.png")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            master.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            master.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "AppIcon.icns")], check=True)
    print("wrote", HERE / "AppIcon.icns")


if __name__ == "__main__":
    main()
