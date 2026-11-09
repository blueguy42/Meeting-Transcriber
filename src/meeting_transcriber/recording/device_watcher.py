"""Notices when the default microphone or sound output is switched while nothing is being recorded (during a recording the
Recorder follows the devices itself)."""

import threading

from .coreaudio import default_input_id, default_output_id, device_name


class DeviceWatcher:
    """Looks at CoreAudio's default input and output once a second. A change is reported with the new device's name, but only
    while `active()` says so (the baseline keeps following the devices either way, so nothing stale is reported later)."""

    def __init__(self, on_change, active=lambda: True, interval: float = 1.0):
        self.on_change = on_change  # ("mic" | "system", device name); called from the watcher thread
        self.active = active
        self.interval = interval
        self._last = {"mic": default_input_id(), "system": default_output_id()}
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.poll()

    def poll(self) -> None:
        for track, current in (("mic", default_input_id), ("system", default_output_id)):
            new = current()
            if new is None or new == self._last[track]:
                continue
            self._last[track] = new
            name = device_name(new)
            if name and self.active():
                try:
                    self.on_change(track, name)
                except Exception:
                    pass
