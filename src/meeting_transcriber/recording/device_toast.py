"""What the "device changed" toast says. A mic switch and a system switch to the same device within MERGE_S seconds (a
headset moves both) are one "Mic and System" line; to two different devices they are one toast with a line each."""

import time

MERGE_S = 2.0
TOAST_S = 2.0  # how long a toast stays


class DeviceToasts:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._last: dict[str, tuple[str, float]] = {}  # track -> (device, when)

    def message(self, track: str, device: str) -> list[tuple[str, str]]:
        """-> the toast's lines [(who, device)], who being "Mic", "System" or "Mic and System". track: "mic" or "system"."""
        now = self._clock()
        other_track = "system" if track == "mic" else "mic"
        other = self._last.get(other_track)
        self._last[track] = (device, now)
        if other and now - other[1] <= MERGE_S:
            if other[0] == device:
                return [("Mic and System", device)]
            mic, system = (device, other[0]) if track == "mic" else (other[0], device)
            return [("Mic", mic), ("System", system)]  # two devices, one toast
        return [("Mic" if track == "mic" else "System", device)]
