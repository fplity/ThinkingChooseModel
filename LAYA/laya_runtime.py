"""Headless local Laya worker for the PowerShell/WPF overlay.

The worker emits only versioned, typed JSONL status and fixed-template events.
OCR text and classifier inputs stay in memory and are never sent over IPC.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import math
import os
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
DEFAULT_TARGET_CONTACT = "陈颢予"
POLL_SECONDS = 3.0
PROTOCOL_VERSION = 1

FIXED_TEMPLATES: tuple[dict[str, str], ...] = (
    {"choice": "warm_reply", "label": "自然接话", "text": "好呀，听起来不错。"},
    {"choice": "clarify", "label": "顺势询问", "text": "你想什么时候去？"},
    {"choice": "boundary", "label": "礼貌拒绝", "text": "这次先不去了。"},
    {"choice": "wait", "label": "缓一缓", "text": "我先看看时间。"},
)
TEMPLATE_BY_CHOICE = {item["choice"]: item for item in FIXED_TEMPLATES}
TEMPLATE_BY_ID = {f"candidate_{index}": item for index, item in enumerate(FIXED_TEMPLATES, 1)}

COMMAND_FIELDS = {
    "start": frozenset(("v", "cmd")),
    "pause": frozenset(("v", "cmd")),
    "shutdown": frozenset(("v", "cmd")),
    "fill": frozenset(("v", "cmd", "candidate_id")),
}


class ProtocolError(ValueError):
    """An invalid local protocol command, with no user payload in its message."""


def parse_command(line: str) -> dict[str, Any]:
    """Parse exactly one allowed JSONL command; reject extra keys and payloads."""
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        raise ProtocolError("invalid_json") from None
    if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != PROTOCOL_VERSION:
        raise ProtocolError("invalid_envelope")
    command = value.get("cmd")
    if not isinstance(command, str) or command not in COMMAND_FIELDS:
        raise ProtocolError("unknown_command")
    if frozenset(value) != COMMAND_FIELDS[command]:
        raise ProtocolError("invalid_fields")
    if command == "fill" and value.get("candidate_id") not in TEMPLATE_BY_ID:
        raise ProtocolError("invalid_candidate")
    return {"v": PROTOCOL_VERSION, "cmd": command, **({"candidate_id": value["candidate_id"]} if command == "fill" else {})}


def header_signature(text: str) -> str:
    # OCR may add whitespace around the title, but every non-whitespace
    # character remains significant so Latin or numeric suffixes cannot pass.
    return "".join(text.split())


def normalize_target_contact(value: Any) -> str:
    """Return a safe display name used to bind OCR to one configured chat."""
    if not isinstance(value, str):
        return DEFAULT_TARGET_CONTACT
    candidate = value.strip()
    if not candidate or len(candidate) > 80 or any(ord(char) < 32 for char in candidate):
        return DEFAULT_TARGET_CONTACT
    return candidate


TARGET_CONTACT = normalize_target_contact(os.environ.get("LAYA_TARGET_CONTACT"))
TARGET_OCR_HEADERS = frozenset((header_signature(TARGET_CONTACT),))


def normalize_body(text: str | None) -> str:
    if not isinstance(text, str):
        return ""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())[-5000:]


def _safe_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    score = float(value)
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        return None
    return score


def rank_candidates(answers: Any) -> list[dict[str, Any]]:
    """Map model output only to the four fixed local templates."""
    step = answers.get("next_step", {}) if isinstance(answers, dict) else {}
    if not isinstance(step, dict):
        step = {}
    probabilities = step.get("probabilities", {})
    if not isinstance(probabilities, dict):
        probabilities = {}
    selected = step.get("choice")
    ranked: list[tuple[int, dict[str, Any]]] = []
    for index, template in enumerate(FIXED_TEMPLATES):
        choice = template["choice"]
        score = _safe_score(probabilities.get(choice))
        if score is None and selected == choice:
            score = 1.0
        ranked.append((index, {"choice": choice, "label": template["label"], "text": template["text"], "score": score}))
    ranked.sort(key=lambda pair: (pair[1]["score"] is not None, pair[1]["score"] if pair[1]["score"] is not None else -1.0, -pair[0]), reverse=True)
    result: list[dict[str, Any]] = []
    for index, item in ranked[:3]:
        result.append({"id": f"candidate_{len(result) + 1}", "choice": item["choice"], "label": item["label"], "text": item["text"], "score": item["score"]})
    return result


def build_questions() -> dict[str, Any]:
    return {
        "reply_now": {
            "type": "choice",
            "instructions": "Based only on this conversation, is it a good idea to reply now?",
            "criteria": {
                "reply": "A brief, warm reply is appropriate now.",
                "wait": "The person should wait or ask for clarification before replying.",
            },
        },
        "interest": {
            "type": "choice",
            "instructions": "What level of interest is supported by the recent conversation? Do not infer more than the visible messages show.",
            "criteria": {
                "positive": "There are clear positive signals in the recent messages.",
                "unclear": "The visible signals are mixed or there is not enough context.",
                "distant": "The recent messages show little interest or clear distance.",
            },
        },
        "next_step": {
            "type": "choice",
            "instructions": "What is the most appropriate next step in this conversation?",
            "criteria": {
                "warm_reply": "Respond warmly and accept or continue the proposal.",
                "clarify": "Ask one direct, low-pressure question to clarify the other person's intent or plan.",
                "boundary": "Set a clear, polite boundary or decline.",
                "wait": "Do not commit yet; wait for a clearer message or follow-through.",
            },
        },
    }


def _initialize_win32() -> tuple[Any, Any]:
    if os.name != "nt":
        raise OSError("windows_only")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
    user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
    user32.IsIconic.restype = ctypes.wintypes.BOOL
    user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
    user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
    user32.GetClientRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    user32.GetClientRect.restype = ctypes.wintypes.BOOL
    user32.ClientToScreen.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.POINT)]
    user32.ClientToScreen.restype = ctypes.wintypes.BOOL
    user32.GetGUIThreadInfo.argtypes = [ctypes.wintypes.DWORD, ctypes.c_void_p]
    user32.GetGUIThreadInfo.restype = ctypes.wintypes.BOOL
    user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    user32.GetWindowRect.restype = ctypes.wintypes.BOOL

    kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.LPWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p

    user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
    user32.OpenClipboard.restype = ctypes.wintypes.BOOL
    user32.EmptyClipboard.restype = ctypes.wintypes.BOOL
    user32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.restype = ctypes.wintypes.BOOL
    user32.keybd_event.argtypes = [ctypes.wintypes.BYTE, ctypes.wintypes.BYTE, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.c_ulong)]
    user32.keybd_event.restype = None
    return user32, kernel32


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hwndActive", ctypes.wintypes.HWND),
        ("hwndFocus", ctypes.wintypes.HWND),
        ("hwndCapture", ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize", ctypes.wintypes.HWND),
        ("hwndCaret", ctypes.wintypes.HWND),
        ("rcCaret", ctypes.wintypes.RECT),
    ]


class WindowsAdapter:
    """All desktop-facing calls used by production; constructed only on start."""

    def __init__(self, overlay_hwnd: int) -> None:
        self.user32, self.kernel32 = _initialize_win32()
        self.overlay_hwnd = overlay_hwnd

    def foreground_rect(self) -> dict[str, int] | None:
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        process_id = ctypes.wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        handle = self.kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return None
        try:
            size = ctypes.wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                return None
            if Path(image.value).name.casefold() != "weixin.exe":
                return None
        finally:
            self.kernel32.CloseHandle(handle)
        if self.user32.IsIconic(hwnd) or not self.user32.IsWindowVisible(hwnd):
            return None
        rect = ctypes.wintypes.RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        origin = ctypes.wintypes.POINT(0, 0)
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 500 or height < 300:
            return None
        return {"left": origin.x, "top": origin.y, "width": width, "height": height, "hwnd": int(hwnd)}

    def overlay_bounds(self) -> tuple[int, int, int, int] | None:
        if not self.overlay_hwnd:
            return None
        rect = ctypes.wintypes.RECT()
        if not self.user32.GetWindowRect(ctypes.wintypes.HWND(self.overlay_hwnd), ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    def read_header(self, rect: dict[str, int], screen: Any, numpy: Any, ocr: Any) -> str:
        head_height = min(88, max(54, int(rect["height"] * 0.11)))
        image = numpy.asarray(
            screen.grab({"left": rect["left"], "top": rect["top"], "width": rect["width"], "height": head_height})
        )[:, :, :3]
        image = numpy.repeat(numpy.repeat(image, 2, axis=0), 2, axis=1)
        result = ocr(image)
        return "\n".join(line.strip() for line in (result.txts or ()) if line and line.strip())

    def read_body(self, rect: dict[str, int], screen: Any, numpy: Any, ocr: Any) -> str:
        head_height = min(88, max(54, int(rect["height"] * 0.11)))
        top = rect["top"] + head_height
        bottom_margin = max(16, int(rect["height"] * 0.055))
        chat_height = rect["height"] - head_height - bottom_margin
        if chat_height < 120:
            return ""
        image = numpy.asarray(
            screen.grab({"left": rect["left"], "top": top, "width": rect["width"], "height": chat_height})
        )[:, :, :3]
        bounds = self.overlay_bounds()
        if bounds:
            x, y, width, height = bounds
            x1 = max(0, x - rect["left"])
            y1 = max(0, y - top)
            x2 = min(image.shape[1], x + width - rect["left"])
            y2 = min(image.shape[0], y + height - top)
            if x1 < x2 and y1 < y2:
                image[y1:y2, x1:x2] = 245
        result = ocr(image)
        return "\n".join(line.strip() for line in (result.txts or ()) if line and line.strip())

    def composer_has_caret(self, rect: dict[str, int]) -> bool:
        thread_id = self.user32.GetWindowThreadProcessId(ctypes.wintypes.HWND(rect["hwnd"]), None)
        info = _GuiThreadInfo()
        info.cbSize = ctypes.sizeof(info)
        if not thread_id or not self.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) or not info.hwndCaret:
            return False
        point = ctypes.wintypes.POINT(info.rcCaret.left, info.rcCaret.bottom)
        if not self.user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
            return False
        relative_x = point.x - rect["left"]
        relative_y = point.y - rect["top"]
        return 0 <= relative_x <= rect["width"] and rect["height"] * 0.68 <= relative_y <= rect["height"]

    def copy_to_clipboard(self, text: str) -> bool:
        if not self.user32.OpenClipboard(None):
            return False
        allocated = None
        transferred = False
        try:
            if not self.user32.EmptyClipboard():
                return False
            payload = (text + "\0").encode("utf-16-le")
            allocated = self.kernel32.GlobalAlloc(0x0002 | 0x0040, len(payload))
            if not allocated:
                return False
            locked = self.kernel32.GlobalLock(allocated)
            if not locked:
                return False
            try:
                ctypes.memmove(locked, payload, len(payload))
            finally:
                self.kernel32.GlobalUnlock(allocated)
            transferred = bool(self.user32.SetClipboardData(13, allocated))
            return transferred
        finally:
            self.user32.CloseClipboard()
            if allocated and not transferred:
                self.kernel32.GlobalFree(allocated)

    def paste_ctrl_v(self) -> None:
        extra = ctypes.c_ulong(0)
        self.user32.keybd_event(0x11, 0, 0, ctypes.byref(extra))
        self.user32.keybd_event(0x56, 0, 0, ctypes.byref(extra))
        self.user32.keybd_event(0x56, 0, 2, ctypes.byref(extra))
        self.user32.keybd_event(0x11, 0, 2, ctypes.byref(extra))


class SessionGuard:
    """Header-first, same-session gate shared by capture and fill operations."""

    def __init__(self) -> None:
        self.bound_header: str | None = None
        self.blocked = False

    @staticmethod
    def _same_window(first: dict[str, int] | None, second: dict[str, int] | None) -> bool:
        return bool(first and second and first.get("hwnd") == second.get("hwnd"))

    def _check_header(self, signature: str) -> str:
        if self.bound_header is not None and signature != self.bound_header:
            self.blocked = True
            return "session_changed"
        if signature not in TARGET_OCR_HEADERS:
            return "wrong_header"
        if self.bound_header is None:
            self.bound_header = signature
        return "ok"

    def capture_body(
        self,
        adapter: Any,
        screen: Any,
        numpy: Any,
        ocr: Any,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[str, str | None]:
        cancelled = should_cancel or (lambda: False)
        if cancelled():
            return "cancelled", None
        if self.blocked:
            return "session_changed", None
        first = adapter.foreground_rect()
        if first is None:
            return "foreground_required", None
        if cancelled():
            return "cancelled", None
        first_header = adapter.read_header(first, screen, numpy, ocr)
        if cancelled():
            return "cancelled", None
        signature = header_signature(first_header)
        code = self._check_header(signature)
        if code != "ok":
            return code, None
        second = adapter.foreground_rect()
        if not self._same_window(first, second):
            return "foreground_changed", None
        if cancelled():
            return "cancelled", None
        second_header = adapter.read_header(second, screen, numpy, ocr)
        if cancelled():
            return "cancelled", None
        signature = header_signature(second_header)
        code = self._check_header(signature)
        if code != "ok":
            return code, None
        final = adapter.foreground_rect()
        if not self._same_window(second, final):
            return "foreground_changed", None
        if cancelled():
            return "cancelled", None
        before_body = adapter.foreground_rect()
        if not self._same_window(final, before_body):
            return "foreground_changed", None
        if cancelled():
            return "cancelled", None
        # Bind the header again at the body-read boundary. A chat switch inside
        # the same HWND must be rejected before any body screenshot/OCR starts.
        final_header = adapter.read_header(before_body, screen, numpy, ocr)
        if cancelled():
            return "cancelled", None
        signature = header_signature(final_header)
        code = self._check_header(signature)
        if code != "ok":
            return code, None
        if cancelled():
            return "cancelled", None
        body = adapter.read_body(before_body, screen, numpy, ocr)
        after_capture = adapter.foreground_rect()
        if not self._same_window(final, after_capture):
            return "foreground_changed", None
        after_header = adapter.read_header(after_capture, screen, numpy, ocr)
        if cancelled():
            return "cancelled", None
        signature = header_signature(after_header)
        code = self._check_header(signature)
        if code != "ok":
            return code, None
        after_header = adapter.foreground_rect()
        if not self._same_window(after_capture, after_header):
            return "foreground_changed", None
        return "ok", body

    def fill_candidate(self, adapter: Any, text: str, screen: Any, numpy: Any, ocr: Any) -> str:
        if self.blocked:
            return "session_changed"
        first = adapter.foreground_rect()
        if first is None:
            return "foreground_required"
        signature = header_signature(adapter.read_header(first, screen, numpy, ocr))
        code = self._check_header(signature)
        if code != "ok":
            return code
        second = adapter.foreground_rect()
        if not self._same_window(first, second):
            return "foreground_changed"
        if not adapter.composer_has_caret(second):
            return "copied_no_caret" if adapter.copy_to_clipboard(text) else "clipboard_failed"

        if not adapter.copy_to_clipboard(text):
            return "clipboard_failed"

        # Recheck the foreground, exact header and caret after clipboard access,
        # directly before Ctrl+V. No Enter key or send action exists here.
        before_paste = adapter.foreground_rect()
        if not self._same_window(second, before_paste):
            return "foreground_changed"
        signature = header_signature(adapter.read_header(before_paste, screen, numpy, ocr))
        code = self._check_header(signature)
        if code != "ok":
            return code
        final = adapter.foreground_rect()
        if not self._same_window(before_paste, final):
            return "foreground_changed"
        if not adapter.composer_has_caret(final):
            return "copied_no_caret"
        adapter.paste_ctrl_v()
        return "draft_filled"


STATUS_TEXT = {
    "worker_ready": "等待启动",
    "starting": "正在启动本机 OCR 和 Laya…",
    "monitoring": "监测中 · 仅检查指定会话",
    "paused": "已暂停 · 不会读取屏幕",
    "foreground_required": "请将指定微信聊天置于前台",
    "wrong_header": "当前标题未确认 · 未读取聊天正文",
    "session_changed": "检测到会话变化 · 已锁定并暂停",
    "foreground_changed": "窗口焦点变化 · 本次扫描已停止",
    "short_body": "会话已确认 · 等待可读内容",
    "analyzing": "分类已更新 · 使用本地固定模板",
    "copied": "已复制固定回复",
    "copied_no_caret": "已复制 · 输入框无光标，未填入",
    "draft_filled": "草稿已填入 · 不会自动发送",
    "not_ready": "监测未就绪 · 未填入草稿",
    "clipboard_failed": "无法访问剪贴板 · 未填入草稿",
    "model_error": "本地识别初始化失败",
    "runtime_error": "本地识别暂时不可用",
    "invalid_command": "收到无效本地命令",
}


class LayaWorker:
    """Local worker command loop and isolated monitor thread."""

    def __init__(
        self,
        overlay_hwnd: int,
        emit: Callable[[dict[str, Any]], None],
        adapter_factory: Callable[[int], Any] = WindowsAdapter,
        model_factory: Callable[[], tuple[Any, Any]] | None = None,
        screen_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.overlay_hwnd = overlay_hwnd
        self.emit = emit
        self.adapter_factory = adapter_factory
        self.model_factory = model_factory or self._load_models
        self.screen_factory = screen_factory
        self.shutdown_event = threading.Event()
        self.monitor_event = threading.Event()
        self.monitor_thread: threading.Thread | None = None
        self.ready_event = threading.Event()
        self.guard = SessionGuard()
        self._lock = threading.RLock()
        self._ocr: Any = None
        self._router: Any = None
        self._adapter: Any = None
        self._screen: Any = None
        self._numpy: Any = None
        self._latest_candidates: dict[str, dict[str, str]] = {}
        self._last_text = ""
        self._last_status = "worker_ready"

    @staticmethod
    def _load_models() -> tuple[Any, Any]:
        os.environ.setdefault("HF_HOME", str(ROOT / "models" / "huggingface"))
        os.environ.setdefault("RAPIDOCR_HOME", str(ROOT / "models" / "ocr"))
        from rapidocr import RapidOCR
        from laya import Router
        return RapidOCR(), Router(max_loaded=1, device="cuda", preload=False)

    def _send(self, code: str, *, event: str = "status", items: list[dict[str, Any]] | None = None) -> None:
        payload: dict[str, Any] = {"v": PROTOCOL_VERSION, "event": event, "code": code}
        if event == "candidates":
            payload["items"] = items or []
        self.emit(payload)

    def handle_command(self, command: dict[str, Any]) -> bool:
        """Return False only for a valid shutdown command."""
        kind = command["cmd"]
        if kind == "start":
            if self.monitor_thread is None or not self.monitor_thread.is_alive():
                self.monitor_event.set()
                self._send("starting")
                self.monitor_thread = threading.Thread(target=self._monitor_loop, name="laya-headless-worker", daemon=True)
                self.monitor_thread.start()
            else:
                self.monitor_event.set()
                self._send("starting" if not self.ready_event.is_set() else "monitoring")
        elif kind == "pause":
            self.monitor_event.clear()
            self._send("paused")
        elif kind == "fill":
            self._fill(command["candidate_id"])
        elif kind == "shutdown":
            self.shutdown_event.set()
            self.monitor_event.clear()
            return False
        return True

    def _monitor_loop(self) -> None:
        try:
            self._adapter = self.adapter_factory(self.overlay_hwnd)
            # Third-party startup output is drained to stderr, which the host
            # consumes and discards. stdout remains protocol-only JSONL.
            with redirect_stdout(sys.stderr):
                if self.screen_factory:
                    self._screen = self.screen_factory()
                else:
                    import mss

                    self._screen = mss.mss()
                import numpy as np

                self._numpy = np
                self._ocr, self._router = self.model_factory()
            self.ready_event.set()
            self._send("monitoring" if self.monitor_event.is_set() else "paused")
        except Exception:
            self._send("model_error")
            return

        while not self.shutdown_event.is_set():
            if not self.monitor_event.wait(0.2):
                continue
            try:
                with self._lock:
                    code, body = self.guard.capture_body(
                        self._adapter,
                        self._screen,
                        self._numpy,
                        self._ocr,
                        should_cancel=lambda: self.shutdown_event.is_set() or not self.monitor_event.is_set(),
                    )
                if self.shutdown_event.is_set() or not self.monitor_event.is_set():
                    continue
                if code == "cancelled":
                    continue
                if code == "session_changed":
                    self.monitor_event.clear()
                    self._send(code)
                    continue
                if code != "ok":
                    self._send(code)
                    self.shutdown_event.wait(POLL_SECONDS)
                    continue
                normalized = normalize_body(body)
                if self.shutdown_event.is_set() or not self.monitor_event.is_set():
                    continue
                if len(normalized) < 8:
                    self._send("short_body")
                elif normalized != self._last_text:
                    if self.shutdown_event.is_set() or not self.monitor_event.is_set():
                        continue
                    self._send("analyzing")
                    if self.shutdown_event.is_set() or not self.monitor_event.is_set():
                        continue
                    with redirect_stdout(sys.stderr):
                        result = self._router.predict(
                            {"conversation": normalized, "contact": TARGET_CONTACT}, build_questions(), model="english"
                        )
                    if self.shutdown_event.is_set() or not self.monitor_event.is_set():
                        continue
                    self._last_text = normalized
                    answers = result.get("answers", {}) if isinstance(result, dict) else {}
                    candidates = rank_candidates(answers)
                    with self._lock:
                        self._latest_candidates = {item["id"]: {"choice": item["choice"], "text": item["text"]} for item in candidates}
                    event_items = [
                        {"id": item["id"], "label": item["label"], "text": item["text"], "score": item["score"]}
                        for item in candidates
                    ]
                    self._send("analyzing", event="candidates", items=event_items)
            except Exception:
                if self.monitor_event.is_set() and not self.shutdown_event.is_set():
                    self._send("runtime_error")
            self.shutdown_event.wait(POLL_SECONDS)

    def _fill(self, candidate_id: str) -> None:
        if not self.ready_event.is_set() or not self.monitor_event.is_set() or self.guard.blocked or self._adapter is None:
            self._send("not_ready")
            return
        try:
            with self._lock:
                candidate = self._latest_candidates.get(candidate_id)
                if not candidate:
                    self._send("not_ready")
                    return
                code = self.guard.fill_candidate(
                    self._adapter, candidate["text"], self._screen, self._numpy, self._ocr
                )
                if self.guard.blocked:
                    self.monitor_event.clear()
        except Exception:
            code = "runtime_error"
        self._send(code if code in STATUS_TEXT else "runtime_error")


def emit_event(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def run_protocol(stdin: Any = None, stdout: Any = None, *, overlay_hwnd: int = 0, worker_factory: Callable[..., LayaWorker] = LayaWorker) -> int:
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    emit_lock = threading.Lock()

    def emit(payload: dict[str, Any]) -> None:
        with emit_lock:
            output_stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()

    worker = worker_factory(overlay_hwnd, emit)
    emit({"v": PROTOCOL_VERSION, "event": "status", "code": "worker_ready"})
    try:
        for line in input_stream:
            try:
                command = parse_command(line)
            except ProtocolError:
                emit({"v": PROTOCOL_VERSION, "event": "status", "code": "invalid_command"})
                continue
            if not worker.handle_command(command):
                break
    finally:
        worker.shutdown_event.set()
        worker.monitor_event.clear()
        thread = worker.monitor_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Headless Laya local worker")
    parser.add_argument("--overlay-hwnd", type=int, required=True)
    args = parser.parse_args(argv)
    return run_protocol(overlay_hwnd=args.overlay_hwnd)


if __name__ == "__main__":
    raise SystemExit(main())
