"""Tells the other people in a meeting apart ("Others 1", "Others 2", ...), on the system-audio track.

sherpa-onnx (pyannote segmentation + 3D-Speaker voice embeddings, ONNX on the CPU) cuts the audio into
speech turns and groups them by voice, deliberately too fine. Each group is then averaged into one voiceprint,
and groups whose voiceprints are close are merged. Merging whole voiceprints (instead of one threshold on
individual turns) holds up from a 2-minute call to a 1-hour one, where a fixed threshold does not.
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import numpy as np

from . import config

SR = 16000
SEG_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMB_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SEG_FILE, EMB_FILE = "pyannote-segmentation-3-0.onnx", "3dspeaker-campplus-zh-en.onnx"
EMB_BYTES = 28_281_164  # a truncated download must not be used as a model

FINE_THRESHOLD = 0.5  # sherpa's own clustering, set low so it over-splits instead of merging two people
MERGE_DISTANCE = 0.35  # cosine distance between voiceprints: same voice merges below ~0.2, different voices are above ~0.45
PER_GROUP_SECONDS = 40.0  # speech used to build one group's voiceprint
MIN_TURN_SECONDS, MAX_TURN_SECONDS = 1.0, 8.0


def models_dir() -> Path:
    return config.settings_dir() / "models"


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        subprocess.run(["curl", "-fsSL", "--retry", "2", "-o", str(tmp), url], check=True, capture_output=True, timeout=600)
        tmp.replace(dest)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"could not download the speaker models ({type(e).__name__}); check your internet connection") from e


def ensure_models(progress=lambda msg: None) -> tuple[Path, Path]:
    """The two model files, downloaded on first use (about 35 MB in total)."""
    d = models_dir()
    d.mkdir(parents=True, exist_ok=True)
    seg, emb = d / SEG_FILE, d / EMB_FILE
    if not seg.exists():
        progress("Downloading speaker model 1 of 2…")
        with tempfile.TemporaryDirectory() as t:
            archive = Path(t) / "seg.tar.bz2"
            _download(SEG_URL, archive)
            with tarfile.open(archive) as tar:
                member = next((m for m in tar.getmembers() if m.isfile() and m.name.endswith("/model.onnx")), None)
                if member is None:
                    raise RuntimeError("the speaker segmentation download was not what was expected")
                with tar.extractfile(member) as src, open(seg.with_suffix(".part"), "wb") as out:
                    shutil.copyfileobj(src, out)
            seg.with_suffix(".part").replace(seg)
    if not emb.exists() or emb.stat().st_size != EMB_BYTES:
        progress("Downloading speaker model 2 of 2…")
        _download(EMB_URL, emb)
        if emb.stat().st_size != EMB_BYTES:
            emb.unlink(missing_ok=True)
            raise RuntimeError("the speaker model download was incomplete")
    return seg, emb


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def _voiceprints(audio: np.ndarray, turns: list, extractor) -> dict:
    """group -> (summed unit-embedding weighted by seconds, seconds of speech in the group)."""
    by: dict[int, list] = {}
    for a, b, g in turns:
        by.setdefault(g, []).append((a, b))
    out = {}
    for g, spans in by.items():
        total = sum(b - a for a, b in spans)
        vec, used = None, 0.0
        for a, b in sorted(spans, key=lambda s: s[0] - s[1]):  # longest turns first: cleanest voice
            if b - a < MIN_TURN_SECONDS and used > 0:
                continue
            b = min(b, a + MAX_TURN_SECONDS)
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=SR, waveform=audio[int(a * SR) : int(b * SR)])
            stream.input_finished()
            emb = _unit(np.array(extractor.compute(stream), dtype=np.float64)) * (b - a)
            vec = emb if vec is None else vec + emb
            used += b - a
            if used >= PER_GROUP_SECONDS:
                break
        if vec is not None:
            out[g] = (vec, total)
    return out


def merge_groups(prints: dict, speakers: int = 0, max_distance: float = MERGE_DISTANCE) -> dict:
    """Merge groups with close voiceprints. Returns {group: speaker}, speaker 0 = who talks the most.
    With `speakers` > 0, merge until that many are left instead of using the distance."""
    groups = {g: {"members": [g], "vec": v.copy(), "w": w} for g, (v, w) in prints.items()}
    total = sum(g["w"] for g in groups.values())
    tiny_s = max(3.0, min(20.0, 0.03 * total))  # too little speech to be a person of its own: absorbed first
    while len(groups) > 1:
        ids = list(groups)
        tiny = [i for i in ids if groups[i]["w"] < tiny_s]
        if len(tiny) == len(ids):
            tiny = []  # nobody is big enough to absorb them
        best = (9.0, None, None)
        for i in tiny or ids:
            for j in ids:
                if i != j:
                    d = 1 - float(_unit(groups[i]["vec"]) @ _unit(groups[j]["vec"]))
                    if d < best[0]:
                        best = (d, i, j)
        d, i, j = best
        if not tiny:
            if speakers and len(groups) <= speakers:
                break
            if not speakers and d > max_distance:
                break
        groups[j]["members"] += groups[i]["members"]
        groups[j]["vec"] = groups[j]["vec"] + groups[i]["vec"]
        groups[j]["w"] += groups[i]["w"]
        del groups[i]
    ranked = sorted(groups.values(), key=lambda g: -g["w"])
    return {m: n for n, g in enumerate(ranked) for m in g["members"]}


def diarize(audio: np.ndarray, speakers: int = 0, progress=lambda msg: None) -> list[tuple[float, float, int]]:
    """Speech turns (start s, end s, speaker) of 16 kHz mono float audio, time-ordered; speaker 0 talks the most.
    Raises RuntimeError if the models cannot be fetched or loaded."""
    import sherpa_onnx

    if len(audio) < SR:
        return []
    seg_model, emb_model = ensure_models(progress)
    threads = max(1, min(8, (os.cpu_count() or 4) // 2))
    try:
        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg_model)),
                num_threads=threads,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model), num_threads=threads),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=FINE_THRESHOLD),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not cfg.validate():
            raise RuntimeError("the speaker models could not be loaded")
        engine = sherpa_onnx.OfflineSpeakerDiarization(cfg)
        extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg.embedding)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"the speaker models could not be loaded ({e})") from e

    shown = [-1]

    def tick(done, total, _arg=None):
        percent = int(100 * done / max(total, 1))
        if percent != shown[0]:  # the engine reports every chunk; the menu only needs each percent once
            shown[0] = percent
            progress(f"Telling speakers apart… {percent}%")
        return 0

    turns = [(r.start, r.end, r.speaker) for r in engine.process(audio, callback=tick).sort_by_start_time()]
    if not turns:
        return []
    progress("Telling speakers apart… matching voices")
    mapping = merge_groups(_voiceprints(audio, turns, extractor), speakers)
    return [(a, b, mapping[g]) for a, b, g in turns if g in mapping]
