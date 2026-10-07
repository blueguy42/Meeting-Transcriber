"""Global hotkeys through Carbon's RegisterEventHotKey, the API macOS provides for exactly this.

Why not pynput: its listener asks macOS for the keyboard layout from a background thread, which
macOS 14+ kills with a trap (the app crashed whenever the hotkeys were restarted). This API runs
callbacks on the main thread, needs no Accessibility / Input Monitoring permission, and swallows the
key combination so it does not leak into the frontmost app.

Hotkeys are written as "<ctrl>+<alt>+r" (modifiers: ctrl, alt, shift, cmd; keys: a-z, 0-9, <f1>-<f20>).
Keys are physical positions (ANSI layout), the same as the key recorder in the Settings window.
"""

import ctypes
import re
import traceback
from ctypes import CFUNCTYPE, POINTER, Structure, byref, c_int32, c_uint32, c_void_p

_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode(), "big")


class _EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


class _EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


_HANDLER = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
_CLASS_KEYBOARD, _KIND_HOTKEY_PRESSED = _fourcc("keyb"), 5
_PARAM_DIRECT_OBJECT, _TYPE_HOTKEY_ID = _fourcc("----"), _fourcc("hkid")
_SIGNATURE = _fourcc("MTrn")
_ERR_EXISTS, _ERR_NOT_HANDLED = -9878, -9874

_carbon.GetApplicationEventTarget.restype = c_void_p
_carbon.InstallEventHandler.argtypes = [c_void_p, _HANDLER, c_uint32, POINTER(_EventTypeSpec), c_void_p, POINTER(c_void_p)]
_carbon.InstallEventHandler.restype = c_int32
_carbon.RegisterEventHotKey.argtypes = [c_uint32, c_uint32, _EventHotKeyID, c_void_p, c_uint32, POINTER(c_void_p)]
_carbon.RegisterEventHotKey.restype = c_int32
_carbon.UnregisterEventHotKey.argtypes = [c_void_p]
_carbon.UnregisterEventHotKey.restype = c_int32
_carbon.GetEventParameter.argtypes = [c_void_p, c_uint32, c_uint32, c_void_p, c_uint32, c_void_p, c_void_p]
_carbon.GetEventParameter.restype = c_int32

_MODIFIERS = {"cmd": 1 << 8, "shift": 1 << 9, "alt": 1 << 11, "ctrl": 1 << 12}  # Carbon modifier bits
_KEYCODES = {  # ANSI virtual key codes
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26,
    "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
}
_FKEYS = {1: 122, 2: 120, 3: 99, 4: 118, 5: 96, 6: 97, 7: 98, 8: 100, 9: 101, 10: 109, 11: 103, 12: 111,
          13: 105, 14: 107, 15: 113, 16: 106, 17: 64, 18: 79, 19: 80, 20: 90}
_SPEC = re.compile(r"((?:<(?:ctrl|alt|shift|cmd)>\+)+)([a-z0-9]|<f(?:[1-9]|1[0-9]|20)>)")


def parse(spec: str) -> tuple[int, int]:
    """'<ctrl>+<alt>+r' -> (virtual key code, Carbon modifier mask). Raises ValueError."""
    m = _SPEC.fullmatch(spec or "")
    if not m:
        raise ValueError(f"not a valid hotkey: {spec!r}")
    mask = 0
    for mod in re.findall(r"<(\w+)>", m.group(1)):
        mask |= _MODIFIERS[mod]
    key = m.group(2)
    code = _FKEYS[int(key[2:-1])] if key.startswith("<") else _KEYCODES[key]
    return code, mask


class Hotkeys:
    """Owns the app's global hotkeys. Call set() whenever the settings change."""

    def __init__(self):
        self._callbacks: dict[int, object] = {}
        self._refs: list[int] = []
        self._next_id = 1
        self._upp = _HANDLER(self._on_event)  # keep a reference: ctypes frees the callback otherwise
        spec = _EventTypeSpec(_CLASS_KEYBOARD, _KIND_HOTKEY_PRESSED)
        self._handler_ref = c_void_p()
        status = _carbon.InstallEventHandler(
            _carbon.GetApplicationEventTarget(), self._upp, 1, byref(spec), None, byref(self._handler_ref)
        )
        if status != 0:
            raise OSError(f"could not install the hotkey handler (error {status})")

    def _on_event(self, call_ref, event, user_data):
        hk = _EventHotKeyID()
        if _carbon.GetEventParameter(event, _PARAM_DIRECT_OBJECT, _TYPE_HOTKEY_ID, None, ctypes.sizeof(hk), None, byref(hk)) == 0:
            callback = self._callbacks.get(hk.id)
            if callback is not None:
                try:
                    callback()
                except Exception:
                    traceback.print_exc()  # never let an exception unwind into Carbon
                return 0
        return _ERR_NOT_HANDLED  # not ours: let any other handler see it

    def clear(self) -> None:
        for ref in self._refs:
            _carbon.UnregisterEventHotKey(ref)
        self._refs.clear()
        self._callbacks.clear()

    def set(self, mapping: dict) -> list[str]:
        """Replace all hotkeys with {spec: callback}. Returns a message for each one that could not be set."""
        self.clear()
        problems = []
        for spec, callback in mapping.items():
            try:
                code, mask = parse(spec)
            except ValueError as e:
                problems.append(str(e))
                continue
            hk_id, ref = self._next_id, c_void_p()
            self._next_id += 1
            status = _carbon.RegisterEventHotKey(
                code, mask, _EventHotKeyID(_SIGNATURE, hk_id), _carbon.GetApplicationEventTarget(), 0, byref(ref)
            )
            if status == 0:
                self._refs.append(ref.value)
                self._callbacks[hk_id] = callback
            elif status == _ERR_EXISTS:
                problems.append(f"{spec} is already used by another app or the system")
            else:
                problems.append(f"{spec} could not be registered (error {status})")
        return problems
