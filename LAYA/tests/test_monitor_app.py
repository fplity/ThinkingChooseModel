import ast
import json
import re
import sys
import threading
import types
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import laya_runtime as runtime


LAYA = Path(__file__).resolve().parents[1]
WPF_SOURCE = LAYA / "monitor_app.ps1"
LAUNCH_SOURCE = LAYA / "Run-Chat-Overlay.ps1"
STOP_SOURCE = LAYA / "Stop-Chat-Overlay.ps1"
TARGET_RECT = {"hwnd": 101, "left": 0, "top": 0, "width": 800, "height": 600}


class FakeAdapter:
    def __init__(self, *, rects=None, headers=None, carets=None):
        self.rects = list(rects or [TARGET_RECT])
        self.headers = list(headers or [runtime.TARGET_CONTACT])
        self.carets = list(carets or [True])
        self.calls = []
        self.clipboard = []
        self.pastes = 0
        self.body_reads = 0

    def foreground_rect(self):
        self.calls.append("foreground")
        if len(self.rects) > 1:
            return self.rects.pop(0)
        return self.rects[0]

    def read_header(self, _rect, _screen, _numpy, _ocr):
        self.calls.append("header")
        if len(self.headers) > 1:
            return self.headers.pop(0)
        return self.headers[0]

    def read_body(self, _rect, _screen, _numpy, _ocr):
        self.calls.append("body")
        self.body_reads += 1
        return "synthetic local test body"

    def composer_has_caret(self, _rect):
        self.calls.append("caret")
        if len(self.carets) > 1:
            return self.carets.pop(0)
        return self.carets[0]

    def copy_to_clipboard(self, text):
        self.calls.append("clipboard")
        self.clipboard.append(text)
        return True

    def paste_ctrl_v(self):
        self.calls.append("paste_ctrl_v")
        self.pastes += 1


class ProtocolTests(unittest.TestCase):
    def test_target_contact_can_be_configured_without_relaxing_header_matching(self):
        self.assertEqual(runtime.normalize_target_contact('  Alice  '), 'Alice')
        self.assertEqual(runtime.normalize_target_contact(''), runtime.DEFAULT_TARGET_CONTACT)
        self.assertEqual(runtime.normalize_target_contact('x' * 81), runtime.DEFAULT_TARGET_CONTACT)
        self.assertEqual(runtime.header_signature('Alice Chat'), 'AliceChat')

        original_headers = runtime.TARGET_OCR_HEADERS
        try:
            runtime.TARGET_OCR_HEADERS = frozenset((runtime.header_signature('Alice Chat'),))
            adapter = FakeAdapter(headers=['Alice Chat', 'Alice Chat', 'Alice Chat', 'Alice Chat'])
            guard = runtime.SessionGuard()
            code, body = guard.capture_body(adapter, None, None, None)
            self.assertEqual(code, 'ok')
            self.assertEqual(body, 'synthetic local test body')
        finally:
            runtime.TARGET_OCR_HEADERS = original_headers

    def test_accepts_only_the_versioned_command_shapes(self):
        self.assertEqual(runtime.parse_command('{"v":1,"cmd":"start"}'), {"v": 1, "cmd": "start"})
        self.assertEqual(
            runtime.parse_command('{"v":1,"cmd":"fill","candidate_id":"candidate_2"}'),
            {"v": 1, "cmd": "fill", "candidate_id": "candidate_2"},
        )
        for invalid in (
            '{"v":2,"cmd":"start"}',
            '{"v":1,"cmd":"send"}',
            '{"v":1,"cmd":"start","text":"private payload"}',
            '{"v":1,"cmd":"fill","candidate_id":"../../chat"}',
            '{"v":1,"cmd":"fill","candidate_id":"candidate_1","text":"private payload"}',
            'not json',
        ):
            with self.subTest(invalid=invalid), self.assertRaises(runtime.ProtocolError):
                runtime.parse_command(invalid)

    def test_ranker_returns_only_fixed_template_text_and_bounded_scores(self):
        ranked = runtime.rank_candidates(
            {
                "conversation": "must never leave the process",
                "answers": {
                    "next_step": {
                        "choice": "clarify",
                        "probabilities": {"warm_reply": 0.1, "clarify": 0.8, "boundary": 0.5, "wait": float("nan")},
                    }
                },
            }["answers"]
        )
        self.assertEqual(len(ranked), 3)
        self.assertEqual([item["choice"] for item in ranked], ["clarify", "boundary", "warm_reply"])
        self.assertEqual([item["id"] for item in ranked], ["candidate_1", "candidate_2", "candidate_3"])
        allowed_text = {item["text"] for item in runtime.FIXED_TEMPLATES}
        self.assertTrue(all(item["text"] in allowed_text for item in ranked))
        self.assertFalse(any("conversation" in item for item in ranked))
        self.assertIsNone(runtime._safe_score(float("nan")))
        self.assertIsNone(runtime._safe_score(2.0))

    def test_run_protocol_emits_typed_status_only(self):
        class FakeWorker:
            def __init__(self, _hwnd, _emit):
                self.shutdown_event = type("Flag", (), {"set": lambda _self: None})()
                self.monitor_event = type("Flag", (), {"clear": lambda _self: None})()
                self.monitor_thread = None

            def handle_command(self, command):
                return command["cmd"] != "shutdown"

        output = StringIO()
        rc = runtime.run_protocol(
            StringIO('{"v":1,"cmd":"start"}\n{"v":1,"cmd":"shutdown"}\n'),
            output,
            worker_factory=FakeWorker,
        )
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(rc, 0)
        self.assertEqual(messages, [{"v": 1, "event": "status", "code": "worker_ready"}])

    def test_worker_candidates_match_wpf_allowlist_and_enable_fill(self):
        host = WPF_SOURCE.read_text(encoding="utf-8")
        allowlist_match = re.search(
            r"\$script:WorkerTemplateAllowlistJson\s*=\s*'([^']+)'", host
        )
        self.assertIsNotNone(allowlist_match)
        host_allowlist = json.loads(allowlist_match.group(1))
        allowed_pairs = {(item["label"], item["text"]) for item in host_allowlist}

        ranked = runtime.rank_candidates(
            {
                "next_step": {
                    "choice": "wait",
                    "probabilities": {
                        "wait": 0.95,
                        "clarify": 0.80,
                        "boundary": 0.70,
                        "warm_reply": 0.10,
                    },
                }
            }
        )
        worker_event = {
            "v": 1,
            "event": "candidates",
            "code": "analyzing",
            "items": [
                {key: item[key] for key in ("id", "label", "text", "score")}
                for item in ranked
            ],
        }
        event_items = worker_event["items"]
        accepted = (
            len(event_items) == 3
            and {item["id"] for item in event_items}
            == {"candidate_1", "candidate_2", "candidate_3"}
            and all((item["label"], item["text"]) in allowed_pairs for item in event_items)
        )
        self.assertTrue(accepted)

        setter = host.split("function Set-CandidateItems", 1)[1].split(
            "function Invoke-WorkerEvent", 1
        )[0]
        handler = host.split("function Invoke-WorkerEvent", 1)[1].split(
            "function New-CandidateRow", 1
        )[0]
        self.assertIn("$script:WorkerTemplateAllowlist | Where-Object", setter)
        self.assertIn("$view.Fill.IsEnabled = $true", setter)
        self.assertIn("return $true", setter)
        reject_invalid_event = "if (-not (Set-CandidateItems -Items @($eventData.items))) { return }"
        self.assertIn(reject_invalid_event, handler)
        self.assertLess(handler.index(reject_invalid_event), handler.index("$script:WorkerReady = $true"))

    def test_mock_worker_thread_stops_after_protocol_shutdown_without_desktop_access(self):
        class NoWindowAdapter:
            def __init__(self, _overlay_hwnd):
                pass

            def foreground_rect(self):
                return None

        workers = []

        def make_worker(hwnd, emit):
            worker = runtime.LayaWorker(
                hwnd,
                emit,
                adapter_factory=NoWindowAdapter,
                model_factory=lambda: (object(), object()),
                screen_factory=object,
            )
            workers.append(worker)
            return worker

        output = StringIO()
        with patch.dict(sys.modules, {"numpy": types.ModuleType("numpy")}):
            rc = runtime.run_protocol(
                StringIO('{"v":1,"cmd":"start"}\n{"v":1,"cmd":"shutdown"}\n'),
                output,
                worker_factory=make_worker,
            )
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(rc, 0)
        self.assertEqual(len(workers), 1)
        self.assertFalse(workers[0].monitor_thread.is_alive())
        self.assertTrue(all(message["event"] == "status" for message in messages))
        self.assertTrue(all(set(message) == {"v", "event", "code"} for message in messages))
        self.assertTrue(all(message["code"] in runtime.STATUS_TEXT for message in messages))

    def test_captured_chat_body_reaches_local_router_without_ipc_payload(self):
        observed = []

        class RecordingRouter:
            def predict(self, state, _questions, model=None):
                observed.append((state, model))
                return {"answers": {"next_step": {"choice": "wait"}}}

        worker = runtime.LayaWorker(
            0,
            lambda _event: None,
            adapter_factory=lambda _hwnd: object(),
            model_factory=lambda: (object(), RecordingRouter()),
            screen_factory=object,
        )
        worker.guard.capture_body = lambda *_args, **_kwargs: (
            "ok",
            "真实窗口中的可见聊天消息",
        )

        with patch.object(runtime, "POLL_SECONDS", 0.01):
            worker.handle_command({"cmd": "start"})
            thread = worker.monitor_thread
            self.assertIsNotNone(thread)
            for _ in range(100):
                if observed:
                    break
                thread.join(timeout=0.01)
            worker.shutdown_event.set()
            worker.monitor_event.clear()
            thread.join(timeout=1)

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0]["conversation"], "真实窗口中的可见聊天消息")
        self.assertEqual(observed[0][0]["contact"], runtime.TARGET_CONTACT)
        self.assertEqual(observed[0][1], "english")


class SessionSafetyTests(unittest.TestCase):
    def test_import_is_headless_and_does_not_initialize_windows(self):
        source = Path(runtime.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(any(name == "tkinter" or name.startswith("tkinter.") for name in imported))
        self.assertFalse(any("tk.Tk" in line or "tk.Canvas" in line for line in source.splitlines()))
        self.assertIn("def _initialize_win32()", source)

    def test_header_is_checked_before_body_capture(self):
        adapter = FakeAdapter(headers=["Other contact"])
        guard = runtime.SessionGuard()
        code, body = guard.capture_body(adapter, None, None, None)
        self.assertEqual(code, "wrong_header")
        self.assertIsNone(body)
        self.assertEqual(adapter.body_reads, 0)
        self.assertNotIn("body", adapter.calls)

    def test_header_normalization_preserves_digits_and_latin_suffixes(self):
        self.assertEqual(runtime.header_signature(f"  {runtime.TARGET_CONTACT}\n"), runtime.TARGET_CONTACT)
        for suffix in ("123", "DeepSeek", "A1"):
            with self.subTest(suffix=suffix):
                adapter = FakeAdapter(headers=[runtime.TARGET_CONTACT + suffix])
                guard = runtime.SessionGuard()
                code, body = guard.capture_body(adapter, None, None, None)
                self.assertEqual(code, "wrong_header")
                self.assertIsNone(body)
                self.assertEqual(adapter.body_reads, 0)

    def test_target_header_binds_session_and_body_is_revalidated_after_capture(self):
        adapter = FakeAdapter(
            headers=[
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT,
            ]
        )
        guard = runtime.SessionGuard()
        code, body = guard.capture_body(adapter, None, None, None)
        self.assertEqual(code, "ok")
        self.assertEqual(guard.bound_header, runtime.TARGET_CONTACT)
        self.assertEqual(body, "synthetic local test body")
        self.assertLess(adapter.calls.index("body"), len(adapter.calls) - 1)
        self.assertEqual(adapter.calls.count("header"), 4)

    def test_same_hwnd_header_change_before_body_capture_prevents_body_read(self):
        adapter = FakeAdapter(
            headers=[
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT + "123",
            ]
        )
        guard = runtime.SessionGuard()
        code, body = guard.capture_body(adapter, None, None, None)
        self.assertEqual(code, "session_changed")
        self.assertIsNone(body)
        self.assertTrue(guard.blocked)
        self.assertEqual(adapter.body_reads, 0)
        self.assertNotIn("body", adapter.calls)
        self.assertEqual(adapter.calls.count("header"), 3)

    def test_same_hwnd_header_change_during_body_capture_discards_body(self):
        adapter = FakeAdapter(
            rects=[TARGET_RECT, TARGET_RECT, TARGET_RECT, TARGET_RECT, TARGET_RECT],
            headers=[
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT,
                runtime.TARGET_CONTACT + "123",
            ],
        )
        guard = runtime.SessionGuard()
        code, body = guard.capture_body(adapter, None, None, None)
        self.assertEqual(code, "session_changed")
        self.assertIsNone(body)
        self.assertTrue(guard.blocked)
        self.assertEqual(adapter.body_reads, 1)
        self.assertEqual(adapter.calls.count("header"), 4)

    def test_pause_during_header_ocr_prevents_body_capture_and_classification(self):
        second_header_started = threading.Event()
        allow_second_header_return = threading.Event()

        class HeaderBlockingAdapter:
            def __init__(self, _overlay_hwnd):
                self.header_reads = 0
                self.body_reads = 0

            def foreground_rect(self):
                return TARGET_RECT

            def read_header(self, _rect, _screen, _numpy, _ocr):
                self.header_reads += 1
                if self.header_reads == 2:
                    second_header_started.set()
                    allow_second_header_return.wait(timeout=3)
                return runtime.TARGET_CONTACT

            def read_body(self, _rect, _screen, _numpy, _ocr):
                self.body_reads += 1
                return "synthetic local test body that must not be classified"

        class RecordingRouter:
            def __init__(self):
                self.calls = 0

            def predict(self, *_args, **_kwargs):
                self.calls += 1
                return {"answers": {"next_step": {"choice": "wait"}}}

        adapter = HeaderBlockingAdapter(0)
        router = RecordingRouter()
        worker = runtime.LayaWorker(
            0,
            lambda _event: None,
            adapter_factory=lambda _overlay_hwnd: adapter,
            model_factory=lambda: (object(), router),
            screen_factory=object,
        )
        with patch.dict(sys.modules, {"numpy": types.ModuleType("numpy")}):
            with patch.object(runtime, "normalize_body", wraps=runtime.normalize_body) as normalize:
                worker.handle_command({"cmd": "start"})
                thread = worker.monitor_thread
                try:
                    self.assertTrue(second_header_started.wait(timeout=3))
                    worker.handle_command({"cmd": "pause"})
                finally:
                    allow_second_header_return.set()
                    worker.shutdown_event.set()
                    if thread is not None:
                        thread.join(timeout=3)
                self.assertIsNotNone(thread)
                self.assertFalse(thread.is_alive())
                normalize.assert_not_called()
        self.assertEqual(adapter.body_reads, 0)
        self.assertEqual(router.calls, 0)

    def test_pause_during_body_capture_skips_normalization_and_classification(self):
        capture_entered = threading.Event()
        allow_body_return = threading.Event()

        class BlockingAdapter:
            def __init__(self, _overlay_hwnd):
                pass

            def foreground_rect(self):
                return TARGET_RECT

            def read_header(self, _rect, _screen, _numpy, _ocr):
                return runtime.TARGET_CONTACT

            def read_body(self, _rect, _screen, _numpy, _ocr):
                capture_entered.set()
                allow_body_return.wait(timeout=3)
                return "synthetic local test body that must not be classified"

        class RecordingRouter:
            def __init__(self):
                self.calls = 0

            def predict(self, *_args, **_kwargs):
                self.calls += 1
                return {"answers": {"next_step": {"choice": "wait"}}}

        router = RecordingRouter()
        worker = runtime.LayaWorker(
            0,
            lambda _event: None,
            adapter_factory=BlockingAdapter,
            model_factory=lambda: (object(), router),
            screen_factory=object,
        )
        with patch.dict(sys.modules, {"numpy": types.ModuleType("numpy")}):
            with patch.object(runtime, "normalize_body", wraps=runtime.normalize_body) as normalize:
                worker.handle_command({"cmd": "start"})
                thread = worker.monitor_thread
                try:
                    self.assertTrue(capture_entered.wait(timeout=3))
                    worker.handle_command({"cmd": "pause"})
                finally:
                    allow_body_return.set()
                    worker.shutdown_event.set()
                    if thread is not None:
                        thread.join(timeout=3)
                self.assertIsNotNone(thread)
                self.assertFalse(thread.is_alive())
                normalize.assert_not_called()
        self.assertEqual(router.calls, 0)

    def test_changed_header_stops_before_reading_a_different_body(self):
        adapter = FakeAdapter(headers=["Other contact"])
        guard = runtime.SessionGuard()
        guard.bound_header = runtime.TARGET_CONTACT
        code, body = guard.capture_body(adapter, None, None, None)
        self.assertEqual(code, "session_changed")
        self.assertIsNone(body)
        self.assertTrue(guard.blocked)
        self.assertEqual(adapter.body_reads, 0)

    def test_foreground_change_between_header_and_body_stops_capture(self):
        other = dict(TARGET_RECT, hwnd=202)
        adapter = FakeAdapter(rects=[TARGET_RECT, other], headers=[runtime.TARGET_CONTACT])
        guard = runtime.SessionGuard()
        code, body = guard.capture_body(adapter, None, None, None)
        self.assertEqual(code, "foreground_changed")
        self.assertIsNone(body)
        self.assertEqual(adapter.body_reads, 0)

    def test_fill_requires_bound_exact_header_and_caret_before_ctrl_v(self):
        adapter = FakeAdapter(
            rects=[TARGET_RECT, TARGET_RECT, TARGET_RECT, TARGET_RECT],
            headers=[runtime.TARGET_CONTACT, runtime.TARGET_CONTACT],
            carets=[True, True],
        )
        guard = runtime.SessionGuard()
        guard.bound_header = runtime.TARGET_CONTACT
        code = guard.fill_candidate(adapter, runtime.FIXED_TEMPLATES[0]["text"], None, None, None)
        self.assertEqual(code, "draft_filled")
        self.assertEqual(adapter.pastes, 1)
        self.assertEqual(adapter.calls[-1], "paste_ctrl_v")
        self.assertNotIn("enter", adapter.calls)
        self.assertEqual(len(adapter.clipboard), 1)

    def test_absent_caret_copies_only_and_never_pastes(self):
        adapter = FakeAdapter(headers=[runtime.TARGET_CONTACT], carets=[False])
        guard = runtime.SessionGuard()
        guard.bound_header = runtime.TARGET_CONTACT
        text = runtime.FIXED_TEMPLATES[1]["text"]
        code = guard.fill_candidate(adapter, text, None, None, None)
        self.assertEqual(code, "copied_no_caret")
        self.assertEqual(adapter.clipboard, [text])
        self.assertEqual(adapter.pastes, 0)
        self.assertNotIn("paste_ctrl_v", adapter.calls)

    def test_changed_header_during_final_fill_guard_refuses_paste(self):
        adapter = FakeAdapter(
            rects=[TARGET_RECT, TARGET_RECT, TARGET_RECT, TARGET_RECT],
            headers=[runtime.TARGET_CONTACT, "Other contact"],
            carets=[True],
        )
        guard = runtime.SessionGuard()
        guard.bound_header = runtime.TARGET_CONTACT
        code = guard.fill_candidate(adapter, runtime.FIXED_TEMPLATES[0]["text"], None, None, None)
        self.assertEqual(code, "session_changed")
        self.assertEqual(adapter.pastes, 0)


class WpfContractTests(unittest.TestCase):
    def test_real_launcher_passes_configured_contact_to_the_wpf_host(self):
        host = WPF_SOURCE.read_text(encoding='utf-8')
        launcher = LAUNCH_SOURCE.read_text(encoding='utf-8')
        self.assertIn('[string]$TargetContact', host)
        self.assertIn('$env:LAYA_TARGET_CONTACT = $script:TargetContact', host)
        self.assertIn("'-TargetContact'", launcher)
        self.assertIn("$TargetContact", launcher)

    def test_active_entrypoint_is_sta_powershell_wpf_without_tk_path(self):
        host = WPF_SOURCE.read_text(encoding="utf-8")
        launcher = LAUNCH_SOURCE.read_text(encoding="utf-8")
        runtime_source = Path(runtime.__file__).read_text(encoding="utf-8")
        self.assertIn("PresentationFramework", host)
        self.assertIn("System.Windows.Window", host)
        self.assertIn("New-Object System.Windows.Window", host)
        self.assertIn("[System.Windows.Threading.Dispatcher]::Run()", host)
        self.assertIn("$script:Window.Show()", host)
        self.assertIn("-STA", launcher)
        self.assertIn("monitor_app.ps1", launcher)
        self.assertNotIn("monitor_app.py", launcher)
        self.assertNotIn("tkinter", runtime_source.lower())
        self.assertIn("AllowsTransparency = $true", host)
        self.assertIn("TextRenderingMode]::Auto", host)
        self.assertIn("WS_EX_NOACTIVATE", host)

    def test_synthetic_preview_cannot_start_worker_or_touch_system_clipboard(self):
        host = WPF_SOURCE.read_text(encoding="utf-8")
        preview_copy = host.split("function Invoke-CopyCandidate", 1)[1].split("function Invoke-FillCandidate", 1)[0]
        preview_fill = host.split("function Invoke-FillCandidate", 1)[1].split("function Set-CandidateItems", 1)[0]
        startup = host.split("$script:Window.Show()", 1)[1]
        self.assertIn("if ($script:PreviewMode)", preview_copy)
        self.assertLess(preview_copy.index("if ($script:PreviewMode)"), preview_copy.index("[System.Windows.Clipboard]::SetText"))
        self.assertIn("if ($script:PreviewMode)", preview_fill)
        self.assertLess(preview_fill.index("if ($script:PreviewMode)"), preview_fill.index("Send-WorkerCommand 'fill'"))
        self.assertIn("if (-not $script:PreviewMode)", startup)
        self.assertLess(startup.index("if (-not $script:PreviewMode)"), startup.index("Start-Worker"))
        preview_action = preview_copy.index("Set-Status ('预览：")
        preview_return = preview_copy.index("return", preview_action)
        clipboard_write = preview_copy.index("[System.Windows.Clipboard]::SetText")
        self.assertLess(preview_return, clipboard_write)

    def test_stop_and_launcher_match_exact_host_command_line_and_do_not_force_kill(self):
        launcher = LAUNCH_SOURCE.read_text(encoding="utf-8")
        stopper = STOP_SOURCE.read_text(encoding="utf-8")
        self.assertIn("monitor_app.ps1", launcher)
        self.assertIn("ExecutablePath", launcher)
        self.assertIn("CommandLine", launcher)
        self.assertIn("CloseMainWindow", stopper)
        self.assertIn("CommandLine", stopper)
        self.assertNotIn("Stop-Process", stopper)
        self.assertNotIn("process.Kill", stopper)


if __name__ == "__main__":
    unittest.main()
