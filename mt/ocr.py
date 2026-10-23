"""Reading text out of pictures with Apple's Vision framework (on this Mac, nothing is uploaded).

What the tests in docs showed (Indonesian and English, simulated scans):
  * Vision's accurate mode reads Indonesian (id-ID) well; the "fast" mode only knows six languages, so it is never used.
  * Small text is read far better when the picture is enlarged 2x first (10 px text: 28% errors -> 0.5%).
  * A whole page with small text on lots of white can be skipped entirely, so tall images are read in horizontal
    strips (about 800 px tall at 150 dpi) and lines that appear in two overlapping strips are kept once.
"""

import re

LANGUAGES = ["id-ID", "en-US"]  # Indonesian first: meetings here mix Indonesian and English
STRIP = 800  # strip height in pixels (at a page scale of 150 dpi)
OVERLAP = 120
MIN_SIDE = 60  # smaller pictures are icons and logos, not documents
ENLARGE_BELOW = 1600  # pictures at most this big (longest side) are enlarged 2x before reading


def available() -> bool:
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401

        return True
    except Exception:
        return False


def _languages(request) -> list[str]:
    """The wanted languages that this macOS version can actually read."""
    try:
        have, _err = request.supportedRecognitionLanguagesAndReturnError_(None)
        have = list(have or [])
    except Exception:
        have = []
    picked = [l for l in LANGUAGES if l in have]
    return picked or []  # empty: let Vision detect the language itself


def image_from_bytes(data: bytes):
    """CGImage from encoded picture bytes (PNG, JPEG, TIFF, HEIC, GIF...). None if it is not a picture."""
    import Quartz
    from Foundation import NSData

    nsdata = NSData.dataWithBytes_length_(data, len(data))
    src = Quartz.CGImageSourceCreateWithData(nsdata, None)
    if src is None or Quartz.CGImageSourceGetCount(src) < 1:
        return None
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def size_of(cg) -> tuple[int, int]:
    import Quartz

    return int(Quartz.CGImageGetWidth(cg)), int(Quartz.CGImageGetHeight(cg))


def is_worth_reading(cg) -> bool:
    w, h = size_of(cg)
    return min(w, h) >= MIN_SIDE and w * h >= 8000


def enlarged(cg, factor: int = 2):
    import Quartz

    w, h = size_of(cg)
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(None, w * factor, h * factor, 8, 0, cs, Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationHigh)
    Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
    Quartz.CGContextFillRect(ctx, ((0, 0), (w * factor, h * factor)))  # transparent pictures: text on white
    Quartz.CGContextDrawImage(ctx, ((0, 0), (w * factor, h * factor)), cg)
    return Quartz.CGBitmapContextCreateImage(ctx)


def _lines(cg) -> list[str]:
    import Quartz
    import Vision

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    langs = _languages(req)
    if langs:
        req.setRecognitionLanguages_(langs)
    else:
        try:
            req.setAutomaticallyDetectsLanguage_(True)
        except Exception:
            pass
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"text recognition failed: {err}")
    found = sorted(req.results() or [], key=lambda o: -o.boundingBox().origin.y)  # top to bottom
    out = []
    for obs in found:
        cands = obs.topCandidates_(1)
        if cands:
            out.append(str(cands[0].string()))
    return out


def _strips(cg, strip: int):
    import Quartz

    w, h = size_of(cg)
    if h <= strip * 1.25:
        yield cg
        return
    y = 0
    while y < h:
        sh = min(strip, h - y)
        yield Quartz.CGImageCreateWithImageInRect(cg, ((0, y), (w, sh)))
        if y + sh >= h:
            break
        y += strip - OVERLAP


def _key(line: str) -> str:
    return re.sub(r"\W+", "", line.lower())


def read_image(cg, strip: int = STRIP) -> list[str]:
    """Lines of text in a picture, top to bottom. Small pictures are enlarged first; tall ones are read in strips."""
    w, h = size_of(cg)
    if max(w, h) <= ENLARGE_BELOW:
        cg, strip = enlarged(cg), strip * 2
    seen, lines = set(), []
    for part in _strips(cg, strip):
        for line in _lines(part):
            k = _key(line)
            if k and k not in seen:  # the same line seen in two overlapping strips
                seen.add(k)
                lines.append(line)
    return lines


def new_lines(ocr_lines: list[str], known_text: str) -> list[str]:
    """The OCR lines that are not already in `known_text` (a PDF page's own text layer), so a page that has both real
    text and pictures with text gives each piece of text once."""
    known = _key(known_text)
    return [l for l in ocr_lines if _key(l) and _key(l) not in known]
