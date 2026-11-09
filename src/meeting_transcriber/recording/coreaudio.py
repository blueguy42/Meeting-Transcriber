"""The default input and output devices and their names, straight from CoreAudio (PortAudio's own list is a stale snapshot)."""


def _coreaudio():
    import ctypes

    return ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")


def _audio_prop(ca, dev: int, selector: int, out, scope: int = 0x676C6F62) -> bool:
    import ctypes

    class Addr(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

    addr = Addr(selector, scope, 0)  # scope 'glob' = global, element = main
    size = ctypes.c_uint32(ctypes.sizeof(out))
    return ca.AudioObjectGetPropertyData(dev, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(out)) == 0


def _default_device_id(selector: int) -> int | None:
    try:
        import ctypes

        dev = ctypes.c_uint32(0)
        ok = _audio_prop(_coreaudio(), 1, selector, dev)  # 1 = the system object
        return dev.value if ok and dev.value else None
    except Exception:
        return None


def default_input_id() -> int | None:
    """CoreAudio's current default input device id (cheap; PortAudio's own device list is a stale snapshot)."""
    return _default_device_id(0x64496E20)  # 'dIn '


def default_output_id() -> int | None:
    """CoreAudio's current default output device id (what system audio is played through)."""
    return _default_device_id(0x644F7574)  # 'dOut'


def device_name(dev: int | None) -> str | None:
    """Human-readable name of a CoreAudio device id."""
    if not dev:
        return None
    try:
        import ctypes

        ref = ctypes.c_void_p()
        if not _audio_prop(_coreaudio(), dev, 0x6C6E616D, ref):  # 'lnam' (kAudioObjectPropertyName)
            return None
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        buf = ctypes.create_string_buffer(256)
        ok = cf.CFStringGetCString(ref, buf, len(buf), 0x08000100)  # UTF-8
        cf.CFRelease(ref)
        return buf.value.decode() if ok else None
    except Exception:
        return None
