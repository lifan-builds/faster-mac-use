# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Faster Mac Use; Section 7 attribution terms in ATTRIBUTION.md.
"""50Hz Background Intrusion Guard: Verifies zero cursor movement and zero focus changes during runs."""

from __future__ import annotations
import ctypes
from dataclasses import dataclass
import math
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
        raise RuntimeError("CGEventCreate returned NULL; cursor probe unavailable")
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
    except (subprocess.SubprocessError, OSError):
        return ""


@dataclass
class IntrusionReport:
    samples: int
    cursor_moved_px: float
    focus_changes: int
    passed_background_invariant: bool
    focus_probe_failures: int = 0
    focus_monitored: bool = True


class BackgroundIntrusionGuard:
    """Samples cursor coordinates at 50Hz and active app focus to enforce background-only operation."""

    def __init__(self, hz: float = 50.0, cursor_tolerance_px: float = 1.0) -> None:
        if hz <= 0:
            raise ValueError("hz must be positive")
        self.interval = 1.0 / hz
        self.cursor_tolerance_px = cursor_tolerance_px
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started = False
        self._last_report: IntrusionReport | None = None
        self._worker_failed = False
        self.initial_cursor: tuple[float, float] = (0.0, 0.0)
        self.initial_front_asn: str = ""
        self._prev_cursor: tuple[float, float] = (0.0, 0.0)
        self._prev_asn: str = ""
        self.max_cursor_delta: float = 0.0
        self.focus_changes: int = 0
        self.focus_probe_failures: int = 0
        self.focus_samples: int = 0
        self.samples: int = 0

    def __enter__(self) -> BackgroundIntrusionGuard:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=1.0)
        stop_event = threading.Event()
        self._stop = stop_event
        self._last_report = None
        self._worker_failed = False
        self.max_cursor_delta = 0.0
        self.focus_changes = 0
        self.focus_probe_failures = 0
        self.focus_samples = 0
        self.samples = 0
        try:
            self.initial_cursor = get_cursor_position()
        except RuntimeError:
            self.initial_cursor = (math.nan, math.nan)
            self._worker_failed = True
        self._prev_cursor = self.initial_cursor
        self.initial_front_asn = get_frontmost_app_id()
        self._prev_asn = self.initial_front_asn
        if self.initial_front_asn:
            self.focus_samples = 1
        else:
            self.focus_probe_failures = 1
        self._started = True
        self._thread = threading.Thread(target=self._loop, args=(stop_event,), daemon=True)
        self._thread.start()

    def _sample_step(self, *, check_focus: bool) -> None:
        cx, cy = get_cursor_position()
        cur_asn = get_frontmost_app_id() if check_focus else None
        with self._lock:
            if not (math.isfinite(cx) and math.isfinite(cy)):
                self._worker_failed = True
            else:
                dx = cx - self._prev_cursor[0]
                dy = cy - self._prev_cursor[1]
                step_dist = math.hypot(dx, dy)
                if step_dist > 0.5:
                    if not human_hid_moved_recently(0.10):
                        self.max_cursor_delta += step_dist
                self._prev_cursor = (cx, cy)

            if check_focus:
                if not cur_asn:
                    self.focus_probe_failures += 1
                else:
                    self.focus_samples += 1
                    if not self._prev_asn:
                        self._prev_asn = cur_asn
                    elif cur_asn != self._prev_asn:
                        if not human_hid_interacted_recently(0.45):
                            self.focus_changes += 1
                        self._prev_asn = cur_asn

            self.samples += 1

    def _loop(self, stop_event: threading.Event) -> None:
        tick = 0
        try:
            while not stop_event.is_set():
                self._sample_step(check_focus=(tick % 5 == 0))
                tick += 1
                stop_event.wait(self.interval)
        except Exception:
            with self._lock:
                self._worker_failed = True

    def stop(self) -> IntrusionReport:
        if self._last_report is not None:
            return self._last_report
        if not self._started:
            return IntrusionReport(
                samples=0,
                cursor_moved_px=0.0,
                focus_changes=0,
                passed_background_invariant=False,
                focus_probe_failures=0,
                focus_monitored=False,
            )
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                with self._lock:
                    self._worker_failed = True
        if not self._worker_failed and self.samples == 0:
            try:
                self._sample_step(check_focus=True)
            except Exception:
                with self._lock:
                    self._worker_failed = True

        with self._lock:
            focus_monitored = (
                not self._worker_failed
                and self.samples > 0
                and self.focus_samples >= 2
                and self.focus_probe_failures == 0
            )
            passed = (
                focus_monitored
                and (self.max_cursor_delta <= self.cursor_tolerance_px)
                and (self.focus_changes == 0)
            )
            report = IntrusionReport(
                samples=self.samples,
                cursor_moved_px=round(self.max_cursor_delta, 2),
                focus_changes=self.focus_changes,
                passed_background_invariant=passed,
                focus_probe_failures=self.focus_probe_failures,
                focus_monitored=focus_monitored,
            )
            self._last_report = report
            return report
