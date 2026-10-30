"""The permission flow: welcome, ask, explain a refusal, restart."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from . import permissions
from .desktop import alert, bring_to_front, notify, on_main

log = logging.getLogger("mt.permission_flow")


class PermissionFlow:
    """Asking for the microphone and Screen Recording permissions, explaining a refusal, and restarting when macOS needs it.

    Mixed into App; it uses self._overlay, self.cfg and the other parts of App."""

    # ---- permissions
    def _welcome(self):
        if permissions.welcomed():
            return
        if not permissions.missing():
            permissions.mark_welcomed()
            return
        bring_to_front()
        if not alert(
            "Welcome to Meeting Transcriber",
            "To record your meetings it needs two permissions:\n\n"
            "•  Microphone: to record your voice\n"
            "•  Screen & System Audio Recording: to capture other participants' audio and take screenshots "
            "(no video is recorded)\n\n"
            "macOS will now ask you for each one.",
            ok="Continue", cancel="Later",
        ):
            return  # asked again next launch
        permissions.mark_welcomed()
        self._request_next()

    def _request_next(self):
        """One system prompt at a time: microphone first, then screen & system audio."""
        if permissions.mic_status() == "undetermined":
            permissions.request_mic(lambda ok: on_main(self._request_next))
            return
        if permissions.mic_status() == "denied":
            self._explain_denied("microphone")
        if not permissions.screen_granted():
            permissions.request_screen()
            self._awaiting_restart = True
            notify("Meeting Transcriber", "Allow Screen & System Audio Recording",
                   "Switch it on in System Settings, then click the restart button on the bar.")

    def _explain_denied(self, which):
        bring_to_front()
        if alert(
            f"{permissions.LABELS[which]} is turned off",
            "macOS only asks once. Turn it on for Meeting Transcriber in System Settings → Privacy & Security.",
            ok="Open System Settings", cancel="Not now",
        ):
            permissions.open_settings(which)

    def fix_permissions(self, _):
        """Ask (if macOS hasn't yet) or point to the switch (if it was turned off)."""
        todo = permissions.missing()
        if "microphone" in todo:
            if permissions.mic_status() == "undetermined":
                bring_to_front()
                permissions.request_mic(lambda ok: on_main(self.fix_permissions, None))
            else:
                self._explain_denied("microphone")
        elif "screen" in todo:
            if self._awaiting_restart:  # you already switched it on: macOS applies it after a restart
                self.restart()
                return
            permissions.request_screen()
            permissions.open_settings("screen")
            self._awaiting_restart = True

    def _watch_permissions(self):
        """Refresh the cached list of missing permissions every couple of seconds (off the main thread)."""
        while True:
            time.sleep(2)
            try:
                self._missing = permissions.missing()
            except Exception:
                log.exception("Permission check failed")

    def _restart_pending(self) -> bool:
        """You switched on Screen Recording in System Settings, so the next step is a restart."""
        missing = self._missing
        return self._awaiting_restart and "screen" in missing and "microphone" not in missing

    def restart(self):
        """Quit and start again (macOS only lets a running app use Screen Recording after a restart)."""
        if self.rec.recording:
            notify("Meeting Transcriber", "Stop the recording first", "Then click the restart button again.")
            return
        helper = os.environ.get("MT_HELPER", "")  # <App>.app/Contents/Resources/sck-audio when run as the app
        bundle = Path(helper).parents[2] if helper.endswith(".app/Contents/Resources/sck-audio") else None
        again = ["/usr/bin/open", str(bundle)] if bundle else [sys.executable, "-m", "mt.app"]
        subprocess.Popen(["/bin/sh", "-c", 'sleep 1; exec "$@"', "sh", *again], start_new_session=True,
                         cwd=str(Path(__file__).resolve().parent.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.quit(None)
