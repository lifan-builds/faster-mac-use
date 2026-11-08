"""50Hz Background Intrusion Guard: Verifies zero cursor movement and zero focus changes during runs."""

from __future__ import annotations
import ctypes
from dataclasses import dataclass
import subprocess
import threading
import time

_cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


_cg.CGEventCreate.argtypes = [ctypes.c_void_p]
_cg.CGEventCreate.restype = ctypes.c_void_p
_cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
_cg.CGEventGetLocation.restype = _CGPoint
_cg.CFRelease.argtypes = [ctypes.c_void_p]
_cg.CFRelease.restype = None
_cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
_cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double

_kCGEventSourceStateHIDSystemState = 1
_kCGEventLeftMouseDown = 1
_kCGEventRightMouseDown = 3
_kCGEventMouseMoved = 5
_kCGEventLeftMouseDragged = 6
_kCGEventKeyDown = 10
_kCGEventFlagsChanged = 12


def get_cursor_position() -> tuple[float, float]:
    ev = _cg.CGEventCreate(None)
    if not ev:
        return (0.0, 0.0)
    try:
        pt = _cg.CGEventGetLocation(ev)
        return (float(pt.x), float(pt.y))
    finally:
        _cg.CFRelease(ev)


def human_hid_moved_recently(window_sec: float = 0.08) -> bool:
    """Returns True if physical HID hardware (trackpad/mouse) generated a move event within window_sec."""
    t_move = float(_cg.CGEventSourceSecondsSinceLastEventType(_kCGEventSourceStateHIDSystemState, _kCGEventMouseMoved))
    t_drag = float(_cg.CGEventSourceSecondsSinceLastEventType(_kCGEventSourceStateHIDSystemState, _kCGEventLeftMouseDragged))
    return min(t_move, t_drag) <= window_sec


def human_hid_interacted_recently(window_sec: float = 0.45) -> bool:
    """Returns True if physical HID hardware generated mouse or keyboard input within window_sec."""
    events = (
        _kCGEventLeftMouseDown,
        _kCGEventRightMouseDown,
        _kCGEventMouseMoved,
        _kCGEventLeftMouseDragged,
        _kCGEventKeyDown,
        _kCGEventFlagsChanged,
    )
    return min(
        float(_cg.CGEventSourceSecondsSinceLastEventType(_kCGEventSourceStateHIDSystemState, ev))
        for ev in events
    ) <= window_sec


def get_frontmost_app_id() -> str:
    try:
        return subprocess.check_output(["/usr/bin/lsappinfo", "front"], text=True, timeout=0.5).strip()
    except Exception:
        return ""


@dataclass
class IntrusionReport:
    samples: int
    cursor_moved_px: float
    focus_changes: int
    passed_background_invariant: bool


class BackgroundIntrusionGuard:
    """Samples cursor coordinates at 50Hz and active app focus to enforce background-only operation."""

    def __init__(self, hz: float = 50.0, cursor_tolerance_px: float = 1.0) -> None:
        self.interval = 1.0 / hz
        self.cursor_tolerance_px = cursor_tolerance_px
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.initial_cursor: tuple[float, float] = (0.0, 0.0)
        self.initial_front_asn: str = ""
        self.max_cursor_delta: float = 0.0
        self.focus_changes: int = 0
        self.samples: int = 0

    def __enter__(self) -> BackgroundIntrusionGuard:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    def start(self) -> None:
        self.initial_cursor = get_cursor_position()
        self.initial_front_asn = get_frontmost_app_id()
        self.max_cursor_delta = 0.0
        self.focus_changes = 0
        self.samples = 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        tick = 0
        prev_cursor = self.initial_cursor
        prev_asn = self.initial_front_asn
        while not self._stop.is_set():
            cx, cy = get_cursor_position()
            dx = cx - prev_cursor[0]
            dy = cy - prev_cursor[1]
            step_dist = (dx * dx + dy * dy) ** 0.5
            if step_dist > 0.5:
                if not human_hid_moved_recently(0.10):
                    self.max_cursor_delta += step_dist
            prev_cursor = (cx, cy)
            if tick % 5 == 0 and prev_asn:
                cur_asn = get_frontmost_app_id()
                if cur_asn and cur_asn != prev_asn:
                    if not human_hid_interacted_recently(0.45):
                        self.focus_changes += 1
                    prev_asn = cur_asn
            tick += 1
            self.samples += 1
            time.sleep(self.interval)

    def stop(self) -> IntrusionReport:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        passed = (self.max_cursor_delta <= self.cursor_tolerance_px) and (self.focus_changes == 0)
        return IntrusionReport(
            samples=self.samples,
            cursor_moved_px=round(self.max_cursor_delta, 2),
            focus_changes=self.focus_changes,
            passed_background_invariant=passed,
        )
