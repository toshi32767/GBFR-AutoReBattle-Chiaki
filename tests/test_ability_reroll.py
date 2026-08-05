import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from module.ability_reroll import (
    ABILITY_STOP_MODE_REMAINING_MSP,
    ABILITY_STOP_MODE_SPENT_MSP,
    AbilityJournal,
    AbilityRoll,
    compare_roll_sets,
    evaluate_attribute_groups,
    evaluate_ability_rolls,
    extract_ability_rolls,
    extract_msp_from_ocr,
    infer_stars_from_ability_value,
    meets_attribute_thresholds,
    meets_attribute_star_sum,
    normalize_ability_name,
    parse_ocr_star_count,
    parse_ability_value,
    parse_msp_value,
    msp_stop_status,
    total_stars,
)
from main import (
    _advance_ability_success_to_result,
    _ability_confirmation_ready,
    _ability_offer_ready,
    _ability_result_highlight,
    _ability_selected_level,
    _ability_stage,
    _cancel_ability_result,
    _clear_ability_reroll_state,
    _config_bool,
    _load_ability_config,
    _move_ability_offer_to_lv3,
    ability_roll_display_name,
    play_ability_qualified_alert,
)


def roll(name: str, stars: int, value: float, side: str = "new") -> AbilityRoll:
    return AbilityRoll(name, f"{name} +{value:g}%", stars, value, side, 0)


class AbilityRerollTests(unittest.TestCase):
    def test_config_bool_does_not_treat_false_text_as_enabled(self):
        self.assertFalse(_config_bool("false"))
        self.assertFalse(_config_bool("0"))
        self.assertTrue(_config_bool("true"))
        self.assertTrue(_config_bool("1"))

    def test_ability_config_normalizes_persisted_checkbox_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability.json"
            path.write_text(
                json.dumps(
                    {
                        "auto_overwrite": "false",
                        "auto_overwrite_if_all_better": "true",
                    }
                ),
                encoding="utf-8",
            )
            config = _load_ability_config(path)

        self.assertIs(config["auto_overwrite"], False)
        self.assertIs(config["auto_overwrite_if_all_better"], True)

    def test_msp_config_defaults_and_normalizes_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability.json"
            path.write_text(
                json.dumps(
                    {
                        "stop_mode": "spent_msp",
                        "msp_spent_limit": -3,
                        "msp_remaining_limit": "bad",
                    }
                ),
                encoding="utf-8",
            )
            config = _load_ability_config(path)

        self.assertEqual(config["stop_mode"], "spent_msp")
        self.assertEqual(config["msp_spent_limit"], 1)
        self.assertEqual(config["msp_remaining_limit"], 0)

    def test_old_attribute_threshold_config_remains_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability.json"
            path.write_text(
                json.dumps({"thresholds": {"HP": 9}}),
                encoding="utf-8",
            )
            config = _load_ability_config(path)

        self.assertIs(config["attribute_thresholds_enabled"], True)

    def test_old_attribute_config_is_synthesized_as_first_group(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability.json"
            path.write_text(
                json.dumps(
                    {
                        "thresholds": {"HP": 9},
                        "attribute_thresholds_enabled": True,
                    }
                ),
                encoding="utf-8",
            )
            config = _load_ability_config(path)

        self.assertEqual(len(config["attribute_groups"]), 1)
        self.assertEqual(config["attribute_groups"][0]["thresholds"], {"HP": 9})
        self.assertEqual(config["attribute_groups"][0]["name"], "组合 1")

    def test_stop_after_completion_defaults_to_stopping_and_normalizes_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability.json"
            path.write_text(json.dumps({}), encoding="utf-8")
            default_config = _load_ability_config(path)
            path.write_text(
                json.dumps({"stop_after_completion": "false"}), encoding="utf-8"
            )
            continued_config = _load_ability_config(path)

        self.assertIs(default_config["stop_after_completion"], True)
        self.assertIs(continued_config["stop_after_completion"], False)

    def test_multiple_attribute_groups_accept_when_any_group_passes(self):
        values = [
            roll("暴击率", 10, 20),
            roll("能力伤害", 9, 18),
            roll("昏厥值", 8, 16),
            roll("HP", 9, 1600),
        ]
        passed, reason = evaluate_attribute_groups(
            values,
            [
                {
                    "name": "技能组合",
                    "enabled": True,
                    "thresholds": {"攻击力": 10, "能力伤害": 10},
                    "attribute_thresholds_enabled": True,
                },
                {
                    "name": "生存组合",
                    "enabled": True,
                    "thresholds": {"HP": 9, "暴击率": 10},
                    "attribute_thresholds_enabled": True,
                },
            ],
        )
        self.assertTrue(passed)
        self.assertIn("生存组合", reason)

    def test_multiple_attribute_groups_ignore_disabled_and_empty_groups(self):
        values = [roll("HP", 8, 1000)]
        passed, reason = evaluate_attribute_groups(
            values,
            [
                {
                    "name": "关闭组合",
                    "enabled": False,
                    "thresholds": {"HP": 1},
                    "attribute_thresholds_enabled": True,
                },
                {"name": "空组合", "enabled": True, "thresholds": {}},
            ],
        )
        self.assertFalse(passed)
        self.assertIn("未启用有效属性组合", reason)

    def test_group_failure_does_not_blame_attribute_named_like_the_group(self):
        values = [
            roll("昏厥值", 10, 20),
            roll("能力伤害上限", 9, 16),
            roll("奥义伤害上限", 9, 16),
            # The game's exact table maps +2% to 3 stars.
            roll("普通攻击伤害上限", 3, 2),
        ]
        passed, reason = evaluate_attribute_groups(
            values,
            [
                {
                    "name": "昏厥值",
                    "enabled": True,
                    "thresholds": {
                        "昏厥值": 9,
                        "能力伤害上限": 9,
                        "奥义伤害上限": 9,
                        "普通攻击伤害上限": 9,
                    },
                    "attribute_thresholds_enabled": True,
                    "attribute_sum_enabled": True,
                    "attribute_sum_min": 36,
                }
            ],
        )
        self.assertFalse(passed)
        self.assertIn("组合“昏厥值”未满足", reason)
        self.assertIn("逐项条件：普通攻击伤害上限 星数不足：3 < 9", reason)
        self.assertIn("星数之和条件：指定属性星数之和 31 < 36", reason)
        self.assertNotIn("昏厥值未满足：", reason)

    def test_evaluate_rolls_reports_the_successful_attribute_group(self):
        values = [
            roll("HP", 9, 1600),
            roll("暴击率", 8, 16),
            roll("攻击力", 8, 700),
            roll("昏厥值", 8, 16),
        ]
        evaluation = evaluate_ability_rolls(
            [],
            values,
            total_enabled=False,
            total_min=36,
            thresholds={},
            compare_enabled=False,
            auto_overwrite=True,
            attribute_groups=[
                {
                    "name": "组合 A",
                    "enabled": True,
                    "thresholds": {"攻击力": 10},
                    "attribute_thresholds_enabled": True,
                },
                {
                    "name": "组合 B",
                    "enabled": True,
                    "thresholds": {"HP": 9},
                    "attribute_thresholds_enabled": True,
                },
            ],
        )
        self.assertTrue(evaluation.attribute_group_ok)
        self.assertIn("组合 B", evaluation.attribute_group_reason)
        self.assertTrue(evaluation.overall_ok)
        self.assertTrue(evaluation.auto_accept)

    def test_msp_parser_handles_inline_and_split_upper_right_ocr(self):
        self.assertEqual(parse_msp_value("MSP：12,345"), 12345)
        items = [
            {"text": "MSP", "location": (1660, 70, 1725, 105)},
            {"text": "9876", "location": (1740, 70, 1840, 105)},
            # A numeric ability value outside the upper-right HUD must not win.
            {"text": "HP+2000", "location": (1100, 600, 1300, 630)},
        ]
        self.assertEqual(extract_msp_from_ocr(items, (1920, 1080)), 9876)
        icon_only_items = [
            {"text": "642995", "location": (1750, 66, 1865, 105)},
            {"text": "R2", "location": (1630, 65, 1680, 100)},
            {"text": "100%", "location": (1020, 185, 1100, 210)},
        ]
        self.assertEqual(extract_msp_from_ocr(icon_only_items, (1920, 1080)), 642995)

    def test_msp_stop_modes_are_mutually_evaluated(self):
        self.assertEqual(
            msp_stop_status(
                ABILITY_STOP_MODE_SPENT_MSP,
                current_msp=650,
                initial_msp=1000,
                limit=350,
            ),
            (True, "已使用 MSP 350 >= 350"),
        )
        self.assertEqual(
            msp_stop_status(
                ABILITY_STOP_MODE_REMAINING_MSP,
                current_msp=650,
                initial_msp=1000,
                limit=700,
            ),
            (True, "剩余 MSP 650 <= 700"),
        )
        self.assertFalse(
            msp_stop_status(
                "attributes",
                current_msp=0,
                initial_msp=1000,
                limit=0,
            )[0]
        )

    def test_journal_display_name_includes_stars_without_changing_attribute_name(self):
        self.assertEqual(
            ability_roll_display_name({"attribute": "攻击力", "stars": 10}),
            "攻击力（10星）",
        )
        self.assertEqual(
            ability_roll_display_name({"attribute": "HP", "stars": 9}),
            "HP（9星）",
        )
        self.assertEqual(
            ability_roll_display_name({"attribute": "攻击力"}),
            "攻击力",
        )

    def test_ability_timing_config_is_normalized_to_a_tenth(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability.json"
            path.write_text(
                json.dumps(
                    {
                        "offer_navigation_settle_seconds": 1.26,
                        "success_settle_seconds": 0.01,
                        "success_continue_interval_seconds": 2.24,
                        "reroll_settle_seconds": 31,
                        "result_timeout_seconds": 9,
                    }
                ),
                encoding="utf-8",
            )
            config = _load_ability_config(path)

        self.assertEqual(config["offer_navigation_settle_seconds"], 1.3)
        self.assertEqual(config["success_settle_seconds"], 0.1)
        self.assertEqual(config["success_continue_interval_seconds"], 2.2)
        self.assertEqual(config["reroll_settle_seconds"], 30.0)
        self.assertEqual(config["result_timeout_seconds"], 10.0)

    def test_attribute_aliases_and_values(self):
        self.assertEqual(normalize_ability_name("奥义連锁伤害 +16%"), "奥义连锁伤害")
        self.assertEqual(normalize_ability_name("能力的 HP 回复上限 +20%"), "能力的HP回复上限")
        self.assertEqual(parse_ability_value("HP +1600"), 1600.0)

    def test_thresholds_and_total_stars(self):
        values = [roll("暴击率", 10, 20), roll("能力伤害", 9, 20), roll("昏厥值", 8, 16), roll("HP", 9, 1600)]
        self.assertEqual(total_stars(values), 36)
        self.assertEqual(meets_attribute_thresholds(values, {"能力伤害": 9})[0], True)
        self.assertEqual(meets_attribute_thresholds(values, {"能力伤害": 10})[0], False)

    def test_attribute_star_sum_uses_only_selected_attributes(self):
        values = [
            roll("暴击率", 10, 20),
            roll("能力伤害", 9, 20),
            roll("昏厥值", 8, 16),
            roll("HP", 9, 1600),
        ]
        passed, reason, star_sum = meets_attribute_star_sum(
            values,
            ("能力伤害", "HP"),
            18,
        )
        self.assertTrue(passed)
        self.assertEqual(star_sum, 18)
        self.assertIn("18 >= 18", reason)
        self.assertFalse(
            meets_attribute_star_sum(values, ("能力伤害", "HP"), 19)[0]
        )

    def test_attribute_star_sum_is_an_independent_overall_condition(self):
        values = [
            roll("暴击率", 8, 16),
            roll("能力伤害", 8, 16),
            roll("昏厥值", 8, 16),
            roll("HP", 8, 1000),
        ]
        evaluation = evaluate_ability_rolls(
            [],
            values,
            total_enabled=True,
            total_min=36,
            thresholds={"能力伤害": 10, "HP": 10},
            compare_enabled=False,
            auto_overwrite=False,
            attribute_thresholds_enabled=False,
            attribute_sum_enabled=True,
            attribute_sum_min=16,
        )
        self.assertEqual(evaluation.attribute_sum_stars, 16)
        self.assertTrue(evaluation.attribute_sum_ok)
        self.assertFalse(evaluation.attributes_ok)
        self.assertTrue(evaluation.overall_ok)
        self.assertIn("指定属性星数之和", evaluation.overall_reason)

    def test_value_rules_use_the_exact_game_tables(self):
        percent_values = (0, 1, 2, 4, 6, 8, 10, 12, 16, 20)
        attack_values = (0, 100, 200, 300, 400, 500, 600, 700, 800, 1000)
        hp_values = (0, 200, 400, 500, 600, 800, 1000, 1200, 1600, 2000)
        for stars, value in enumerate(percent_values, start=1):
            self.assertEqual(infer_stars_from_ability_value("暴击率", value), stars)
            self.assertEqual(infer_stars_from_ability_value("昏厥值", value), stars)
        for stars, value in enumerate(attack_values, start=1):
            self.assertEqual(infer_stars_from_ability_value("攻击力", value), stars)
        for stars, value in enumerate(hp_values, start=1):
            self.assertEqual(infer_stars_from_ability_value("HP", value), stars)

    def test_value_rules_do_not_interpolate_between_game_table_rows(self):
        self.assertIsNone(infer_stars_from_ability_value("暴击率", 18))
        self.assertIsNone(infer_stars_from_ability_value("攻击力", 900))
        self.assertIsNone(infer_stars_from_ability_value("HP", 1800))

    def test_standalone_ocr_star_rows_associate_by_column_and_allow_one_star(self):
        items = [
            {"text": "能力伤害+24%", "location": (1010, 400, 1220, 430)},
            {"text": "★★★", "location": (1010, 438, 1080, 454)},
            {"text": "昏厥值+16", "location": (1010, 470, 1190, 500)},
            {"text": "★", "location": (1010, 508, 1030, 524)},
            {"text": "攻击力+500", "location": (1010, 540, 1190, 570)},
            {"text": "★★★★", "location": (1010, 578, 1090, 594)},
            {"text": "HP+1600", "location": (1010, 610, 1160, 640)},
            {"text": "★★★★★", "location": (1010, 648, 1120, 664)},
            # A star row from the other column must not be consumed here.
            {"text": "★★★★★★★★", "location": (350, 438, 510, 454)},
        ]
        rolls, unknown = extract_ability_rolls(items, None, side="new")
        self.assertEqual([roll.stars for roll in rolls], [3, 9, 6, 9])
        self.assertEqual(
            [roll.stars_source for roll in rolls],
            ["star_ocr", "value_rule", "value_rule", "value_rule"],
        )
        self.assertEqual(unknown, [])
        self.assertEqual(parse_ocr_star_count("★"), 1)
        self.assertIsNone(parse_ocr_star_count("能力伤害+8%"))

    def test_value_rule_overrides_conflicting_ocr_and_records_validation(self):
        items = [
            {"text": "暴击率+20%", "location": (1010, 400, 1220, 430)},
            {"text": "★★★★★", "location": (1010, 438, 1120, 454)},
        ]
        rolls, _ = extract_ability_rolls(items, None, side="new")
        self.assertEqual(rolls[0].stars, 10)
        self.assertEqual(rolls[0].stars_source, "value_rule")
        self.assertIn("OCR=5 星", rolls[0].star_validation or "")
        self.assertIn("数值规则=10 星", rolls[0].star_validation or "")

    def test_value_backfill_happens_before_total_star_calculation(self):
        items = [
            {"text": "暴击率+20%", "location": (1010, 400, 1220, 430)},
            {"text": "昏厥值+16", "location": (1010, 470, 1190, 500)},
            {"text": "攻击力+1000", "location": (1010, 540, 1190, 570)},
            {"text": "HP+1600", "location": (1010, 610, 1160, 640)},
        ]
        rolls, _ = extract_ability_rolls(items, None, side="new")
        self.assertEqual([roll.stars for roll in rolls], [10, 9, 10, 9])
        self.assertEqual(total_stars(rolls), 38)
        self.assertEqual({roll.stars_source for roll in rolls}, {"value_rule"})

    def test_incomplete_rows_never_produce_a_total(self):
        values = [roll("暴击率", 10, 20), roll("能力伤害", 10, 20)]
        evaluation = evaluate_ability_rolls(
            [],
            values,
            total_enabled=True,
            total_min=1,
            thresholds={},
            compare_enabled=False,
            auto_overwrite=False,
        )
        self.assertIsNone(evaluation.total_stars)
        self.assertFalse(evaluation.total_ok)
        self.assertIn("需要完整 4 项", evaluation.total_reason)

    def test_comparison_ignores_order_but_requires_same_attributes(self):
        old = [roll("攻击力", 6, 10, "old"), roll("HP", 6, 1000, "old"), roll("暴击率", 7, 14, "old"), roll("昏厥值", 8, 16, "old")]
        new = [roll("昏厥值", 9, 18), roll("暴击率", 8, 16), roll("HP", 7, 1200), roll("攻击力", 7, 12)]
        self.assertTrue(compare_roll_sets(old, new)[0])
        replacement = [roll("昏厥值", 9, 18), roll("暴击率", 8, 16), roll("能力伤害", 7, 1200), roll("攻击力", 7, 12)]
        self.assertFalse(compare_roll_sets(old, replacement)[0])

    def test_overall_rules_accept_changed_attribute_set_at_equal_total(self):
        old = [
            roll("能力伤害上限", 8, 20, "old"),
            roll("能力伤害", 8, 8, "old"),
            roll("奥义伤害上限", 8, 20, "old"),
            roll("昏厥值", 8, 12, "old"),
        ]
        new = [
            roll("HP", 9, 1600),
            roll("攻击力", 9, 1000),
            roll("暴击率", 9, 20),
            roll("奥义伤害", 9, 20),
        ]
        evaluation = evaluate_ability_rolls(
            old,
            new,
            total_enabled=True,
            total_min=36,
            thresholds={"HP": 9},
            compare_enabled=True,
            auto_overwrite=False,
        )
        self.assertEqual(evaluation.total_stars, 36)
        self.assertTrue(evaluation.overall_ok)
        self.assertFalse(evaluation.same_attribute_set)
        self.assertFalse(evaluation.accepted_by_comparison)
        self.assertTrue(evaluation.should_accept)
        self.assertFalse(evaluation.auto_accept)
        self.assertIn(">= 36", evaluation.total_reason)
        self.assertIn("仅按整体条件判断", evaluation.comparison_reason)

    def test_overall_total_rule_is_an_alternative_to_attribute_group(self):
        old = [
            roll("能力伤害上限", 8, 20, "old"),
            roll("能力伤害", 8, 8, "old"),
            roll("奥义伤害上限", 8, 20, "old"),
            roll("昏厥值", 8, 12, "old"),
        ]
        new = [
            roll("HP", 9, 1600),
            roll("攻击力", 9, 1000),
            roll("暴击率", 9, 20),
            roll("奥义伤害", 9, 20),
        ]
        evaluation = evaluate_ability_rolls(
            old,
            new,
            total_enabled=True,
            total_min=36,
            thresholds={"能力伤害": 9},
            compare_enabled=False,
            auto_overwrite=False,
        )
        self.assertTrue(evaluation.total_ok)
        self.assertFalse(evaluation.attributes_ok)
        self.assertTrue(evaluation.overall_ok)
        self.assertTrue(evaluation.should_accept)
        self.assertIn("任一满足", evaluation.overall_reason)

    def test_overall_attribute_group_can_pass_below_total(self):
        old = [roll("HP", 8, 1000, "old")]
        new = [roll("HP", 8, 1000)]
        evaluation = evaluate_ability_rolls(
            old,
            new,
            total_enabled=True,
            total_min=36,
            thresholds={"HP": 8},
            compare_enabled=False,
            auto_overwrite=False,
        )
        self.assertFalse(evaluation.total_ok)
        self.assertTrue(evaluation.attributes_ok)
        self.assertTrue(evaluation.overall_ok)
        self.assertTrue(evaluation.should_accept)

    def test_overall_rules_fail_when_both_enabled_paths_fail(self):
        values = [
            roll("暴击率", 8, 16),
            roll("能力伤害", 8, 16),
            roll("昏厥值", 8, 16),
            roll("HP", 8, 1000),
        ]
        evaluation = evaluate_ability_rolls(
            values,
            values,
            total_enabled=True,
            total_min=36,
            thresholds={"能力伤害": 9},
            compare_enabled=False,
            auto_overwrite=False,
        )
        self.assertFalse(evaluation.total_ok)
        self.assertFalse(evaluation.attributes_ok)
        self.assertFalse(evaluation.overall_ok)
        self.assertFalse(evaluation.should_accept)

    def test_comparison_only_does_not_make_every_result_overall_acceptable(self):
        values = [
            roll("暴击率", 8, 16),
            roll("能力伤害", 8, 16),
            roll("昏厥值", 8, 16),
            roll("HP", 8, 1000),
        ]
        evaluation = evaluate_ability_rolls(
            values,
            values,
            total_enabled=False,
            total_min=36,
            thresholds={},
            compare_enabled=True,
            auto_overwrite=False,
        )
        self.assertFalse(evaluation.overall_ok)
        self.assertFalse(evaluation.comparison_ok)
        self.assertFalse(evaluation.should_accept)

    def test_same_attributes_can_accept_by_paired_comparison(self):
        old = [
            roll("能力伤害", 5, 8, "old"),
            roll("昏厥值", 5, 12, "old"),
            roll("HP", 5, 1000, "old"),
            roll("攻击力", 5, 500, "old"),
        ]
        new = [
            roll("攻击力", 6, 600),
            roll("HP", 6, 1200),
            roll("昏厥值", 6, 16),
            roll("能力伤害", 6, 10),
        ]
        evaluation = evaluate_ability_rolls(
            old,
            new,
            total_enabled=True,
            total_min=36,
            thresholds={},
            compare_enabled=True,
            auto_overwrite=False,
        )
        self.assertTrue(evaluation.same_attribute_set)
        self.assertTrue(evaluation.comparison_ok)
        self.assertTrue(evaluation.accepted_by_comparison)
        self.assertTrue(evaluation.auto_accept)

    def test_extraction_keeps_unknown_ocr(self):
        items = [
            {"text": "暴击率 +20%", "location": (1010, 440, 1220, 470)},
            {"text": "能力伤害上限 +6%", "location": (1010, 510, 1240, 540)},
            {"text": "未收录的新词条 +8%", "location": (1010, 580, 1240, 610)},
        ]
        rolls, unknown = extract_ability_rolls(items, None, side="new")
        self.assertEqual([item.attribute for item in rolls], ["暴击率", "能力伤害上限"])
        self.assertEqual(unknown, ["未收录的新词条 +8%"])

    def test_journal_records_raw_round_and_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability-journal.json"
            journal = AbilityJournal(path)
            journal.record_round(
                old_rolls=[],
                new_rolls=[roll("暴击率", 8, 16)],
                raw_ocr=["暴击率 +16%", "新词条测试"],
                decision="reroll",
                reason="threshold not met",
                action="moon_sent",
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["rounds"]), 1)
            self.assertEqual(data["rounds"][0]["action"], "moon_sent")
            self.assertEqual(data["attributes"]["暴击率"]["count"], 1)
            self.assertEqual(data["unknown_ocr"]["新词条测试"]["count"], 1)
            self.assertEqual(
                data["attributes"]["暴击率"]["value_star_counts"]["16.000000"]["8"],
                1,
            )

    def test_journal_history_can_fill_missing_star_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability-journal.json"
            journal = AbilityJournal(path)
            journal.record_round(
                old_rolls=[],
                new_rolls=[roll("能力伤害", 7, 22)],
                raw_ocr=["能力伤害 +22%"],
                decision="reroll",
                reason="test",
            )
            evidence = journal.star_evidence()
            self.assertEqual(evidence["能力伤害"]["22.000000"], 7)
            items = [{"text": "能力伤害+22%", "location": (1010, 400, 1220, 430)}]
            rolls, _ = extract_ability_rolls(items, None, side="new", star_evidence=evidence)
            self.assertEqual(rolls[0].stars, 7)
            self.assertEqual(rolls[0].stars_source, "history_value")

    def test_journal_clear_resets_rounds_aggregates_and_unknown_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ability-journal.json"
            journal = AbilityJournal(path)
            journal.record_round(
                old_rolls=[],
                new_rolls=[roll("暴击率", 8, 16)],
                raw_ocr=["暴击率 +16%", "未收录词条"],
                unknown_ocr=["未收录词条"],
                decision="reroll",
                reason="test",
            )
            self.assertEqual(journal.clear(), (1, 1, 1))
            cleared = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(cleared["rounds"], [])
            self.assertEqual(cleared["attributes"], {})
            self.assertEqual(cleared["unknown_ocr"], {})

    def test_ability_stage_detects_candidate_and_confirmation_pages(self):
        candidate_items = [
            {"text": "普通攻击伤害上限+20%", "location": (340, 585, 870, 620)},
            {"text": "攻击力+600", "location": (340, 650, 870, 685)},
            {"text": "能力的HP回复上限+20%", "location": (340, 715, 870, 750)},
            {"text": "HP+1600", "location": (340, 780, 870, 815)},
        ]
        self.assertTrue(_ability_offer_ready(candidate_items, None))
        self.assertEqual(_ability_stage(candidate_items, None), "offer")

        confirmation_items = [
            {"text": "上限突破", "location": (800, 240, 1020, 275)},
            {"text": "当前能力值提升效果", "location": (700, 420, 1050, 455)},
            {"text": "能力伤害上限+20%", "location": (480, 470, 700, 500)},
            {"text": "能力伤害+8%", "location": (1010, 470, 1220, 500)},
            {"text": "执行", "location": (900, 800, 970, 830)},
            {"text": "取消", "location": (900, 850, 970, 880)},
        ]
        self.assertTrue(_ability_confirmation_ready(confirmation_items, None))
        self.assertEqual(_ability_stage(confirmation_items, None), "confirmation")

    def test_ability_stage_accepts_candidate_rows_on_the_right_column(self):
        # This matches the actual character-enhancement start screen: its
        # four candidate rows are rendered in the right panel.
        right_candidate_items = [
            {"text": "奥义伤害上限+12%", "location": (1326, 422, 1525, 449)},
            {"text": "能力伤害上限+12%", "location": (1326, 545, 1525, 571)},
            {"text": "普通攻击伤害上限+16%", "location": (1327, 667, 1567, 693)},
            {"text": "昏厥值+12", "location": (1326, 789, 1441, 815)},
        ]
        self.assertTrue(_ability_offer_ready(right_candidate_items, None))
        self.assertEqual(_ability_stage(right_candidate_items, None), "offer")

    def test_ability_stage_separates_success_page_from_coverage_page(self):
        success_items = [
            {"text": "Over the Limit!", "location": (500, 220, 850, 270)},
            {"text": "昏厥值+16", "location": (1120, 420, 1300, 450)},
            {"text": "暴击率+4%", "location": (1120, 500, 1300, 530)},
            {"text": "奥义伤害上限+20%", "location": (1120, 580, 1360, 610)},
            {"text": "HP+1000", "location": (1120, 660, 1300, 690)},
        ]
        self.assertEqual(_ability_stage(success_items, None), "success")

        result_items = [
            {"text": "能力值覆盖确认", "location": (700, 220, 1020, 260)},
            {"text": "当前能力值提升效果", "location": (400, 360, 700, 390)},
            {"text": "新的能力值提升效果", "location": (1100, 360, 1400, 390)},
            {"text": "奥义伤害上限+12%", "location": (400, 420, 650, 450)},
            {"text": "能力伤害上限+12%", "location": (400, 500, 650, 530)},
            {"text": "普通攻击伤害上限+16%", "location": (400, 580, 700, 610)},
            {"text": "昏厥值+12", "location": (400, 660, 580, 690)},
            {"text": "昏厥值+16", "location": (1100, 420, 1280, 450)},
            {"text": "暴击率+4%", "location": (1100, 500, 1280, 530)},
            {"text": "奥义伤害上限+20%", "location": (1100, 580, 1340, 610)},
            {"text": "HP+1000", "location": (1100, 660, 1280, 690)},
        ]
        self.assertEqual(_ability_stage(result_items, None), "result")

    def test_ability_result_highlight_detects_yes_no_or_ambiguous(self):
        def frame_with_selected(row: str | None) -> Image.Image:
            frame = Image.new("RGB", (1920, 1080), (10, 20, 50))
            draw = ImageDraw.Draw(frame)
            centers = {"yes": 791, "no": 846}
            if row in centers:
                center = centers[row]
                draw.rectangle((680, center - 8, 1240, center + 8), fill=(20, 100, 220))
            return frame

        self.assertEqual(_ability_result_highlight(frame_with_selected("yes")), "yes")
        self.assertEqual(_ability_result_highlight(frame_with_selected("no")), "no")
        self.assertIsNone(_ability_result_highlight(frame_with_selected(None)))

    def test_selected_level_uses_blue_yellow_and_purple_highlights(self):
        items = [
            {"text": "Lv1", "location": (120, 130, 160, 150)},
            {"text": "Lv2", "location": (280, 130, 320, 150)},
            {"text": "Lv3", "location": (440, 130, 480, 150)},
        ]
        colors = {
            1: (40, 100, 220),
            2: (230, 190, 20),
            3: (160, 40, 190),
        }
        for selected, expected in ((1, 1), (2, 2), (3, 3)):
            frame = Image.new("RGB", (600, 320), "white")
            draw = ImageDraw.Draw(frame)
            for level, center_x in zip((1, 2, 3), (140, 300, 460)):
                color = colors[selected] if level == selected else (235, 235, 235)
                draw.rectangle((center_x - 55, 75, center_x + 55, 185), fill=color)
            self.assertEqual(_ability_selected_level(items, frame), expected)

    def test_offer_navigation_moves_left_or_right_and_stops_at_lv3(self):
        class FakeRelink:
            running = True
            paused = False

            def __init__(self):
                self.pressed = []

            def press(self, key):
                self.pressed.append(key)

        fake = FakeRelink()
        stage = ("offer", Image.new("RGB", (600, 320)), [])
        refreshed = ("offer", Image.new("RGB", (600, 320)), [])
        with patch("main._ability_selected_level", side_effect=[1, 3]), patch(
            "main._wait_for_ability_stage", return_value=refreshed
        ), patch("main.sleep"):
            result = _move_ability_offer_to_lv3(fake, stage, 10.0)
        self.assertIsNotNone(result)
        self.assertEqual(fake.pressed, ["a"])

    def test_stopping_ability_reroll_clears_inputs_and_shutdown_state(self):
        class FakeRelink:
            running = True

            def __init__(self):
                self.released = 0
                self.shutdown = 0

            def release_automation_inputs(self):
                self.released += 1

            def request_shutdown(self):
                self.shutdown += 1
                self.running = False

        fake = FakeRelink()
        state = {"phase": "result_wait", "round": 9}
        _clear_ability_reroll_state(fake, state)
        self.assertEqual(state, {"phase": "stopped", "round": 0})
        self.assertEqual(fake.released, 1)
        self.assertEqual(fake.shutdown, 1)
        self.assertFalse(fake.running)

    def test_rejecting_ability_result_uses_moon_without_cross_or_navigation(self):
        class FakeRelink:
            def __init__(self):
                self.pressed = []

            def press(self, key):
                self.pressed.append(key)

        fake = FakeRelink()
        _cancel_ability_result(fake)
        self.assertEqual(fake.pressed, ["backspace"])

    def test_qualified_result_plays_bundled_voice_alert(self):
        class FakeWinSound:
            SND_FILENAME = 1
            SND_NODEFAULT = 2

            def __init__(self):
                self.calls = []

            def PlaySound(self, path, flags):
                self.calls.append((path, flags))

        fake_winsound = FakeWinSound()
        with patch.dict(sys.modules, {"winsound": fake_winsound}):
            self.assertTrue(play_ability_qualified_alert())
        self.assertEqual(len(fake_winsound.calls), 1)
        self.assertTrue(fake_winsound.calls[0][0].endswith("ability-qualified.wav"))
        self.assertEqual(fake_winsound.calls[0][1], 3)

    def test_success_page_repeats_cross_until_result_page(self):
        class FakeRelink:
            running = True
            paused = False

            def __init__(self):
                self.pressed = []

            def press(self, key):
                self.pressed.append(key)

        success = ("success", Image.new("RGB", (100, 100)), [])
        result = ("result", Image.new("RGB", (100, 100)), [])
        fake = FakeRelink()
        with patch(
            "main._wait_for_ability_stage", side_effect=[success, success, result]
        ), patch(
            "main.sleep"
        ) as mocked_sleep:
            actual = _advance_ability_success_to_result(fake, 10.0)

        self.assertIs(actual[0], result[1])
        self.assertIs(actual[1], result[2])
        self.assertEqual(fake.pressed, ["enter", "enter"])
        self.assertEqual(mocked_sleep.call_count, 2)
        self.assertEqual([call.args[0] for call in mocked_sleep.call_args_list], [1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
