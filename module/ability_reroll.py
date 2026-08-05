"""Ability-over-limit reroll parsing, comparison, and journal storage.

The game changes the order of the four ability rows between rolls.  This
module therefore treats the normalized ability name as the row identity and
keeps the original OCR text for later diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np


ABILITY_ATTRIBUTE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("能力的HP回复上限", ("能力的HP回复上限", "能力HP回复上限", "能力的HP回復上限")),
    ("普通攻击伤害上限", ("普通攻击伤害上限", "普通攻擊傷害上限")),
    ("奥义连锁伤害", ("奥义连锁伤害", "奥義連鎖傷害", "奥义連锁伤害")),
    ("能力伤害上限", ("能力伤害上限", "能力傷害上限")),
    ("奥义伤害上限", ("奥义伤害上限", "奥義傷害上限")),
    ("能力伤害", ("能力伤害", "能力傷害")),
    ("奥义伤害", ("奥义伤害", "奥義傷害")),
    ("攻击力", ("攻击力", "攻擊力")),
    ("暴击率", ("暴击率", "暴擊率")),
    ("昏厥值", ("昏厥值", "昏厥値", "昏厥值")),
    ("HP", ("HP", "ＨＰ")),
)

ABILITY_STOP_MODE_ATTRIBUTES = "attributes"
ABILITY_STOP_MODE_SPENT_MSP = "spent_msp"
ABILITY_STOP_MODE_REMAINING_MSP = "remaining_msp"
ABILITY_STOP_MODES = {
    ABILITY_STOP_MODE_ATTRIBUTES,
    ABILITY_STOP_MODE_SPENT_MSP,
    ABILITY_STOP_MODE_REMAINING_MSP,
}

_NUMERIC_RE = re.compile(r"(?<![A-Za-z])([+-]?\d+(?:\.\d+)?)\s*%?")
_MSP_MARKER_RE = re.compile(r"M\s*[S5]\s*P", re.IGNORECASE)
_INTEGER_RE = re.compile(r"(?<![A-Za-z])([0-9][0-9,\s]*)(?![A-Za-z])")


def _config_bool(value: object, fallback: bool = False) -> bool:
    """Parse persisted group switches without treating ``"false"`` as true."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "是", "开启", "已开启"}:
            return True
        if normalized in {"0", "false", "no", "off", "否", "关闭", "未开启"}:
            return False
    return fallback


def normalize_ability_stop_mode(value: object) -> str:
    """Return one of the persisted ability-reroll stop modes."""

    normalized = str(value or "").strip().lower()
    aliases = {
        "attribute": ABILITY_STOP_MODE_ATTRIBUTES,
        "attributes": ABILITY_STOP_MODE_ATTRIBUTES,
        "属性": ABILITY_STOP_MODE_ATTRIBUTES,
        "spent": ABILITY_STOP_MODE_SPENT_MSP,
        "spent_msp": ABILITY_STOP_MODE_SPENT_MSP,
        "used_msp": ABILITY_STOP_MODE_SPENT_MSP,
        "已使用msp": ABILITY_STOP_MODE_SPENT_MSP,
        "remaining": ABILITY_STOP_MODE_REMAINING_MSP,
        "remaining_msp": ABILITY_STOP_MODE_REMAINING_MSP,
        "剩余msp": ABILITY_STOP_MODE_REMAINING_MSP,
    }
    return aliases.get(normalized, ABILITY_STOP_MODE_ATTRIBUTES)


def _integer_from_text(text: str) -> int | None:
    match = _INTEGER_RE.search(str(text).replace("，", ","))
    if match is None:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    return int(digits) if digits else None


def parse_msp_value(text: str) -> int | None:
    """Read an integer displayed next to the upper-right ``MSP`` label."""

    raw = str(text).replace("Ｍ", "M").replace("Ｓ", "S").replace("Ｐ", "P")
    marker = _MSP_MARKER_RE.search(raw)
    if marker is None:
        return None
    return _integer_from_text(raw[marker.end() :])


def extract_msp_from_ocr(
    ocr_items: Iterable[dict[str, Any]] | None,
    frame_size: tuple[int, int] = (1920, 1080),
) -> int | None:
    """Extract MSP from the upper-right OCR region.

    RapidOCR may return ``MSP 12345`` as one box or split the label and value
    into adjacent boxes.  The latter form is associated by position while
    restricting both boxes to the upper-right HUD, so ability values in the
    dialog cannot be mistaken for MSP.
    """

    items = list(ocr_items or [])
    width, height = frame_size
    marker_items: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
    numeric_items: list[tuple[dict[str, Any], tuple[float, float, float, float], int]] = []

    def upper_right(location: tuple[float, float, float, float]) -> bool:
        center_x = (location[0] + location[2]) * 0.5
        center_y = (location[1] + location[3]) * 0.5
        return center_x >= width * 0.60 and center_y <= height * 0.35

    for item in items:
        raw_text = str(item.get("text", "")).strip()
        location = _location(item)
        if not raw_text or location is None or not upper_right(location):
            continue
        normalized = raw_text.replace("Ｍ", "M").replace("Ｓ", "S").replace("Ｐ", "P")
        if _MSP_MARKER_RE.search(normalized):
            marker_items.append((item, location))
            direct = parse_msp_value(raw_text)
            if direct is not None:
                return direct
            continue
        if "%" not in raw_text and "％" not in raw_text:
            value = _integer_from_text(raw_text)
            if value is not None:
                numeric_items.append((item, location, value))

    for _, marker_location in marker_items:
        marker_x = (marker_location[0] + marker_location[2]) * 0.5
        marker_y = (marker_location[1] + marker_location[3]) * 0.5
        nearby = []
        for _, location, value in numeric_items:
            value_x = (location[0] + location[2]) * 0.5
            value_y = (location[1] + location[3]) * 0.5
            vertical_gap = abs(value_y - marker_y)
            horizontal_gap = abs(value_x - marker_x)
            if vertical_gap <= max(60.0, height * 0.06) and horizontal_gap <= width * 0.25:
                nearby.append((vertical_gap * 3.0 + horizontal_gap, value))
        if nearby:
            return min(nearby, key=lambda item: item[0])[1]

    # The game often renders only the crystal icon and the number; the literal
    # ``MSP`` label is visual/UI metadata and is not present in the screenshot.
    # In that layout the value is the long standalone number at the extreme
    # upper-right. Percentages, button labels, and LV numbers are excluded by
    # the candidate filtering above.
    fallback = [
        (location, value)
        for _, location, value in numeric_items
        if ((location[0] + location[2]) * 0.5 >= width * 0.80)
        and ((location[1] + location[3]) * 0.5 <= height * 0.18)
        and len(str(value)) >= 3
    ]
    if fallback:
        return max(
            fallback,
            key=lambda item: (
                (item[0][0] + item[0][2]) * 0.5,
                -((item[0][1] + item[0][3]) * 0.5),
            ),
        )[1]
    return None


def msp_stop_status(
    stop_mode: object,
    *,
    current_msp: int | None,
    initial_msp: int | None,
    limit: int,
) -> tuple[bool, str]:
    """Evaluate one mutually-exclusive MSP stop rule."""

    mode = normalize_ability_stop_mode(stop_mode)
    if mode == ABILITY_STOP_MODE_ATTRIBUTES:
        return False, "按属性条件停止"
    if current_msp is None:
        return False, "MSP 尚未识别"
    if mode == ABILITY_STOP_MODE_REMAINING_MSP:
        if current_msp <= limit:
            return True, f"剩余 MSP {current_msp} <= {limit}"
        return False, f"剩余 MSP {current_msp} > {limit}"
    if initial_msp is None:
        return False, "尚未建立起始 MSP"
    spent = max(0, initial_msp - current_msp)
    if spent >= limit:
        return True, f"已使用 MSP {spent} >= {limit}"
    return False, f"已使用 MSP {spent} < {limit}"


def parse_ocr_star_count(text: str) -> int | None:
    """Count a standalone OCR star line without treating it as an ability."""

    raw = str(text).strip()
    # A single star is a valid one-star result.  Restrict this parser to a
    # star-only OCR box so punctuation or an asterisk in an ability name can
    # never become evidence accidentally.
    if not raw or any(char not in "★☆* \t" for char in raw):
        return None
    filled = raw.count("★") + raw.count("*")
    if filled:
        return filled
    unfilled = raw.count("☆")
    return unfilled or None


def ability_value_key(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{float(value):.6f}"


# These are lookup tables rather than formulas.  The game deliberately uses
# irregular steps at the high end, so division-based inference misclassifies
# values such as Attack +500 and HP +1600.
_STAR_VALUE_TABLES: dict[str, tuple[float, ...]] = {
    "percent": (0, 1, 2, 4, 6, 8, 10, 12, 16, 20),
    "attack": (0, 100, 200, 300, 400, 500, 600, 700, 800, 1000),
    "hp": (0, 200, 400, 500, 600, 800, 1000, 1200, 1600, 2000),
}

_PERCENT_ATTRIBUTES = {
    "能力的HP回复上限",
    "普通攻击伤害上限",
    "奥义连锁伤害",
    "能力伤害上限",
    "奥义伤害上限",
    "能力伤害",
    "奥义伤害",
    "暴击率",
    "昏厥值",
}


def infer_stars_from_ability_value(attribute: str | None, value: float | None) -> int | None:
    """Infer stars from the game's exact 1★-10★ value tables.

    A returned value is the star position in the table, not a rounded ratio.
    Values outside the table are intentionally left unknown so they can still
    be resolved from the visible star band or an unambiguous history entry.
    """

    if attribute is None or value is None:
        return None
    if attribute in _PERCENT_ATTRIBUTES:
        table = _STAR_VALUE_TABLES["percent"]
    elif attribute == "攻击力":
        table = _STAR_VALUE_TABLES["attack"]
    elif attribute == "HP":
        table = _STAR_VALUE_TABLES["hp"]
    else:
        return None
    for stars, expected_value in enumerate(table, start=1):
        if abs(float(value) - expected_value) <= 1e-6:
            return stars
    return None


def normalize_ability_text(text: str) -> str:
    """Remove OCR spacing/punctuation while preserving Chinese and digits."""

    return re.sub(r"[\s:：+＋%％()（）·・,，。]", "", str(text)).strip()


def normalize_ability_name(text: str) -> str | None:
    normalized = normalize_ability_text(text)
    for canonical, aliases in ABILITY_ATTRIBUTE_ALIASES:
        if any(normalize_ability_text(alias) in normalized for alias in aliases):
            return canonical
    return None


def parse_ability_value(text: str) -> float | None:
    """Read the displayed numeric effect, e.g. ``HP +1600`` or ``+8%``."""

    normalized = str(text).replace("％", "%").replace("＋", "+")
    values = [float(match.group(1)) for match in _NUMERIC_RE.finditer(normalized)]
    return values[-1] if values else None


def _location(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    value = item.get("location")
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        return tuple(float(part) for part in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def count_star_icons(
    frame: Any,
    location: tuple[float, float, float, float],
) -> int | None:
    """Count bright filled blue stars below one OCR property line.

    The unfilled star slots are intentionally excluded.  They remain visible
    in the game but do not represent the current star value.  Counting the
    repeated icon widths is more stable than asking OCR to recognize ``*``.
    """

    try:
        pixels = np.asarray(frame.convert("RGB"), dtype=np.float32)
    except (AttributeError, TypeError, ValueError):
        return None
    if pixels.ndim != 3 or pixels.shape[2] < 3:
        return None

    height, width = pixels.shape[:2]
    x0 = max(0, int(location[0]) - 6)
    # Ten slots occupy about 200 px at the supplied stream scale. Do not let
    # a long OCR text box extend into the separator or the next column.
    x1 = min(width, x0 + 220)
    scan_y0 = max(0, int(location[3]) + 5)
    scan_y1 = min(height, int(location[3]) + 20)
    if scan_y1 <= scan_y0:
        return None
    # The first few pixels after the text can contain the icon glow or the
    # dialog separator. Find the narrow horizontal band with the strongest
    # star signal, then count columns only in that band so neighboring text
    # cannot merge otherwise separate stars.
    scan = pixels[scan_y0:scan_y1, x0:x1]
    scan_red, scan_green, scan_blue = scan[:, :, 0], scan[:, :, 1], scan[:, :, 2]
    scan_mask = (
        (scan.mean(axis=2) > 45.0)
        & (scan_blue > scan_red * 1.10)
        & (scan_blue >= scan_green * 0.95)
        & ((scan.max(axis=2) - scan.min(axis=2)) > 10.0)
    )
    peak_y = scan_y0 + int(np.argmax(scan_mask.sum(axis=1)))
    y0 = max(scan_y0, peak_y - 3)
    y1 = min(height, peak_y + 10)
    if x1 <= x0 or y1 <= y0:
        return None

    crop = pixels[y0:y1, x0:x1]
    red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    # Blue star pixels are visible even through the translucent dialog.  The
    # brightness floor intentionally drops for compressed/remote frames.
    mask = (
        (crop.mean(axis=2) > 45.0)
        & (blue > red * 1.10)
        & (blue >= green * 0.95)
        & ((crop.max(axis=2) - crop.min(axis=2)) > 10.0)
    )
    column_score = mask.sum(axis=0)
    groups: list[tuple[int, int]] = []
    active = False
    start = 0
    for index, score in enumerate(column_score):
        if score >= 2 and not active:
            start = index
            active = True
        if active and (score < 2 or index == len(column_score) - 1):
            end = index if index == len(column_score) - 1 else index - 1
            if 7 <= end - start + 1 <= 18:
                groups.append((start, end))
            active = False
    return len(groups)


@dataclass(frozen=True)
class AbilityRoll:
    attribute: str | None
    raw_text: str
    stars: int | None
    value: float | None
    side: str
    row_index: int
    stars_source: str = "unknown"
    star_validation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute": self.attribute,
            "raw_text": self.raw_text,
            "stars": self.stars,
            "value": self.value,
            "side": self.side,
            "row_index": self.row_index,
            "stars_source": self.stars_source,
            "star_validation": self.star_validation,
        }


def extract_ability_rolls(
    ocr_items: Iterable[dict[str, Any]] | None,
    frame: Any = None,
    *,
    side: str,
    star_evidence: dict[str, dict[str, int]] | None = None,
) -> tuple[list[AbilityRoll], list[str]]:
    """Extract one roll per matching property and return unmatched OCR text."""

    if side not in {"old", "new"}:
        raise ValueError("side must be old or new")
    items = list(ocr_items or [])
    try:
        frame_width = int(frame.width) if frame is not None else 1920
    except (AttributeError, TypeError, ValueError):
        frame_width = 1920

    rolls: list[AbilityRoll] = []
    unmatched: list[str] = []
    star_lines: list[tuple[tuple[float, float, float, float], int]] = []
    attribute_items: list[tuple[dict[str, Any], str, tuple[float, float, float, float]]] = []
    seen: set[str] = set()
    for item in items:
        raw_text = str(item.get("text", "")).strip()
        if not raw_text:
            continue
        location = _location(item)
        if location is None:
            unmatched.append(raw_text)
            continue
        center_x = (location[0] + location[2]) * 0.5 / max(1, frame_width)
        # Ability dialog columns occupy roughly .20-.48 and .52-.80 of the
        # client frame. Exclude the title, question, and bottom buttons.
        in_side = (
            0.18 <= center_x < 0.50
            if side == "old"
            else 0.50 < center_x <= 0.82
        )
        # The animated ``Over the Limit`` candidate page places its fourth
        # row lower than the two-column confirmation dialog. Keep the broad
        # ability panel range here; only known attribute names are accepted,
        # so the surrounding tab/button labels remain excluded by name and
        # the lower row is not dropped before stage detection.
        if not in_side or location[1] < 0.34 * getattr(frame, "height", 1110) or location[1] > 0.85 * getattr(frame, "height", 1110):
            continue
        attribute = normalize_ability_name(raw_text)
        if attribute is None:
            star_count = parse_ocr_star_count(raw_text)
            if star_count is not None:
                # RapidOCR often returns the filled-star row as a separate
                # box below the ability name. Keep it for the association
                # pass instead of counting it as an unknown property.
                star_lines.append((location, star_count))
                continue
            unmatched.append(raw_text)
            continue
        if attribute in seen:
            continue
        seen.add(attribute)
        attribute_items.append((item, attribute, location))

    used_star_lines: set[int] = set()
    for item, attribute, location in attribute_items:
        raw_text = str(item.get("text", "")).strip()
        value = parse_ability_value(raw_text)
        stars_source = "unknown"
        star_validation = None
        stars = count_star_icons(frame, location) if frame is not None else None
        if stars:
            stars_source = "frame"
        else:
            explicit_stars = parse_ocr_star_count(raw_text)
            if explicit_stars is not None:
                stars = explicit_stars
                stars_source = "inline_ocr"
        if not stars:
            # The star line is normally directly below the property line.
            # Match within this side and consume each OCR line once.
            candidates: list[tuple[float, float, int]] = []
            for index, (star_location, star_count) in enumerate(star_lines):
                if index in used_star_lines:
                    continue
                vertical_gap = star_location[1] - location[3]
                horizontal_gap = abs(
                    (star_location[0] + star_location[2]) * 0.5
                    - (location[0] + location[2]) * 0.5
                )
                same_column = horizontal_gap <= max(
                    80.0, (location[2] - location[0]) * 0.5
                )
                if 0 <= vertical_gap <= 100 and same_column:
                    candidates.append((vertical_gap, horizontal_gap, index))
            if candidates:
                _, _, star_index = min(candidates)
                used_star_lines.add(star_index)
                stars = star_lines[star_index][1]
                stars_source = "star_ocr"
        value_key = ability_value_key(value)
        value_stars = infer_stars_from_ability_value(attribute, value)
        historical_stars = (
            star_evidence.get(attribute, {}).get(value_key)
            if star_evidence is not None and value_key is not None
            else None
        )
        validations: list[str] = []
        observed_stars = stars
        if value_stars is not None:
            if observed_stars is not None and observed_stars != value_stars:
                validations.append(
                    f"OCR={observed_stars} 星；数值规则={value_stars} 星"
                )
            stars = value_stars
            stars_source = "value_rule"
        if historical_stars is not None:
            if stars is None:
                stars = historical_stars
                stars_source = "history_value"
            elif stars != historical_stars:
                validations.append(
                    f"当前={stars} 星；历史同数值={historical_stars} 星"
                )
        if validations:
            star_validation = "；".join(validations)
        rolls.append(
            AbilityRoll(
                attribute=attribute,
                raw_text=raw_text,
                stars=stars,
                value=value,
                side=side,
                row_index=len(rolls),
                stars_source=stars_source,
                star_validation=star_validation,
            )
        )
    return rolls, unmatched


def compare_roll_sets(
    old_rolls: Iterable[AbilityRoll],
    new_rolls: Iterable[AbilityRoll],
) -> tuple[bool, str]:
    """Check that every new row strictly improves its same-named old row.

    Attribute order is ignored. Stars are the primary displayed quality. If
    star counts tie, the numeric effect must increase. Different attribute
    sets are not considered an improvement because one old ability would have
    been lost.
    """

    old_map = {roll.attribute: roll for roll in old_rolls if roll.attribute}
    new_map = {roll.attribute: roll for roll in new_rolls if roll.attribute}
    if len(old_map) != 4 or len(new_map) != 4:
        return False, f"旧词条 {len(old_map)}/4，新词条 {len(new_map)}/4，无法逐项比较"
    if set(old_map) != set(new_map):
        missing = sorted(set(old_map) - set(new_map))
        added = sorted(set(new_map) - set(old_map))
        return False, f"属性集合变化：缺少 {missing or '无'}，新增 {added or '无'}"

    improved = 0
    for attribute, old in old_map.items():
        new = new_map[attribute]
        if old.stars is None or new.stars is None:
            return False, f"{attribute} 星数未识别"
        if new.stars > old.stars:
            improved += 1
            continue
        if new.stars < old.stars:
            return False, f"{attribute} 星数下降：{old.stars} -> {new.stars}"
        if old.value is None or new.value is None:
            return False, f"{attribute} 数值未识别"
        if new.value <= old.value:
            return False, f"{attribute} 数值未提高：{old.value:g} -> {new.value:g}"
        improved += 1
    return improved == 4, "四项属性均按名称匹配且星数/数值严格提高"


def total_stars(rolls: Iterable[AbilityRoll]) -> int | None:
    values = [roll.stars for roll in rolls]
    if len(values) != 4 or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def meets_attribute_thresholds(
    rolls: Iterable[AbilityRoll], thresholds: dict[str, int]
) -> tuple[bool, str]:
    if not thresholds:
        return True, "未启用具体属性阈值"
    by_name = {roll.attribute: roll for roll in rolls if roll.attribute}
    for attribute, minimum in thresholds.items():
        roll = by_name.get(attribute)
        if roll is None or roll.stars is None:
            return False, f"{attribute} 未识别"
        if roll.stars < minimum:
            return False, f"{attribute} 星数不足：{roll.stars} < {minimum}"
    return True, "已选择属性均达到最低星数"


def meets_attribute_star_sum(
    rolls: Iterable[AbilityRoll], attributes: Iterable[str], minimum: int
) -> tuple[bool, str, int | None]:
    """Check the current star sum for the selected attributes."""

    selected = [str(attribute) for attribute in attributes if str(attribute)]
    if not selected:
        return False, "未选择用于求和的属性", None
    by_name = {roll.attribute: roll for roll in rolls if roll.attribute}
    missing = [attribute for attribute in selected if attribute not in by_name]
    if missing:
        return False, f"指定属性未识别：{missing}", None
    missing_stars = [
        attribute
        for attribute in selected
        if by_name[attribute].stars is None
    ]
    if missing_stars:
        return False, f"指定属性星数未识别：{missing_stars}", None
    star_sum = sum(by_name[attribute].stars or 0 for attribute in selected)
    if star_sum >= minimum:
        return True, f"指定属性星数之和 {star_sum} >= {minimum}", star_sum
    return False, f"指定属性星数之和 {star_sum} < {minimum}", star_sum


def evaluate_attribute_groups(
    rolls: Iterable[AbilityRoll],
    groups: Iterable[dict[str, object]],
) -> tuple[bool, str]:
    """Evaluate alternative attribute combinations.

    A group is one acceptable combination.  Its enabled individual-threshold
    rule and enabled selected-attribute-sum rule are alternatives, matching
    the legacy single-group behavior.  Groups themselves are ORed: one valid
    group is enough to qualify the roll.  Disabled and empty groups never
    qualify on their own.
    """

    current = list(rolls)
    active_groups = 0
    failed_reasons: list[str] = []
    for index, raw_group in enumerate(groups, start=1):
        if not isinstance(raw_group, dict):
            continue
        if not _config_bool(raw_group.get("enabled"), True):
            continue

        raw_thresholds = raw_group.get("thresholds", {})
        thresholds: dict[str, int] = {}
        if isinstance(raw_thresholds, dict):
            for name, value in raw_thresholds.items():
                canonical = normalize_ability_name(str(name)) or str(name).strip()
                if not canonical:
                    continue
                try:
                    minimum = int(value)
                except (TypeError, ValueError):
                    continue
                if minimum >= 0:
                    thresholds[canonical] = minimum
        threshold_rule_enabled = _config_bool(
            raw_group.get("attribute_thresholds_enabled"), bool(thresholds)
        )
        sum_rule_enabled = _config_bool(raw_group.get("attribute_sum_enabled"), False)
        try:
            sum_minimum = max(0, int(raw_group.get("attribute_sum_min", 0)))
        except (TypeError, ValueError):
            sum_minimum = 0

        group_name = str(raw_group.get("name") or f"组合 {index}").strip()
        group_name = group_name or f"组合 {index}"
        checks: list[tuple[str, bool, str]] = []
        if threshold_rule_enabled and thresholds:
            passed, reason = meets_attribute_thresholds(current, thresholds)
            checks.append(("逐项", passed, reason))
        if sum_rule_enabled and thresholds:
            passed, reason, _star_sum = meets_attribute_star_sum(
                current, thresholds, sum_minimum
            )
            checks.append(("星数之和", passed, reason))
        if not checks:
            failed_reasons.append(f"{group_name} 未配置有效条件")
            continue

        active_groups += 1
        passed_checks = [label for label, passed, _reason in checks if passed]
        if passed_checks:
            return True, f"{group_name}满足：{'、'.join(passed_checks)}"
        # A user may name a group after one of its attributes (for example,
        # "昏厥值").  Prefix the name as a group label and retain each rule
        # label; otherwise a failure in another row is easily read as that
        # attribute itself being below the threshold.
        failed_reasons.append(
            f"组合“{group_name}”未满足："
            + "；".join(
                f"{label}条件：{reason}"
                for label, _passed, reason in checks
            )
        )

    if active_groups == 0:
        return False, "未启用有效属性组合"
    return False, "；".join(failed_reasons) or "所有属性组合均未满足"


@dataclass(frozen=True)
class AbilityEvaluation:
    """Result of applying overall and same-attribute acceptance rules."""

    total_stars: int | None
    total_ok: bool
    attributes_ok: bool
    attribute_sum_stars: int | None
    attribute_sum_ok: bool
    overall_ok: bool
    same_attribute_set: bool
    comparison_ok: bool
    accepted_by_comparison: bool
    should_accept: bool
    auto_accept: bool
    total_reason: str
    attributes_reason: str
    attribute_sum_reason: str
    overall_reason: str
    comparison_reason: str
    attribute_group_ok: bool = False
    attribute_group_reason: str = "未使用多组合条件"


def evaluate_ability_rolls(
    old_rolls: Iterable[AbilityRoll],
    new_rolls: Iterable[AbilityRoll],
    *,
    total_enabled: bool,
    total_min: int,
    thresholds: dict[str, int],
    compare_enabled: bool,
    auto_overwrite: bool,
    attribute_thresholds_enabled: bool | None = None,
    attribute_sum_enabled: bool = False,
    attribute_sum_min: int = 0,
    attribute_groups: Iterable[dict[str, object]] | None = None,
) -> AbilityEvaluation:
    """Evaluate independent overall rules and optional paired comparison.

    A changed attribute set is valid for the overall star/attribute rules, but
    it cannot be compared by name. Paired comparison is therefore considered
    only when both sides contain the same four attributes.
    """

    old = list(old_rolls)
    new = list(new_rolls)
    normalized_groups = list(attribute_groups) if attribute_groups is not None else None
    new_total = total_stars(new)
    total_ok = not total_enabled or (new_total is not None and new_total >= total_min)
    if not total_enabled:
        total_reason = "未启用总星数条件"
    elif new_total is None:
        recognized_stars = sum(1 for roll in new if roll.stars is not None)
        total_reason = (
            f"总星数暂不能计算：新词条已识别 {recognized_stars}/{len(new)} 项星数，"
            f"需要完整 4 项（要求 >= {total_min}）"
        )
    elif total_ok:
        total_reason = f"总星数 {new_total} >= {total_min}"
    else:
        total_reason = f"总星数 {new_total} < {total_min}"

    attribute_group_ok = False
    attribute_group_reason = "未使用多组合条件"
    if normalized_groups is not None:
        attribute_group_ok, attribute_group_reason = evaluate_attribute_groups(
            new, normalized_groups
        )
        attributes_ok = attribute_group_ok
        attributes_reason = attribute_group_reason
        attributes_rule_enabled = any(
            isinstance(group, dict)
            and _config_bool(group.get("enabled"), True)
            for group in normalized_groups
        )
        attribute_sum_ok = False
        attribute_sum_stars = None
        attribute_sum_reason = "由各属性组合分别判断"
    else:
        attributes_ok, attributes_reason = meets_attribute_thresholds(new, thresholds)
        selected_attributes = list(thresholds)
        if attribute_thresholds_enabled is None:
            attribute_thresholds_enabled = bool(thresholds)
        attributes_rule_enabled = bool(attribute_thresholds_enabled and thresholds)
        if attribute_sum_enabled:
            attribute_sum_ok, attribute_sum_reason, attribute_sum_stars = meets_attribute_star_sum(
                new,
                selected_attributes,
                attribute_sum_min,
            )
        else:
            attribute_sum_ok = False
            attribute_sum_stars = None
            attribute_sum_reason = "未启用指定属性星数之和条件"
    total_rule_enabled = bool(total_enabled)
    overall_conditions: list[tuple[str, bool]] = []
    if total_rule_enabled:
        overall_conditions.append(("总星数", total_ok))
    if attributes_rule_enabled:
        overall_conditions.append(
            ("指定属性组合" if normalized_groups is not None else "指定属性逐项", attributes_ok)
        )
    if normalized_groups is None and attribute_sum_enabled:
        overall_conditions.append(("指定属性星数之和", attribute_sum_ok))
    overall_ok = any(result for _, result in overall_conditions)
    if not overall_conditions:
        overall_reason = "未启用整体条件"
    elif overall_ok:
        passed = "、".join(name for name, result in overall_conditions if result)
        overall_reason = f"{passed}条件满足（整体条件任一满足）"
    else:
        overall_reason = "总星数、指定属性逐项、指定属性星数之和条件均未满足"
    old_attributes = {roll.attribute for roll in old if roll.attribute}
    new_attributes = {roll.attribute for roll in new if roll.attribute}
    same_attribute_set = (
        len(old_attributes) == 4
        and len(new_attributes) == 4
        and old_attributes == new_attributes
    )

    if not compare_enabled:
        comparison_reason = "未启用逐项比较"
        comparison_ok = False
    elif not same_attribute_set:
        comparison_reason = "属性未能一一对应，跳过逐项比较；仅按整体条件判断"
        comparison_ok = False
    else:
        comparison_ok, comparison_reason = compare_roll_sets(old, new)

    accepted_by_comparison = compare_enabled and comparison_ok
    should_accept = overall_ok or accepted_by_comparison
    auto_accept = (overall_ok and auto_overwrite) or accepted_by_comparison
    return AbilityEvaluation(
        total_stars=new_total,
        total_ok=total_ok,
        attributes_ok=attributes_ok,
        attribute_sum_stars=attribute_sum_stars,
        attribute_sum_ok=attribute_sum_ok,
        overall_ok=overall_ok,
        same_attribute_set=same_attribute_set,
        comparison_ok=comparison_ok,
        accepted_by_comparison=accepted_by_comparison,
        should_accept=should_accept,
        auto_accept=auto_accept,
        total_reason=total_reason,
        attributes_reason=attributes_reason,
        attribute_sum_reason=attribute_sum_reason,
        overall_reason=overall_reason,
        comparison_reason=comparison_reason,
        attribute_group_ok=attribute_group_ok,
        attribute_group_reason=attribute_group_reason,
    )


class AbilityJournal:
    """Persist all observed rows, including unknown OCR strings."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                loaded.setdefault("rounds", [])
                loaded.setdefault("attributes", {})
                loaded.setdefault("unknown_ocr", {})
                return loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        return {"version": 1, "updated_at": None, "rounds": [], "attributes": {}, "unknown_ocr": {}}

    def record_round(
        self,
        *,
        old_rolls: Iterable[AbilityRoll],
        new_rolls: Iterable[AbilityRoll],
        raw_ocr: Iterable[str],
        unknown_ocr: Iterable[str] | None = None,
        decision: str,
        reason: str,
        action: str | None = None,
    ) -> None:
        old = list(old_rolls)
        new = list(new_rolls)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        round_data = {
            "time": timestamp,
            "decision": decision,
            "reason": reason,
            "old": [roll.as_dict() for roll in old],
            "new": [roll.as_dict() for roll in new],
            "raw_ocr": list(raw_ocr),
        }
        if action:
            # ``decision`` is the rule outcome; ``action`` records what the
            # worker actually attempted on the page. Keeping both fields
            # makes a keymap or input-delivery problem distinguishable from a
            # threshold/evaluation problem in the journal.
            round_data["action"] = action
        self.data["rounds"].append(round_data)
        for roll in [*old, *new]:
            if roll.attribute is None:
                continue
            item = self.data["attributes"].setdefault(
                roll.attribute,
                {"count": 0, "star_counts": {}, "raw_texts": []},
            )
            item["count"] = int(item.get("count", 0)) + 1
            if roll.stars is not None:
                key = str(roll.stars)
                counts = item.setdefault("star_counts", {})
                counts[key] = int(counts.get(key, 0)) + 1
            if roll.value is not None and roll.stars is not None and roll.stars > 0:
                value_counts = item.setdefault("value_star_counts", {})
                value_key = ability_value_key(roll.value)
                if value_key is not None:
                    value_item = value_counts.setdefault(value_key, {})
                    star_key = str(roll.stars)
                    value_item[star_key] = int(value_item.get(star_key, 0)) + 1
            raw_texts = item.setdefault("raw_texts", [])
            if roll.raw_text not in raw_texts:
                raw_texts.append(roll.raw_text)
        # Keep the complete OCR snapshot in each round for diagnosis, but only
        # aggregate parser misses in ``unknown_ocr``. Older callers can omit
        # this argument and retain the original behavior.
        for raw_text in unknown_ocr if unknown_ocr is not None else raw_ocr:
            text = str(raw_text).strip()
            if not text:
                continue
            item = self.data["unknown_ocr"].setdefault(text, {"count": 0, "last_seen": None})
            item["count"] = int(item.get("count", 0)) + 1
            item["last_seen"] = timestamp
        self.data["updated_at"] = timestamp
        self.save()

    def star_evidence(self) -> dict[str, dict[str, int]]:
        """Return unambiguous historical value-to-star mappings.

        Older journals do not have OCR coordinates, so history is deliberately
        keyed by normalized attribute and displayed numeric value.  A mapping
        is usable only when every recorded star value for that pair agrees.
        This makes old data useful for filling a missing star row without
        allowing one inconsistent OCR result to silently decide the result.
        """

        observations: dict[str, dict[str, dict[int, int]]] = {}
        for round_data in self.data.get("rounds", []):
            if not isinstance(round_data, dict):
                continue
            for side in ("old", "new"):
                rows = round_data.get(side, [])
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    attribute = normalize_ability_name(str(row.get("attribute", "")))
                    if attribute is None:
                        continue
                    try:
                        value = float(row.get("value"))
                        stars = int(row.get("stars"))
                    except (TypeError, ValueError):
                        continue
                    if stars <= 0:
                        continue
                    value_key = ability_value_key(value)
                    if value_key is None:
                        continue
                    by_value = observations.setdefault(attribute, {}).setdefault(value_key, {})
                    by_value[stars] = by_value.get(stars, 0) + 1

        evidence: dict[str, dict[str, int]] = {}
        for attribute, values in observations.items():
            for value_key, star_counts in values.items():
                if len(star_counts) == 1:
                    evidence.setdefault(attribute, {})[value_key] = next(iter(star_counts))
        return evidence

    def clear(self) -> tuple[int, int, int]:
        """Remove all journal history while keeping the journal file usable.

        The caller is responsible for confirming with the user and ensuring
        that no ability-reroll worker is writing the same file concurrently.
        Return counts so the UI can report exactly what was removed.
        """

        rounds = self.data.get("rounds", [])
        attributes = self.data.get("attributes", {})
        unknown_ocr = self.data.get("unknown_ocr", {})
        removed = (
            len(rounds) if isinstance(rounds, list) else 0,
            len(attributes) if isinstance(attributes, dict) else 0,
            len(unknown_ocr) if isinstance(unknown_ocr, dict) else 0,
        )
        version = self.data.get("version", 1)
        self.data = {
            "version": version,
            "updated_at": None,
            "rounds": [],
            "attributes": {},
            "unknown_ocr": {},
        }
        self.save()
        return removed

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
