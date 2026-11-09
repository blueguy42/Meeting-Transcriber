"""What the bar needs from the app: a few questions it asks and a few things it reports. The app's BarPresenter implements this."""

from typing import Protocol


class BarHost(Protocol):
    def overlay_in_captures(self) -> bool:
        """Should the bar show up in screenshots and screen shares?"""

    def overlay_action(self, op: str) -> None:
        """A toolbar button was used: "toggle" | "shot" | "notes" | "docs_add" | "mute_mic" | "mute_system" | "pause" |
        "cancel_rec" | "perm" | "quit"."""

    def overlay_tip(self, tip_id: str) -> dict | None:
        """{"text", "hotkey"} for a tooltip, or None."""

    def overlay_menu(self) -> list:
        """The More menu: [{"id", "label", "hint"?, "sub"?: [{"id", "label", "checked"}]} | {"divider": True}]."""

    def overlay_menu_click(self, item_id: str) -> None: ...

    def overlay_status(self) -> dict:
        """The processing panel: {"jobs": [...], "canRecord": bool}."""

    def overlay_status_action(self, act: str, job_id: int | None) -> None:
        """A button in the processing panel: "summary" | "folder" | "record" | "close" | "pause" | "resume" | "cancel" | "again" | "dismiss"."""

    def overlay_docs(self) -> dict:
        """The documents list: {"docs": [...]}."""

    def overlay_docs_action(self, act: str, doc_id: str) -> None: ...

    def overlay_notes_info(self) -> dict:
        """How the notes panel inserts the time: {"timeKey", "timeLabel"}."""

    def overlay_notes_save(self, text: str) -> str | None:
        """Everything typed so far; returns an error message, or None."""
