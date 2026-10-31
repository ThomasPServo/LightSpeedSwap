"""ASCII-only model of the CoreFoundation search used by these selectors.

Empty search strings never match. Unlike Python str.startswith(''), this is
the documented CFStringFindWithOptions contract and the 24B119 native branch.
"""
import ctypes
import sys


def anchored_ascii_match(haystack, needle):
    if not haystack.isascii() or not needle.isascii():
        raise ValueError("The research model only covers ASCII carrier selectors")
    return bool(needle) and haystack.lower().startswith(needle.lower())


CASES = [("DD", "", False), ("", "", False), ("", "DD", False),
         ("DD", "dd", True), ("DDFF", "DD", True), ("DD", "DDFF", False),
         ("DD", "DD.bundle", False), ("XDD", "DD", False)]


def check_native_macos_contract():
    """Call real macOS CoreFoundation, no phone access; skip other platforms."""
    if sys.platform != "darwin":
        return "not run (host is not macOS)"
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    class CFRange(ctypes.Structure):
        _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringFindWithOptions.argtypes = [ctypes.c_void_p, ctypes.c_void_p, CFRange,
                                          ctypes.c_ulong, ctypes.c_void_p]
    cf.CFStringFindWithOptions.restype = ctypes.c_ubyte
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    for haystack, needle, expected in CASES:
        a = cf.CFStringCreateWithCString(None, haystack.encode("ascii"), 0x08000100)
        b = cf.CFStringCreateWithCString(None, needle.encode("ascii"), 0x08000100)
        try:
            if not a or not b:
                raise RuntimeError("CoreFoundation failed to create a test string")
            native = bool(cf.CFStringFindWithOptions(a, b, CFRange(0, len(haystack)), 9, None))
            if native != expected or native != anchored_ascii_match(haystack, needle):
                raise RuntimeError("Native CoreFoundation contract differs from the selector model")
        finally:
            if a: cf.CFRelease(a)
            if b: cf.CFRelease(b)
    return "8 native macOS CoreFoundation cases passed"
