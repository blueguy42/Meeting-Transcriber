"""The microphone and system-audio device changes of one recording, as one list for meta.json."""

MERGE_WITHIN_S = 2.0  # a mic and a system switch this close, to the same device, are one switch (a headset moves both)


def merged_events(mic_events: list[dict], system_events: list[dict], rel) -> list[dict]:
    """Mic and system device changes in one time-ordered list, in recording seconds (`rel(t)` turns a wall-clock time into those).
    A mic and a system switch to the same device within MERGE_WITHIN_S become one "mic+system" entry."""
    evs = [{**e, "track": "mic"} for e in mic_events] + [{**e, "track": "system"} for e in system_events]
    evs.sort(key=lambda e: e["t"])
    out: list[dict] = []
    for e in evs:
        prev = out[-1] if out else None
        if (
            prev
            and {prev["track"], e["track"]} == {"mic", "system"}
            and prev["event"] == e["event"] == "switched"
            and prev.get("device") == e.get("device")
            and e["t"] - prev["t"] <= MERGE_WITHIN_S
        ):
            out[-1] = {**prev, "track": "mic+system"}
        else:
            out.append(e)
    return [{"at": round(rel(e["t"]), 1), **{k: v for k, v in e.items() if k != "t" and v is not None}} for e in out]
