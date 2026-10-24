"""Give Linux process monitors a useful name without extra dependencies."""
import ctypes
import sys


def set_process_name(name):
    if not sys.platform.startswith('linux'):
        return False
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
        prctl.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_ulong,
                          ctypes.c_ulong, ctypes.c_ulong]
        prctl.restype = ctypes.c_int
        return prctl(15, name.encode('ascii')[:15], 0, 0, 0) == 0  # PR_SET_NAME
    except (AttributeError, OSError, UnicodeError):
        return False
