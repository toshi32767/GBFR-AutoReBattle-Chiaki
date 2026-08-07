import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from time import monotonic

import main
import numpy as np
from PIL import Image, ImageDraw
from module.controller import (
    Controller,
    crop_normalized_relative_region,
    crop_window_capture_to_client_area,
    normalize_recognition_frame,
    prepare_ocr_image,
    stream_caption_matches_target,
)


class FakeRelink:
    def __init__(self, languages=("zh", "ja")):
        self.running = True
        self.paused = False
        self.background_mode = False
        self.languages = languages
        self.confirmed = []
        self.pressed = []
        self.released = 0

    def ui_language_candidates(self):
        return self.languages

    def confirm_ui_language(self, language, evidence=""):
        self.confirmed.append((language, evidence))

    def release_automation_inputs(self):
        self.released += 1

    def press(self, key, movement=None, interval=None):
        self.pressed.append((key, movement))

    def capture_frame_state(self):
        return (0, None)

    def wait_for_fresh_capture(self, serial, timeout=0):
        return True


class ImageRelink:
    def __init__(self, image):
        self.image = image

    def get_window_rect(self, silent=False):
        return (0, 0, self.image.width, self.image.height)

    def screenshot(self, region=None):
        if region is None:
            return self.image
        left, top, width, height = region
        return self.image.crop((left, top, left + width, top + height))

    def ui_language_candidates(self):
        return ("zh", "ja")

    def screenshot_text(self, region_key):
        return self.image

    def recognize_line(self, crop, confidence=0.65, language=None):
        return ""

    def confirm_ui_language(self, language, evidence=""):
        pass


class ReBattleRecoveryTests(unittest.TestCase):
    def tearDown(self):
        # The production state is process-wide because watchdog threads and
        # state machines live in the same process. Keep tests isolated even
        # when an assertion fails while a transaction is held.
        main.clear_automation_flow_state("测试清理")

    def test_automation_flow_is_reentrant_and_blocks_other_threads(self):
        entered = threading.Event()
        release = threading.Event()
        observed = []

        def owner():
            with main.automation_flow("town_recovery") as acquired:
                observed.append(acquired)
                with main.automation_flow("quest_navigation") as nested:
                    observed.append(nested)
                    entered.set()
                    release.wait(timeout=2.0)

        worker = threading.Thread(target=owner)
        worker.start()
        self.assertTrue(entered.wait(timeout=1.0))
        self.assertTrue(main.automation_flow_active())
        self.assertEqual(main.automation_flow_name(), "town_recovery > quest_navigation")
        with main.automation_flow("stream_recovery") as acquired:
            self.assertFalse(acquired)
        release.set()
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(observed, [True, True])
        self.assertFalse(main.automation_flow_active())

    def test_reconnect_route_defers_while_other_flow_owns_navigation(self):
        entered = threading.Event()
        release = threading.Event()

        def owner():
            with main.automation_flow("town_recovery"):
                entered.set()
                release.wait(timeout=2.0)

        worker = threading.Thread(target=owner)
        worker.start()
        self.assertTrue(entered.wait(timeout=1.0))
        relink = FakeRelink()
        with patch.object(main, "_route_reconnected_screen_impl") as route:
            self.assertIsNone(main.route_reconnected_screen(relink, timeout=1.0))
        route.assert_not_called()
        release.set()
        worker.join(timeout=2.0)

    def test_stream_window_watchdog_defers_when_flow_is_active(self):
        class MissingWindowRelink(FakeRelink):
            def stream_binding_is_valid(self):
                return False

        relink = MissingWindowRelink()
        began = []

        def stop_after_poll(_seconds):
            relink.running = False

        with patch.dict(
            main.RECOVERY_CONFIG,
            {"chiaki_exe": str(Path(main.__file__)), "nickname": "PS5", "host": "127.0.0.1"},
            clear=True,
        ), patch.object(main, "automation_flow_name", return_value="town_recovery"), patch.object(
            main, "sleep", side_effect=stop_after_poll
        ):
            main.stream_window_watchdog(
                relink,
                lambda: False,
                lambda: began.append(True),
                lambda _phase: None,
            )
        self.assertEqual(began, [])

    def test_recognition_profiles_upscale_low_resolution_without_stretching(self):
        frame = Image.new("RGB", (640, 360), (20, 30, 40))
        normalized, metadata = normalize_recognition_frame(frame, "chiaki_360p")
        self.assertEqual(normalized.size, (640, 360))
        self.assertEqual(metadata["source_size"], (640, 360))
        self.assertFalse(metadata["letterbox_detected"])

    def test_small_ocr_crops_are_upscaled_but_full_canvas_is_unchanged(self):
        small = Image.new("RGB", (80, 24), (20, 30, 40))
        medium = Image.new("RGB", (640, 120), (20, 30, 40))
        low_resolution_full = Image.new("RGB", (1280, 720), (20, 30, 40))
        full = Image.new("RGB", (1920, 1080), (20, 30, 40))
        self.assertEqual(prepare_ocr_image(small).size, (240, 72))
        self.assertEqual(prepare_ocr_image(medium).size, (1280, 240))
        self.assertEqual(prepare_ocr_image(low_resolution_full).size, low_resolution_full.size)
        self.assertEqual(prepare_ocr_image(full).size, full.size)

    def test_relative_regions_use_normalized_client_canvas_at_540p(self):
        # A 540p HWND capture may be physically 962x572 and then crop to a
        # DPI-virtualized 946x533 client frame. After normalization, regions
        # must use the 960x540 client canvas directly, not the old outer size.
        canvas = Image.new("RGB", (960, 540), (0, 0, 0))
        crop = crop_normalized_relative_region(canvas, (0.70, 0.60, 0.90, 0.80))
        self.assertEqual(crop.size, (192, 108))

    def test_screenshot_text_crops_from_normalized_canvas_not_window_chrome(self):
        relink = Controller.__new__(Controller)
        relink.text2region = {"继续": (0.70, 0.60, 0.90, 0.80)}
        relink.window_rect = (100, 100, 962, 572)
        relink.screenshot = lambda region=None: Image.new("RGB", (960, 540), (0, 0, 0))
        self.assertEqual(relink.screenshot_text("继续").size, (192, 108))

    def test_ocr_resize_policy_uses_low_resolution_profile_and_bounds_large_crops(self):
        small = Image.new("RGB", (80, 24), (20, 30, 40))
        medium = Image.new("RGB", (640, 120), (20, 30, 40))
        large = Image.new("RGB", (1600, 1000), (20, 30, 40))
        self.assertEqual(
            prepare_ocr_image(small, "chiaki_360p").size,
            (240, 72),
        )
        self.assertGreaterEqual(
            prepare_ocr_image(medium, "chiaki_540p").width,
            medium.width * 2,
        )
        bounded = prepare_ocr_image(large, "chiaki_1080p")
        self.assertLessEqual(bounded.width * bounded.height, 1600000)

    def test_ocr_resize_budget_handles_fractional_scale_with_integral_dimensions(self):
        # 900x400 at the 540p policy starts at 2x (1.44M pixels), so the
        # budget clamps it to a non-integer scale of sqrt(1M / 360k). This
        # used to pass floats directly to Pillow and repeatedly disable OCR.
        bounded = prepare_ocr_image(
            Image.new("RGB", (900, 400), (20, 30, 40)), "chiaki_540p"
        )
        self.assertEqual(bounded.size, (1500, 666))
        self.assertTrue(all(isinstance(value, int) for value in bounded.size))
        self.assertLessEqual(bounded.width * bounded.height, 1000000)

    def test_ocr_coordinates_are_mapped_back_after_low_resolution_upscale(self):
        class FakeModel:
            def __call__(self, image, use_det=True, use_cls=False):
                self.received_size = image.size
                return [
                    ([[30, 15], [150, 15], [150, 45], [30, 45]], "测试", 0.99)
                ], None

        relink = Controller.__new__(Controller)
        relink.recognition_profile = "chiaki_360p"
        relink.ocrmodels = {"zh": FakeModel()}
        relink.detected_ui_language = "zh"
        relink._ocr_lock = threading.Lock()
        items = relink.ocr(Image.new("RGB", (80, 24), (20, 30, 40)), language="zh")
        self.assertEqual(relink.ocrmodels["zh"].received_size, (240, 72))
        self.assertEqual(items[0]["location"], (9, 4, 51, 16))

    def test_fixed_chiaki_profiles_keep_one_16_9_geometry(self):
        expected = {
            "chiaki_360p": (640, 360),
            "chiaki_540p": (960, 540),
            "chiaki_720p": (1280, 720),
            "chiaki_1080p": (1920, 1080),
        }
        self.assertEqual(
            set(main.RECOGNITION_PROFILES) - {"auto"}, set(expected)
        )
        for profile, size in expected.items():
            frame = Image.new("RGB", size, (30, 40, 50))
            normalized, metadata = normalize_recognition_frame(frame, profile)
            self.assertEqual(normalized.size, size)
            self.assertEqual(metadata["normalized_size"], size)
            self.assertAlmostEqual(size[0] / size[1], 16 / 9, places=6)

    def test_auto_profile_uses_nearest_chiaki_rung_for_full_frame_ocr(self):
        expected = {
            (640, 360): "chiaki_360p",
            (960, 540): "chiaki_540p",
            (1280, 720): "chiaki_720p",
            (1920, 1080): "chiaki_1080p",
        }
        for source_size, canvas in expected.items():
            with self.subTest(source_size=source_size):
                frame = Image.new("RGB", source_size, (30, 40, 50))
                normalized, metadata = normalize_recognition_frame(frame, "auto")
                self.assertEqual(normalized.size, source_size)
                self.assertEqual(metadata["normalization_canvas"], canvas)

    def test_settlement_center_enhancement_is_fallback_only(self):
        class OcrProbe(FakeRelink):
            def __init__(self, result):
                super().__init__(languages=("zh",))
                self.image = Image.new("RGB", (80, 40), (20, 30, 40))
                self.result = result
                self.calls = []

            def screenshot_text(self, _region):
                return self.image

            def ocr(self, image, confidence=0.35, language=None):
                self.calls.append(image)
                return self.result

        hit = OcrProbe([{"text": "结算确认"}])
        self.assertEqual(main.read_settlement_center_texts(hit), {"zh": "结算确认"})
        self.assertEqual(len(hit.calls), 1)

        miss = OcrProbe([])
        self.assertEqual(main.read_settlement_center_texts(miss), {"zh": ""})
        self.assertEqual(len(miss.calls), 3)

    def test_recognition_profiles_crop_confident_16_10_black_bars(self):
        frame = Image.new("RGB", (1920, 1200), (0, 0, 0))
        game = Image.new("RGB", (1920, 1080), (35, 45, 55))
        frame.paste(game, (0, 60))
        normalized, metadata = normalize_recognition_frame(frame, "auto")
        self.assertEqual(metadata["content_rect"], (0, 60, 1920, 1080))
        self.assertTrue(metadata["letterbox_detected"])
        self.assertEqual(normalized.size, (1920, 1080))

    def test_recognition_profiles_preserve_unusual_non_black_client(self):
        frame = Image.new("RGB", (1920, 1200), (35, 45, 55))
        normalized, metadata = normalize_recognition_frame(frame, "auto")
        self.assertEqual(metadata["content_rect"], (0, 0, 1920, 1200))
        self.assertFalse(metadata["letterbox_detected"])
        self.assertLessEqual(normalized.width, 1920)

    def test_background_window_capture_is_cropped_back_to_client_area(self):
        frame = Image.new("RGB", (962, 572), (10, 10, 10))
        ImageDraw.Draw(frame).rectangle((1, 31, 960, 570), fill=(40, 120, 200))
        cropped = crop_window_capture_to_client_area(
            frame, (100, 100, 1062, 672), (101, 131, 1061, 671)
        )
        self.assertEqual(cropped.size, (960, 540))
        self.assertEqual(cropped.getpixel((0, 0)), (40, 120, 200))

    def test_client_only_background_capture_is_not_cropped_twice(self):
        frame = Image.new("RGB", (960, 540), (40, 120, 200))
        cropped = crop_window_capture_to_client_area(
            frame, (100, 100, 1062, 672), (101, 131, 1061, 671)
        )
        self.assertEqual(cropped.size, (960, 540))

    def test_auto_profile_bounds_4k_ocr_canvas(self):
        frame = Image.new("RGB", (3840, 2160), (35, 45, 55))
        normalized, metadata = normalize_recognition_frame(frame, "auto")
        self.assertEqual(normalized.size, (1920, 1080))
        self.assertEqual(metadata["scale"], 0.5)

    def test_resize_settling_fence_is_shared_by_controller_input_modes(self):
        relink = Controller.__new__(Controller)
        relink._geometry_changed_at = monotonic()
        self.assertTrue(relink.recognition_resize_settling())
        relink._geometry_changed_at = monotonic() - 2.0
        self.assertFalse(relink.recognition_resize_settling())

    def test_window_move_does_not_start_resize_settling(self):
        relink = Controller.__new__(Controller)
        relink.window_rect = (100, 100, 960, 540)
        relink.geometry_generation = 0
        relink._geometry_changed_at = 0.0
        relink._window_was_iconic = False
        relink._get_hwnd = lambda: 1
        with patch("module.controller.win32gui.IsIconic", return_value=False), \
             patch("module.controller.win32gui.GetClientRect", return_value=(0, 0, 960, 540)), \
             patch("module.controller.win32gui.ClientToScreen", side_effect=[(120, 130), (1080, 670)]):
            rect = relink.get_window_rect(silent=True)
        self.assertEqual(rect, (120, 130, 960, 540))
        self.assertFalse(relink.recognition_resize_settling())

    def test_window_resize_starts_settling_once(self):
        relink = Controller.__new__(Controller)
        relink.window_rect = (100, 100, 960, 540)
        relink.geometry_generation = 0
        relink._geometry_changed_at = 0.0
        relink._window_was_iconic = False
        relink._get_hwnd = lambda: 1
        with patch("module.controller.win32gui.IsIconic", return_value=False), \
             patch("module.controller.win32gui.GetClientRect", return_value=(0, 0, 1280, 720)), \
             patch("module.controller.win32gui.ClientToScreen", side_effect=[(100, 100), (1380, 820)]):
            rect = relink.get_window_rect(silent=True)
        self.assertEqual(rect, (100, 100, 1280, 720))
        self.assertTrue(relink.recognition_resize_settling())

    def test_launcher_process_watch_ignores_an_unset_parent_pid(self):
        self.assertTrue(main.launcher_process_is_alive(0))

    def test_launcher_watchdog_stops_orphaned_automation(self):
        relink = FakeRelink()
        relink.shutdown_requested = False
        relink.shutdown_reason = None

        def request_shutdown(reason):
            relink.shutdown_requested = True
            relink.shutdown_reason = reason
            relink.release_automation_inputs()

        relink.request_shutdown = request_shutdown
        with patch.object(main, "launcher_process_is_alive", return_value=False), patch.object(
            main, "sleep"
        ):
            main.watch_launcher_process(relink, 1234)
            for _ in range(50):
                if relink.shutdown_requested:
                    break
                threading.Event().wait(0.01)

        self.assertTrue(relink.shutdown_requested)
        self.assertEqual(relink.shutdown_reason, "launcher_closed")
        self.assertEqual(relink.released, 1)

    def test_shutdown_reason_separates_manual_stop_from_chiaki_close(self):
        relink = Controller.__new__(Controller)
        relink._shutdown_requested = False
        relink._shutdown_reason = None
        relink._paused = False
        relink._running = True
        with patch.object(Controller, "release_automation_inputs"):
            relink.request_shutdown("manual_hotkey")
            self.assertTrue(relink.shutdown_requested)
            self.assertEqual(relink.shutdown_reason, "manual_hotkey")

            relink._shutdown_requested = False
            relink._shutdown_reason = None
            relink._running = True
            relink.request_shutdown("close_chiaki_hotkey")
            self.assertEqual(relink.shutdown_reason, "close_chiaki_hotkey")

    def test_close_chiaki_command_is_registered_as_a_combo(self):
        relink = Controller.__new__(Controller)
        relink._hotkeys = {}
        relink._hotkey_combos = {}
        with patch.object(Controller, "_start_hotkey"):
            relink.set_close_chiaki_key()

        self.assertNotIn(Controller.KEY_MAP["f5"], relink._hotkeys)
        self.assertIn(
            (Controller.KEY_MAP["f5"],
             tuple(sorted((Controller.KEY_MAP["ctrl"], Controller.KEY_MAP["shift"])))),
            relink._hotkey_combos,
        )

    def test_window_matching_uses_user_configured_title(self):
        self.assertTrue(
            stream_caption_matches_target("自定义串流窗口", "自定义串流窗口 - 1920x1080")
        )
        self.assertFalse(
            stream_caption_matches_target("Chiaki | Stream", "Chiaki")
        )
        self.assertFalse(
            stream_caption_matches_target("自定义串流窗口", "Chiaki | Stream")
        )

    def test_foreground_press_skips_global_input_when_window_is_missing(self):
        relink = Controller.__new__(Controller)
        relink._paused = False
        relink.background_mode = False
        relink._foreground_pressed_keys = set()
        with patch.object(Controller, "_get_hwnd", return_value=None), patch.object(
            Controller, "_send_key"
        ) as send_key:
            self.assertFalse(relink.press("enter"))
        send_key.assert_not_called()

    def test_foreground_release_can_clear_a_key_pressed_before_window_loss(self):
        relink = Controller.__new__(Controller)
        relink._paused = False
        relink.background_mode = False
        relink._foreground_pressed_keys = {"enter"}
        with patch.object(Controller, "_get_hwnd", return_value=None), patch.object(
            Controller, "_send_key"
        ) as send_key:
            self.assertTrue(relink.press("enter", movement="release"))
        send_key.assert_called_once_with(Controller.KEY_MAP["enter"], keyup=True)
        self.assertEqual(relink._foreground_pressed_keys, set())

    def test_background_mode_never_runs_legacy_mouse_clicks(self):
        relink = Controller.__new__(Controller)
        relink.background_mode = True
        with patch.object(Controller, "_get_hwnd") as get_hwnd:
            relink.click()
        get_hwnd.assert_not_called()

    def test_settlement_no_navigation_is_not_repeated_on_a_stale_frame(self):
        class DialogRelink(FakeRelink):
            def __init__(self):
                super().__init__()
                self.image = Image.new("RGB", (1000, 1000), (20, 20, 20))
                self._paint_selection("no")

            def _paint_selection(self, selection):
                draw = ImageDraw.Draw(self.image)
                draw.rectangle((350, 600, 650, 650), fill=(20, 20, 20))
                draw.rectangle((350, 650, 650, 700), fill=(20, 20, 20))
                y0 = 600 if selection == "yes" else 650
                draw.rectangle((350, y0, 650, y0 + 50), fill=(35, 80, 220))

            def screenshot(self, region=None):
                if region is None:
                    return self.image
                left, top, width, height = region
                return self.image.crop((left, top, left + width, top + height))

            def press(self, key, movement=None, interval=None):
                super().press(key, movement, interval)
                if key == main.D_PAD_UP_KEY:
                    self._paint_selection("yes")

        relink = DialogRelink()
        state = {"no_to_yes_sent": False}
        with patch.object(main, "sleep"):
            self.assertEqual(
                main.handle_settlement_confirmation(relink, state), "navigated"
            )
            self.assertEqual(
                main.handle_settlement_confirmation(relink, state), "confirmed"
            )
            self.assertEqual(
                main.handle_settlement_confirmation(relink, state), "waiting"
            )
            self.assertEqual(
                [key for key, _ in relink.pressed],
                [main.D_PAD_UP_KEY, main.CROSS_KEY],
            )

    def test_japanese_visual_confirmation_fallback_requires_known_language(self):
        class JapaneseDialogRelink(FakeRelink):
            def __init__(self, configured_language, detected_language):
                super().__init__()
                self.ui_language_mode = configured_language
                self.detected_ui_language = detected_language

        japanese = JapaneseDialogRelink("ja", "ja")
        automatic = JapaneseDialogRelink("auto", None)
        chinese = JapaneseDialogRelink("zh", "zh")
        with patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(main, "detect_stable_result_ui", return_value="继续"):
            self.assertTrue(main.japanese_settlement_highlight_dialog_active(japanese))
            self.assertFalse(main.japanese_settlement_highlight_dialog_active(automatic))
        self.assertFalse(main.japanese_settlement_highlight_dialog_active(chinese))

    def test_japanese_visual_confirmation_does_not_steal_next_prompt(self):
        class JapaneseDialogRelink(FakeRelink):
            ui_language_mode = "ja"
            detected_ui_language = "ja"

        relink = JapaneseDialogRelink(("ja",))
        with patch.object(
            main, "region_has_marker", return_value=True
        ), patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(
            main, "detect_stable_result_ui", return_value="继续"
        ):
            self.assertFalse(main.japanese_settlement_highlight_dialog_active(relink))

    def test_japanese_visual_confirmation_can_establish_language_from_dialog(self):
        class JapaneseDialogRelink:
            ui_language_mode = "auto"
            detected_ui_language = None

            def confirm_ui_language(self, language, semantic):
                self.confirmed = (language, semantic)

        japanese = JapaneseDialogRelink()
        with patch.object(
            main,
            "read_region_texts",
            return_value={"zh": "", "ja": "リザルト確認"},
        ), patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(
            main, "detect_stable_result_ui", return_value="继续"
        ), patch.object(japanese, "confirm_ui_language") as confirm:
            self.assertTrue(main.japanese_settlement_highlight_dialog_active(japanese))
        confirm.assert_called_once_with("ja", "settlement_confirmation")

    def test_japanese_visual_confirmation_does_not_fire_from_town_blue_hud(self):
        class JapaneseDialogRelink(FakeRelink):
            ui_language_mode = "ja"
            detected_ui_language = "ja"

        relink = JapaneseDialogRelink(("ja",))
        with patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(main, "detect_stable_result_ui", return_value=None):
            self.assertFalse(main.japanese_settlement_highlight_dialog_active(relink))

    def test_japanese_result_confirmation_uses_center_dialog_when_prompt_is_gone(self):
        class JapaneseDialogRelink(FakeRelink):
            ui_language_mode = "ja"
            detected_ui_language = "ja"

        relink = JapaneseDialogRelink(("ja",))
        with patch.object(
            main, "read_region_texts", return_value={"zh": "", "ja": "リザルト確認"}
        ), patch.object(
            main, "detect_stable_result_ui", return_value=None
        ), patch.object(
            main, "region_has_marker", return_value=False
        ), patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ):
            self.assertTrue(main.japanese_settlement_highlight_dialog_active(relink))
            self.assertEqual(
                main.handle_japanese_settlement_highlight(relink), "confirmed"
            )
        self.assertEqual(relink.pressed, [(main.CROSS_KEY, None)])

    def test_japanese_retry_button_never_enters_generic_highlight_fallback(self):
        class JapaneseDialogRelink(FakeRelink):
            ui_language_mode = "ja"
            detected_ui_language = "ja"

        relink = JapaneseDialogRelink(("ja",))
        with patch.object(
            main, "read_region_texts", return_value={"zh": "", "ja": "BATTLE RESULT"}
        ), patch.object(
            main, "read_japanese_result_retry_text", return_value="再挑戦する"
        ), patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(
            main, "detect_stable_result_ui", return_value="继续"
        ):
            self.assertFalse(main.japanese_settlement_highlight_dialog_active(relink))

    def test_japanese_next_cross_allows_one_followup_default_yes_confirmation(self):
        class JapaneseDialogRelink(FakeRelink):
            ui_language_mode = "ja"
            detected_ui_language = "ja"

        relink = JapaneseDialogRelink(("ja",))
        with patch.object(main, "region_has_marker", return_value=True), patch.object(
            main, "detect_stable_result_ui", return_value="继续"
        ), patch.object(main, "settlement_confirmation_selection", return_value=None):
            self.assertTrue(main.press_verified_result_continue(relink))
        self.assertGreater(getattr(relink, "_japanese_result_confirmation_deadline", 0), main.time())

        with patch.object(
            main, "detect_stable_result_ui", return_value=None
        ), patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(main, "time", return_value=0.0):
            # The pending deadline is in the future relative to the patched
            # clock, so only the post-次へ confirmation is allowed here.
            self.assertTrue(main.japanese_settlement_highlight_dialog_active(relink))
            self.assertEqual(main.handle_japanese_settlement_highlight(relink), "confirmed")
        self.assertEqual(
            [key for key, _ in relink.pressed],
            [main.CROSS_KEY, main.CROSS_KEY, main.CROSS_KEY, main.CROSS_KEY],
        )

    def test_japanese_settlement_highlight_confirms_yes_directly(self):
        relink = FakeRelink()
        with patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ):
            result = main.handle_japanese_settlement_highlight(relink)
        self.assertEqual(result, "confirmed")
        self.assertEqual(relink.pressed, [(main.CROSS_KEY, None)])

    def test_japanese_retry_confirmation_moves_up_then_confirms_yes(self):
        relink = FakeRelink(("ja",))
        with patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(relink, "wait_for_fresh_capture", return_value=True):
            self.assertEqual(
                main.handle_japanese_retry_confirmation(relink), "confirmed"
            )
        self.assertEqual(
            [key for key, _ in relink.pressed], [main.D_PAD_UP_KEY, main.CROSS_KEY]
        )

    def test_japanese_retry_confirmation_marker_wins_over_enabled_retry_page(self):
        relink = FakeRelink(("ja",))
        with patch.object(
            main,
            "read_settlement_center_texts",
            return_value={"zh": "", "ja": "再挑戦確認 引き続きこのクエストに挑戦しますか"},
        ):
            self.assertTrue(main.japanese_retry_confirmation_present(relink))

    def test_japanese_retry_confirmation_title_crop_wins_before_retry_controls(self):
        relink = FakeRelink(("ja",))
        with patch.object(
            main,
            "read_japanese_retry_confirmation_title",
            return_value="再挑戦確認",
        ), patch.object(
            main,
            "read_settlement_center_texts",
            side_effect=AssertionError("title crop should take priority"),
        ):
            self.assertTrue(main.japanese_retry_confirmation_present(relink))

    def test_japanese_retry_confirmation_accepts_low_resolution_title_variant(self):
        relink = FakeRelink(("ja",))
        with patch.object(
            main,
            "read_settlement_center_texts",
            return_value={"zh": "", "ja": "再排戦確譚"},
        ):
            self.assertTrue(main.japanese_retry_confirmation_present(relink))

    def test_japanese_retry_confirmation_is_excluded_from_generic_highlight(self):
        class JapaneseDialogRelink(FakeRelink):
            ui_language_mode = "ja"
            detected_ui_language = "ja"

        relink = JapaneseDialogRelink(("ja",))
        with patch.object(
            main,
            "read_region_texts",
            return_value={"zh": "", "ja": "再挑戦確認"},
        ), patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ), patch.object(main, "detect_stable_result_ui", return_value="继续"):
            self.assertFalse(main.japanese_settlement_highlight_dialog_active(relink))

    def test_unexpected_town_recovery_returns_router_outcome(self):
        relink = FakeRelink()
        with patch.object(main, "route_reconnected_screen", return_value="battle_wait") as route:
            outcome = main.recover_unexpected_town_state(
                relink,
                reason="结算阶段持续没有识别到继续",
                timeout=17.0,
            )

        self.assertEqual(outcome, "battle_wait")
        self.assertEqual(relink.released, 1)
        route.assert_called_once_with(relink, timeout=17.0)

    def test_unexpected_town_recovery_keeps_failed_route_failed(self):
        relink = FakeRelink()
        with patch.object(main, "route_reconnected_screen", return_value=None):
            outcome = main.recover_unexpected_town_state(
                relink,
                reason="battle HUD 丢失",
            )

        self.assertIsNone(outcome)
        self.assertEqual(relink.released, 1)
        self.assertEqual(relink.pressed, [])

    def test_menu_limit_falls_back_to_town_probe_and_quest_recovery(self):
        relink = FakeRelink()
        clock = {"value": 0.0}

        def fake_time():
            clock["value"] += 0.1
            return clock["value"]

        def fake_sleep(seconds):
            clock["value"] += float(seconds)

        states = iter(("game_menu", "game_menu", "game_menu", "game_menu", "town_menu"))
        with patch.object(main, "time", side_effect=fake_time), patch.object(
            main, "sleep", side_effect=fake_sleep
        ), patch.object(main, "frame_activity_signature", return_value=None), patch.object(
            main, "classify_reconnected_screen", side_effect=states
        ), patch.object(main, "recover_last_town_quest", return_value=True) as recover:
            outcome = main.route_reconnected_screen(relink, timeout=30.0)

        self.assertEqual(outcome, "battle_wait")
        self.assertIn((main.L2_KEY, None), relink.pressed)
        recover.assert_called_once_with(relink, destination_menu_open=True)

    def test_marker_language_selection_supports_chinese_and_japanese(self):
        for language, marker in (
            ("zh", "任务中心"),
            ("ja", "クエストカウンター"),
        ):
            with self.subTest(language=language):
                relink = FakeRelink()
                texts = {"zh": "", "ja": ""}
                texts[language] = marker
                self.assertEqual(
                    main._match_marker_language(
                        relink, texts, "quest_destination"
                    ),
                    language,
                )
                self.assertEqual(relink.confirmed[-1][0], language)

    def test_japanese_fast_travel_marker_is_classified_before_quest_menu(self):
        relink = FakeRelink(("ja",))
        with patch.object(
            main, "battle_hud_visual_candidate", return_value=(False, 0.0)
        ), patch.object(main, "detect_stable_result_ui", return_value=None), patch.object(
            main,
            "full_frame_texts",
            return_value={"zh": "", "ja": "移動先遥 鍛冶屋 決定"},
        ):
            self.assertEqual(
                main.classify_reconnected_screen(relink, allow_town_menu=True),
                "town_fast_travel",
            )

    def test_fast_travel_requires_matching_language_configuration(self):
        relink = FakeRelink(("zh",))
        with patch.object(
            main, "battle_hud_visual_candidate", return_value=(False, 0.0)
        ), patch.object(main, "detect_stable_result_ui", return_value=None), patch.object(
            main,
            "full_frame_texts",
            return_value={"zh": "移動先遥 鍛冶屋 決定", "ja": ""},
        ):
            self.assertIsNone(
                main.classify_reconnected_screen(relink, allow_town_menu=True)
            )

    def test_fast_travel_reuses_town_state_machine_without_second_l2(self):
        relink = FakeRelink()
        clock = {"value": 0.0}

        def fake_time():
            clock["value"] += 0.1
            return clock["value"]

        def fake_sleep(seconds):
            clock["value"] += float(seconds)

        # The router first performs a normal probe, then opens L2 and performs
        # the town-menu probe with allow_town_menu=True.
        states = iter((None, None, "town_fast_travel"))
        with patch.object(main, "time", side_effect=fake_time), patch.object(
            main, "sleep", side_effect=fake_sleep
        ), patch.object(main, "frame_activity_signature", return_value=None), patch.object(
            main, "classify_reconnected_screen", side_effect=states
        ), patch.object(
            main, "recover_last_town_quest", return_value=True
        ) as recover:
            outcome = main.route_reconnected_screen(relink, timeout=30.0)

        self.assertEqual(outcome, "battle_wait")
        recover.assert_called_once_with(relink, destination_menu_open=True)
        self.assertEqual(
            [key for key, _ in relink.pressed].count(main.L2_KEY), 1
        )
        self.assertNotIn((main.MOON_KEY, None), relink.pressed)

    def test_japanese_retry_ocr_variant_is_limited_to_result_control(self):
        self.assertTrue(
            main._text_matches_marker(
                "ひ再規戦するあ）", "ja", "result_retry_available"
            )
        )
        self.assertTrue(
            main._text_matches_marker(
                "ひ再規戦するあ）", "ja", "result_retry_any"
            )
        )
        self.assertTrue(
            main._text_matches_marker(
                "再排製する", "ja", "result_retry_available"
            )
        )

    def test_japanese_retry_fuzzy_ocr_accepts_partial_540p_forms(self):
        self.assertEqual(main._japanese_retry_text_state("再挑戦す"), "available")
        self.assertEqual(main._japanese_retry_text_state("再排製"), "available")
        self.assertEqual(main._japanese_retry_text_state("再戦"), "available")
        self.assertEqual(main._japanese_retry_text_state("再挑戦をキヤンセル"), "enabled")
        self.assertIsNone(main._japanese_retry_text_state("獲得報酬"))

    def test_japanese_retry_state_uses_lower_left_button_fallback(self):
        class RetryRelink(FakeRelink):
            def screenshot(self):
                return Image.new("RGB", (960, 540), (0, 0, 0))

            def ocr(self, image, confidence=0.40, language=None):
                self.ocr_region = image.size
                return [{"text": "再挑戦する"}]

        relink = RetryRelink(("ja",))
        with patch.object(
            main, "read_region_texts", return_value={"zh": "", "ja": ""}
        ), patch.object(
            main, "result_repeat_indicator_is_stably_gold", return_value=False
        ):
            self.assertEqual(main.result_retry_state(relink), "available")
        self.assertEqual(relink.ocr_region, (1440, 456))

    def test_japanese_retry_cancel_text_requires_gold_indicator(self):
        relink = FakeRelink(("ja",))
        with patch.object(
            main, "read_region_texts", return_value={"zh": "", "ja": "キャンセル"}
        ), patch.object(
            main, "result_repeat_indicator_is_stably_gold", return_value=False
        ):
            self.assertIsNone(main.result_retry_state(relink))

    def test_japanese_result_continue_accepts_final_glyph_from_narrow_crop(self):
        self.assertTrue(main._text_matches_marker("へ", "ja", "result_continue"))

    def test_japanese_challenge_confirmation_accepts_short_event_forms(self):
        for text in ("再挑戦", "再規戦する", "挑戦する", "再戦"):
            with self.subTest(text=text):
                self.assertTrue(
                    main._text_matches_marker(
                        text, "ja", "challenge_confirmation"
                    )
                )

        self.assertFalse(
            main._text_matches_marker(
                "戦闘報酬を獲得", "ja", "challenge_confirmation"
            )
        )

    def test_japanese_challenge_confirmation_accepts_common_choice_pairs(self):
        for text in (
            "はい いいえ",
            "決定 キャンセル",
            "実行 キャンセル",
            "確認 キャンセル",
            "OK キャンセル",
            "再挑戦する キャンセル",
        ):
            with self.subTest(text=text):
                self.assertTrue(
                    main._text_matches_marker(
                        text, "ja", "challenge_confirmation"
                    )
                )

        self.assertFalse(
            main._text_matches_marker(
                "キャンセル", "ja", "challenge_confirmation"
            )
        )

    def test_right_side_ready_panel_triggers_box_for_both_languages(self):
        for language, ready in (("zh", "准备完毕"), ("ja", "準備OK")):
            with self.subTest(language=language):
                relink = FakeRelink((language,))
                with patch.object(
                    main,
                    "town_ready_panel_has_box_icon",
                    return_value=True,
                ):
                    self.assertTrue(main.town_ready_panel_present(relink, {}))

    def test_ready_panel_box_icon_is_language_independent(self):
        image = Image.new("RGB", (1000, 1000), (24, 24, 24))
        draw = ImageDraw.Draw(image)
        draw.rectangle((810, 535, 850, 585), fill=(65, 130, 210))
        draw.rectangle((814, 540, 846, 580), outline=(235, 235, 235), width=3)
        self.assertTrue(main.town_ready_panel_has_box_icon(ImageRelink(image)))

        draw.rectangle((814, 540, 846, 580), fill=(65, 130, 210))
        self.assertFalse(main.town_ready_panel_has_box_icon(ImageRelink(image)))

    def test_center_ready_confirmation_modal_is_detected_visually(self):
        modal = Image.new("RGB", (1000, 1000), (24, 24, 24))
        draw = ImageDraw.Draw(modal)
        draw.rectangle((240, 70, 770, 890), fill=(48, 60, 104))
        self.assertTrue(main.town_ready_confirmation_dialog_present(ImageRelink(modal)))

        town = Image.new("RGB", (1000, 1000), (80, 70, 45))
        self.assertFalse(main.town_ready_confirmation_dialog_present(ImageRelink(town)))

    def test_center_ready_confirmation_uses_the_highlighted_top_row(self):
        image = Image.new("RGB", (1000, 1000), (28, 38, 68))
        draw = ImageDraw.Draw(image)
        draw.rectangle((400, 715, 600, 775), fill=(64, 132, 210))
        draw.rectangle((400, 765, 600, 835), fill=(36, 62, 95))
        self.assertEqual(main.town_ready_confirmation_selection(ImageRelink(image)), "ready")

        draw.rectangle((400, 715, 600, 775), fill=(36, 62, 95))
        draw.rectangle((400, 765, 600, 835), fill=(64, 132, 210))
        self.assertEqual(main.town_ready_confirmation_selection(ImageRelink(image)), "cancel")

    def test_ready_confirmation_modal_takes_priority_over_right_box_card(self):
        relink = FakeRelink(("zh",))
        with patch.object(main, "sleep"), patch.object(
            main, "detect_stable_battle_hud", side_effect=(False, False, True)
        ), patch.object(
            main, "full_frame_texts", return_value={"zh": "准备完毕", "ja": ""}
        ), patch.object(
            main, "town_ready_confirmation_dialog_present", return_value=True
        ), patch.object(
            main, "town_ready_confirmation_selection", return_value="ready"
        ), patch.object(main, "town_ready_panel_present", return_value=True) as panel, patch.object(
            main, "press_recovery_cross"
        ) as press_cross, patch.object(main, "press_recovery_square") as press_square:
            self.assertTrue(main.recover_last_town_quest(relink, destination_menu_open=True))

        self.assertEqual(press_cross.call_args_list[-1].args, (relink, 2.0))
        press_square.assert_not_called()
        panel.assert_not_called()

    def test_ready_confirmation_sends_cross_after_the_initial_square(self):
        relink = FakeRelink(("zh",))
        with patch.object(main, "sleep"), patch.object(
            main, "detect_stable_battle_hud", side_effect=(False, False, False, True)
        ), patch.object(
            main, "full_frame_texts", return_value={"zh": "准备完毕", "ja": ""}
        ), patch.object(
            main, "town_ready_confirmation_dialog_present", side_effect=(False, False, True, True)
        ), patch.object(
            main, "town_ready_confirmation_selection", return_value="ready"
        ), patch.object(main, "town_ready_panel_present", return_value=True), patch.object(
            main, "press_recovery_cross"
        ) as press_cross, patch.object(main, "press_recovery_square") as press_square:
            self.assertTrue(main.recover_last_town_quest(relink, destination_menu_open=True))

        press_square.assert_called_once_with(relink, 2.0)
        self.assertEqual(press_cross.call_args_list[-1].args, (relink, 2.0))

    def test_japanese_ready_dialog_requires_ready_and_cancel_structure(self):
        self.assertTrue(
            main.japanese_ready_dialog_structure_matches("受注済み 準備 OK キャンセル")
        )
        self.assertFalse(main.japanese_ready_dialog_structure_matches("準備 OK"))
        self.assertFalse(main.japanese_ready_dialog_structure_matches("キャンセル"))
        self.assertFalse(
            main.japanese_ready_dialog_structure_matches("確認 OK キャンセル")
        )

    def test_town_recovery_backs_out_of_abandon_confirmation_before_box(self):
        relink = FakeRelink(("zh",))

        def marker(_relink, _texts, semantic):
            return semantic == "quest_abandon_confirmation"

        with patch.object(main, "sleep"), patch.object(
            main, "detect_stable_battle_hud", side_effect=(False, True)
        ), patch.object(
            main,
            "full_frame_texts",
            return_value={"zh": "确定要放弃已承接的任务吗？", "ja": ""},
        ), patch.object(main, "full_frame_has_marker", side_effect=marker), patch.object(
            main, "town_ready_confirmation_is_confirmable", return_value=(False, None, {})
        ), patch.object(main, "town_ready_confirmation_dialog_present", return_value=False), patch.object(
            main, "town_ready_panel_present", return_value=False
        ), patch.object(main, "press_recovery_moon") as press_moon:
            self.assertTrue(main.recover_last_town_quest(relink, destination_menu_open=True))

        self.assertEqual(press_moon.call_count, 3)

    def test_ready_page_abandon_menu_does_not_trigger_three_moons(self):
        relink = FakeRelink(("zh",))
        with patch.object(main, "full_frame_has_marker", return_value=True):
            self.assertFalse(
                main.town_quest_abandon_confirmation_present(
                    relink,
                    {"zh": "查看/放弃已承接任务 准备完毕 取消 确定", "ja": ""},
                )
            )

    def test_accepted_quest_holds_navigation_when_box_is_temporarily_missing(self):
        relink = FakeRelink(("zh",))
        box_states = iter((False, False, True))
        with patch.object(main, "sleep"), patch.object(
            main, "detect_stable_battle_hud", side_effect=(False, False, False, True)
        ), patch.object(
            main,
            "full_frame_texts",
            return_value={"zh": "已承接任务", "ja": ""},
        ), patch.object(
            main, "town_ready_panel_present", side_effect=lambda _relink: next(box_states)
        ), patch.object(
            main, "town_ready_confirmation_is_confirmable", return_value=(False, None, {})
        ), patch.object(
            main, "town_ready_confirmation_dialog_present", return_value=False
        ), patch.object(main, "press_recovery_cross") as press_cross, patch.object(
            main, "press_recovery_square"
        ) as press_square:
            self.assertTrue(main.recover_last_town_quest(relink, destination_menu_open=True))

        # The initial task-center entry Cross is expected; no generic Cross
        # may be sent while the accepted-quest page waits for Box.
        self.assertEqual(press_cross.call_count, 1)
        self.assertEqual(press_cross.call_args.args, (relink, 2.5))
        press_square.assert_called_once_with(relink, 2.0)

    def test_quest_action_label_does_not_latch_accepted_quest_wait(self):
        self.assertFalse(main.town_quest_accepted_state_present({"zh": "开始任务", "ja": ""}))
        self.assertFalse(main.town_quest_accepted_state_present({"zh": "查看已承接任务", "ja": ""}))
        self.assertTrue(main.town_quest_accepted_state_present({"zh": "已承接任务", "ja": ""}))
        self.assertTrue(main.town_quest_accepted_state_present({"zh": "", "ja": "受注しました"}))

    def test_battle_timer_marker_supports_both_client_languages(self):
        for language, marker in (("zh", "剩余时间 08:12"), ("ja", "残り時間 08:12")):
            with self.subTest(language=language):
                relink = FakeRelink((language,))
                with patch.object(
                    main, "read_region_texts", return_value={language: marker}
                ):
                    self.assertTrue(main.battle_timer_marker_present(relink))

    def test_unexpected_town_recovery_requires_missing_timer_and_hud(self):
        relink = FakeRelink()
        with patch.object(main, "battle_timer_marker_state", return_value=True):
            self.assertEqual(
                main.unexpected_town_recovery_signal(relink), "battle_timer"
            )
        with patch.object(main, "battle_timer_marker_state", return_value=None):
            self.assertEqual(
                main.unexpected_town_recovery_signal(relink),
                "capture_unavailable",
            )
        with patch.object(main, "battle_timer_marker_state", return_value=False), patch.object(
            main, "battle_hud_visual_candidate", return_value=(True, 0.2)
        ):
            self.assertEqual(
                main.unexpected_town_recovery_signal(relink), "battle_hud"
            )
        with patch.object(main, "battle_timer_marker_state", return_value=False), patch.object(
            main, "battle_hud_visual_candidate", return_value=(False, 0.01)
        ):
            self.assertEqual(
                main.unexpected_town_recovery_signal(relink),
                "timer_missing_no_battle_hud",
            )

    def test_unexpected_town_recovery_delay_is_three_minutes(self):
        self.assertEqual(main.UNEXPECTED_TOWN_RECOVERY_DELAY_SECONDS, 180.0)

    def test_gold_repeat_indicator_is_language_independent(self):
        off = Image.new("RGB", (1000, 1000), (75, 54, 42))
        on = off.copy()
        draw = ImageDraw.Draw(on)
        draw.rectangle((224, 704, 248, 736), fill=(210, 170, 55))

        self.assertFalse(main.result_repeat_indicator_is_gold(ImageRelink(off)))
        self.assertTrue(main.result_repeat_indicator_is_gold(ImageRelink(on)))
        self.assertIsNone(main.result_retry_state(ImageRelink(on)))

    def test_retry_action_bar_visually_distinguishes_the_second_result_page(self):
        summary = Image.new("RGB", (1000, 1000), (60, 60, 55))
        retry_page = summary.copy()
        draw = ImageDraw.Draw(retry_page)
        draw.rectangle((30, 890, 250, 925), fill=(45, 105, 150))

        self.assertFalse(main.result_repeat_control_is_visible(ImageRelink(summary)))
        self.assertTrue(main.result_repeat_control_is_visible(ImageRelink(retry_page)))

    def test_japanese_retry_page_uses_white_ps5_button_marker(self):
        image = Image.new("RGB", (1000, 1000), (40, 55, 80))
        draw = ImageDraw.Draw(image)
        draw.ellipse((75, 885, 115, 925), fill=(235, 235, 235))
        self.assertTrue(main.result_repeat_ps_button_is_visible(ImageRelink(image)))

    def test_japanese_retry_page_white_ps5_marker_rejects_plain_background(self):
        image = Image.new("RGB", (1000, 1000), (40, 55, 80))
        self.assertFalse(main.result_repeat_ps_button_is_visible(ImageRelink(image)))

    def test_japanese_result_msp_marker_is_secondary_fallback(self):
        class MspRelink(FakeRelink):
            def screenshot(self):
                return Image.new("RGB", (1163, 648), (0, 0, 0))

            def ocr(self, image, confidence=0.35, language=None):
                return [{"text": "獲得MSP 770"}]

        self.assertTrue(main.result_msp_marker_is_visible(MspRelink(("ja",))))

    def test_japanese_retry_page_requires_action_bar_before_msp_fallback(self):
        relink = FakeRelink(("ja",))
        with patch.object(
            main, "result_repeat_control_is_visible", return_value=False
        ), patch.object(
            main, "result_repeat_ps_button_is_visible", return_value=True
        ), patch.object(main, "result_msp_marker_is_visible", return_value=True):
            self.assertFalse(main.japanese_retry_page_is_visible(relink))

        with patch.object(
            main, "result_repeat_control_is_visible", return_value=True
        ), patch.object(
            main, "result_repeat_ps_button_is_visible", return_value=False
        ), patch.object(main, "result_msp_marker_is_visible", return_value=True):
            self.assertTrue(main.japanese_retry_page_is_visible(relink))

    def test_confirmed_japanese_repeat_sends_second_cross_only_when_still_on_retry_page(self):
        relink = FakeRelink(("ja",))
        with patch.object(main, "detect_stable_battle_hud", return_value=False), patch.object(
            main, "japanese_settlement_highlight_dialog_active", return_value=False
        ), patch.object(main, "japanese_retry_page_is_visible", return_value=True), patch.object(
            main, "result_retry_state", return_value="enabled"
        ), patch.object(main, "result_progress_prompt_is_visible", return_value=True
        ), patch.object(main, "sleep"):
            self.assertTrue(main.press_confirmed_repeat_continue(relink))
        self.assertEqual([key for key, _ in relink.pressed], [main.CROSS_KEY, main.CROSS_KEY])

    def test_confirmed_japanese_repeat_waits_for_the_shared_progress_prompt(self):
        relink = FakeRelink(("ja",))
        with patch.object(main, "result_progress_prompt_is_visible", return_value=False):
            self.assertFalse(main.press_confirmed_repeat_continue(relink))
        self.assertEqual(relink.pressed, [])

    def test_repeat_toggle_retries_once_when_available_state_remains_stable(self):
        relink = FakeRelink()
        clock = {"value": 0.0}

        def fake_monotonic():
            clock["value"] += 0.5
            return clock["value"]

        with patch.object(
            main, "result_retry_state", side_effect=("available",) * 40
        ), patch.object(main, "monotonic", side_effect=fake_monotonic), patch.object(
            main, "sleep"
        ):
            result = main.ensure_auto_repeat_enabled(relink, "available")

        self.assertFalse(result)
        self.assertEqual([key for key, _ in relink.pressed], [main.SQUARE_KEY, main.SQUARE_KEY])

    def test_repeat_toggle_does_not_retry_when_state_is_not_stably_available(self):
        relink = FakeRelink()
        with patch.object(
            main, "result_retry_state", side_effect=("available", "enabled")
        ), patch.object(main, "RESULT_REPEAT_CONFIRM_TIMEOUT_SECONDS", 0.0), patch.object(
            main, "sleep"
        ):
            result = main.ensure_auto_repeat_enabled(relink, "available")

        self.assertFalse(result)
        self.assertEqual([key for key, _ in relink.pressed], [main.SQUARE_KEY])

    def test_failed_repeat_town_probe_requires_two_result_absences_and_no_timer(self):
        relink = FakeRelink()
        with patch.object(main, "detect_stable_result_ui", side_effect=(None, None)), patch.object(
            main, "unexpected_town_recovery_signal", return_value="timer_missing_no_battle_hud"
        ):
            self.assertTrue(main.failed_repeat_has_left_result_screen(relink))

    def test_failed_repeat_town_probe_keeps_result_screen_out_of_town_recovery(self):
        relink = FakeRelink()
        with patch.object(main, "detect_stable_result_ui", return_value="继续"):
            self.assertFalse(main.failed_repeat_has_left_result_screen(relink))

    def test_chinese_settlement_dialog_requires_both_settlement_and_confirmation(self):
        relink = FakeRelink(("zh",))
        relink.ui_language_mode = "zh"
        relink.detected_ui_language = "zh"
        relink.screenshot = lambda: Image.new("RGB", (1000, 1000), (28, 40, 70))
        relink.ocr = lambda _image, confidence, language: [
            {"text": "结算确认 是否完成结算确认"}
        ]
        self.assertTrue(main.chinese_settlement_confirmation_present(relink))

        relink.ocr = lambda _image, confidence, language: [{"text": "结算进度"}]
        self.assertFalse(main.chinese_settlement_confirmation_present(relink))

    def test_repeat_toggle_requires_two_enabled_confirmations(self):
        relink = FakeRelink()
        clock = iter((0.0, 0.5, 1.0, 1.5, 2.0))
        with patch.object(
            main, "result_retry_state", side_effect=("enabled", "available", "enabled", "enabled")
        ), patch.object(main, "monotonic", side_effect=lambda: next(clock)), patch.object(
            main, "sleep"
        ):
            result = main.ensure_auto_repeat_enabled(relink, "available")

        self.assertTrue(result)
        self.assertEqual([key for key, _ in relink.pressed], [main.SQUARE_KEY])

    def test_shell_execute_error_text_is_actionable(self):
        self.assertIn("访问被拒绝", main._shell_execute_error_text(5))
        self.assertIn("Windows 返回码", main._shell_execute_error_text(999))

    def test_last_town_quest_macro_accepts_both_language_markers(self):
        cases = (
            ("zh", "任务中心", "已承接任务", "准备完毕"),
            ("ja", "クエストカウンター", "受注しました", "準備OK"),
        )
        for language, counter, accepted, ready in cases:
            with self.subTest(language=language):
                relink = FakeRelink((language,))

                def marker_result(_relink, semantics, _timeout, poll=1.5):
                    if semantics == ("quest_counter",):
                        return "quest_counter"
                    if semantics == ("quest_ready",):
                        return "quest_ready"
                    self.fail(f"unexpected marker request: {semantics}")

                with patch.object(main, "sleep"), patch.object(
                    main, "press_recovery_cross"
                ), patch.object(
                    main, "press_recovery_square"
                ) as press_square, patch.object(
                    main, "town_ready_panel_present", return_value=True
                ), patch.object(
                    main, "wait_for_any_marker", side_effect=marker_result
                ), patch.object(
                    main, "detect_stable_battle_hud", side_effect=(False, False, True)
                ), patch.object(
                    main,
                    "full_frame_texts",
                    side_effect=(
                        (
                            {"zh": accepted, "ja": ""}
                            if language == "zh"
                            else {"zh": "", "ja": accepted}
                        ),
                        (
                            {"zh": accepted, "ja": ""}
                            if language == "zh"
                            else {"zh": "", "ja": accepted}
                        ),
                        (
                            {"zh": ready, "ja": ""}
                            if language == "zh"
                            else {"zh": "", "ja": ready}
                        ),
                    ),
                ):
                    self.assertTrue(
                        main.recover_last_town_quest(
                            relink, destination_menu_open=True
                        )
                    )

                self.assertIn((main.LEFT_STICK_UP_KEY, "press"), relink.pressed)
                self.assertIn((main.LEFT_STICK_UP_KEY, "release"), relink.pressed)
                self.assertIn((main.PYRAMID_KEY, None), relink.pressed)
                press_square.assert_called_once_with(relink, 2.0)

    def test_town_quest_failure_keeps_chiaki_open(self):
        relink = FakeRelink()
        clock = {"value": 0.0}

        def fake_time():
            clock["value"] += 0.1
            return clock["value"]

        def fake_sleep(seconds):
            clock["value"] += float(seconds)

        with patch.object(main, "time", side_effect=fake_time), patch.object(
            main, "sleep", side_effect=fake_sleep
        ), patch.object(main, "frame_activity_signature", return_value=None), patch.object(
            main, "classify_reconnected_screen", return_value="town_menu"
        ), patch.object(main, "recover_last_town_quest", return_value=False):
            outcome = main.route_reconnected_screen(relink, timeout=10.0)

        self.assertEqual(outcome, "town_recovery_failed")

    def test_collection_list_is_closed_with_moon_before_quest_navigation(self):
        relink = FakeRelink(("zh",))
        texts = {"zh": "任务中心 收藏列表", "ja": ""}
        with patch.object(main, "press_recovery_moon") as press_moon:
            self.assertTrue(main.dismiss_town_collection_list(relink, texts))
        press_moon.assert_called_once_with(relink, 1.0)

    def test_collection_list_marker_precedes_town_destination_marker(self):
        relink = FakeRelink(("zh",))
        with patch.object(main, "battle_hud_visual_candidate", return_value=(False, 0.0)), patch.object(
            main, "detect_stable_result_ui", return_value=None
        ), patch.object(
            main,
            "full_frame_texts",
            return_value={"zh": "任务中心 收藏列表", "ja": ""},
        ):
            self.assertEqual(
                main.classify_reconnected_screen(relink, allow_town_menu=True),
                "town_collection_list",
            )

    def test_l2_ring_detector_accepts_yellow_ring(self):
        pixels = np.zeros((240, 320, 3), dtype=np.uint8)
        image = Image.fromarray(pixels, mode="RGB")
        ImageDraw.Draw(image).ellipse((120, 70, 200, 150), outline=(255, 205, 35), width=8)

        detected, details = main.detect_l2_target_ring_pixels(np.asarray(image))

        self.assertTrue(detected)
        self.assertGreaterEqual(details["candidates"], 1)

    def test_l2_ring_detector_rejects_empty_battle_crop(self):
        detected, details = main.detect_l2_target_ring_pixels(
            np.zeros((240, 320, 3), dtype=np.uint8)
        )

        self.assertFalse(detected)
        self.assertEqual(details["candidates"], 0.0)

    def test_ring_arc_experiment_accepts_occluded_gold_ring(self):
        pixels = np.zeros((240, 320, 3), dtype=np.uint8)
        image = Image.fromarray(pixels, mode="RGB")
        # A visible left/bottom arc remains when the target body covers the
        # rest of the lock ring.
        ImageDraw.Draw(image).arc(
            (105, 55, 215, 165),
            start=115,
            end=280,
            fill=(255, 184, 42),
            width=6,
        )

        detected, details = main.detect_l2_target_ring_arcs(np.asarray(image))

        self.assertTrue(detected)
        self.assertGreaterEqual(details["max_arc_run"], 5)

    def test_ring_arc_experiment_rejects_broad_gold_flash(self):
        pixels = np.zeros((240, 320, 3), dtype=np.uint8)
        image = Image.fromarray(pixels, mode="RGB")
        ImageDraw.Draw(image).ellipse(
            (100, 45, 220, 165),
            fill=(255, 184, 42),
            outline=(255, 210, 80),
            width=4,
        )

        detected, details = main.detect_l2_target_ring_arcs(np.asarray(image))

        self.assertFalse(detected)
        self.assertGreater(details["interior_gold"], 0.18)

    def test_scheme7_uses_scheme6_guard_before_first_l2(self):
        relink = FakeRelink()
        clock = {"value": 0.0}
        details = {
            "score": 0.0,
            "arc_sectors": 0.0,
            "max_arc_run": 0.0,
            "thinness": 0.0,
            "interior_gold": 0.0,
        }

        def fake_time():
            return clock["value"]

        def fake_sleep(seconds):
            clock["value"] += float(seconds)

        def stop_after_l2(key, movement=None, interval=None):
            relink.pressed.append((key, movement))
            if key == main.L2_KEY:
                relink.running = False

        relink.press = stop_after_l2
        with patch.object(main, "time", side_effect=fake_time), patch.object(
            main, "sleep", side_effect=fake_sleep
        ), patch.object(
            main, "l2_target_ring_arc_snapshot", return_value=(False, details)
        ), patch.object(main, "remote_sba_fill_fraction", return_value=0.0):
            main.ring_arc_experiment_focus_watchdog(relink, lambda: True)

        self.assertEqual([key for key, _ in relink.pressed], [main.L2_KEY])
        self.assertGreaterEqual(clock["value"], 9.0)

    def test_boss_blue_bar_detector_accepts_horizontal_bar(self):
        pixels = np.zeros((240, 320, 3), dtype=np.uint8)
        pixels[100:112, 70:260] = (35, 145, 225)

        detected, details = main.detect_boss_blue_bar_pixels(pixels)

        self.assertTrue(detected)
        self.assertGreater(details["longest_run"], 20)

    def test_boss_blue_bar_detector_rejects_short_blue_effect(self):
        pixels = np.zeros((240, 320, 3), dtype=np.uint8)
        pixels[100:112, 70:90] = (35, 145, 225)

        detected, _ = main.detect_boss_blue_bar_pixels(pixels)

        self.assertFalse(detected)

    def test_video_frame_with_boss_bar_but_no_lock_ring(self):
        frame_path = Path(__file__).parent / "fixtures" / "targeting_bar_without_ring.jpg"
        if not frame_path.exists():
            self.skipTest("targeting video fixture is unavailable")
        pixels = np.asarray(Image.open(frame_path).convert("RGB"))

        ring, _ = main.detect_l2_target_ring_pixels(pixels)
        bar, _ = main.detect_boss_blue_bar_pixels(pixels)

        self.assertTrue(bar)
        self.assertFalse(ring)

    def test_video_frame_with_boss_bar_and_lock_ring(self):
        frame_path = Path(__file__).parent / "fixtures" / "targeting_bar_with_ring.jpg"
        if not frame_path.exists():
            self.skipTest("targeting video fixture is unavailable")
        pixels = np.asarray(Image.open(frame_path).convert("RGB"))

        ring, _ = main.detect_l2_target_ring_pixels(pixels)
        bar, _ = main.detect_boss_blue_bar_pixels(pixels)

        self.assertTrue(bar)
        self.assertTrue(ring)


if __name__ == "__main__":
    unittest.main()
