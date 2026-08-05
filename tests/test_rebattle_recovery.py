import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import main
import numpy as np
from PIL import Image, ImageDraw
from module.controller import Controller, stream_caption_matches_target


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
        with patch.object(main, "settlement_confirmation_selection", return_value="yes"):
            self.assertTrue(main.japanese_settlement_highlight_dialog_active(japanese))
            self.assertFalse(main.japanese_settlement_highlight_dialog_active(automatic))
        self.assertFalse(main.japanese_settlement_highlight_dialog_active(chinese))

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
        ), patch.object(japanese, "confirm_ui_language") as confirm:
            self.assertTrue(main.japanese_settlement_highlight_dialog_active(japanese))
        confirm.assert_called_once_with("ja", "settlement_confirmation")

    def test_japanese_settlement_highlight_confirms_yes_directly(self):
        relink = FakeRelink()
        with patch.object(
            main, "settlement_confirmation_selection", return_value="yes"
        ):
            result = main.handle_japanese_settlement_highlight(relink)
        self.assertEqual(result, "confirmed")
        self.assertEqual(relink.pressed, [(main.CROSS_KEY, None)])

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

    def test_japanese_challenge_confirmation_accepts_short_event_forms(self):
        for text in ("再挑戦", "再規戦する", "挑戦", "再戦", "戦"):
            with self.subTest(text=text):
                self.assertTrue(
                    main._text_matches_marker(
                        text, "ja", "challenge_confirmation"
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
        self.assertEqual(main.result_retry_state(ImageRelink(on)), "enabled")

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
