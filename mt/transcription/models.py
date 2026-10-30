"""Making sure the Whisper model is on this Mac and complete, downloading it (with progress) if not."""

import threading
import time
from pathlib import Path

from ..progress import downloading_whisper


MODEL_STALL_S = 300  # no new bytes for this long = the download is stuck (the first bytes can take a minute)
MODEL_WEIGHTS = ("weights.safetensors", "weights.npz", "model.safetensors")


def _has_weights(folder: Path) -> bool:
    return any((folder / w).is_file() and (folder / w).stat().st_size > 0 for w in MODEL_WEIGHTS)


def ensure_model(model: str, progress=lambda msg: None) -> None:
    """Check that the Whisper model is on this Mac and complete, downloading it if not. Without this a broken
    cache (a download that was interrupted, leaving empty weight files) makes the first transcription hang."""
    local = Path(model).expanduser()
    if local.exists():
        if not _has_weights(local):
            raise RuntimeError(f"The Whisper model folder {local} has no weights file ({', '.join(MODEL_WEIGHTS)}).")
        return
    from huggingface_hub import constants, snapshot_download

    try:
        if _has_weights(Path(snapshot_download(model, local_files_only=True))):
            return
    except Exception:
        pass  # not (completely) downloaded yet
    progress(downloading_whisper(0))
    result: dict = {}

    def fetch():
        try:
            result["path"] = snapshot_download(model)
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=fetch, name="model-download", daemon=True)
    t.start()
    blobs = Path(constants.HF_HUB_CACHE) / ("models--" + model.replace("/", "--")) / "blobs"
    last, moved = -1, time.monotonic()
    while t.is_alive():
        t.join(2)
        size = sum(f.stat().st_size for f in blobs.glob("*") if f.is_file()) if blobs.exists() else 0
        if size != last:
            last, moved = size, time.monotonic()
            progress(downloading_whisper(size // 2**20))
        elif time.monotonic() - moved > MODEL_STALL_S:
            raise RuntimeError(
                f"Downloading the Whisper model {model} is stuck at {size // 2**20} MB. Check your internet connection, "
                f"delete the folder {blobs.parent} and try again (or pick another model in Settings)."
            )
    if "error" in result:
        raise RuntimeError(f"Could not download the Whisper model {model} ({type(result['error']).__name__}): {str(result['error'])[:150]}")
    if not _has_weights(Path(result["path"])):
        raise RuntimeError(f"The Whisper model {model} downloaded without a weights file; it may not be an MLX Whisper model.")
