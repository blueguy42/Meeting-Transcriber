"""Small audio helpers for the recorder: a streaming resampler and cutting paused stretches out of a recording."""

import wave
from pathlib import Path


def make_resampler(src: int, dst: int):
    """Streaming linear resampler for mono int16 chunks (speech only, so no fancy filter)."""
    import numpy as np

    step = src / dst
    state = {"tail": np.zeros(0, dtype=np.float32), "pos": 0.0}

    def run(chunk):
        x = np.concatenate([state["tail"], chunk.astype(np.float32)])
        idx = np.arange(state["pos"], len(x) - 1, step)
        out = np.interp(idx, np.arange(len(x)), x)
        used = int(idx[-1] // 1) if len(idx) else 0
        next_pos = (idx[-1] + step if len(idx) else state["pos"]) - used
        state["tail"], state["pos"] = x[used:], next_pos
        return out.astype(np.int16)

    return run


def cut_wav(path: Path, first: float, spans: list) -> None:
    """Remove the paused stretches from a recording that kept running through them (the system audio helper cannot be
    told to stop writing). `first` is the wall-clock time of its first sample, `spans` the (start, end) wall-clock pauses."""
    with wave.open(str(path), "rb") as r:
        params, rate, total = r.getparams(), r.getframerate(), r.getnframes()
        cuts: list[list[int]] = []
        for a, b in sorted(spans):
            x, y = max(int(round((a - first) * rate)), 0), min(int(round((b - first) * rate)), total)
            if y > x:
                if cuts and x <= cuts[-1][1]:
                    cuts[-1][1] = max(cuts[-1][1], y)
                else:
                    cuts.append([x, y])
        if not cuts:
            return
        tmp = path.with_name(path.stem + ".cut.wav")
        with wave.open(str(tmp), "wb") as w:
            w.setparams(params)
            pos = 0
            for x, y in [*cuts, [total, total]]:
                r.setpos(pos)
                left = x - pos
                while left > 0:
                    chunk = r.readframes(min(left, 1 << 16))
                    if not chunk:
                        break
                    w.writeframes(chunk)
                    left -= len(chunk) // (params.sampwidth * params.nchannels)
                pos = y
    tmp.replace(path)
