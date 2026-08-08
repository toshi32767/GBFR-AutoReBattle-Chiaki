# ============================================================
# GBFR Auto ReBattle — 主入口
# ============================================================

import threading
from contextlib import contextmanager
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from time import monotonic, sleep, time
from pathlib import Path
import ctypes
import importlib.util
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tkinter as tk
import tkinter.scrolledtext as scrolledtext
from tkinter import filedialog, messagebox, simpledialog, ttk
import webbrowser
from module.controller import (
    Controller,
    RECOGNITION_PROFILES,
    WindowsCapture,
    adjust_window_rect_ex,
    vg,
)
from module.ability_reroll import (
    ABILITY_ATTRIBUTE_ALIASES,
    ABILITY_STOP_MODE_ATTRIBUTES,
    ABILITY_STOP_MODE_REMAINING_MSP,
    ABILITY_STOP_MODE_SPENT_MSP,
    AbilityJournal,
    evaluate_ability_rolls,
    extract_msp_from_ocr,
    extract_ability_rolls,
    normalize_ability_name,
    normalize_ability_stop_mode,
    msp_stop_status,
)
from module.log import Log, get_runtime_log_dir
from module.psn_account import LOGIN_URL, account_id_from_redirect, run_account_id_prompt
import argparse
import math
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

log = logging.getLogger("GBFR")

try:
    import winreg
except ImportError:  # pragma: no cover - this application targets Windows
    winreg = None


# Chiaki's stream window accepts keyboard mappings configured in Settings.
# These defaults match the stock Chiaki mapping except for W, which must be
# assigned to "Left Stick Up" by the user.
CHIAKI_WINDOW_TITLE = "Chiaki | Stream"
CROSS_KEY = "enter"
MOON_KEY = "backspace"
LEFT_STICK_UP_KEY = "w"
LEFT_STICK_DOWN_KEY = "s"
LEFT_STICK_LEFT_KEY = "a"
LEFT_STICK_RIGHT_KEY = "d"
RIGHT_STICK_LEFT_KEY = "q"
RIGHT_STICK_RIGHT_KEY = "e"
D_PAD_UP_KEY = "up"
D_PAD_DOWN_KEY = "down"
PYRAMID_KEY = "c"
R1_KEY = "3"
SQUARE_KEY = "\\"
L2_KEY = "l"
REFOCUS_SECONDS = 15.0
REFOCUS_MODE_MELEE = "melee"
REFOCUS_MODE_RANGED = "ranged"
REFOCUS_MODE_BOSS_RING = "boss_ring"
REFOCUS_MODE_L2_RING = "l2_ring"
REFOCUS_MODE_L2_SBA = "l2_sba"
REFOCUS_MODE_SBA_RING_GUARDED = "sba_ring_guarded"
REFOCUS_MODE_RING_ARC_EXPERIMENT = "ring_arc_experiment"
REFOCUS_MODE_LABELS = {
    REFOCUS_MODE_MELEE: "索敌方案 1",
    REFOCUS_MODE_RANGED: "索敌方案 2",
    REFOCUS_MODE_BOSS_RING: "索敌方案 3",
    REFOCUS_MODE_L2_RING: "索敌方案 4",
    REFOCUS_MODE_L2_SBA: "索敌方案 5",
    REFOCUS_MODE_SBA_RING_GUARDED: "索敌方案 6",
    REFOCUS_MODE_RING_ARC_EXPERIMENT: "索敌方案 7（圆弧+奥义保护）",
}
REFOCUS_MODE_DEFAULT = REFOCUS_MODE_MELEE
REFOCUS_MODE = REFOCUS_MODE_DEFAULT
DEBUG_MODE = False
DEBUG_DIAGNOSTIC_PATH: Path | None = None
REMOTE_REFOCUS_SBA_POLL_SECONDS = 10.0
REMOTE_REFOCUS_SKILL_POLL_SECONDS = 1.0
REMOTE_REFOCUS_SKILL_STATIC_SECONDS = 15.0
REMOTE_REFOCUS_SKILL_CHANGE_THRESHOLD = 8.0
REMOTE_REFOCUS_SBA_FULL_THRESHOLD = 0.94
L2_RING_PROBE_INTERVAL_SECONDS = 3.0
L2_RING_SETTLE_SECONDS = 0.35
L2_RING_SAMPLE_INTERVAL_SECONDS = 0.12
L2_RING_CONFIRM_SAMPLES = 3
L2_RING_PRESENT_MIN_SAMPLES = 2
L2_RING_MISSING_CONFIRMATIONS = 2
L2_SBA_PROBE_INTERVAL_SECONDS = 3.0
L2_SBA_GROWTH_EPSILON = 0.01
L2_SBA_RING_GUARDED_PROBE_INTERVAL_SECONDS = 3.0
L2_SBA_RING_GUARDED_L2_COOLDOWN_SECONDS = 10.0
L2_SBA_RING_GUARDED_MISSING_GROUPS = 4
L2_SBA_RING_GUARDED_RECOVER_ATTEMPTS = 2
L2_SBA_RING_GUARDED_RECENT_GROWTH_SECONDS = 8.0
L2_SBA_RING_GUARDED_RELEASE_PROTECTION_SECONDS = 8.0
RING_ARC_EXPERIMENT_POLL_SECONDS = 0.75
RING_ARC_EXPERIMENT_TRACK_SECONDS = 1.6
RING_ARC_EXPERIMENT_TRACK_CENTER_DISTANCE = 0.14
RING_ARC_EXPERIMENT_TRACK_RADIUS_RATIO = 0.42
L2_BOSS_RING_PROBE_INTERVAL_SECONDS = 3.0
L2_BOSS_RING_SAMPLE_INTERVAL_SECONDS = 0.12
L2_BOSS_RING_CONFIRM_SAMPLES = 3
L2_BOSS_RING_PRESENT_MIN_SAMPLES = 2
L2_BOSS_BAR_MISSING_GROUPS = 3
CHIAKI_FREEZE_TIMEOUT_SECONDS = 600.0
MIN_FREEZE_TIMEOUT_SECONDS = 5.0
REFOCUS_SEARCH_SECONDS = 1.0
REFOCUS_STABILIZE_SECONDS = 1.5
REFOCUS_CONFIRM_SAMPLES = 2
REFOCUS_CONFIRM_INTERVAL_SECONDS = 0.5

UI_LANGUAGE_LABELS = {
    "auto": "自动识别",
    "zh": "简体中文",
    "ja": "日文",
}
APP_LANGUAGE_LABELS = {
    "zh": "简体中文",
    "ja": "日本語",
    "en": "English",
}
GAME_LANGUAGE_LABELS = {
    "zh": {"auto": "自动识别", "zh": "简体中文", "ja": "日文"},
    "ja": {"auto": "自動判定", "zh": "簡体字中国語", "ja": "日本語"},
    "en": {"auto": "Auto detect", "zh": "Simplified Chinese", "ja": "Japanese"},
}
APP_TRANSLATIONS = {
    "ja": {
        "GBFR 自动重战": "GBFR 自動再戦",
        "工具界面语言": "ツール表示言語",
        "应用界面": "適用",
        "Chiaki 程序": "Chiaki プログラム",
        "浏览": "参照",
        "自动查找": "自動検索",
        "启动 Chiaki": "Chiaki を起動",
        "串流窗口标题": "ストリームウィンドウタイトル",
        "应用标题": "タイトルを適用",
        "捕获当前标题": "現在のタイトルを取得",
        "一键同步输入配置": "入力設定を同期",
        "游戏界面语言": "ゲーム表示言語",
        "应用语言": "ゲーム言語を適用",
        "识别画面档位": "認識解像度",
        "恢复 Chiaki 画面": "Chiaki 解像度を復元",
        "战斗索敌方案": "戦闘ターゲット検索方式",
        "索敌方案": "ターゲット検索方式",
        "应用索敌方案": "検索方式を適用",
        "检查后台环境": "バックグラウンド環境を確認",
        "安装 ViGEmBus": "ViGEmBus をインストール",
        "安装 HidHide": "HidHide をインストール",
        "启动自动重战": "自動再戦を開始",
        "暂停/继续（F3）": "一時停止/再開 (F3)",
        "一键重连并挂机": "再接続して放置開始",
        "获取 PSN AccountID": "PSN AccountID を取得",
        "停止并关闭 Chiaki": "停止して Chiaki を終了",
        "打开日志目录": "ログフォルダーを開く",
        "设置…": "設定…",
        "能力提升重抽…": "能力強化リロール…",
        "查看词条记录": "能力記録を表示",
        "串流卡死恢复（配置会保存在本机）": "ストリーム停止時の復旧 (設定はこのPCに保存)",
        "自动结束设置（点击“应用设置”后生效，运行中修改也会生效）": "自動終了設定 (適用後に有効)",
        "本轮挂机统计": "今回の放置統計",
        "运行日志（暂停自动滚动后可拖动右侧滚动条查看历史）": "実行ログ (自動スクロールを一時停止して履歴を確認)",
        "查看最新日志": "最新ログを表示",
    },
    "en": {
        "GBFR 自动重战": "GBFR Auto ReBattle",
        "工具界面语言": "Tool UI language",
        "应用界面": "Apply",
        "Chiaki 程序": "Chiaki program",
        "浏览": "Browse",
        "自动查找": "Find automatically",
        "启动 Chiaki": "Start Chiaki",
        "串流窗口标题": "Stream window title",
        "应用标题": "Apply title",
        "捕获当前标题": "Capture current title",
        "一键同步输入配置": "Sync input settings",
        "游戏界面语言": "Game language",
        "应用语言": "Apply game language",
        "识别画面档位": "Recognition resolution",
        "恢复 Chiaki 画面": "Restore Chiaki resolution",
        "战斗索敌方案": "Battle targeting strategy",
        "索敌方案": "Targeting strategy",
        "应用索敌方案": "Apply strategy",
        "检查后台环境": "Check background environment",
        "安装 ViGEmBus": "Install ViGEmBus",
        "安装 HidHide": "Install HidHide",
        "启动自动重战": "Start Auto ReBattle",
        "暂停/继续（F3）": "Pause/Resume (F3)",
        "一键重连并挂机": "Reconnect and start",
        "获取 PSN AccountID": "Get PSN AccountID",
        "停止并关闭 Chiaki": "Stop and close Chiaki",
        "打开日志目录": "Open log folder",
        "设置…": "Settings…",
        "能力提升重抽…": "Ability reroll…",
        "查看词条记录": "View ability journal",
        "串流卡死恢复（配置会保存在本机）": "Stream recovery (saved locally)",
        "自动结束设置（点击“应用设置”后生效，运行中修改也会生效）": "Automatic stop settings",
        "本轮挂机统计": "Session statistics",
        "运行日志（暂停自动滚动后可拖动右侧滚动条查看历史）": "Runtime log (pause scrolling to inspect history)",
        "查看最新日志": "Show latest log",
    },
}
UI_MARKERS = {
    "zh": {
        "battle_hud": ("跳跃",),
        "battle_timer": ("剩余时间", "剩余时", "余时间"),
        # In the compressed result prompt, the recognition model can turn
        # 续 into 绒/级 or confuse 继 with 细.  This crop is restricted to the
        # bottom-right result control and still requires two stable frames.
        "result_continue": ("继续", "继绒", "继级", "继线", "细续"),
        "result_retry_available": ("再次",),
        "result_retry_cancel": ("撤销再次挑战", "取消再次挑战", "撤销", "取消"),
        "result_retry_any": ("再次", "撤销", "取消"),
        "result_screen": ("再次挑战", "撤销", "战斗结算", "结算确认"),
        "settlement": ("结算",),
        "confirmation": ("确认",),
        "challenge_confirmation": ("挑战",),
        "movie": ("跳过", "剧情跳过", "SKIP"),
        "game_menu": (
            "主菜单",
            "持有物",
            "玩家设置",
            "角色详情",
            "退出任务",
            "贵重之物",
            "收藏列表",
            "筛选/排序",
        ),
        # This modal can be rendered over the quest-center background, so it
        # must be classified before the broader ``quest_destination`` marker.
        "town_collection_list": ("收藏列表",),
        # L2 in town opens the quick-travel overlay on some clients. It must
        # be closed before the quest-center macro is allowed to run.
        "town_fast_travel": ("移动目的地", "移动先", "简易移动"),
        "quest_destination": ("任务中心",),
        "quest_counter": ("任务中心", "任务选择"),
        "quest_accepted": ("已承接任务", "开始任务"),
        "quest_abandon_confirmation": ("放弃已承接", "取消任务", "放弃任务"),
        "quest_page": (
            "任务中心",
            "任务选择",
            "承接任务",
            "上一次",
            "难度",
            "匹配设置",
        ),
        "quest_ready": ("准备完毕",),
    },
    "ja": {
        # The recorded HUD crop can trim the final character at some aspect
        # ratios, so ジャン is the stable language-specific prefix.
        # Stream compression occasionally turns ジャンプ into シャンプ,
        # ジンプ, or シッンプ. The stable ``ンプ`` suffix is safe here because
        # battle acceptance also requires the timer and skill-panel geometry.
        "battle_hud": ("ジャン", "ンプ"),
        "battle_timer": ("残り時間", "残り時"),
        # Japanese result prompts sit very close to the right edge. OCR may
        # return a katakana-lookalike for the second character, so keep the
        # common variants while the crop remains limited to the prompt area.
        # At smaller Chiaki client sizes RapidOCR may retain only the final
        # glyph from the bottom-right prompt. This marker is used exclusively
        # in the narrow ``继续`` crop and still requires two fresh frames.
        "result_continue": ("次へ", "次ヘ", "次へ進む", "へ"),
        # Native-size recognition can confuse 挑 with 規 on the compressed
        # result prompt. The crop is restricted to this one control, so this
        # is a safe Japanese-only OCR fallback.
        "result_retry_available": (
            "再挑戦する",
            "再規戦する",
            "再挑戦す",
            "再規戦す",
            # Low-resolution Japanese OCR variants from the supplied result
            # page. These are accepted only in the dedicated lower-left crop.
            "再排殺する",
            "回事排殺する",
            "再挑殺する",
            "再排戦する",
            "再排製する",
            "ロ事排殺する",
            # The first two glyphs remain stable in the left-quarter crop;
            # accept this prefix when the final Japanese characters are lost.
            "再排",
        ),
        "result_retry_cancel": ("キャンセル", "挑戦をキ"),
        "result_retry_any": ("再挑戦", "再規戦", "キャンセル", "挑戦をキ"),
        "result_screen": ("再挑戦", "再規戦", "リザルト確認", "BATTLE RESULT"),
        # The Japanese title is small and blurred on low-size clients. The
        # center-crop OCR can turn リザルト確認 into リサルト確調/確譚; these
        # variants are used only by the settlement center crop.
        "settlement": ("リザルト", "リサルト", "リザルト確"),
        "confirmation": ("確認", "確調", "確譚"),
        # The 10-battle confirmation is read from the same central ``结算``
        # crop as the Chinese prompt.  Until a Japanese capture confirms the
        # exact wording, accept the short OCR forms as an event signal and
        # reuse the Chinese W + Cross action sequence.
        # Never use the single glyph ``戦`` here: normal result objectives
        # contain ``戦闘`` and can appear on the same center crop. A challenge
        # page must have a challenge phrase or a complete choice pair.
        "challenge_confirmation": ("再挑戦", "再規戦", "再戦", "挑戦する"),
        "challenge_confirmation_retry": ("再挑戦確認", "再規戦確認"),
        "movie": ("スキップ", "SKIP"),
        "game_menu": (
            "メインメニュー",
            "所持品",
            "プレイヤー設定",
            "キャラクター詳細",
            "クエストをリタイア",
            "大事なもの",
            "図鑑",
            "絞り込み",
        ),
        "town_collection_list": ("お気に入り一覧", "お気に入りリスト", "コレクション一覧"),
        "town_fast_travel": (
            "移動先選択",
            "移動先遥",
            "簡易移動",
            "簡易移動先",
            "移動先",
        ),
        "quest_destination": ("クエストカウンター",),
        "quest_counter": ("クエストカウンター", "クエスト選択"),
        # ``受注クエスト確認`` is a normal quest-counter menu entry, not proof
        # that a quest was accepted. Only the completion toast/ready prompt can
        # advance recovery to the Square step.
        "quest_accepted": ("受注しました", "準備OK"),
        "quest_abandon_confirmation": (
            "受注中のクエストを破棄",
            "クエストを破棄",
            "クエストをキャンセル",
        ),
        "quest_page": (
            "クエストカウンター",
            "クエスト受注",
            "受注クエスト",
            "クエスト選択",
            "クエストを受注する",
            "マッチング設定",
            "マッチング方式",
        ),
        "quest_ready": ("準備OK",),
    },
}

# Normalized centers of the top and right trigger skills in a 16:9 Chiaki frame.
SKILL_TRIGGER_CENTERS = (
    (0.8172, 0.8083),  # upper skill
    (0.8276, 0.8259),  # right skill
)
SKILL_PATCH_HALF_SIZE = (0.0031, 0.0056)
SKILL_TRIGGER_MIN_BRIGHTNESS = 180.0
SKILL_TRIGGER_DIMMED_MIN_BRIGHTNESS = 125.0
SKILL_TRIGGER_DIMMED_MIN_BLUE_CHROMA = 25.0
SKILL_TRIGGER_DIMMED_MIN_P95 = 175.0
SKILL_TRIGGER_DIM_GRACE_SECONDS = 5.0
SKILL_MONITOR_IDLE_POLL_SECONDS = 2.0
SKILL_MONITOR_ACTIVE_POLL_SECONDS = 1.0
# The battle-only blue skill/action panel occupies this fixed lower-right area.
# OCR of the much smaller Jump label is not sufficient evidence by itself:
# town control hints share the same area and can occasionally be misread as
# ``跳跃``.  The thresholds below were calibrated against the supplied Chinese
# town screenshot and 64 consecutive Japanese battle frames.
BATTLE_HUD_LAYOUT_REGION = (0.68, 0.72, 0.97, 0.91)
BATTLE_HUD_BLUE_MIN_VALUE = 145.0
BATTLE_HUD_BLUE_MIN_CHROMA = 38.0
BATTLE_HUD_BLUE_MIN_FRACTION = 0.08
BATTLE_HUD_CONFIRM_INTERVAL_SECONDS = 0.55
# The result page shows the auto-repeat status as a small language-independent
# icon in the right side of the left ``结算进度/リザルトの進行状況`` row.  Its
# gold check is enabled; the gray icon means the option is still available.
RESULT_REPEAT_INDICATOR_REGION = (0.22, 0.70, 0.25, 0.74)
# Search inside the user-marked lower-left result panel, but score only small
# local windows around the status icon. The whole panel is about 8.9% of a
# 540P frame and averaging it would dilute the actual gold signal.
RESULT_REPEAT_INDICATOR_SEARCH_REGION = (0.16, 0.66, 0.27, 0.81)
# The 540P icon crop contains dark result-background gradients and thin gold
# UI lines. A 4% threshold treated those unrelated pixels as an enabled icon.
# Require a denser, more saturated gold signal inside the marked position.
RESULT_REPEAT_GOLD_MIN_FRACTION = 0.10
# The first BATTLE RESULT summary page has only 次へ. The retry page adds a
# lower-left blue action bar. Use the bar as a visual page discriminator when
# the Japanese text is still unreadable; otherwise a strict retry guard would
# incorrectly block the summary page before it can advance to the control.
RESULT_REPEAT_CONTROL_REGION = (0.03, 0.89, 0.25, 0.925)
RESULT_REPEAT_CONTROL_BLUE_MIN_FRACTION = 0.20
# The white circular PS5 button glyph at the left edge of the retry bar is a
# smaller and more stable page marker than the bar's changing blue background.
RESULT_REPEAT_PS_BUTTON_REGION = (0.065, 0.875, 0.135, 0.93)
RESULT_REPEAT_PS_BUTTON_MIN_FRACTION = 0.018
# Secondary Japanese-page fallback. This is only the reward row containing
# ``獲得MSP`` and its value, not the whole upper half of the frame.
RESULT_MSP_MARKER_REGION = (0.25, 0.12, 0.62, 0.23)
RESULT_MSP_MARKER_MIN_DIGITS = 1
# The Japanese ``次へ`` glyph can disappear from OCR at 540P. This is only a
# result-phase fallback; it is never used by the general battle recognizer.
RESULT_CONTINUE_VISUAL_REGION = (0.80, 0.88, 0.995, 0.995)
RESULT_CONTINUE_VISUAL_MIN_FRACTION = 0.004
RESULT_REPEAT_CONFIRM_SAMPLES = 2
RESULT_REPEAT_CONFIRM_TIMEOUT_SECONDS = 4.5
# A Japanese 540P result summary may require several Cross inputs before the
# lower-left retry page is rendered. Stop immediately once that page appears.
JAPANESE_RESULT_CONTINUE_MAX_PRESSES = 3
# A dropped virtual-controller report can leave the visible result page in
# the still-available state. Retry only after that state is stable; Square is
# a toggle, so this must remain deliberately bounded.
RESULT_REPEAT_MAX_TOGGLE_ATTEMPTS = 2
RESULT_REPEAT_RETRY_SETTLE_SECONDS = 1.0
FREEZE_LOW_ACTIVITY_SCORE = 1.5
FREEZE_BASE_LOW_ACTIVITY_RATIO = 0.80
FREEZE_DURATION_LOW_ACTIVITY_RATIO = 0.55
FREEZE_DURATION_CONFIRM_SECONDS = 60.0
STREAM_WINDOW_LOST_CONFIRM_SECONDS = 3.0
STREAM_WINDOW_WATCHDOG_POLL_SECONDS = 1.0
# A result/loading gap is not evidence of a town return.  Keep this watchdog
# deliberately long; the positive screen probes below remain the fast path.
UNEXPECTED_TOWN_RECOVERY_DELAY_SECONDS = 180.0
UNEXPECTED_TOWN_RECOVERY_TIMEOUT_SECONDS = 75.0
UNEXPECTED_TOWN_RECOVERY_MAX_ATTEMPTS = 3
# The selected-quest card sits at the right side of the town screen.  Its
# final action line displays Box/Square together with ``准备完毕``/``準備OK``.
# Crop it independently during town recovery so scene-heavy full-frame OCR
# cannot hide the one prompt that must not receive Cross.
TOWN_READY_PANEL_REGION = (0.68, 0.38, 0.99, 0.64)
# The Box outline itself is a language-independent white square centered on
# the selected quest's blue final-action row. Both signals are required; the
# white outline alone is too common in other town UI elements.
TOWN_READY_BOX_ICON_REGION = (0.81, 0.535, 0.85, 0.585)
TOWN_READY_BOX_MIN_WHITE_FRACTION = 0.04
TOWN_READY_BOX_MIN_BLUE_FRACTION = 0.55
# The accepted-quest confirmation is a large centered dark-blue modal. It is
# distinct from the right-side Box card and lets non-Chinese clients pause for
# OCR evidence instead of guessing that Cross is safe.
TOWN_READY_DIALOG_REGION = (0.24, 0.07, 0.77, 0.89)
TOWN_READY_DIALOG_MIN_DARK_BLUE_FRACTION = 0.65
TOWN_READY_DIALOG_READY_REGION = (0.40, 0.715, 0.60, 0.775)
TOWN_READY_DIALOG_CANCEL_REGION = (0.40, 0.765, 0.60, 0.835)
TOWN_READY_DIALOG_SELECTION_MIN_DELTA = 0.10
# The Japanese destination list is a large left-side overlay. A dedicated
# crop avoids full-frame OCR being dominated by the character and town HUD.
TOWN_DESTINATION_MENU_REGION = (0.14, 0.12, 0.80, 0.90)
# A successful Box/Cross can be followed by a long loading transition on a
# remote client. Do not re-send inputs during this interval: the prior input
# may already have accepted the quest.
TOWN_RECOVERY_NAVIGATION_TIMEOUT_SECONDS = 120.0
TOWN_RECOVERY_BATTLE_CONFIRM_TIMEOUT_SECONDS = 60.0
TOWN_RECOVERY_DEBUG_REPORT_SECONDS = 10.0
AUTOMATION_INPUT_LOCK = threading.Lock()
# The input lock only serializes individual controller operations.  A town
# recovery, reconnect route, or ability reroll is a multi-step transaction and
# must not be interrupted by a watchdog deciding to recover the stream midway.
_AUTOMATION_FLOW_STATE_LOCK = threading.RLock()
_AUTOMATION_FLOW_OWNER: int | None = None
_AUTOMATION_FLOW_STACK: list[str] = []
_AUTOMATION_FLOW_STARTED_AT: float | None = None
_CAPTURE_UNAVAILABLE_WARNED = False
SESSION_STATS = None
SCHEDULE_FILE: Path | None = None
RECOVERY_CONFIG: dict[str, object] = {}
INITIAL_AUTOMATION_PHASE = "startup_probe"
PS5_DISCOVERY_PORT = 9302
PS5_DISCOVERY_PROTOCOL_VERSION = "00030010"
PS5_DISCOVERY_LOCAL_PORT_MIN = 9303
PS5_DISCOVERY_LOCAL_PORT_MAX = 9319


def automation_flow_active() -> bool:
    """Return whether a multi-step automation transaction owns navigation."""
    with _AUTOMATION_FLOW_STATE_LOCK:
        return bool(_AUTOMATION_FLOW_STACK)


def automation_flow_name() -> str | None:
    """Return the active transaction path for diagnostics and watchdog logs."""
    with _AUTOMATION_FLOW_STATE_LOCK:
        if not _AUTOMATION_FLOW_STACK:
            return None
        return " > ".join(_AUTOMATION_FLOW_STACK)


def _begin_automation_flow(name: str) -> bool:
    """Claim a re-entrant transaction for the current thread without blocking."""
    global _AUTOMATION_FLOW_OWNER, _AUTOMATION_FLOW_STARTED_AT
    owner = threading.get_ident()
    with _AUTOMATION_FLOW_STATE_LOCK:
        if _AUTOMATION_FLOW_OWNER not in (None, owner):
            return False
        if _AUTOMATION_FLOW_OWNER is None:
            _AUTOMATION_FLOW_OWNER = owner
            _AUTOMATION_FLOW_STARTED_AT = monotonic()
        _AUTOMATION_FLOW_STACK.append(name)
        return True


def _finish_automation_flow(name: str) -> None:
    """Release one transaction level held by the current thread."""
    global _AUTOMATION_FLOW_OWNER, _AUTOMATION_FLOW_STARTED_AT
    owner = threading.get_ident()
    with _AUTOMATION_FLOW_STATE_LOCK:
        if _AUTOMATION_FLOW_OWNER != owner or not _AUTOMATION_FLOW_STACK:
            return
        if _AUTOMATION_FLOW_STACK[-1] != name:
            log.error(
                "自动化流程保护释放顺序异常：当前=%s，尝试释放=%s",
                automation_flow_name(),
                name,
            )
            return
        _AUTOMATION_FLOW_STACK.pop()
        if not _AUTOMATION_FLOW_STACK:
            _AUTOMATION_FLOW_OWNER = None
            _AUTOMATION_FLOW_STARTED_AT = None


@contextmanager
def automation_flow(name: str):
    """Protect a high-level navigation flow from asynchronous recovery input."""
    acquired = _begin_automation_flow(name)
    if acquired:
        log.debug("自动化流程保护进入：%s", automation_flow_name())
    try:
        yield acquired
    finally:
        if acquired:
            _finish_automation_flow(name)
            log.debug("自动化流程保护退出：%s", name)


def clear_automation_flow_state(reason: str) -> None:
    """Clear a stale transaction during explicit shutdown or state-machine exit."""
    global _AUTOMATION_FLOW_OWNER, _AUTOMATION_FLOW_STARTED_AT
    with _AUTOMATION_FLOW_STATE_LOCK:
        if _AUTOMATION_FLOW_STACK:
            log.warning(
                "清除未完成的自动化流程保护（%s）：%s",
                reason,
                " > ".join(_AUTOMATION_FLOW_STACK),
            )
        _AUTOMATION_FLOW_STACK.clear()
        _AUTOMATION_FLOW_OWNER = None
        _AUTOMATION_FLOW_STARTED_AT = None

OCR_RUNTIME_DEPENDENCIES = {
    "onnxruntime": "onnxruntime",
    "Shapely": "shapely",
    "pyclipper": "pyclipper",
    "scikit-image": "skimage",
    "PyYAML": "yaml",
}

# The 10-battle confirmation can use a short challenge prompt or only show its
# two choice labels after stream compression.  A choice pair is accepted only
# from the central result crop while the state machine is already in ``result``;
# a lone ``キャンセル`` must never be enough to advance the game.
JAPANESE_CHALLENGE_CONFIRMATION_OPTION_SETS = (
    ("はい", "いいえ"),
    ("決定", "キャンセル"),
    ("実行", "キャンセル"),
    ("確認", "キャンセル"),
    ("OK", "キャンセル"),
    ("再挑戦する", "キャンセル"),
)
# The remote-targeting experiment monitors the full four-slot diamond. Keep
# this separate from SKILL_TRIGGER_CENTERS so the established melee/mage
# watchdog remains behaviorally unchanged during A/B testing.
REMOTE_SKILL_CENTERS = (
    (0.8172, 0.8083),  # up
    (0.8276, 0.8259),  # right
    (0.8172, 0.8435),  # down
    (0.8068, 0.8259),  # left
)
REMOTE_SBA_REGION = (0.055, 0.121, 0.255, 0.150)
REMOTE_SBA_TEXT_EXCLUDE = 0.80

# The centered accepted-quest modal has a stable Japanese choice structure:
# a ready action (``準備OK``; OCR may split it into two items) and a cancel
# action.  Requiring both sides prevents a lone OCR fragment from confirming a
# different Japanese menu.
JAPANESE_READY_DIALOG_CANCEL_MARKERS = ("キャンセル", "取消")


def missing_ocr_runtime_dependencies() -> list[str]:
    """Return OCR imports missing from the active Python environment."""

    return [
        package
        for package, module_name in OCR_RUNTIME_DEPENDENCIES.items()
        if importlib.util.find_spec(module_name) is None
    ]


def missing_runtime_dependencies() -> list[str]:
    """Return runtime components that prevent a complete source checkout run."""

    missing = []
    if WindowsCapture is None:
        missing.append("windows-capture")
    if vg is None:
        missing.append("vgamepad")
    missing.extend(missing_ocr_runtime_dependencies())
    return missing


def launcher_process_is_alive(process_id: int) -> bool:
    """Return whether the GUI process that launched this worker still exists."""
    if process_id <= 0 or os.name != "nt":
        return True
    process_query_limited_information = 0x1000
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, int(process_id)
        )
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError):
        # A temporary OS-query failure must not stop a live unattended run.
        return True


def watch_launcher_process(relink: Controller, process_id: int) -> None:
    """Stop an orphaned GUI child without treating a Chiaki loss as a stop."""
    if process_id <= 0:
        return

    def worker() -> None:
        while not relink.shutdown_requested:
            sleep(1.0)
            if launcher_process_is_alive(process_id):
                continue
            log.warning("控制面板进程已关闭，停止后台自动化并释放输入")
            relink.request_shutdown("launcher_closed")
            return

    threading.Thread(target=worker, daemon=True, name="gbfr-launcher-watchdog").start()


ABILITY_RESULT_MARKERS = (
    "能力值覆盖确认",
    "能力值提升效果",
    "新的能力提升效果",
    "ステータス上書き確認",
    "新たに獲得したステータス",
)
ABILITY_SUCCESS_MARKERS = (
    "Over the Limit",
    "上限突破成功",
    "突破成功",
)
ABILITY_CONFIRM_MARKERS = ("执行", "執行", "実行", "Execute")
# These are deliberately language-exclusive page labels.  ``上限突破`` itself
# is shared by the Chinese and Japanese clients, so it must never lock OCR to
# either model on its own.
ABILITY_LANGUAGE_MARKERS = {
    "zh": (
        "有几率不提升基础能力值",
        "可获得的能力值",
        "消耗MSP进行上限突破",
        "当前能力值提升效果",
        "新的能力提升效果",
        "能力值覆盖确认",
    ),
    "ja": (
        "ステータス上書き確認",
        "新たに獲得したステータス",
        "現在の能力値上昇効果",
        "上限突破を行う",
        "上限突破を実行",
    ),
}
ABILITY_NAVIGATION_MAX_STEPS = 4
ABILITY_NAVIGATION_SETTLE_SECONDS = 0.75
ABILITY_SUCCESS_SETTLE_SECONDS = 2.0
ABILITY_SUCCESS_CONTINUE_INTERVAL_SECONDS = 1.0
ABILITY_REROLL_SETTLE_SECONDS = 0.8
ABILITY_ACCEPT_HIGHLIGHT_SETTLE_SECONDS = 0.8
ABILITY_RESULT_TIMEOUT_SECONDS = 90.0
ABILITY_OCR_PASSES = 3
# Candidate, execution-confirmation, and success pages each have a strict
# structural classifier.  A second full-screen OCR pass adds several seconds
# on 1080p streams without making those transitions safer.  The final
# overwrite result is different: it contains the data used for a destructive
# decision, so it deliberately remains double-checked.
ABILITY_STAGE_DOUBLE_CHECK = frozenset({"result"})
ABILITY_QUALIFIED_SOUND_FILE = "ability-qualified.wav"
ABILITY_CURRENT_EFFECT_MARKERS = (
    "当前能力值提升效果",
    "目前能力值提升效果",
    "現在の能力値上昇効果",
    "現在の効果",
)
ABILITY_RELINK_DICT = {
    # The ability feature deliberately captures the full client area. The
    # parser uses the two fixed dialog columns and retains all OCR strings.
    "能力提升": [0.0, 0.0, 1.0, 1.0],
}


def ability_roll_display_name(roll: object) -> str:
    """Format a journal roll name without changing its matching key."""
    if not isinstance(roll, dict):
        return "未识别"
    name = str(roll.get("attribute") or roll.get("raw_text") or "未识别")
    stars = roll.get("stars")
    if stars is None:
        return name
    try:
        number = float(stars)
        stars_text = str(int(number)) if number.is_integer() else f"{number:g}"
    except (TypeError, ValueError):
        stars_text = str(stars)
    return f"{name}（{stars_text}星）"


def play_ability_qualified_alert() -> bool:
    """Play the bundled voice alert when a qualified result is found.

    Keep playback synchronous so the independent ability worker cannot exit
    before an asynchronous Windows sound finishes. Missing audio support or a
    damaged/missing resource is non-fatal; the toast and log still provide the
    normal notification path.
    """

    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)))
    try:
        roots.append(Path(__file__).resolve().parent)
    except OSError:
        pass
    try:
        roots.append(Path(sys.executable).resolve().parent)
    except OSError:
        pass
    sound_path = next(
        (
            root / "assets" / ABILITY_QUALIFIED_SOUND_FILE
            for root in roots
            if (root / "assets" / ABILITY_QUALIFIED_SOUND_FILE).is_file()
        ),
        None,
    )
    if sound_path is None:
        log.warning("达标提醒音频不存在：%s", ABILITY_QUALIFIED_SOUND_FILE)
        return False
    try:
        import winsound

        winsound.PlaySound(
            str(sound_path),
            winsound.SND_FILENAME | winsound.SND_NODEFAULT,
        )
    except (ImportError, OSError, RuntimeError) as exc:
        log.warning("达标提醒音频播放失败：%s", exc)
        return False
    log.info("已播放能力提升达标人声提醒：%s", sound_path.name)
    return True

# Chiaki 2.2.0 defaults from gui/src/settings.cpp. QSettings stores only user
# overrides under HKCU\Software\Chiaki\Chiaki\keymap, so synchronization must
# merge both sources instead of treating absent registry values as missing.
CHIAKI_KEYMAP_DEFAULTS = {
    "cross": "Return",
    "box": "\\",
    "pyramid": "C",
    "l2": "1",
    "r1": "3",
    "d-pad_up": "Up",
    "left_stick_up": "Insert",
    "left_stick_down": "Delete",
    "left_stick_left": "[",
    "left_stick_right": "]",
    "right_stick_left": "-",
    "right_stick_right": "=",
}
AUTOMATION_KEY_FIELDS = {
    "cross": "cross",
    "square": "box",
    "pyramid": "pyramid",
    "l2": "l2",
    "r1": "r1",
    "dpad_up": "d-pad_up",
    "left_up": "left_stick_up",
    "left_down": "left_stick_down",
    "left_left": "left_stick_left",
    "left_right": "left_stick_right",
    "right_left": "right_stick_left",
    "right_right": "right_stick_right",
}


def read_chiaki_reconnect_targets() -> tuple[list[str], list[str]]:
    """Read non-secret Chiaki host nicknames and manually saved addresses."""
    nicknames: set[str] = set()
    addresses: set[str] = set()
    if winreg is None:
        return [], []
    base = r"Software\Chiaki\Chiaki"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\registered_hosts") as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    with winreg.OpenKey(key, str(index + 1)) as host_key:
                        value, _ = winreg.QueryValueEx(host_key, "server_nickname")
                        if isinstance(value, str) and value.strip():
                            nicknames.add(value.strip())
                except OSError:
                    continue
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\manual_hosts") as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    with winreg.OpenKey(key, str(index + 1)) as host_key:
                        value, _ = winreg.QueryValueEx(host_key, "host")
                        if isinstance(value, str) and value.strip():
                            addresses.add(value.strip())
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(nicknames), sorted(addresses)


def _decode_chiaki_qbytearray(value: object) -> bytes:
    """Decode the UTF-16LE QSettings representation used by Chiaki on Windows.

    Chiaki stores ``QByteArray`` values as a registry binary value containing
    ``@ByteArray(<bytes>)``.  Registered host keys are ASCII and padded with
    NUL bytes, so this deliberately accepts only that narrow representation.
    """
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-16le").rstrip("\x00")
        except UnicodeDecodeError:
            return b""
    elif isinstance(value, str):
        text = value
    else:
        return b""
    if not (text.startswith("@ByteArray(") and text.endswith(")")):
        return b""
    body = text[len("@ByteArray(") : -1]
    return body.encode("ascii", "ignore").rstrip(b"\x00")


def read_chiaki_registkey(nickname: str) -> str | None:
    """Return one registered host's wakeup credential without logging it."""
    if winreg is None or not nickname.strip():
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Chiaki\Chiaki\registered_hosts",
        ) as hosts_key:
            for index in range(winreg.QueryInfoKey(hosts_key)[0]):
                try:
                    # QSettings' Windows array keys are numbered from 1.
                    with winreg.OpenKey(hosts_key, str(index + 1)) as host_key:
                        host_name, _ = winreg.QueryValueEx(host_key, "server_nickname")
                        if str(host_name).strip() != nickname.strip():
                            continue
                        raw, _ = winreg.QueryValueEx(host_key, "rp_regist_key")
                        key = _decode_chiaki_qbytearray(raw)
                        # CLI wakeup accepts the plaintext eight-character hex
                        # registration credential (not the full binary key).
                        if len(key) >= 8:
                            candidate = key[:8].decode("ascii", "ignore")
                            if all(char in "0123456789abcdefABCDEF" for char in candidate):
                                return candidate
                except OSError:
                    continue
    except OSError:
        return None
    return None


def find_chiaki_executables(base_dir: Path | None = None) -> list[Path]:
    """Find packaged or installed Chiaki without assuming one installer layout."""
    roots: list[Path] = []
    if base_dir is not None:
        roots.extend((base_dir / "Chiaki" / "chiaki.exe", base_dir / "chiaki.exe"))
    for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(variable)
        if value:
            root = Path(value)
            roots.extend(
                (
                    root / "Chiaki" / "chiaki.exe",
                    root / "chiaki4deck" / "chiaki.exe",
                    root / "Chiaki-ng" / "chiaki.exe",
                )
            )
    # Portable apps commonly register an App Paths entry even when they do not
    # have an uninstall record.  Read both per-user and machine-wide locations.
    if winreg is not None:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key_path in (
                r"Software\Microsoft\Windows\CurrentVersion\App Paths\chiaki.exe",
                r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chiaki.exe",
            ):
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        if value:
                            roots.append(Path(str(value).strip('"')))
                except OSError:
                    continue
            for uninstall_path in (
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ):
                try:
                    with winreg.OpenKey(hive, uninstall_path) as uninstall_key:
                        for index in range(winreg.QueryInfoKey(uninstall_key)[0]):
                            try:
                                with winreg.OpenKey(
                                    uninstall_key, winreg.EnumKey(uninstall_key, index)
                                ) as app_key:
                                    display_name = str(
                                        winreg.QueryValueEx(app_key, "DisplayName")[0]
                                    )
                                    if "chiaki" not in display_name.lower():
                                        continue
                                    install_location = str(
                                        winreg.QueryValueEx(app_key, "InstallLocation")[0]
                                    ).strip('" ')
                                    if install_location:
                                        roots.append(Path(install_location) / "chiaki.exe")
                            except OSError:
                                continue
                except OSError:
                    continue
    found: list[Path] = []
    seen: set[str] = set()
    for candidate in roots:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        normalized = os.path.normcase(str(resolved))
        if resolved.is_file() and normalized not in seen:
            found.append(resolved)
            seen.add(normalized)
    return found


def normalize_chiaki_key(text: str) -> str | None:
    """Convert a Qt QKeySequence string to Controller.KEY_MAP syntax."""
    value = str(text).strip()
    if not value or "+" in value:
        return None
    aliases = {
        "return": "enter",
        "enter": "enter",
        "backspace": "backspace",
        "esc": "escape",
        "escape": "escape",
        "space": "space",
        "ins": "insert",
        "insert": "insert",
        "del": "delete",
        "delete": "delete",
        "pgup": "pageup",
        "pageup": "pageup",
        "pgdown": "pagedown",
        "pagedown": "pagedown",
        "left": "left",
        "right": "right",
        "up": "up",
        "down": "down",
        "backslash": "\\",
    }
    lowered = value.lower()
    if lowered in aliases:
        return aliases[lowered]
    if len(value) == 1:
        return value.lower() if value.isalpha() else value
    if lowered.startswith("f") and lowered[1:].isdigit():
        return lowered
    return None


def read_chiaki_keymap() -> tuple[dict[str, str], list[str]]:
    """Read Chiaki QSettings overrides and merge the upstream defaults."""
    raw = dict(CHIAKI_KEYMAP_DEFAULTS)
    source_notes = ["Chiaki 2.2.0 默认键位"]
    if winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Chiaki\Chiaki\keymap",
            ) as key:
                index = 0
                overrides = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    if name in raw and isinstance(value, str):
                        raw[name] = value
                        overrides += 1
                source_notes.append(f"注册表覆盖 {overrides} 项")
        except FileNotFoundError:
            source_notes.append("未发现注册表覆盖")

    mapped: dict[str, str] = {}
    unsupported: list[str] = []
    for output_name, chiaki_name in AUTOMATION_KEY_FIELDS.items():
        key_name = normalize_chiaki_key(raw[chiaki_name])
        if key_name is None:
            unsupported.append(f"{chiaki_name}={raw[chiaki_name]}")
        else:
            mapped[output_name] = key_name
    return mapped, source_notes + unsupported


def apply_foreground_keymap(mapping: dict[str, str]) -> None:
    """Apply a synchronized Chiaki keyboard map to foreground automation."""
    global CROSS_KEY, SQUARE_KEY, PYRAMID_KEY, L2_KEY, R1_KEY
    global LEFT_STICK_UP_KEY, LEFT_STICK_DOWN_KEY
    global LEFT_STICK_LEFT_KEY, LEFT_STICK_RIGHT_KEY
    global RIGHT_STICK_LEFT_KEY, RIGHT_STICK_RIGHT_KEY
    global D_PAD_UP_KEY

    CROSS_KEY = mapping["cross"]
    SQUARE_KEY = mapping["square"]
    PYRAMID_KEY = mapping["pyramid"]
    L2_KEY = mapping["l2"]
    R1_KEY = mapping["r1"]
    LEFT_STICK_UP_KEY = mapping["left_up"]
    LEFT_STICK_DOWN_KEY = mapping["left_down"]
    LEFT_STICK_LEFT_KEY = mapping["left_left"]
    LEFT_STICK_RIGHT_KEY = mapping["left_right"]
    RIGHT_STICK_LEFT_KEY = mapping["right_left"]
    RIGHT_STICK_RIGHT_KEY = mapping["right_right"]
    D_PAD_UP_KEY = mapping["dpad_up"]


def decode_console_bytes(data: bytes) -> tuple[str, bytes]:
    """Decode live logs while preserving an incomplete final CJK character."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data:
        return "", b""

    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), b""
        except UnicodeDecodeError as exc:
            if exc.end == len(data):
                try:
                    return data[:exc.start].decode(encoding), data[exc.start:]
                except UnicodeDecodeError:
                    pass

    return data.decode("utf-8", errors="replace"), b""


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, int(seconds + 0.5))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class BattleSessionStats:
    """Thread-safe run statistics shared with the GUI through one JSON file."""

    def __init__(
        self,
        path: Path,
        max_battles: int = 0,
        max_runtime_minutes: float = 0.0,
        stop_at: str = "",
    ) -> None:
        self.path = path
        self.max_battles = max(0, int(max_battles))
        self.max_runtime_seconds = max(0.0, float(max_runtime_minutes) * 60.0)
        self.stop_at_text = stop_at.strip()
        self.stop_at_timestamp = self._next_stop_timestamp(self.stop_at_text)
        self._lock = threading.Lock()
        # Runtime limits measure the automation session itself, including time
        # spent waiting for Chiaki/game state. Per-battle duration remains a
        # separate timer started by ``start_battle``.
        self.session_started_at: float | None = time()
        self.current_battle_started_at: float | None = None
        self.current_pause_started_at: float | None = None
        self.current_paused_seconds = 0.0
        self.battles: list[dict[str, object]] = []
        self.status = "等待战斗"
        self.stop_reason = ""
        self._write_locked()

    @staticmethod
    def _next_stop_timestamp(value: str) -> float | None:
        if not value:
            return None
        parsed = datetime.strptime(value, "%H:%M")
        now = datetime.now()
        target = now.replace(
            hour=parsed.hour,
            minute=parsed.minute,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()

    def _effective_current_duration(self, now: float) -> float | None:
        if self.current_battle_started_at is None:
            return None
        paused = self.current_paused_seconds
        if self.current_pause_started_at is not None:
            paused += now - self.current_pause_started_at
        return max(0.0, now - self.current_battle_started_at - paused)

    def start_battle(self) -> None:
        with self._lock:
            now = time()
            if self.current_battle_started_at is None:
                self.current_battle_started_at = now
                self.current_pause_started_at = None
                self.current_paused_seconds = 0.0
            self.status = "战斗中"
            self._write_locked(now)

    def finish_battle(self) -> float | None:
        with self._lock:
            now = time()
            duration = self._effective_current_duration(now)
            if duration is None:
                self.status = "结算中"
                self._write_locked(now)
                return None
            self.battles.append(
                {
                    "number": len(self.battles) + 1,
                    "duration_seconds": round(duration, 3),
                    "ended_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                }
            )
            self.current_battle_started_at = None
            self.current_pause_started_at = None
            self.current_paused_seconds = 0.0
            self.status = "结算中"
            self._write_locked(now)
            return duration

    def discard_interrupted_battle(self) -> None:
        """Drop a frozen/incomplete battle before reconnecting the stream."""
        with self._lock:
            self.current_battle_started_at = None
            self.current_pause_started_at = None
            self.current_paused_seconds = 0.0
            self.status = "串流恢复中"
            self._write_locked()

    def sync_controller_state(self, running: bool, paused: bool) -> None:
        with self._lock:
            now = time()
            if paused and self.current_pause_started_at is None:
                self.current_pause_started_at = now
            elif not paused and self.current_pause_started_at is not None:
                self.current_paused_seconds += now - self.current_pause_started_at
                self.current_pause_started_at = None

            if paused:
                self.status = "已暂停"
            elif not running and not self.stop_reason:
                self.status = "等待启动"
            elif running and self.current_battle_started_at is None:
                self.status = "等待战斗" if not self.battles else "结算中"
            self._write_locked(now)

    def reached_limit(self) -> str | None:
        with self._lock:
            now = time()
            completed = len(self.battles)
            if self.max_battles and completed >= self.max_battles:
                return f"已完成设定的 {self.max_battles} 场战斗"
            if (
                self.max_runtime_seconds
                and self.session_started_at is not None
                and now - self.session_started_at >= self.max_runtime_seconds
            ):
                return f"已达到设定的 {_format_duration(self.max_runtime_seconds)} 运行时间"
            if self.stop_at_timestamp is not None and now >= self.stop_at_timestamp:
                return f"已到设定时间 {self.stop_at_text}"
            return None

    def update_limits(
        self,
        max_battles: int,
        max_runtime_minutes: float,
        stop_at: str,
    ) -> None:
        """Apply GUI changes without restarting the automation child."""
        if max_battles < 0 or not math.isfinite(max_runtime_minutes) or max_runtime_minutes < 0:
            raise ValueError("自动结束设置必须是非负数字")
        if stop_at:
            datetime.strptime(stop_at, "%H:%M")
        with self._lock:
            self.max_battles = int(max_battles)
            self.max_runtime_seconds = float(max_runtime_minutes) * 60.0
            self.stop_at_text = stop_at.strip()
            self.stop_at_timestamp = self._next_stop_timestamp(self.stop_at_text)
            self._write_locked()

    def stop(self, reason: str) -> None:
        with self._lock:
            self.stop_reason = reason
            self.status = "已按计划结束"
            self._write_locked()

    def refresh(self) -> None:
        with self._lock:
            self._write_locked()

    def battle_timing_snapshot(self) -> dict[str, float | int | None]:
        """Return battle timing values for the frozen-stream watchdog."""
        with self._lock:
            now = time()
            durations = [float(item["duration_seconds"]) for item in self.battles]
            return {
                "completed": len(durations),
                "current": self._effective_current_duration(now),
                "average": sum(durations) / len(durations) if durations else None,
                "longest": max(durations) if durations else None,
            }

    def _write_locked(self, now: float | None = None) -> None:
        now = time() if now is None else now
        durations = [float(item["duration_seconds"]) for item in self.battles]
        runtime = 0.0 if self.session_started_at is None else now - self.session_started_at
        payload = {
            "version": 1,
            "updated_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "session_started_at": (
                datetime.fromtimestamp(self.session_started_at).isoformat(timespec="seconds")
                if self.session_started_at is not None
                else None
            ),
            "status": self.status,
            "stop_reason": self.stop_reason,
            "completed_battles": len(self.battles),
            "current_battle_seconds": self._effective_current_duration(now),
            "total_runtime_seconds": runtime,
            "last_battle_seconds": durations[-1] if durations else None,
            "average_battle_seconds": sum(durations) / len(durations) if durations else None,
            "shortest_battle_seconds": min(durations) if durations else None,
            "longest_battle_seconds": max(durations) if durations else None,
            "limits": {
                "max_battles": self.max_battles,
                "max_runtime_seconds": self.max_runtime_seconds,
                "stop_at": self.stop_at_text,
            },
            "battles": self.battles,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            # Statistics are auxiliary; a locked profile must not stop combat.
            pass


def _stats_start_battle() -> None:
    if SESSION_STATS is not None:
        SESSION_STATS.start_battle()


def _stats_finish_battle() -> float | None:
    if SESSION_STATS is None:
        return None
    return SESSION_STATS.finish_battle()


def _stats_watchdog(relink: Controller) -> None:
    """Refresh the JSON panel and stop cleanly when a configured limit hits."""
    schedule_mtime: int | None = None
    while not relink.shutdown_requested:
        if SESSION_STATS is not None:
            if SCHEDULE_FILE is not None and SCHEDULE_FILE.is_file():
                try:
                    current_mtime = SCHEDULE_FILE.stat().st_mtime_ns
                    if current_mtime != schedule_mtime:
                        schedule_mtime = current_mtime
                        schedule_data = json.loads(
                            SCHEDULE_FILE.read_text(encoding="utf-8")
                        )
                        SESSION_STATS.update_limits(
                            int(schedule_data.get("max_battles", 0) or 0),
                            float(schedule_data.get("max_runtime_minutes", 0) or 0),
                            str(schedule_data.get("stop_at", "") or "").strip(),
                        )
                        log.info("已应用自动结束设置（运行中修改立即生效）")
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    log.warning("自动结束设置读取失败，沿用当前设置：%s", exc)
            SESSION_STATS.sync_controller_state(relink.running, relink.paused)
            reason = SESSION_STATS.reached_limit()
            if reason and relink.running:
                SESSION_STATS.stop(reason)
                log.warning("达到自动结束条件：%s", reason)
                relink.request_shutdown("schedule_limit")
                return
        sleep(1.0)


def close_chiaki_for_title(title: str) -> None:
    """Ask every top-level window belonging to the matching Chiaki process to close."""
    try:
        import win32con
        import win32gui
        import win32process

        target_hwnd = _find_window_handle(title)
        if not target_hwnd:
            log.info("自动结束时未找到 Chiaki 窗口：%s", title)
            return
        _, target_pid = win32process.GetWindowThreadProcessId(target_hwnd)
        windows: list[int] = []

        def callback(hwnd: int, _: object) -> bool:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == target_pid:
                    windows.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        for hwnd in windows:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        log.info("自动结束：已请求关闭 Chiaki 进程 PID=%d（窗口数=%d）", target_pid, len(windows))
    except Exception:
        log.warning("自动结束时关闭 Chiaki 失败", exc_info=True)


def recover_lost_target(
    relink: Controller,
    battle_is_active: Callable[[], bool],
    turn_key: str,
    camera_key: str,
) -> bool:
    """Turn while locking once, then verify the post-turn focus state."""
    direction_name = "左" if turn_key == LEFT_STICK_LEFT_KEY else "右"

    with AUTOMATION_INPUT_LOCK:
        if not relink.running or relink.paused or not battle_is_active():
            return False

        log.warning("停止前进，后退并向%s转向寻找敌人", direction_name)
        l2_sent = False
        search_started = time()
        l2_deadline = search_started + REFOCUS_SEARCH_SECONDS * 0.5
        try:
            relink.press(LEFT_STICK_UP_KEY, movement="release")
            relink.press(LEFT_STICK_DOWN_KEY, movement="press")
            relink.press(turn_key, movement="press")
            relink.press(camera_key, movement="press")

            deadline = search_started + REFOCUS_SEARCH_SECONDS
            while relink.running and not relink.paused and battle_is_active() and time() < deadline:
                if not l2_sent and time() >= l2_deadline:
                    relink.press(L2_KEY)
                    l2_sent = True
                    log.info("转身索敌进行中发送一次 L2，继续完成搜索弧线")
                sleep(0.1)
        finally:
            # Always neutralize every movement key touched by the recovery.
            # This also prevents a battle/result transition from leaving the
            # virtual stick held off-center.
            for key in (
                camera_key,
                turn_key,
                LEFT_STICK_DOWN_KEY,
                LEFT_STICK_UP_KEY,
            ):
                try:
                    relink.press(key, movement="release")
                except Exception:
                    log.debug("释放索敌移动键 %s 失败", key, exc_info=True)

        if not relink.running or relink.paused or not battle_is_active():
            log.info("索敌过程中战斗阶段已结束，不发送 L2")
            return False

        log.info(
            "搜索弧线完成，L2 已发送=%s；双摇杆已回中，等待画面稳定 %.1f 秒",
            "是" if l2_sent else "否",
            REFOCUS_STABILIZE_SECONDS,
        )
        stabilize_deadline = time() + REFOCUS_STABILIZE_SECONDS
        while (
            relink.running
            and not relink.paused
            and battle_is_active()
            and time() < stabilize_deadline
        ):
            sleep(0.1)

        if not relink.running or relink.paused or not battle_is_active():
            log.info("等待画面稳定时战斗阶段已结束，不发送 L2")
            return False

        confirmation_values: list[list[float]] = []
        for sample_index in range(REFOCUS_CONFIRM_SAMPLES):
            trigger_bright, values = skill_trigger_slots_bright(relink)
            confirmation_values.append([round(value, 1) for value in values])
            if not trigger_bright:
                log.info(
                    "画面稳定后第 %d/%d 次确认已不再高亮，本轮转身锁定结果有效: %s",
                    sample_index + 1,
                    REFOCUS_CONFIRM_SAMPLES,
                    confirmation_values[-1],
                )
                return True

            if sample_index + 1 < REFOCUS_CONFIRM_SAMPLES:
                confirm_deadline = time() + REFOCUS_CONFIRM_INTERVAL_SECONDS
                while (
                    relink.running
                    and not relink.paused
                    and battle_is_active()
                    and time() < confirm_deadline
                ):
                    sleep(0.1)
                if not relink.running or relink.paused or not battle_is_active():
                    log.info("二次确认期间战斗阶段已结束，不发送 L2")
                    return False

        if not relink.running or relink.paused or not battle_is_active():
            log.info("二次确认完成时战斗阶段已结束，不发送 L2")
            return False

        log.warning(
            "画面稳定后连续 %d 次仍高亮；本轮不再补发 L2，等待下一轮反向搜索: %s",
            REFOCUS_CONFIRM_SAMPLES,
            confirmation_values,
        )
        return True


def skill_trigger_slots_bright(relink: Controller) -> tuple[bool, list[float]]:
    """Return whether the upper or right trigger skill is bright.

    The command wheel darkens the complete battle HUD.  A second
    blue/purple-chroma path keeps a genuinely available skill detectable
    through that overlay without treating an ordinary grey cooldown icon as
    bright.
    """
    rect = relink.get_window_rect(silent=True)
    if rect is None:
        raise RuntimeError("无法读取 Chiaki 窗口尺寸")
    left, top, width, height = rect
    half_w = max(3, int(width * SKILL_PATCH_HALF_SIZE[0]))
    half_h = max(3, int(height * SKILL_PATCH_HALF_SIZE[1]))
    centers = [
        (int(width * center_x), int(height * center_y))
        for center_x, center_y in SKILL_TRIGGER_CENTERS
    ]
    crop_left = min(x for x, _ in centers) - half_w
    crop_top = min(y for _, y in centers) - half_h
    crop_right = max(x for x, _ in centers) + half_w + 1
    crop_bottom = max(y for _, y in centers) + half_h + 1

    combined = relink.screenshot(
        region=(
            left + crop_left,
            top + crop_top,
            crop_right - crop_left,
            crop_bottom - crop_top,
        )
    )
    pixels = np.asarray(combined, dtype=np.uint8)
    values: list[float] = []
    bright_states: list[bool] = []
    for center_x, center_y in centers:
        local_x = center_x - crop_left
        local_y = center_y - crop_top
        patch = pixels[
            local_y - half_h : local_y + half_h + 1,
            local_x - half_w : local_x + half_w + 1,
        ]
        channels = patch.astype(np.float32)
        maximum = channels.max(axis=2)
        brightness = float(maximum.mean())
        blue_chroma = float(
            (
                channels[:, :, 2]
                - (channels[:, :, 0] + channels[:, :, 1]) * 0.5
            ).mean()
        )
        brightness_p95 = float(np.percentile(maximum, 95))
        values.append(brightness)
        is_bright = (
            brightness >= SKILL_TRIGGER_MIN_BRIGHTNESS
            or (
                brightness >= SKILL_TRIGGER_DIMMED_MIN_BRIGHTNESS
                and blue_chroma >= SKILL_TRIGGER_DIMMED_MIN_BLUE_CHROMA
                and brightness_p95 >= SKILL_TRIGGER_DIMMED_MIN_P95
            )
        )
        bright_states.append(is_bright)

    return any(bright_states), values


def remote_skill_slots_signature(relink: Controller) -> list[tuple[float, float, float]]:
    """Capture a compact signature for the four remote-search skill slots."""
    rect = relink.get_window_rect(silent=True)
    if rect is None:
        raise RuntimeError("无法读取 Chiaki 窗口尺寸")
    left, top, width, height = rect
    half_w = max(3, int(width * SKILL_PATCH_HALF_SIZE[0]))
    half_h = max(3, int(height * SKILL_PATCH_HALF_SIZE[1]))
    centers = [
        (int(width * center_x), int(height * center_y))
        for center_x, center_y in REMOTE_SKILL_CENTERS
    ]
    crop_left = min(x for x, _ in centers) - half_w
    crop_top = min(y for _, y in centers) - half_h
    crop_right = max(x for x, _ in centers) + half_w + 1
    crop_bottom = max(y for _, y in centers) + half_h + 1
    screenshot = relink.screenshot(
        region=(
            left + crop_left,
            top + crop_top,
            crop_right - crop_left,
            crop_bottom - crop_top,
        )
    )
    pixels = np.asarray(screenshot, dtype=np.uint8)
    signatures: list[tuple[float, float, float]] = []
    for center_x, center_y in centers:
        local_x = center_x - crop_left
        local_y = center_y - crop_top
        patch = pixels[
            local_y - half_h : local_y + half_h + 1,
            local_x - half_w : local_x + half_w + 1,
        ].astype(np.float32)
        maximum = patch.max(axis=2)
        blue_chroma = float(
            (patch[:, :, 2] - (patch[:, :, 0] + patch[:, :, 1]) * 0.5).mean()
        )
        signatures.append(
            (
                float(maximum.mean()),
                blue_chroma,
                float(np.percentile(maximum, 95)),
            )
        )
    return signatures


def remote_skill_slots_changed(
    previous: list[tuple[float, float, float]] | None,
    current: list[tuple[float, float, float]],
) -> bool:
    """Treat a visible cooldown/brightness transition as skill activity."""
    if previous is None or len(previous) != len(current):
        return True
    return any(
        max(abs(old_value - new_value) for old_value, new_value in zip(old, new))
        >= REMOTE_REFOCUS_SKILL_CHANGE_THRESHOLD
        for old, new in zip(previous, current)
    )


def remote_sba_fill_fraction(relink: Controller) -> float:
    """Read the first character's SBA percentage, with a visual fallback."""
    rect = relink.get_window_rect(silent=True)
    if rect is None:
        raise RuntimeError("无法读取 Chiaki 窗口尺寸")
    left, top, width, height = rect
    x0, y0, x1, y1 = REMOTE_SBA_REGION
    crop_left = left + int(width * x0)
    crop_top = top + int(height * y0)
    crop_width = max(4, int(width * (x1 - x0)))
    crop_height = max(4, int(height * (y1 - y0)))
    crop = relink.screenshot(
        region=(crop_left, crop_top, crop_width, crop_height)
    )
    # The percentage is deliberately read only once every ten seconds. This
    # crop contains the bar and its percentage text, while excluding the next
    # party member's HUD. OCR is less sensitive to bright combat backgrounds
    # than trying to classify every orange pixel in the bar.
    try:
        text = str(relink.recognize_line(crop, confidence=0.35) or "")
        match = re.search(r"(?<!\d)(?:100|[0-9]{1,2})\s*[%％]", text)
        if match:
            value = int(re.search(r"\d+", match.group(0)).group(0))
            return min(1.0, max(0.0, value / 100.0))
    except Exception:
        log.debug("远程索敌奥义条 OCR 失败，使用像素补充判断", exc_info=True)

    # Fallback is intentionally conservative. It is only allowed to report
    # full when almost the complete bar has a strong warm fill; ordinary scene
    # colors therefore cannot easily satisfy the remote trigger.
    pixels = np.asarray(crop, dtype=np.uint8).astype(np.float32)
    pixels = pixels[:, : max(1, int(pixels.shape[1] * REMOTE_SBA_TEXT_EXCLUDE))]
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    orange = (
        (red >= 105)
        & (red >= green * 1.12)
        & (green >= blue * 1.25)
        & ((red - blue) >= 45)
    )
    column_fraction = orange.mean(axis=0)
    active_columns = column_fraction >= 0.12
    filled = 0
    for active in active_columns:
        if not active:
            break
        filled += 1
    fraction = filled / max(1, len(active_columns))
    return fraction if fraction >= 0.94 else 0.0


def remote_focus_watchdog(
    relink: Controller, battle_is_active: Callable[[], bool]
) -> None:
    """Experimental remote-targeting watchdog using SBA + four skills only."""
    skill_static_since: float | None = None
    previous_signature: list[tuple[float, float, float]] | None = None
    sba_full = False
    next_sba_sample = 0.0
    search_left = True

    while relink.running:
        if relink.paused or not battle_is_active():
            skill_static_since = None
            previous_signature = None
            sba_full = False
            next_sba_sample = 0.0
            sleep(0.5)
            continue

        try:
            now = time()
            if now >= next_sba_sample:
                sba_fill = remote_sba_fill_fraction(relink)
                sba_full = sba_fill >= REMOTE_REFOCUS_SBA_FULL_THRESHOLD
                next_sba_sample = now + REMOTE_REFOCUS_SBA_POLL_SECONDS
                log.debug(
                    "远程索敌奥义条采样：填充 %.1f%%，满格判定=%s",
                    sba_fill * 100.0,
                    "是" if sba_full else "否",
                )
                if not sba_full:
                    skill_static_since = None
                    previous_signature = None

            signature = remote_skill_slots_signature(relink)
            if not sba_full:
                sleep(REMOTE_REFOCUS_SKILL_POLL_SECONDS)
                continue
            if remote_skill_slots_changed(previous_signature, signature):
                skill_static_since = now
            elif skill_static_since is None:
                skill_static_since = now
            previous_signature = signature

            if (
                skill_static_since is not None
                and now - skill_static_since >= REMOTE_REFOCUS_SKILL_STATIC_SECONDS
            ):
                log.warning(
                    "远程索敌方案检测到奥义条满格且四个技能持续 %.0f 秒无变化，开始恢复索敌",
                    REMOTE_REFOCUS_SKILL_STATIC_SECONDS,
                )
                turn_key = LEFT_STICK_LEFT_KEY if search_left else LEFT_STICK_RIGHT_KEY
                camera_key = RIGHT_STICK_LEFT_KEY if search_left else RIGHT_STICK_RIGHT_KEY
                if recover_lost_target(
                    relink, battle_is_active, turn_key, camera_key
                ):
                    search_left = not search_left
                skill_static_since = now
        except Exception:
            log.debug("远程索敌方案采样失败", exc_info=True)
        sleep(REMOTE_REFOCUS_SKILL_POLL_SECONDS)


def detect_l2_target_ring_pixels(pixels: np.ndarray) -> tuple[bool, dict[str, float]]:
    """Detect the gold lock ring by multi-radius circular perimeter voting."""
    array = np.asarray(pixels, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] < 3 or array.shape[0] < 8 or array.shape[1] < 8:
        return False, {"yellow_fraction": 0.0, "candidates": 0.0, "best_score": 0.0}

    sampled = array[::3, ::3, :3].astype(np.int16)
    red, green, blue = sampled[:, :, 0], sampled[:, :, 1], sampled[:, :, 2]
    yellow = (
        (red >= 145)
        & (green >= 95)
        & (blue <= 165)
        & (red - blue >= 38)
        & (green - blue >= 12)
    )
    yellow_fraction = float(yellow.mean())
    if not yellow.any():
        return False, {
            "yellow_fraction": yellow_fraction,
            "candidates": 0.0,
            "best_score": 0.0,
        }

    height, width = yellow.shape
    luma = red * 0.30 + green * 0.59 + blue * 0.11
    gradient_y, gradient_x = np.gradient(luma.astype(np.float32))
    gradient = np.hypot(gradient_x, gradient_y)
    strong_edge = gradient >= 18.0
    angles = np.linspace(0.0, 2.0 * np.pi, 40, endpoint=False)
    min_radius = max(7, int(min(height, width) * 0.035))
    max_radius = max(min_radius, min(42, int(min(height, width) * 0.20)))
    candidates = 0
    best_score = 0.0
    best_geometry: dict[str, float] = {}
    for radius in range(min_radius, max_radius + 1, 2):
        offsets = {
            (int(round(math.sin(angle) * radius)), int(round(math.cos(angle) * radius)))
            for angle in angles
        }
        votes = np.zeros((height, width), dtype=np.uint8)
        edge_votes = np.zeros((height, width), dtype=np.uint8)
        for offset_y, offset_x in offsets:
            source_y0 = max(0, offset_y)
            source_y1 = min(height, height + offset_y)
            source_x0 = max(0, offset_x)
            source_x1 = min(width, width + offset_x)
            target_y0 = max(0, -offset_y)
            target_y1 = target_y0 + (source_y1 - source_y0)
            target_x0 = max(0, -offset_x)
            target_x1 = target_x0 + (source_x1 - source_x0)
            votes[target_y0:target_y1, target_x0:target_x1] += yellow[
                source_y0:source_y1, source_x0:source_x1
            ]
            edge_votes[target_y0:target_y1, target_x0:target_x1] += strong_edge[
                source_y0:source_y1, source_x0:source_x1
            ]

        color_coverage = votes.astype(np.float32) / max(1, len(offsets))
        edge_coverage = edge_votes.astype(np.float32) / max(1, len(offsets))
        valid = color_coverage * edge_coverage
        valid[:radius, :] = 0.0
        valid[-radius:, :] = 0.0
        valid[:, :radius] = 0.0
        valid[:, -radius:] = 0.0
        valid[int(height * 0.78) :, :] = 0.0
        valid[:, : int(width * 0.15)] = 0.0
        valid[:, int(width * 0.85) :] = 0.0
        center_y, center_x = np.unravel_index(int(np.argmax(valid)), valid.shape)
        color_score = float(color_coverage[center_y, center_x])
        edge_score = float(edge_coverage[center_y, center_x])
        coverage = min(color_score, edge_score)
        if color_score < 0.42 or edge_score < 0.32:
            continue
        # Thin lock UI produces a strong edge specifically on the perimeter.
        # The boss itself may fill the center, so center emptiness is not used.
        score = math.sqrt(max(0.0, color_score * edge_score))
        if score >= 0.76:
            candidates += 1
        if score > best_score:
            best_score = score
            best_geometry = {
                "center_x": center_x / max(1, width),
                "center_y": center_y / max(1, height),
                "radius": float(radius),
                "angle_coverage": color_score,
                "edge_coverage": edge_score,
            }

    return best_score >= 0.78 and candidates >= 1, {
        "yellow_fraction": yellow_fraction,
        "candidates": float(candidates),
        "best_score": best_score,
        **best_geometry,
    }


def l2_target_ring_snapshot(relink: Controller) -> tuple[bool, dict[str, float]]:
    """Capture the central battle area for the L2 target-ring experiment."""
    rect = relink.get_window_rect(silent=True)
    if rect is None:
        raise RuntimeError("无法读取 Chiaki 窗口尺寸")
    left, top, width, height = rect
    crop_left = left + int(width * 0.08)
    crop_top = top + int(height * 0.08)
    crop_width = max(8, int(width * 0.84))
    crop_height = max(8, int(height * 0.78))
    pixels = relink.screenshot(
        region=(crop_left, crop_top, crop_width, crop_height)
    )
    return detect_l2_target_ring_pixels(np.asarray(pixels, dtype=np.uint8))


def detect_l2_target_ring_arcs(pixels: np.ndarray) -> tuple[bool, dict[str, float]]:
    """Detect a thin partial gold target ring instead of a complete yellow circle.

    Lock rings are frequently occluded by enemies, damage numbers and visual
    effects.  This experiment scores the continuity and thickness of gold
    perimeter arcs, accepting a stable partial arc while rejecting broad,
    filled combat effects. It supplies visual evidence to scheme 7, whose
    input decisions also require the conservative SBA inactivity policy.
    """
    array = np.asarray(pixels, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] < 3 or array.shape[0] < 16 or array.shape[1] < 16:
        return False, {"score": 0.0, "arc_sectors": 0.0, "max_arc_run": 0.0}

    # Four-pixel sampling preserves the 40-100 px stream rings while keeping
    # high-frequency observation inexpensive enough for a live battle.
    sampled = array[::4, ::4, :3].astype(np.int16)
    red, green, blue = sampled[:, :, 0], sampled[:, :, 1], sampled[:, :, 2]
    # The lock UI is a saturated orange-gold outline.  Brighter, nearly white
    # flashes and purple/red combat effects are excluded before geometry work.
    gold = (
        (red >= 145)
        & (green >= 78)
        & (blue <= 142)
        & (red >= green + 18)
        & (green >= blue + 16)
        & ((red - blue) >= 72)
    )
    height, width = gold.shape
    if not gold.any():
        return False, {"score": 0.0, "arc_sectors": 0.0, "max_arc_run": 0.0}

    luma = red * 0.30 + green * 0.59 + blue * 0.11
    gradient_y, gradient_x = np.gradient(luma.astype(np.float32))
    edge = np.hypot(gradient_x, gradient_y) >= 20.0
    angles = np.linspace(0.0, 2.0 * np.pi, 36, endpoint=False)
    offsets_by_radius: dict[int, list[tuple[int, int]]] = {}
    min_radius = max(7, int(min(height, width) * 0.035))
    max_radius = max(min_radius, min(46, int(min(height, width) * 0.23)))
    best: dict[str, float] = {
        "score": 0.0,
        "arc_sectors": 0.0,
        "max_arc_run": 0.0,
        "center_x": 0.0,
        "center_y": 0.0,
        "radius": 0.0,
        "thinness": 0.0,
        "edge_sectors": 0.0,
        "interior_gold": 0.0,
    }

    for radius in range(min_radius, max_radius + 1, 2):
        offsets = offsets_by_radius.setdefault(
            radius,
            [(int(round(math.sin(angle) * radius)), int(round(math.cos(angle) * radius))) for angle in angles],
        )
        votes = np.zeros((height, width), dtype=np.uint8)
        edge_votes = np.zeros((height, width), dtype=np.uint8)
        for offset_y, offset_x in offsets:
            source_y0 = max(0, offset_y)
            source_y1 = min(height, height + offset_y)
            source_x0 = max(0, offset_x)
            source_x1 = min(width, width + offset_x)
            target_y0 = max(0, -offset_y)
            target_y1 = target_y0 + (source_y1 - source_y0)
            target_x0 = max(0, -offset_x)
            target_x1 = target_x0 + (source_x1 - source_x0)
            votes[target_y0:target_y1, target_x0:target_x1] += gold[source_y0:source_y1, source_x0:source_x1]
            edge_votes[target_y0:target_y1, target_x0:target_x1] += edge[source_y0:source_y1, source_x0:source_x1]

        # Partial arcs only need enough perimeter support to locate a center.
        support = votes.astype(np.float32) * 0.65 + edge_votes.astype(np.float32) * 0.35
        support[:radius, :] = 0.0
        support[-radius:, :] = 0.0
        support[:, :radius] = 0.0
        support[:, -radius:] = 0.0
        # The left party HUD, top boss HUD and bottom control HUD produce
        # stable orange arcs during skill cut-ins. A real target ring stays in
        # the central combat field for this experiment.
        support[: int(height * 0.15), :] = 0.0
        support[int(height * 0.85) :, :] = 0.0
        support[:, : int(width * 0.20)] = 0.0
        support[:, int(width * 0.82) :] = 0.0
        center_y, center_x = np.unravel_index(int(np.argmax(support)), support.shape)
        if support[center_y, center_x] < 8.0:
            continue

        sector_hits: list[bool] = []
        sector_edges: list[bool] = []
        radial_hits: list[int] = []
        for offset_y, offset_x in offsets:
            hits = 0
            has_edge = False
            for radial_adjustment in range(-3, 4):
                scale = (radius + radial_adjustment) / radius
                y = center_y + int(round(offset_y * scale))
                x = center_x + int(round(offset_x * scale))
                if 0 <= y < height and 0 <= x < width:
                    hits += int(gold[y, x])
                    has_edge = has_edge or bool(edge[y, x])
            sector_hits.append(hits >= 1)
            sector_edges.append(has_edge)
            radial_hits.append(hits)

        arc_sectors = sum(sector_hits)
        edge_sectors = sum(sector_edges)
        doubled = sector_hits + sector_hits
        max_arc_run = 0
        current_run = 0
        for hit in doubled:
            current_run = current_run + 1 if hit else 0
            max_arc_run = max(max_arc_run, current_run)
        max_arc_run = min(max_arc_run, len(sector_hits))
        hit_thickness = [hit for hit, present in zip(radial_hits, sector_hits) if present]
        average_thickness = float(np.mean(hit_thickness)) if hit_thickness else 7.0
        # A thin outlined ring normally occupies one to three sampled radial
        # pixels. Broad flashes can create circles too, but occupy most of the
        # seven-pixel radial band and are deliberately penalized.
        thinness = max(0.0, min(1.0, (5.2 - average_thickness) / 3.2))
        y0 = max(0, int(center_y - radius * 0.58))
        y1 = min(height, int(center_y + radius * 0.58) + 1)
        x0 = max(0, int(center_x - radius * 0.58))
        x1 = min(width, int(center_x + radius * 0.58) + 1)
        local_gold = gold[y0:y1, x0:x1]
        local_y, local_x = np.ogrid[y0:y1, x0:x1]
        interior = (
            (local_y - center_y) ** 2 + (local_x - center_x) ** 2
            <= (radius * 0.55) ** 2
        )
        interior_gold = float(local_gold[interior].mean()) if interior.any() else 0.0
        arc_fraction = arc_sectors / len(sector_hits)
        edge_fraction = edge_sectors / len(sector_edges)
        continuity = min(1.0, max_arc_run / 13.0)
        score = (
            arc_fraction * 0.42
            + continuity * 0.30
            + edge_fraction * 0.18
            + thinness * 0.10
        )
        if score > best["score"]:
            best = {
                "score": score,
                "arc_sectors": float(arc_sectors),
                "max_arc_run": float(max_arc_run),
                "center_x": center_x / max(1, width),
                "center_y": center_y / max(1, height),
                "radius": float(radius),
                "thinness": thinness,
                "edge_sectors": float(edge_sectors),
                "interior_gold": interior_gold,
            }

    detected = (
        best["arc_sectors"] >= 7
        and best["max_arc_run"] >= 4
        and best["thinness"] >= 0.20
        and best["interior_gold"] <= 0.18
        and best["radius"] <= 30.0
        and best["score"] >= 0.38
    )
    return detected, best


def l2_target_ring_arc_snapshot(relink: Controller) -> tuple[bool, dict[str, float]]:
    """Capture the battle area for scheme 7's partial-arc ring check."""
    rect = relink.get_window_rect(silent=True)
    if rect is None:
        raise RuntimeError("无法读取 Chiaki 窗口尺寸")
    left, top, width, height = rect
    pixels = relink.screenshot(
        region=(
            left + int(width * 0.08),
            top + int(height * 0.08),
            max(8, int(width * 0.84)),
            max(8, int(height * 0.78)),
        )
    )
    return detect_l2_target_ring_arcs(np.asarray(pixels, dtype=np.uint8))


def detect_boss_blue_bar_pixels(pixels: np.ndarray) -> tuple[bool, dict[str, float]]:
    """Detect a moving blue/purple target-bar candidate at multiple scales."""
    array = np.asarray(pixels, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] < 3 or array.shape[0] < 8 or array.shape[1] < 8:
        return False, {"blue_fraction": 0.0, "longest_run": 0.0, "best_score": 0.0}

    # Keep more spatial detail because the target bar shrinks with distance.
    sampled = array[::2, ::2, :3].astype(np.int16)
    red, green, blue = sampled[:, :, 0], sampled[:, :, 1], sampled[:, :, 2]
    blue_mask = (
        (blue >= 72)
        & (blue - red >= 10)
        & (blue - green >= 0)
        & ((blue + green) >= 145)
    )
    blue_fraction = float(blue_mask.mean())
    height, width = blue_mask.shape
    visited = np.zeros_like(blue_mask, dtype=bool)
    candidates: list[dict[str, float]] = []
    for start_y, start_x in zip(*np.nonzero(blue_mask)):
        if visited[start_y, start_x]:
            continue
        visited[start_y, start_x] = True
        stack = [(int(start_y), int(start_x))]
        points: list[tuple[int, int]] = []
        while stack:
            y, x = stack.pop()
            points.append((y, x))
            for next_y in range(y - 1, y + 2):
                for next_x in range(x - 1, x + 2):
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and blue_mask[next_y, next_x]
                        and not visited[next_y, next_x]
                    ):
                        visited[next_y, next_x] = True
                        stack.append((next_y, next_x))
        if len(points) < 5:
            continue
        ys = np.fromiter((point[0] for point in points), dtype=np.int16)
        xs = np.fromiter((point[1] for point in points), dtype=np.int16)
        box_width = int(xs.max() - xs.min() + 1)
        box_height = int(ys.max() - ys.min() + 1)
        aspect = box_width / max(1, box_height)
        density = len(points) / max(1, box_width * box_height)
        # The projected bar can be short, but it remains a thin horizontal
        # object.  This rejects isolated blue particles and large effects.
        if not (box_width >= 6 and box_height <= max(24, int(height * 0.12)) and aspect >= 2.2):
            continue
        if density < 0.06 or box_width > width * 0.80 or box_height > height * 0.16:
            continue
        center_x = float((xs.min() + xs.max()) * 0.5 / max(1, width))
        center_y = float((ys.min() + ys.max()) * 0.5 / max(1, height))
        point_red = red[ys, xs]
        point_green = green[ys, xs]
        point_blue = blue[ys, xs]
        blue_chroma = float(
            np.mean(point_blue - (point_red + point_green) * 0.5)
        )
        geometry = min(1.0, aspect / 8.0) * min(1.0, density / 0.35)
        color_score = min(1.0, max(0.0, blue_chroma) / 34.0)
        if center_y < 0.06 or center_y > 0.76 or center_x > 0.90:
            continue
        candidates.append(
            {
                "width": float(box_width),
                "height": float(box_height),
                "aspect": aspect,
                "density": density,
                "center_x": center_x,
                "center_y": center_y,
                "blue_chroma": blue_chroma,
                "score": geometry * color_score,
            }
        )

    best = max(candidates, key=lambda item: item["score"], default=None)
    present = best is not None and best["score"] >= 0.12
    return present, {
        "blue_fraction": blue_fraction,
        "candidates": float(len(candidates)),
        "longest_run": 0.0 if best is None else best["width"],
        "run_fraction": 0.0 if best is None else best["width"] / max(1, width),
        "thickness": 0.0 if best is None else best["height"],
        "aspect": 0.0 if best is None else best["aspect"],
        "center_x": 0.0 if best is None else best["center_x"],
        "center_y": 0.0 if best is None else best["center_y"],
        "blue_chroma": 0.0 if best is None else best["blue_chroma"],
        "best_score": 0.0 if best is None else best["score"],
    }


def l2_boss_ring_snapshot(
    relink: Controller,
) -> tuple[bool, dict[str, float], bool, dict[str, float]]:
    """Capture one shared battle frame for the boss-bar/ring experiment."""
    rect = relink.get_window_rect(silent=True)
    if rect is None:
        raise RuntimeError("无法读取 Chiaki 窗口尺寸")
    left, top, width, height = rect
    crop_left = left + int(width * 0.08)
    crop_top = top + int(height * 0.08)
    crop_width = max(8, int(width * 0.84))
    crop_height = max(8, int(height * 0.78))
    pixels = np.asarray(
        relink.screenshot(region=(crop_left, crop_top, crop_width, crop_height)),
        dtype=np.uint8,
    )
    ring_present, ring_details = detect_l2_target_ring_pixels(pixels)
    boss_present, boss_details = detect_boss_blue_bar_pixels(pixels)
    return ring_present, ring_details, boss_present, boss_details


def l2_boss_ring_focus_watchdog(
    relink: Controller, battle_is_active: Callable[[], bool]
) -> None:
    """Experimental scheme combining boss bar, lock ring and SBA telemetry.

    It only probes L2 when a boss bar is visible and the lock ring is absent.
    Refocusing movement is intentionally left to a later, data-backed change.
    """
    next_probe = 0.0
    previous_sba: float | None = None
    previous_sba_at: float | None = None
    boss_missing_groups = 0
    boss_stable_present = False

    while relink.running:
        if relink.paused or not battle_is_active():
            next_probe = 0.0
            previous_sba = None
            previous_sba_at = None
            sleep(0.5)
            continue
        now = time()
        if now < next_probe:
            sleep(min(0.2, next_probe - now))
            continue
        next_probe = now + L2_BOSS_RING_PROBE_INTERVAL_SECONDS

        try:
            ring_votes = 0
            boss_votes = 0
            observations: list[tuple[dict[str, float], dict[str, float]]] = []
            for sample_index in range(L2_BOSS_RING_CONFIRM_SAMPLES):
                if not relink.running or relink.paused or not battle_is_active():
                    break
                ring, ring_details, boss, boss_details = l2_boss_ring_snapshot(relink)
                ring_votes += int(ring)
                boss_votes += int(boss)
                observations.append((ring_details, boss_details))
                if sample_index + 1 < L2_BOSS_RING_CONFIRM_SAMPLES:
                    sleep(L2_BOSS_RING_SAMPLE_INTERVAL_SECONDS)

            ring_present = ring_votes >= L2_BOSS_RING_PRESENT_MIN_SAMPLES
            boss_current_present = boss_votes >= L2_BOSS_RING_PRESENT_MIN_SAMPLES
            # The bar is a changing fill state, not a static rectangle.  Keep
            # a short-lived track through fill animation/combat effects and
            # only clear it after several complete probe groups miss it.
            if boss_current_present:
                boss_missing_groups = 0
                boss_stable_present = True
            elif boss_stable_present:
                boss_missing_groups += 1
                if boss_missing_groups >= L2_BOSS_BAR_MISSING_GROUPS:
                    boss_stable_present = False
            boss_present = boss_stable_present
            sba = remote_sba_fill_fraction(relink)
            delta = None if previous_sba is None else sba - previous_sba
            interval = None if previous_sba_at is None else now - previous_sba_at
            rate = None if delta is None or not interval else delta / interval
            ring_detail = max(observations, key=lambda item: item[0].get("best_score", 0.0), default=({}, {}))[0]
            boss_detail = max(observations, key=lambda item: item[1].get("best_score", 0.0), default=({}, {}))[1]
            log.info(
                "BOSS锁定实验：蓝条当前=%s，蓝条稳定=%s(投票%d/%d，占比%.3f，最长横向%.0f，评分%.3f)，"
                "锁定环=%s(投票%d/%d，候选%.0f，评分%.3f)，奥义=%.1f%%，"
                "奥义增量=%s，采样间隔=%.1fs，增长速度=%s%%/s",
                "有" if boss_current_present else "无",
                "有" if boss_present else "无",
                boss_votes, len(observations), boss_detail.get("blue_fraction", 0.0),
                boss_detail.get("longest_run", 0.0), boss_detail.get("best_score", 0.0),
                "有" if ring_present else "无",
                ring_votes, len(observations), ring_detail.get("candidates", 0.0),
                ring_detail.get("best_score", 0.0), sba * 100.0,
                "未知" if delta is None else f"{delta * 100.0:+.1f}%",
                0.0 if interval is None else interval,
                "未知" if rate is None else f"{rate * 100.0:+.3f}",
            )
            previous_sba = sba
            previous_sba_at = now

            if ring_present:
                continue
            with AUTOMATION_INPUT_LOCK:
                if relink.running and not relink.paused and battle_is_active():
                    relink.press(L2_KEY)
                    log.info(
                        "BOSS锁定实验：未检测到锁定环（蓝条=%s），发送一次 L2",
                        "有" if boss_present else "无",
                    )
        except Exception:
            log.debug("BOSS锁定实验采样失败（已忽略）", exc_info=True)


def l2_ring_focus_watchdog(
    relink: Controller, battle_is_active: Callable[[], bool]
) -> None:
    """Probe L2 only when no ring is visible, then combine with SBA growth."""
    missing_confirmations = 0
    search_left = True
    next_probe = 0.0
    previous_sba: float | None = None
    log_sba_previous: float | None = None

    while relink.running:
        if relink.paused or not battle_is_active():
            missing_confirmations = 0
            next_probe = 0.0
            log_sba_previous = None
            sleep(0.5)
            continue

        now = time()
        if now < next_probe:
            sleep(min(0.2, next_probe - now))
            continue
        next_probe = now + L2_RING_PROBE_INTERVAL_SECONDS

        try:
            present_votes = 0
            observations: list[dict[str, float]] = []
            for sample_index in range(L2_RING_CONFIRM_SAMPLES):
                if not relink.running or relink.paused or not battle_is_active():
                    break
                present, details = l2_target_ring_snapshot(relink)
                observations.append(details)
                present_votes += int(present)
                if sample_index + 1 < L2_RING_CONFIRM_SAMPLES:
                    sleep(L2_RING_SAMPLE_INTERVAL_SECONDS)

            ring_present = present_votes >= L2_RING_PRESENT_MIN_SAMPLES
            # Step 1 of the experiment: collect SBA growth alongside the ring
            # metrics, without using this extra observation to change the
            # existing scheme-3 decision path.
            log_sba_fill = remote_sba_fill_fraction(relink)
            log_sba_grew = (
                log_sba_previous is not None
                and log_sba_fill > log_sba_previous + L2_SBA_GROWTH_EPSILON
            )
            log.info(
                "L键目标环实验：检测到=%s，投票=%d/%d，候选=%d，最佳评分=%.3f，黄色占比=%.3f，奥义=%.1f%%，奥义增长=%s",
                "是" if ring_present else "否",
                present_votes,
                len(observations),
                max((int(item["candidates"]) for item in observations), default=0),
                max((item["best_score"] for item in observations), default=0.0),
                max((item["yellow_fraction"] for item in observations), default=0.0),
                log_sba_fill * 100.0,
                "是" if log_sba_grew else "否",
            )
            log_sba_previous = log_sba_fill
            if ring_present:
                missing_confirmations = 0
                previous_sba = None
                continue

            with AUTOMATION_INPUT_LOCK:
                if not relink.running or relink.paused or not battle_is_active():
                    continue
                relink.press(L2_KEY)
            sleep(L2_RING_SETTLE_SECONDS)
            sba_fill = remote_sba_fill_fraction(relink)
            sba_grew = (
                previous_sba is not None
                and sba_fill > previous_sba + L2_SBA_GROWTH_EPSILON
            )
            log.info(
                "L键目标环实验：无显式光圈后发送 L2，奥义 %.1f%% -> %.1f%%，是否增长=%s",
                (previous_sba or 0.0) * 100.0,
                sba_fill * 100.0,
                "是" if sba_grew else "否",
            )
            previous_sba = sba_fill
            if sba_grew:
                missing_confirmations = 0
                continue

            missing_confirmations += 1
            if missing_confirmations < L2_RING_MISSING_CONFIRMATIONS:
                continue
            missing_confirmations = 0
            log.warning("L键目标环实验连续确认无目标环，开始恢复索敌")
            turn_key = LEFT_STICK_LEFT_KEY if search_left else LEFT_STICK_RIGHT_KEY
            camera_key = RIGHT_STICK_LEFT_KEY if search_left else RIGHT_STICK_RIGHT_KEY
            if recover_lost_target(relink, battle_is_active, turn_key, camera_key):
                search_left = not search_left
        except Exception:
            log.debug("L键目标环实验检测失败（已忽略）", exc_info=True)


def l2_sba_focus_watchdog(
    relink: Controller, battle_is_active: Callable[[], bool]
) -> None:
    """Experimental mode: press L2 every few seconds until SBA starts rising."""
    next_probe = 0.0
    previous_sba: float | None = None
    probing = True

    while relink.running:
        if relink.paused or not battle_is_active():
            next_probe = 0.0
            previous_sba = None
            probing = True
            sleep(0.5)
            continue
        if not probing:
            sleep(0.5)
            continue

        now = time()
        if now < next_probe:
            sleep(min(0.2, next_probe - now))
            continue
        next_probe = now + L2_SBA_PROBE_INTERVAL_SECONDS

        try:
            with AUTOMATION_INPUT_LOCK:
                if not relink.running or relink.paused or not battle_is_active():
                    continue
                relink.press(L2_KEY)
            sleep(L2_RING_SETTLE_SECONDS)
            sba_fill = remote_sba_fill_fraction(relink)
            grew = (
                previous_sba is not None
                and sba_fill > previous_sba + L2_SBA_GROWTH_EPSILON
            )
            log.info(
                "L键持续探测实验：发送 L2，奥义 %.1f%% -> %.1f%%，是否增长=%s",
                (previous_sba or 0.0) * 100.0,
                sba_fill * 100.0,
                "是" if grew else "否",
            )
            if grew:
                probing = False
                log.warning("L键持续探测实验检测到奥义条增长，停止后续 L2 探测")
            previous_sba = sba_fill
        except Exception:
            log.debug("L键持续探测实验检测失败（已忽略）", exc_info=True)


def sba_ring_guarded_focus_watchdog(
    relink: Controller, battle_is_active: Callable[[], bool]
) -> None:
    """Use SBA activity as the primary guard for low-frequency target recovery.

    The target ring is useful positive evidence but is frequently occluded by
    boss UI and combat effects. A missing ring therefore only becomes actionable
    after SBA has also stayed inactive for several samples.
    """
    next_probe = 0.0
    next_l2_allowed = 0.0
    previous_sba: float | None = None
    last_sba_growth_at: float | None = None
    release_protection_until = 0.0
    missing_groups = 0
    l2_attempts = 0
    search_left = True

    while relink.running:
        if relink.paused or not battle_is_active():
            next_probe = 0.0
            next_l2_allowed = 0.0
            previous_sba = None
            last_sba_growth_at = None
            release_protection_until = 0.0
            missing_groups = 0
            l2_attempts = 0
            sleep(0.5)
            continue

        now = time()
        if now < next_probe:
            sleep(min(0.2, next_probe - now))
            continue
        next_probe = now + L2_SBA_RING_GUARDED_PROBE_INTERVAL_SECONDS

        try:
            present_votes = 0
            observations: list[dict[str, float]] = []
            for sample_index in range(L2_RING_CONFIRM_SAMPLES):
                if not relink.running or relink.paused or not battle_is_active():
                    break
                present, details = l2_target_ring_snapshot(relink)
                observations.append(details)
                present_votes += int(present)
                if sample_index + 1 < L2_RING_CONFIRM_SAMPLES:
                    sleep(L2_RING_SAMPLE_INTERVAL_SECONDS)

            if not observations:
                continue
            # Result detection can flip battle_active while a three-frame
            # sample group is in progress. Discard the incomplete group before
            # reading SBA or emitting a battle-only diagnostic.
            if not relink.running or relink.paused or not battle_is_active():
                continue

            ring_present = present_votes >= L2_RING_PRESENT_MIN_SAMPLES
            sba_fill = remote_sba_fill_fraction(relink)
            delta = None if previous_sba is None else sba_fill - previous_sba
            grew = delta is not None and delta > L2_SBA_GROWTH_EPSILON
            released = delta is not None and delta <= -0.15
            if grew:
                last_sba_growth_at = now
            if released:
                release_protection_until = now + L2_SBA_RING_GUARDED_RELEASE_PROTECTION_SECONDS

            recent_growth = (
                last_sba_growth_at is not None
                and now - last_sba_growth_at <= L2_SBA_RING_GUARDED_RECENT_GROWTH_SECONDS
            )
            protected = recent_growth or now < release_protection_until
            if ring_present or protected:
                missing_groups = 0
                l2_attempts = 0
            else:
                missing_groups += 1

            log.info(
                "方案6索敌：锁定环=%s(%d/%d)，奥义=%.1f%%，增量=%s，增长保护=%s，释放保护=%s，缺失组=%d，L2尝试=%d",
                "有" if ring_present else "无",
                present_votes,
                len(observations),
                sba_fill * 100.0,
                "未知" if delta is None else f"{delta * 100.0:+.1f}%",
                "是" if recent_growth else "否",
                "是" if now < release_protection_until else "否",
                missing_groups,
                l2_attempts,
            )
            previous_sba = sba_fill

            if ring_present or protected:
                continue
            if missing_groups < L2_SBA_RING_GUARDED_MISSING_GROUPS:
                continue
            if now < next_l2_allowed:
                continue

            with AUTOMATION_INPUT_LOCK:
                if not relink.running or relink.paused or not battle_is_active():
                    continue
                relink.press(L2_KEY)
            next_l2_allowed = now + L2_SBA_RING_GUARDED_L2_COOLDOWN_SECONDS
            missing_groups = 0
            l2_attempts += 1
            log.warning(
                "方案6索敌：锁定环持续缺失且奥义无增长，发送 L2（第%d次，冷却%.0f秒）",
                l2_attempts,
                L2_SBA_RING_GUARDED_L2_COOLDOWN_SECONDS,
            )

            if l2_attempts < L2_SBA_RING_GUARDED_RECOVER_ATTEMPTS:
                continue
            l2_attempts = 0
            turn_key = LEFT_STICK_LEFT_KEY if search_left else LEFT_STICK_RIGHT_KEY
            camera_key = RIGHT_STICK_LEFT_KEY if search_left else RIGHT_STICK_RIGHT_KEY
            log.warning("方案6索敌：两次低频 L2 后仍无恢复，开始折返索敌")
            if recover_lost_target(relink, battle_is_active, turn_key, camera_key):
                search_left = not search_left
        except Exception:
            log.debug("方案6索敌采样失败（已忽略）", exc_info=True)


def ring_arc_experiment_focus_watchdog(
    relink: Controller, battle_is_active: Callable[[], bool]
) -> None:
    """Use scheme-6 safeguards with scheme-7 partial lock-ring arc evidence."""
    next_probe = 0.0
    next_l2_allowed = 0.0
    previous_sba: float | None = None
    last_sba_growth_at: float | None = None
    release_protection_until = 0.0
    missing_groups = 0
    l2_attempts = 0
    search_left = True

    while relink.running:
        if relink.paused or not battle_is_active():
            next_probe = 0.0
            next_l2_allowed = 0.0
            previous_sba = None
            last_sba_growth_at = None
            release_protection_until = 0.0
            missing_groups = 0
            l2_attempts = 0
            sleep(0.5)
            continue

        now = time()
        if now < next_probe:
            sleep(min(0.2, next_probe - now))
            continue
        next_probe = now + L2_SBA_RING_GUARDED_PROBE_INTERVAL_SECONDS

        try:
            present_votes = 0
            observations: list[dict[str, float]] = []
            for sample_index in range(L2_RING_CONFIRM_SAMPLES):
                if not relink.running or relink.paused or not battle_is_active():
                    break
                candidate, details = l2_target_ring_arc_snapshot(relink)
                observations.append(details)
                present_votes += int(candidate)
                if sample_index + 1 < L2_RING_CONFIRM_SAMPLES:
                    sleep(L2_RING_SAMPLE_INTERVAL_SECONDS)

            if not observations:
                continue
            # The result state can begin while this multi-frame sample group
            # is still being collected. Do not read SBA or emit a scheme-7
            # diagnostic once battle ownership has been released.
            if not relink.running or relink.paused or not battle_is_active():
                continue

            ring_present = present_votes >= L2_RING_PRESENT_MIN_SAMPLES
            ring_detail = max(observations, key=lambda item: item.get("score", 0.0))
            sba_fill = remote_sba_fill_fraction(relink)
            delta = None if previous_sba is None else sba_fill - previous_sba
            grew = delta is not None and delta > L2_SBA_GROWTH_EPSILON
            released = delta is not None and delta <= -0.15
            if grew:
                last_sba_growth_at = now
            if released:
                release_protection_until = now + L2_SBA_RING_GUARDED_RELEASE_PROTECTION_SECONDS
            recent_growth = (
                last_sba_growth_at is not None
                and now - last_sba_growth_at <= L2_SBA_RING_GUARDED_RECENT_GROWTH_SECONDS
            )
            protected = recent_growth or now < release_protection_until
            if ring_present or protected:
                missing_groups = 0
                l2_attempts = 0
            else:
                missing_groups += 1

            log.info(
                "方案7索敌：圆弧环=%s(%d/%d，评分=%.3f，弧段=%d，连续弧=%d，细线=%.2f，内部金色=%.3f)，奥义=%.1f%%，增量=%s，增长保护=%s，释放保护=%s，缺失组=%d，L2尝试=%d",
                "有" if ring_present else "无",
                present_votes,
                len(observations),
                ring_detail.get("score", 0.0),
                int(ring_detail.get("arc_sectors", 0.0)),
                int(ring_detail.get("max_arc_run", 0.0)),
                ring_detail.get("thinness", 0.0),
                ring_detail.get("interior_gold", 0.0),
                sba_fill * 100.0,
                "未知" if delta is None else f"{delta * 100.0:+.1f}%",
                "是" if recent_growth else "否",
                "是" if now < release_protection_until else "否",
                missing_groups,
                l2_attempts,
            )
            previous_sba = sba_fill

            if ring_present or protected:
                continue
            if missing_groups < L2_SBA_RING_GUARDED_MISSING_GROUPS:
                continue
            if now < next_l2_allowed:
                continue

            with AUTOMATION_INPUT_LOCK:
                if not relink.running or relink.paused or not battle_is_active():
                    continue
                relink.press(L2_KEY)
            next_l2_allowed = now + L2_SBA_RING_GUARDED_L2_COOLDOWN_SECONDS
            missing_groups = 0
            l2_attempts += 1
            log.warning(
                "方案7索敌：圆弧环持续缺失且奥义无增长，发送 L2（第%d次，冷却%.0f秒）",
                l2_attempts,
                L2_SBA_RING_GUARDED_L2_COOLDOWN_SECONDS,
            )
            if l2_attempts < L2_SBA_RING_GUARDED_RECOVER_ATTEMPTS:
                continue
            l2_attempts = 0
            turn_key = LEFT_STICK_LEFT_KEY if search_left else LEFT_STICK_RIGHT_KEY
            camera_key = RIGHT_STICK_LEFT_KEY if search_left else RIGHT_STICK_RIGHT_KEY
            log.warning("方案7索敌：两次低频 L2 后仍无恢复，开始折返索敌")
            if recover_lost_target(relink, battle_is_active, turn_key, camera_key):
                search_left = not search_left
        except Exception:
            log.debug("方案7索敌采样失败（已忽略）", exc_info=True)


def focus_watchdog(relink: Controller, battle_is_active: Callable[[], bool]) -> None:
    """Recover target lock when trigger skills stay bright for long enough."""
    log.info("当前索敌方案：%s", REFOCUS_MODE_LABELS.get(REFOCUS_MODE, REFOCUS_MODE))
    if REFOCUS_MODE == REFOCUS_MODE_RANGED:
        remote_focus_watchdog(relink, battle_is_active)
        return
    if REFOCUS_MODE == REFOCUS_MODE_BOSS_RING:
        l2_boss_ring_focus_watchdog(relink, battle_is_active)
        return
    if REFOCUS_MODE == REFOCUS_MODE_L2_RING:
        l2_ring_focus_watchdog(relink, battle_is_active)
        return
    if REFOCUS_MODE == REFOCUS_MODE_L2_SBA:
        l2_sba_focus_watchdog(relink, battle_is_active)
        return
    if REFOCUS_MODE == REFOCUS_MODE_SBA_RING_GUARDED:
        sba_ring_guarded_focus_watchdog(relink, battle_is_active)
        return
    if REFOCUS_MODE == REFOCUS_MODE_RING_ARC_EXPERIMENT:
        ring_arc_experiment_focus_watchdog(relink, battle_is_active)
        return
    bright_since: float | None = None
    dim_since: float | None = None
    search_left = True

    while relink.running:
        if relink.paused:
            bright_since = None
            dim_since = None
            sleep(0.2)
            continue
        if not battle_is_active():
            bright_since = None
            dim_since = None
            sleep(0.5)
            continue

        try:
            trigger_bright, _ = skill_trigger_slots_bright(relink)
            now = time()

            if trigger_bright:
                dim_since = None
                if bright_since is None:
                    bright_since = now
            elif bright_since is not None:
                if dim_since is None:
                    dim_since = now
                elif now - dim_since >= SKILL_TRIGGER_DIM_GRACE_SECONDS:
                    bright_since = None
                    dim_since = None

            if bright_since is not None and now - bright_since >= REFOCUS_SECONDS:
                log.warning(
                    "上方或右侧技能持续高亮 %.0f 秒，开始恢复索敌",
                    REFOCUS_SECONDS,
                )
                turn_key = (
                    LEFT_STICK_LEFT_KEY
                    if search_left
                    else LEFT_STICK_RIGHT_KEY
                )
                camera_key = (
                    RIGHT_STICK_LEFT_KEY
                    if search_left
                    else RIGHT_STICK_RIGHT_KEY
                )
                if recover_lost_target(
                    relink,
                    battle_is_active,
                    turn_key,
                    camera_key,
                ):
                    search_left = not search_left
                bright_since = now
        except Exception:
            log.debug("技能亮度监控异常（已忽略）", exc_info=True)
            bright_since = None
            dim_since = None

        sleep(
            SKILL_MONITOR_ACTIVE_POLL_SECONDS
            if bright_since is not None
            else SKILL_MONITOR_IDLE_POLL_SECONDS
        )


def _text_matches_marker(text: str, language: str, semantic: str) -> bool:
    if language == "ja" and semantic == "challenge_confirmation":
        if any(marker in text for marker in UI_MARKERS[language][semantic]):
            return True
        return any(
            all(choice in text for choice in choices)
            for choices in JAPANESE_CHALLENGE_CONFIRMATION_OPTION_SETS
        )
    return any(marker in text for marker in UI_MARKERS[language][semantic])


def _match_marker_language(
    relink: Controller,
    texts: dict[str, str],
    semantic: str,
) -> str | None:
    for language in relink.ui_language_candidates():
        if _text_matches_marker(texts.get(language, ""), language, semantic):
            relink.confirm_ui_language(language, semantic)
            return language
    return None


def read_region_texts(relink: Controller, region_key: str) -> dict[str, str]:
    """Recognize one fixed crop in each currently eligible UI language.

    The result continuation prompt is too close to the countdown for the
    recognition-only path: it can merge both controls into one string such as
    ``：08时8继线``.  Use the detector for that crop so the prompt remains a
    separate OCR item while the other stable markers keep their fast path.
    """
    global _CAPTURE_UNAVAILABLE_WARNED
    if region_key == "结算":
        return read_settlement_center_texts(relink)
    if region_key == "继续":
        return read_region_detected_texts(
            relink,
            region_key,
            confidence=0.45,
        )
    try:
        crop = relink.screenshot_text(region_key)
        if region_key in {"再次", "撤销"}:
            # The Japanese retry prompt is only about 20 px high in a normal
            # 16:9 stream. Upscaling before recognition prevents 挑 from being
            # confused with 規 while retaining the same narrow crop and marker
            # semantics for the Chinese client.
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            crop = crop.resize((crop.width * 2, crop.height * 2), resampling)
        texts = {
            language: relink.recognize_line(
                crop,
                confidence=(
                    0.58 if language == "ja" else 0.65
                ),
                language=language,
            )
            for language in relink.ui_language_candidates()
        }
    except RuntimeError as exc:
        if not _CAPTURE_UNAVAILABLE_WARNED:
            log.warning("Chiaki 画面/OCR 暂时不可用，保留当前阶段并等待窗口恢复或重建：%s", exc)
            _CAPTURE_UNAVAILABLE_WARNED = True
        return {}
    except Exception:
        if not _CAPTURE_UNAVAILABLE_WARNED:
            log.warning(
                "Chiaki 画面/OCR 暂时不可用，保留当前阶段并等待窗口恢复或重建",
                exc_info=True,
            )
            _CAPTURE_UNAVAILABLE_WARNED = True
        return {}

    if _CAPTURE_UNAVAILABLE_WARNED:
        log.info("Chiaki 画面已恢复，继续当前自动化阶段")
        _CAPTURE_UNAVAILABLE_WARNED = False
    return texts


def read_region_detected_texts(
    relink: Controller,
    region_key: str,
    confidence: float = 0.45,
) -> dict[str, str]:
    """Detect and recognize every line in a broad, fixed screen region."""
    global _CAPTURE_UNAVAILABLE_WARNED
    try:
        crop = relink.screenshot_text(region_key)
        texts: dict[str, str] = {}
        for language in relink.ui_language_candidates():
            result = relink.ocr(
                crop,
                confidence=confidence,
                language=language,
            )
            texts[language] = "".join(
                str(item.get("text", "")) for item in result
            ) if isinstance(result, list) else ""
    except RuntimeError as exc:
        if not _CAPTURE_UNAVAILABLE_WARNED:
            log.warning("Chiaki 画面/OCR 暂时不可用，保留当前阶段并等待窗口恢复或重建：%s", exc)
            _CAPTURE_UNAVAILABLE_WARNED = True
        return {}
    except Exception:
        if not _CAPTURE_UNAVAILABLE_WARNED:
            log.warning(
                "Chiaki 画面/OCR 暂时不可用，保留当前阶段并等待窗口恢复或重建",
                exc_info=True,
            )
            _CAPTURE_UNAVAILABLE_WARNED = True
        return {}

    if _CAPTURE_UNAVAILABLE_WARNED:
        log.info("Chiaki 画面已恢复，继续当前自动化阶段")
        _CAPTURE_UNAVAILABLE_WARNED = False
    return texts


def region_has_marker(relink: Controller, region_key: str, semantic: str) -> bool:
    return _match_marker_language(
        relink,
        read_region_texts(relink, region_key),
        semantic,
    ) is not None


def battle_hud_layout_score(frame: Image.Image) -> float:
    """Return the fraction of battle-panel-blue pixels in the skill HUD area."""
    pixels = np.asarray(frame.convert("RGB"), dtype=np.float32)
    if pixels.ndim != 3 or pixels.shape[0] < 20 or pixels.shape[1] < 20:
        return 0.0
    red = pixels[:, :, 0]
    green = pixels[:, :, 1]
    blue = pixels[:, :, 2]
    blue_chroma = blue - (red + green) * 0.5
    mask = (
        (blue >= BATTLE_HUD_BLUE_MIN_VALUE)
        & (blue_chroma >= BATTLE_HUD_BLUE_MIN_CHROMA)
    )
    return float(mask.mean())


def log_recovery_debug(
    relink: Controller, stage: str, *, hud_score: float | None = None,
    texts: dict[str, str] | None = None,
) -> None:
    """Write bounded recovery evidence when a support run enables DEBUG."""
    if not DEBUG_MODE:
        return
    try:
        rect = relink.get_window_rect(silent=True)
    except Exception:
        rect = None
    payload: dict[str, object] = {
        "stage": stage,
        "window_rect": rect,
        "hud_region": BATTLE_HUD_LAYOUT_REGION,
        "hud_score": None if hud_score is None else round(hud_score, 5),
    }
    try:
        geometry = relink.recognition_geometry_state()
        payload["recognition_geometry"] = geometry
        metadata = getattr(relink, "_last_recognition_metadata", None)
        if metadata:
            payload["normalized_frame"] = metadata
    except Exception:
        pass
    if texts:
        payload["ocr"] = {
            language: str(value)[:500]
            for language, value in texts.items()
            if value
        }
    encoded = json.dumps(payload, ensure_ascii=False)
    log.debug("DEBUG恢复诊断 %s", encoded)
    if DEBUG_DIAGNOSTIC_PATH is not None:
        try:
            with DEBUG_DIAGNOSTIC_PATH.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {"timestamp": datetime.now().isoformat(timespec="milliseconds"), **payload},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            log.debug("DEBUG诊断文件写入失败", exc_info=True)


def battle_hud_visual_candidate(relink: Controller) -> tuple[bool, float]:
    """Cheap pixel-only probe used before recovery OCR.

    The blue action-skill panel is present only in battle HUD frames. This
    probe avoids running OCR on every recovery classification attempt; a
    positive result is still confirmed by the existing two-frame OCR check.
    """
    rect = relink.get_window_rect(silent=True)
    if rect is None:
        return False, 0.0
    left, top, width, height = rect
    x0, y0, x1, y1 = BATTLE_HUD_LAYOUT_REGION
    frame = relink.screenshot(
        region=(
            left + int(width * x0),
            top + int(height * y0),
            max(1, int(width * (x1 - x0))),
            max(1, int(height * (y1 - y0))),
        )
    )
    score = battle_hud_layout_score(frame)
    log_recovery_debug(relink, "battle_hud_visual", hud_score=score)
    return score >= BATTLE_HUD_BLUE_MIN_FRACTION, score


def battle_hud_candidate(relink: Controller) -> bool:
    """Require battle text, timer text, and battle-only skill-panel geometry."""
    texts = read_region_texts(relink, "跳跃")
    language = next(
        (
            candidate
            for candidate in relink.ui_language_candidates()
            if _text_matches_marker(
                texts.get(candidate, ""), candidate, "battle_hud"
            )
        ),
        None,
    )
    if language is None:
        log_recovery_debug(relink, "battle_hud_text_miss", texts=texts)
        return False

    rect = relink.get_window_rect(silent=True)
    if rect is None:
        return False
    left, top, width, height = rect
    x0, y0, x1, y1 = BATTLE_HUD_LAYOUT_REGION
    frame = relink.screenshot(
        region=(
            left + int(width * x0),
            top + int(height * y0),
            max(1, int(width * (x1 - x0))),
            max(1, int(height * (y1 - y0))),
        )
    )
    score = battle_hud_layout_score(frame)
    log_recovery_debug(relink, "battle_hud_layout", hud_score=score, texts=texts)
    if score < BATTLE_HUD_BLUE_MIN_FRACTION:
        log.debug(
            "OCR 命中战斗文字，但技能 HUD 结构不成立（蓝色占比 %.1f%%），忽略本帧",
            score * 100.0,
        )
        return False

    # The task title and timer shift vertically between languages, resolutions,
    # and Chiaki forks. Detect all lines in the right half instead of forcing
    # both languages through one narrow recognition-only strip.
    timer_texts = read_region_detected_texts(relink, "战斗右半屏")
    if not _text_matches_marker(
        timer_texts.get(language, ""), language, "battle_timer"
    ):
        log_recovery_debug(relink, "battle_timer_miss", texts=timer_texts)
        log.debug(
            "战斗文字和技能 HUD 已命中，但右上角未识别到战斗倒计时，忽略本帧"
        )
        return False

    relink.confirm_ui_language(language, "battle_hud+skill_layout+timer")
    return True


def battle_timer_marker_state(relink: Controller) -> bool | None:
    """Return timer visibility, or ``None`` when capture/OCR is unavailable.

    A missing battle HUD can also mean loading, a result transition, or a
    short OCR miss.  The town-recovery watchdog therefore uses the timer label
    as its long-lived negative signal instead of treating every unclassified
    frame as proof that the character is back in town.  Keeping capture failure
    separate prevents a disconnected or temporarily minimized stream from
    starting the town-recovery clock.
    """

    if relink.paused:
        return None
    try:
        texts = read_region_texts(relink, "战斗右半屏")
    except Exception:
        return None
    if not texts:
        return None
    for language in relink.ui_language_candidates():
        if _text_matches_marker(
            texts.get(language, ""), language, "battle_timer"
        ):
            relink.confirm_ui_language(language, "battle_timer")
            return True
    return False


def battle_timer_marker_present(relink: Controller) -> bool:
    """Return whether the live battle countdown label is currently visible."""

    return battle_timer_marker_state(relink) is True


def unexpected_town_recovery_signal(relink: Controller) -> str:
    """Classify whether the town-recovery clock may advance.

    ``timer_missing_no_battle_hud`` is the only state that may advance the
    unexpected-town clock.  A visible timer, a visible blue battle HUD, or an
    unavailable capture all stop the clock.  Result/animation/menu exclusion
    remains the responsibility of the caller's normal state probes.
    """

    timer_state = battle_timer_marker_state(relink)
    if timer_state is None:
        return "capture_unavailable"
    if timer_state:
        return "battle_timer"
    try:
        visual_candidate, _ = battle_hud_visual_candidate(relink)
    except (OSError, RuntimeError):
        return "capture_unavailable"
    if visual_candidate:
        return "battle_hud"
    return "timer_missing_no_battle_hud"


def result_repeat_indicator_is_gold(relink: Controller) -> bool:
    """Detect gold using a local-density search inside the marked panel."""

    try:
        frame = relink.screenshot().convert("RGB")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    width, height = frame.size
    x0, y0, x1, y1 = RESULT_REPEAT_INDICATOR_REGION
    sx0, sy0, sx1, sy1 = RESULT_REPEAT_INDICATOR_SEARCH_REGION
    window_width = max(4, int(width * (x1 - x0)))
    window_height = max(4, int(height * (y1 - y0)))
    search_left = int(width * sx0)
    search_top = int(height * sy0)
    search_right = int(width * sx1)
    search_bottom = int(height * sy1)
    # The icon may shift slightly with client scaling and capture rounding.
    # Scan the marked panel with the same-size local window instead of using
    # the whole panel's much smaller average gold fraction.
    best_fraction = 0.0
    best_offset = (0, 0)
    for top in range(search_top, max(search_top, search_bottom - window_height + 1), 4):
        for left in range(search_left, max(search_left, search_right - window_width + 1), 4):
            crop = frame.crop(
                (left, top, left + window_width, top + window_height)
            )
            score = _gold_pixel_fraction(crop)
            if score > best_fraction:
                best_fraction = score
                best_offset = (left, top)
    log.debug(
        "结算红框局部金色检测 | best_fraction=%.3f | threshold=%.3f | offset=%s | search=%s",
        best_fraction,
        RESULT_REPEAT_GOLD_MIN_FRACTION,
        best_offset,
        RESULT_REPEAT_INDICATOR_SEARCH_REGION,
    )
    return best_fraction >= RESULT_REPEAT_GOLD_MIN_FRACTION


def _gold_pixel_fraction(frame: Image.Image) -> float:
    """Return the fraction of saturated gold pixels in a small RGB crop."""
    pixels = np.asarray(frame.convert("RGB"), dtype=np.float32)
    if pixels.ndim != 3 or pixels.shape[0] < 4 or pixels.shape[1] < 4:
        return 0.0
    red = pixels[:, :, 0]
    green = pixels[:, :, 1]
    blue = pixels[:, :, 2]
    gold = (
        (red >= 140.0)
        & (green >= 105.0)
        & (blue <= 105.0)
        & (red - blue >= 65.0)
        & (green - blue >= 40.0)
        & (red - green <= 115.0)
    )
    return float(gold.mean())


def result_repeat_indicator_is_stably_gold(relink: Controller) -> bool:
    """Require the gold repeat indicator to survive one fresh capture frame."""
    if not result_repeat_indicator_is_gold(relink):
        return False
    # Small test doubles and legacy callers may not expose capture serials. In
    # production Controller always does, and the serial boundary prevents a
    # single transition animation frame from enabling auto-repeat.
    if not hasattr(relink, "capture_frame_state") or not hasattr(
        relink, "wait_for_fresh_capture"
    ):
        return True
    try:
        serial, _ = relink.capture_frame_state()
        if not relink.wait_for_fresh_capture(
            serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
        ):
            return False
        return result_repeat_indicator_is_gold(relink)
    except Exception:
        log.debug("金色自动重战标记二次确认失败", exc_info=True)
        return False


def result_repeat_control_is_visible(relink: Controller) -> bool:
    """Return whether the lower-left retry action bar is visible.

    This distinguishes the preliminary BATTLE RESULT summary from the next
    result page that actually exposes ``再挑戦する``. It is intentionally a
    visual guard, not evidence that auto-repeat is enabled.
    """
    try:
        frame = relink.screenshot().convert("RGB")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    width, height = frame.size
    x0, y0, x1, y1 = RESULT_REPEAT_CONTROL_REGION
    crop = np.asarray(
        frame.crop(
            (
                int(width * x0),
                int(height * y0),
                int(width * x1),
                int(height * y1),
            )
        ),
        dtype=np.float32,
    )
    if crop.ndim != 3 or crop.shape[0] < 4 or crop.shape[1] < 4:
        return False
    red = crop[:, :, 0]
    green = crop[:, :, 1]
    blue = crop[:, :, 2]
    action_blue = (blue >= 80.0) & (blue - red >= 18.0) & (blue - green >= 8.0)
    return float(action_blue.mean()) >= RESULT_REPEAT_CONTROL_BLUE_MIN_FRACTION


def result_repeat_ps_button_is_visible(relink: Controller) -> bool:
    """Detect the white circular PS5 glyph on the lower-left retry bar."""
    try:
        frame = relink.screenshot().convert("RGB")
        width, height = frame.size
        x0, y0, x1, y1 = RESULT_REPEAT_PS_BUTTON_REGION
        crop = np.asarray(
            frame.crop(
                (
                    int(width * x0),
                    int(height * y0),
                    int(width * x1),
                    int(height * y1),
                )
            ),
            dtype=np.float32,
        )
        if crop.ndim != 3 or crop.shape[0] < 4 or crop.shape[1] < 4:
            return False
        red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        white = (
            (red >= 170.0)
            & (green >= 170.0)
            & (blue >= 170.0)
            & (np.maximum.reduce((red, green, blue)) - np.minimum.reduce((red, green, blue)) <= 45.0)
        )
        fraction = float(white.mean())
        visible = fraction >= RESULT_REPEAT_PS_BUTTON_MIN_FRACTION
        log.debug(
            "日文结算 PS5 圆形按钮检测 | white_fraction=%.3f | threshold=%.3f | visible=%s",
            fraction,
            RESULT_REPEAT_PS_BUTTON_MIN_FRACTION,
            visible,
        )
        return visible
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def result_msp_marker_is_visible(relink: Controller) -> bool:
    """Detect the small ``獲得MSP`` reward row as a secondary page gate."""
    try:
        frame = relink.screenshot().convert("RGB")
        width, height = frame.size
        x0, y0, x1, y1 = RESULT_MSP_MARKER_REGION
        crop = frame.crop(
            (int(width * x0), int(height * y0), int(width * x1), int(height * y1))
        )
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        crop = crop.resize((max(1, crop.width * 3), max(1, crop.height * 3)), resampling)
        recognized: list[str] = []
        for language in tuple(dict.fromkeys(relink.ui_language_candidates() or ("ja",))):
            result = relink.ocr(crop, confidence=0.35, language=language)
            if isinstance(result, list):
                recognized.extend(
                    str(item.get("text", ""))
                    for item in result
                    if isinstance(item, dict)
                )
        text = "".join(recognized).upper().replace(" ", "")
        normalized = text.replace("５", "5").replace("Ｐ", "P")
        has_msp = any(marker in normalized for marker in ("MSP", "M5P", "M$P"))
        has_number = sum(char.isdigit() for char in normalized) >= RESULT_MSP_MARKER_MIN_DIGITS
        visible = has_msp and has_number
        log.debug(
            "日文结算 MSP 兜底区域检测 | text=%r | visible=%s",
            text,
            visible,
        )
        return visible
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def japanese_retry_page_is_visible(relink: Controller) -> bool:
    """Use a two-part page gate for every Japanese Square fallback.

    ``獲得MSP`` is also present on the preliminary BATTLE RESULT summary, so
    it cannot identify the retry page on its own.  The lower-left action bar
    is the page discriminator; the PS button or MSP row only corroborates
    that discriminator after it has been found.
    """
    if not result_repeat_control_is_visible(relink):
        return False
    return result_repeat_ps_button_is_visible(relink) or result_msp_marker_is_visible(
        relink
    )


def result_continue_visual_is_visible(relink: Controller) -> bool:
    """Detect the bright right-bottom result ``次へ`` prompt without OCR."""
    try:
        frame = relink.screenshot().convert("RGB")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    width, height = frame.size
    x0, y0, x1, y1 = RESULT_CONTINUE_VISUAL_REGION
    crop = np.asarray(
        frame.crop(
            (
                int(width * x0),
                int(height * y0),
                int(width * x1),
                int(height * y1),
            )
        ),
        dtype=np.float32,
    )
    if crop.ndim != 3 or crop.shape[0] < 4 or crop.shape[1] < 4:
        return False
    red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    bright_prompt = (
        (red >= 150.0)
        & (green >= 150.0)
        & (blue >= 150.0)
        & (np.abs(red - green) <= 55.0)
        & (np.abs(green - blue) <= 55.0)
    )
    cyan_prompt = (
        (blue >= 130.0)
        & (green >= 120.0)
        & (blue - red >= 20.0)
    )
    fraction = float((bright_prompt | cyan_prompt).mean())
    return fraction >= RESULT_CONTINUE_VISUAL_MIN_FRACTION


def result_progress_prompt_is_visible(relink: Controller) -> bool:
    """Recognize the language-specific form of the common result advance step.

    Both clients must expose a result-progress affordance before Cross may
    advance a completed result page.  Chinese uses the narrow ``继续`` OCR
    marker; Japanese first uses its ``次へ`` OCR marker and may fall back to
    the low-resolution visual prompt when that glyph is lost to compression.
    """
    if region_has_marker(relink, "继续", "result_continue"):
        return True
    return (
        "ja" in relink.ui_language_candidates()
        and result_continue_visual_is_visible(relink)
    )


def press_visual_result_continue(
    relink: Controller, repeat_armed: bool
) -> bool:
    """Send Cross for a visible result prompt when OCR misses ``次へ``.

    Before the gold repeat state is confirmed, a visible lower-left retry bar
    blocks this fallback. Once repeat is armed, the same prompt is safe to
    accept on the retry page.
    """
    if relink.paused or not result_continue_visual_is_visible(relink):
        return False
    if not repeat_armed and japanese_retry_page_is_visible(relink):
        return False
    serial, _ = relink.capture_frame_state()
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
    ):
        return False
    if relink.paused or not result_continue_visual_is_visible(relink):
        return False
    with AUTOMATION_INPUT_LOCK:
        if not relink.running:
            return False
        relink.press(CROSS_KEY)
    log.info("日文结算右下视觉确认到次へ，发送 Cross（OCR 兜底）")
    sleep(0.75)
    return True


def detect_stable_battle_hud(relink: Controller) -> bool:
    """Confirm the complete battle HUD on two distinct captured frames."""
    if relink.paused or not battle_hud_candidate(relink):
        return False
    serial, _ = relink.capture_frame_state()
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
    ):
        return False
    if relink.paused or not battle_hud_candidate(relink):
        log.debug("战斗 HUD 仅单帧成立，不切换到战斗阶段")
        return False
    return True


def frame_activity_signature(relink: Controller) -> np.ndarray:
    """Return a cheap grayscale thumbnail for frozen-stream detection."""
    frame = relink.screenshot().resize((64, 36))
    return np.asarray(frame.convert("L"), dtype=np.int16)


def aligned_frame_activity_score(previous: np.ndarray, current: np.ndarray) -> float:
    """Measure visual change while ignoring a one-pixel whole-frame jitter."""
    if previous.shape != current.shape or previous.ndim != 2:
        return float("inf")
    height, width = previous.shape
    if height < 4 or width < 4:
        return float(np.mean(np.abs(current - previous)))
    reference = previous[1:-1, 1:-1]
    scores = []
    for y_offset in (-1, 0, 1):
        for x_offset in (-1, 0, 1):
            shifted = current[
                1 + y_offset : height - 1 + y_offset,
                1 + x_offset : width - 1 + x_offset,
            ]
            scores.append(float(np.mean(np.abs(reference - shifted))))
    return min(scores)


def full_frame_texts(
    relink: Controller,
    confidence: float = 0.52,
) -> dict[str, str]:
    """Run full OCR only during the rare reconnect recovery workflow."""
    frame = relink.screenshot()
    texts: dict[str, str] = {}
    for language in relink.ui_language_candidates():
        result = relink.ocr(frame, confidence=confidence, language=language)
        if isinstance(result, list):
            texts[language] = "".join(
                str(item.get("text", "")) for item in result
            )
        else:
            texts[language] = ""
    return texts


def read_settlement_center_texts(relink: Controller) -> dict[str, str]:
    """Read the central result dialog, using enhancement only as a fallback.

    Small Chiaki windows blur the Japanese title into the result background.
    The right-bottom continue crop remains readable, but the center title and
    question need a wider crop plus contrast/sharpen passes. Keep this scoped
    to the result-center region so no other OCR marker becomes more permissive.
    """
    global _CAPTURE_UNAVAILABLE_WARNED
    try:
        crop = relink.screenshot_text("结算").convert("RGB")
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        texts: dict[str, str] = {}
        for language in relink.ui_language_candidates():
            result = relink.ocr(crop, confidence=0.35, language=language)
            best_text = "".join(
                str(item.get("text", "")) for item in result
            ) if isinstance(result, list) else ""
            base_score = sum(
                1
                for semantic in ("settlement", "confirmation", "challenge_confirmation")
                if _text_matches_marker(best_text, language, semantic)
            )
            if base_score:
                texts[language] = best_text
                log.debug("结算中心 OCR[%s] 命中基础路径: %r", language, best_text)
                continue

            # Low-resolution/compressed Japanese text can disappear in the
            # original crop. Pay the extra OCR cost only after the normal
            # recognition path fails to provide a settlement marker.
            candidates = [
                ImageEnhance.Contrast(crop).enhance(1.8),
                ImageEnhance.Contrast(crop).enhance(1.8).filter(
                    ImageFilter.UnsharpMask(radius=1, percent=180, threshold=2)
                ),
            ]
            best_score = 0
            for candidate in candidates:
                result = relink.ocr(candidate, confidence=0.35, language=language)
                candidate_text = "".join(
                    str(item.get("text", "")) for item in result
                ) if isinstance(result, list) else ""
                score = sum(
                    1
                    for semantic in ("settlement", "confirmation", "challenge_confirmation")
                    if _text_matches_marker(candidate_text, language, semantic)
                )
                if score > best_score or (score == best_score and len(candidate_text) > len(best_text)):
                    best_score = score
                    best_text = candidate_text
            texts[language] = best_text
            log.debug("结算中心 OCR[%s] 进入增强兜底: %r", language, best_text)
    except RuntimeError as exc:
        if not _CAPTURE_UNAVAILABLE_WARNED:
            log.warning("Chiaki 画面/OCR 暂时不可用，保留当前阶段并等待窗口恢复或重建：%s", exc)
            _CAPTURE_UNAVAILABLE_WARNED = True
        return {}
    except Exception:
        if not _CAPTURE_UNAVAILABLE_WARNED:
            log.warning(
                "Chiaki 画面/OCR 暂时不可用，保留当前阶段并等待窗口恢复或重建",
                exc_info=True,
            )
            _CAPTURE_UNAVAILABLE_WARNED = True
        return {}

    if _CAPTURE_UNAVAILABLE_WARNED:
        log.info("Chiaki 画面已恢复，继续当前自动化阶段")
        _CAPTURE_UNAVAILABLE_WARNED = False
    return texts


def town_destination_menu_has_marker(
    relink: Controller, semantic: str = "quest_destination"
) -> bool:
    """Use an enlarged menu crop as a recovery-only OCR fallback."""
    try:
        frame = relink.screenshot().convert("RGB")
        width, height = frame.size
        x0, y0, x1, y1 = TOWN_DESTINATION_MENU_REGION
        crop = frame.crop(
            (int(width * x0), int(height * y0), int(width * x1), int(height * y1))
        )
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        crop = crop.resize((crop.width * 2, crop.height * 2), resampling)
        texts: dict[str, str] = {}
        for language in relink.ui_language_candidates():
            result = relink.ocr(crop, confidence=0.38, language=language)
            texts[language] = (
                "".join(str(item.get("text", "")) for item in result)
                if isinstance(result, list)
                else ""
            )
        language = _match_marker_language(relink, texts, semantic)
        if language is not None:
            log.info("恢复专用菜单 OCR 识别到任务中心入口（语言=%s）", language)
            log_recovery_debug(relink, "quest_destination_menu_crop", texts=texts)
            return True
        log_recovery_debug(relink, "quest_destination_menu_crop_miss", texts=texts)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        log.debug("恢复专用任务菜单 OCR 失败", exc_info=True)
    return False


def full_frame_has_marker(
    relink: Controller,
    texts: dict[str, str],
    semantic: str,
) -> bool:
    return _match_marker_language(relink, texts, semantic) is not None


def town_ready_panel_texts(relink: Controller) -> dict[str, str]:
    """OCR the right-side selected-quest card used for the final Box action."""
    try:
        frame = relink.screenshot()
        width, height = frame.size
        x0, y0, x1, y1 = TOWN_READY_PANEL_REGION
        crop = frame.crop(
            (int(width * x0), int(height * y0), int(width * x1), int(height * y1))
        )
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        crop = crop.resize((crop.width * 2, crop.height * 2), resampling)
        texts: dict[str, str] = {}
        for language in relink.ui_language_candidates():
            result = relink.ocr(crop, confidence=0.48, language=language)
            texts[language] = (
                "".join(str(item.get("text", "")) for item in result)
                if isinstance(result, list)
                else ""
            )
        return texts
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return {}


def town_ready_panel_has_box_icon(relink: Controller) -> bool:
    """Detect the language-independent Box prompt in the right quest card."""
    try:
        frame = relink.screenshot().convert("RGB")
        width, height = frame.size
        x0, y0, x1, y1 = TOWN_READY_BOX_ICON_REGION
        pixels = np.asarray(
            frame.crop(
                (int(width * x0), int(height * y0), int(width * x1), int(height * y1))
            ),
            dtype=np.float32,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    if pixels.ndim != 3 or pixels.shape[2] < 3 or pixels.size == 0:
        return False
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    white_fraction = float(np.mean((red >= 180) & (green >= 180) & (blue >= 180)))
    blue_fraction = float(
        np.mean((blue >= 100) & (blue - (red + green) * 0.5 >= 25))
    )
    return (
        white_fraction >= TOWN_READY_BOX_MIN_WHITE_FRACTION
        and blue_fraction >= TOWN_READY_BOX_MIN_BLUE_FRACTION
    )


def town_ready_panel_present(
    relink: Controller, full_texts: dict[str, str] | None = None
) -> bool:
    """Recognize only the language-independent right-side Box panel."""
    return town_ready_panel_has_box_icon(relink)


def town_quest_accepted_state_present(texts: dict[str, str]) -> bool:
    """Recognize an explicit accepted-quest state, not a menu action label."""
    zh_text = str(texts.get("zh", ""))
    ja_text = str(texts.get("ja", ""))
    # ``查看已承接任务`` is a normal task-selection menu entry. It appears
    # before a quest has been accepted and must keep the generic Cross flow.
    chinese_accepted = (
        "已承接任务" in zh_text and "查看已承接任务" not in zh_text
    )
    return chinese_accepted or any(
        marker in ja_text for marker in ("受注しました", "準備OK")
    )


def town_ready_confirmation_dialog_present(relink: Controller) -> bool:
    """Detect the centered accepted-quest confirmation modal by its blue body."""
    try:
        frame = relink.screenshot().convert("RGB")
        width, height = frame.size
        x0, y0, x1, y1 = TOWN_READY_DIALOG_REGION
        pixels = np.asarray(
            frame.crop(
                (int(width * x0), int(height * y0), int(width * x1), int(height * y1))
            ),
            dtype=np.float32,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    if pixels.ndim != 3 or pixels.shape[2] < 3 or pixels.size == 0:
        return False
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    dark_blue_fraction = float(
        np.mean((blue >= 45) & (blue <= 180) & (blue - red >= 10) & (blue - green >= 5))
    )
    return dark_blue_fraction >= TOWN_READY_DIALOG_MIN_DARK_BLUE_FRACTION


def town_ready_confirmation_selection(relink: Controller) -> str | None:
    """Read the blue focus row of the centered ready/cancel confirmation."""
    try:
        frame = relink.screenshot().convert("RGB")
        width, height = frame.size

        def blue_fraction(region: tuple[float, float, float, float]) -> float:
            x0, y0, x1, y1 = region
            pixels = np.asarray(
                frame.crop(
                    (
                        int(width * x0),
                        int(height * y0),
                        int(width * x1),
                        int(height * y1),
                    )
                ),
                dtype=np.float32,
            )
            if pixels.ndim != 3 or pixels.shape[2] < 3 or pixels.size == 0:
                return 0.0
            red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
            return float(np.mean((blue >= 100) & (blue - (red + green) * 0.5 >= 25)))

        ready_score = blue_fraction(TOWN_READY_DIALOG_READY_REGION)
        cancel_score = blue_fraction(TOWN_READY_DIALOG_CANCEL_REGION)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    delta = ready_score - cancel_score
    if delta >= TOWN_READY_DIALOG_SELECTION_MIN_DELTA:
        return "ready"
    if delta <= -TOWN_READY_DIALOG_SELECTION_MIN_DELTA:
        return "cancel"
    return None


def japanese_ready_dialog_structure_matches(text: str) -> bool:
    """Require both Japanese ready and cancel choices before confirming."""
    normalized = "".join(str(text).upper().split())
    return (
        "準備" in normalized
        and "OK" in normalized
        and any(marker in normalized for marker in JAPANESE_READY_DIALOG_CANCEL_MARKERS)
    )


def town_ready_confirmation_is_confirmable(
    relink: Controller, texts: dict[str, str] | None = None
) -> tuple[bool, str | None, dict[str, str]]:
    """Confirm only the upper action of the centered final quest modal.

    The visual focus row is the primary signal. OCR is retained as a narrow
    language-specific cross-check and diagnostic record for cases where the
    stream produces a frame during the modal's entrance animation.
    """
    if not town_ready_confirmation_dialog_present(relink):
        return False, None, texts or {}
    if texts is None:
        try:
            texts = full_frame_texts(relink)
        except (OSError, RuntimeError):
            texts = {}
    selection = town_ready_confirmation_selection(relink)
    chinese_ready = _text_matches_marker(
        str(texts.get("zh", "")), "zh", "quest_ready"
    )
    japanese_ready = japanese_ready_dialog_structure_matches(
        str(texts.get("ja", ""))
    )
    # The upper blue row is the game's affirmative action on both clients.
    # OCR validates the modal and remains a fallback if its focus fill is
    # temporarily degraded by stream compression.
    return selection == "ready" or chinese_ready or japanese_ready, selection, texts


def wait_for_any_marker(
    relink: Controller,
    semantics: tuple[str, ...],
    timeout: float,
    poll: float = 1.5,
) -> str | None:
    deadline = time() + timeout
    while relink.running and not relink.paused and time() < deadline:
        try:
            texts = full_frame_texts(relink)
        except (OSError, RuntimeError):
            sleep(poll)
            continue
        for semantic in semantics:
            if full_frame_has_marker(relink, texts, semantic):
                return semantic
            if (
                semantic == "quest_destination"
                and town_destination_menu_has_marker(relink, semantic)
            ):
                return semantic
        sleep(poll)
    return None


def press_recovery_cross(relink: Controller, delay: float = 1.0) -> None:
    serial, _ = relink.capture_frame_state()
    with AUTOMATION_INPUT_LOCK:
        relink.press(CROSS_KEY)
    # A visible window is not necessarily input-ready.  For background mode,
    # wait for at least one frame produced after the press before the next
    # navigation decision; this avoids losing both Cross presses at startup.
    relink.wait_for_fresh_capture(serial, timeout=max(1.0, delay))
    sleep(min(0.5, max(0.0, delay)))


def press_recovery_square(relink: Controller, delay: float = 1.0) -> None:
    """Confirm the task-center's final ``准备完毕`` state with Box/Square."""
    serial, _ = relink.capture_frame_state()
    with AUTOMATION_INPUT_LOCK:
        relink.press(SQUARE_KEY)
    relink.wait_for_fresh_capture(serial, timeout=max(1.0, delay))
    sleep(min(0.5, max(0.0, delay)))


def press_recovery_moon(relink: Controller, delay: float = 1.0) -> None:
    """Send Moon/Circle and wait for the recovered stream to react."""
    serial, _ = relink.capture_frame_state()
    with AUTOMATION_INPUT_LOCK:
        relink.press(MOON_KEY)
    relink.wait_for_fresh_capture(serial, timeout=max(1.0, delay))
    sleep(min(0.5, max(0.0, delay)))


def abort_recovery_quest_cancel_dialog(relink: Controller) -> None:
    """Back out of the NPC's abandon-quest confirmation during town recovery."""
    log.info("识别到回城续战中的取消任务确认框，连续发送 3 次 Moon 返回任务卡")
    for index in range(3):
        if not relink.running or relink.paused:
            return
        press_recovery_moon(relink, 0.8)
        log.debug("取消任务确认框：已发送第 %d/3 次 Moon", index + 1)


def town_quest_abandon_confirmation_present(
    relink: Controller, texts: dict[str, str]
) -> bool:
    """Reject the ready page's ``查看/放弃已承接任务`` menu action.

    That action is visible on the normal post-Square ready page and must not
    trigger the three-Moon abandon-dialog recovery. The broad marker remains
    useful for OCR variants, but only after this menu-item exclusion.
    """
    zh_text = str(texts.get("zh", ""))
    ja_text = str(texts.get("ja", ""))
    if "查看/放弃已承接任务" in zh_text or "查看放弃已承接任务" in zh_text:
        return False
    return full_frame_has_marker(relink, texts, "quest_abandon_confirmation")


def dismiss_town_collection_list(
    relink: Controller, texts: dict[str, str] | None = None
) -> bool:
    """Close the town collection modal before applying quest navigation.

    The modal is drawn over the quest-center page, so its background may also
    contain ``任务中心``.  Calling this helper before any quest action keeps a
    background marker from causing Cross/Square to operate on the collection
    item instead of closing the modal.
    """

    if texts is None:
        try:
            texts = full_frame_texts(relink)
        except (OSError, RuntimeError):
            return False
    if not full_frame_has_marker(relink, texts, "town_collection_list"):
        return False
    log.info("识别到主城‘收藏列表’弹窗，发送 Moon 关闭后重新判断流程")
    press_recovery_moon(relink, 1.0)
    return True


def recover_last_town_quest(
    relink: Controller, *, destination_menu_open: bool = False
) -> bool:
    """Run the town task transaction without admitting asynchronous recovery."""
    with automation_flow("town_recovery") as acquired:
        if not acquired:
            log.warning(
                "主城恢复被其它自动化流程占用，保留当前画面等待其完成：%s",
                automation_flow_name(),
            )
            return False
        return _recover_last_town_quest_impl(
            relink, destination_menu_open=destination_menu_open
        )


def _recover_last_town_quest_impl(
    relink: Controller, *, destination_menu_open: bool = False
) -> bool:
    """Re-enter the previous quest using the task-center interaction flow.

    The destination menu and the NPC interaction are separate steps: after
    entering the task center, walk to the NPC and send C/Pyramid once to open
    the quest page. The intermediate menus advance with Cross. The final
    ``准备完毕``/``準備OK`` panel is different: its on-screen prompt is
    Box/Square, so it is confirmed once with Square and then waits for battle
    without sending any more navigation input. The collection modal is handled
    as an explicit exception.
    """
    log.warning("未检测到战斗 HUD，开始从主城恢复上一次任务")
    if not destination_menu_open:
        with AUTOMATION_INPUT_LOCK:
            relink.press(L2_KEY)
        if wait_for_any_marker(relink, ("quest_destination",), 12.0) is None:
            log.error("恢复失败：L2 后未识别到任务中心/クエストカウンター")
            return False

    # The task-center title can be visible before the character is close
    # enough to interact with the NPC. Approach it first, then use the mapped
    # Pyramid/C key exactly once to open the quest counter.
    if dismiss_town_collection_list(relink):
        sleep(0.5)
    press_recovery_cross(relink, 2.5)
    with AUTOMATION_INPUT_LOCK:
        relink.press(LEFT_STICK_UP_KEY, movement="press")
    sleep(1.8)
    with AUTOMATION_INPUT_LOCK:
        relink.press(LEFT_STICK_UP_KEY, movement="release")
        relink.press(PYRAMID_KEY)

    # From this point the task counter owns the navigation. Do not require a
    # second OCR gate before pressing Cross: a compressed transition frame can
    # hide the quest title while Cross is still the correct action.
    quest_deadline = time() + TOWN_RECOVERY_NAVIGATION_TIMEOUT_SECONDS
    ready_confirmation_sent = False
    ready_confirmation_deadline: float | None = None
    ready_confirmation_action: str | None = None
    # Once the NPC/task counter reports that the quest was accepted, a
    # transient OCR miss on the right-side Box prompt must not fall through to
    # the generic Cross navigation path. Keep this latch until the Box action
    # (and subsequent final confirmation) has completed.
    quest_accepted_waiting_for_box = False
    next_accepted_wait_log = 0.0
    last_ready_ocr: tuple[str, str] | None = None
    next_debug_report = 0.0
    abandon_dialog_closed = False
    while relink.running and not relink.paused and time() < quest_deadline:
        # Battle HUD is the only completion signal. Do not stop at an
        # intermediate "accepted"/"ready" text because Cross may still be
        # required to launch the quest.
        try:
            if detect_stable_battle_hud(relink):
                log.info("主城恢复已识别到战斗 HUD，任务已开始")
                return True
        except (AttributeError, OSError, RuntimeError):
            # Lightweight test doubles and a transient capture failure are
            # handled by the OCR path below.
            pass
        try:
            texts = full_frame_texts(relink)
        except (OSError, RuntimeError):
            texts = {}
        if (
            not abandon_dialog_closed
            and town_quest_abandon_confirmation_present(relink, texts)
        ):
            abandon_dialog_closed = True
            abort_recovery_quest_cancel_dialog(relink)
            continue
        if dismiss_town_collection_list(relink, texts):
            continue
        if town_quest_accepted_state_present(texts):
            if not quest_accepted_waiting_for_box:
                quest_accepted_waiting_for_box = True
                log.info("主城恢复状态：已承接任务，进入等待右侧 Box 阶段")
            elif DEBUG_MODE and time() >= next_accepted_wait_log:
                next_accepted_wait_log = time() + TOWN_RECOVERY_DEBUG_REPORT_SECONDS
                log.debug("主城恢复状态：仍在已承接任务页，等待右侧 Box")
        ready_confirmable, ready_selection, ready_texts = (
            town_ready_confirmation_is_confirmable(relink, texts)
        )
        if ready_confirmable:
            # Square only opens this modal ("view/abandon accepted quest").
            # It is not the final task start. A following ready/cancel modal
            # must still receive exactly one Cross.
            if ready_confirmation_action != "cross":
                ready_confirmation_sent = True
                ready_confirmation_action = "cross"
                ready_confirmation_deadline = (
                    time() + TOWN_RECOVERY_BATTLE_CONFIRM_TIMEOUT_SECONDS
                )
                log.info("识别到开战前最终确认窗口，发送一次 Cross 真正开始任务")
                press_recovery_cross(relink, 2.0)
            else:
                sleep(0.5)
            continue
        if town_ready_confirmation_dialog_present(relink):
            current_ready_ocr = (
                str(ready_texts.get("zh", "")),
                str(ready_texts.get("ja", "")),
            )
            if current_ready_ocr != last_ready_ocr:
                last_ready_ocr = current_ready_ocr
                log.info(
                    "主城最终确认窗口 OCR 记录（高亮=%s，未满足备用文字结构，未自动确认）：中文=%r，日文=%r",
                    ready_selection,
                    current_ready_ocr[0],
                    current_ready_ocr[1],
                )
            sleep(0.5)
            continue
        # The centered ready/cancel modal and its Cross action must take
        # precedence over the visually similar right-side quest-card Box icon.
        if town_ready_panel_present(relink):
            if not ready_confirmation_sent:
                quest_accepted_waiting_for_box = False
                ready_confirmation_sent = True
                ready_confirmation_action = "square"
                ready_confirmation_deadline = (
                    time() + TOWN_RECOVERY_BATTLE_CONFIRM_TIMEOUT_SECONDS
                )
                log.info("识别到右侧任务卡 Box 图标，发送一次 Box/Square 开始任务")
                press_recovery_square(relink, 2.0)
            else:
                sleep(0.5)
            continue
        if quest_accepted_waiting_for_box:
            # The task is already accepted. OCR and the Box icon can disappear
            # briefly during a stream resize/transition; hold this state and
            # never guess with Cross until the dedicated Box detector returns.
            if DEBUG_MODE and time() >= next_accepted_wait_log:
                next_accepted_wait_log = time() + TOWN_RECOVERY_DEBUG_REPORT_SECONDS
                log.debug("主城恢复状态：已承接任务页暂未检测到 Box，保持等待")
            sleep(0.5)
            continue
        if ready_confirmation_sent:
            # The final ready screen has already accepted exactly one Box.
            # Do not fall back to Cross while it transitions into loading or
            # battle, otherwise a delayed frame can undo the ready state.
            if (
                ready_confirmation_deadline is not None
                and time() >= ready_confirmation_deadline
            ):
                # Before ending recovery (which would eventually reconnect
                # Chiaki), take one fresh screenshot/OCR pass dedicated to
                # the final confirmation modal. This catches a delayed modal
                # after an earlier Box branch without guessing on other pages.
                final_ready, final_selection, _ = (
                    town_ready_confirmation_is_confirmable(relink)
                )
                # A Box is expected to reveal this final dialog. If Cross was
                # already sent, do not send another Cross based on a stale
                # frame: the quest may already be loading successfully.
                if final_ready and ready_confirmation_action == "square":
                    ready_confirmation_action = "cross"
                    ready_confirmation_deadline = (
                        time() + TOWN_RECOVERY_BATTLE_CONFIRM_TIMEOUT_SECONDS
                    )
                    log.warning(
                        "任务承接等待超时前专项 OCR 识别到最终确认窗口（高亮=%s），补发 Cross",
                        final_selection,
                    )
                    press_recovery_cross(relink, 2.0)
                    continue
                log.error(
                    "%s 后仍未确认战斗 HUD（等待 %.0f 秒），任务输入可能已经成功；"
                    "保留当前画面，不重复发送按键",
                    "Box/Square" if ready_confirmation_action == "square" else "Cross",
                    TOWN_RECOVERY_BATTLE_CONFIRM_TIMEOUT_SECONDS,
                )
                return False
            if DEBUG_MODE and time() >= next_debug_report:
                next_debug_report = time() + TOWN_RECOVERY_DEBUG_REPORT_SECONDS
                log_recovery_debug(relink, "quest_start_wait")
            sleep(0.5)
            continue
        press_recovery_cross(relink, 1.1)
    log.error("恢复失败：60 秒内未通过 Cross 重新进入战斗")
    return False


def classify_reconnected_screen(
    relink: Controller, *, allow_town_menu: bool = False
) -> str | None:
    """Classify only states that have a safe, unambiguous next action."""
    log.info("恢复后开始快速画面状态探测")
    try:
        visual_candidate, visual_score = battle_hud_visual_candidate(relink)
    except (OSError, RuntimeError):
        visual_candidate, visual_score = False, 0.0

    if visual_candidate:
        log.info(
            "恢复后快速探测命中战斗 HUD 外观（蓝色布局 %.1f%%），开始双帧 OCR 确认",
            visual_score * 100.0,
        )
        if detect_stable_battle_hud(relink):
            log.info("恢复后 OCR 已确认战斗 HUD，跳过完整画面 OCR")
            return "battle"
    else:
        log.info(
            "恢复后快速探测未确认战斗 HUD（蓝色布局 %.1f%%），开始 OCR 状态判断",
            visual_score * 100.0,
        )

    result_state = detect_stable_result_ui(relink)
    if result_state:
        log.info("恢复后 OCR 已确认结算控件：%s", result_state)
        return "result"

    log.info("恢复后开始完整 OCR 状态判断（动画/菜单/主城）")
    try:
        texts = full_frame_texts(relink)
    except Exception:
        return None
    if full_frame_has_marker(relink, texts, "result_screen"):
        return "result"
    if full_frame_has_marker(relink, texts, "movie"):
        return "movie"
    # A foreground modal may include the quest-center title behind it. Check
    # the specific modal before the broad town destination marker.
    if allow_town_menu and full_frame_has_marker(
        relink, texts, "town_collection_list"
    ):
        return "town_collection_list"
    # L2 can open the quick-travel/destination list instead of exposing the
    # quest center directly.  The first entry is already selected; pass this
    # state to the existing town state machine so it sends Cross, walks to the
    # NPC, and sends the mapped Pyramid/C key exactly as in the normal route.
    if allow_town_menu and full_frame_has_marker(
        relink, texts, "town_fast_travel"
    ):
        return "town_fast_travel"
    if allow_town_menu and full_frame_has_marker(
        relink, texts, "quest_destination"
    ):
        return "town_menu"
    if allow_town_menu and town_destination_menu_has_marker(
        relink, "quest_destination"
    ):
        return "town_menu"
    if full_frame_has_marker(relink, texts, "game_menu"):
        return "game_menu"

    # A low-contrast battle HUD may not pass the pixel-only probe. Keep the
    # previous behavior as a bounded fallback, but only after the explicit
    # full-OCR pass has failed to classify a safer transition state.
    if not visual_candidate and detect_stable_battle_hud(relink):
        log.info("完整 OCR 未命中其它状态，双帧 OCR 最终确认战斗 HUD")
        return "battle"
    return None


def route_reconnected_screen(relink: Controller, timeout: float = 120.0) -> str | None:
    """Route a reconnect only when no other navigation transaction owns input."""
    with automation_flow("reconnect_route") as acquired:
        if not acquired:
            log.warning(
                "断连/主城恢复延后：当前流程尚未完成：%s",
                automation_flow_name(),
            )
            return None
        return _route_reconnected_screen_impl(relink, timeout=timeout)


def _route_reconnected_screen_impl(
    relink: Controller, timeout: float = 120.0
) -> str | None:
    """Wait through movies/loading and enter town recovery only after proof.

    Two Moon presses can leave the game in battle, a result screen, a movie,
    or a loading transition.  The first two states have existing handlers.
    Movies/loading receive no blind navigation presses. After a bounded safety
    delay, L2 is used as a town probe; the quest macro starts only if OCR then
    confirms the destination menu's ``任务中心`` entry.
    """
    deadline = time() + timeout
    # A town scene never becomes pixel-stable: foliage, NPCs, character idle
    # animations, and UI particles keep changing. Safety comes from excluding
    # battle/result/movie states and waiting for live frames, not from requiring
    # the complete image to remain unchanged.
    town_probe_after = time() + 5.0
    next_full_probe = 0.0
    next_town_probe = town_probe_after
    movie_logged = False
    town_probe_attempts = 0
    max_town_probe_attempts = 3
    menu_close_attempts = 0
    force_town_probe = False

    while relink.running and not relink.paused and time() < deadline:
        now = time()
        try:
            frame_activity_signature(relink)
        except (OSError, RuntimeError):
            sleep(1.0)
            continue

        if now >= next_full_probe:
            state = classify_reconnected_screen(relink)
            next_full_probe = now + 3.0
            if state == "battle":
                log.info("重连后识别到战斗 HUD，交回战斗主流程")
                return "battle_wait"
            if state == "result":
                log.info("重连后识别到结算界面，交回结算主流程")
                return "result"
            if state == "game_menu":
                if force_town_probe:
                    # A false-positive menu OCR result must not starve the
                    # town recovery path. The next iteration performs the
                    # bounded L2 probe instead of sending another Moon.
                    next_town_probe = now
                    next_full_probe = now + 8.0
                    continue
                if menu_close_attempts >= 4:
                    log.warning(
                        "已连续关闭 4 层游戏菜单，改为确认是否已经回到主城"
                    )
                    force_town_probe = True
                    next_town_probe = now
                    next_full_probe = now + 8.0
                    continue
                menu_close_attempts += 1
                log.info(
                    "重连后识别到游戏菜单，发送 Moon 返回（第 %d/4 次）",
                    menu_close_attempts,
                )
                serial, _ = relink.capture_frame_state()
                with AUTOMATION_INPUT_LOCK:
                    relink.press(MOON_KEY)
                relink.wait_for_fresh_capture(serial, timeout=2.5)
                sleep(1.0)
                town_probe_attempts = 0
                if menu_close_attempts >= 4:
                    log.warning(
                        "已连续关闭 4 层游戏菜单，改为确认是否已经回到主城"
                    )
                    force_town_probe = True
                    next_town_probe = time()
                    next_full_probe = time() + 8.0
                else:
                    next_town_probe = time() + 4.0
                    next_full_probe = 0.0
                continue
            if state == "movie":
                if not movie_logged:
                    log.info("重连后处于动画/剧情界面，等待自然结束，不发送额外按键")
                    movie_logged = True
                next_town_probe = max(next_town_probe, now + 10.0)
            elif movie_logged:
                log.info("动画/剧情标记已消失，重新判断当前画面")
                movie_logged = False

        if (
            town_probe_attempts < max_town_probe_attempts
            and now >= next_town_probe
            and not movie_logged
        ):
            town_probe_attempts += 1
            log.info(
                "恢复画面已完成安全等待，发送 L2 探测是否位于主城（第 %d/%d 次）",
                town_probe_attempts,
                max_town_probe_attempts,
            )
            serial, _ = relink.capture_frame_state()
            with AUTOMATION_INPUT_LOCK:
                relink.release_automation_inputs()
                relink.press(L2_KEY)
            relink.wait_for_fresh_capture(serial, timeout=2.5)
            probe_deadline = time() + 8.0
            close_unrecognized_probe = True
            while relink.running and not relink.paused and time() < probe_deadline:
                state = classify_reconnected_screen(
                    relink, allow_town_menu=True
                )
                if state == "battle":
                    force_town_probe = False
                    return "battle_wait"
                if state == "result":
                    force_town_probe = False
                    return "result"
                if state == "movie":
                    force_town_probe = False
                    next_town_probe = time() + 10.0
                    close_unrecognized_probe = False
                    break
                if state == "town_collection_list":
                    dismiss_town_collection_list(relink)
                    close_unrecognized_probe = False
                    next_full_probe = 0.0
                    next_town_probe = time() + 1.0
                    break
                if state in {"town_fast_travel", "town_menu"}:
                    force_town_probe = False
                    if recover_last_town_quest(
                        relink, destination_menu_open=True
                    ):
                        return "battle_wait"
                    # The stream is still a valid, user-visible town screen;
                    # do not treat a quest macro failure as a dead Chiaki
                    # process and close the window underneath the user.
                    log.error("已确认主城但任务承接失败，保留 Chiaki 画面等待人工处理")
                    return "town_recovery_failed"
                sleep(1.0)
            # If L2 opened an unrecognized overlay, a second L2 restores the
            # previous screen before another bounded probe is attempted.
            if close_unrecognized_probe:
                serial, _ = relink.capture_frame_state()
                with AUTOMATION_INPUT_LOCK:
                    relink.release_automation_inputs()
                    relink.press(L2_KEY)
                relink.wait_for_fresh_capture(serial, timeout=2.5)
                log.warning(
                    "L2 后仍未确认主城菜单，已关闭未知浮层并准备再次探测（%d/%d）",
                    town_probe_attempts,
                    max_town_probe_attempts,
                )
                if town_probe_attempts >= max_town_probe_attempts:
                    log.error(
                        "连续 %d 次 L2 主城探测均未识别任务中心，放弃本次恢复",
                        max_town_probe_attempts,
                    )
                    return None
                next_town_probe = time() + 3.0
                next_full_probe = 0.0
                continue
        sleep(1.0)

    log.error("重连后 %.0f 秒内未能确认战斗、结算或主城状态", timeout)
    return None


def recover_unexpected_town_state(
    relink: Controller,
    *,
    reason: str,
    timeout: float = UNEXPECTED_TOWN_RECOVERY_TIMEOUT_SECONDS,
) -> str | None:
    """Reuse the reconnect routing macro after an unexpected town return.

    A result page can disappear before the normal result OCR gets a chance to
    press Continue.  In that case the game may leave the character in town
    instead of entering the next battle.  The caller only reaches this helper
    after a sustained absence of the battle countdown, and the reconnect
    router still performs the conservative L2 probe and both-language
    quest-counter macro.  It returns ``battle_wait``/``result`` only after the
    next safe state is confirmed.
    """

    log.warning(
        "自动重战连续 %.1f 秒未识别到战斗倒计时，按重连模式探测主城并尝试续战：%s",
        UNEXPECTED_TOWN_RECOVERY_DELAY_SECONDS,
        reason,
    )
    with AUTOMATION_INPUT_LOCK:
        relink.release_automation_inputs()
    outcome = route_reconnected_screen(relink, timeout=timeout)
    if outcome in {"battle_wait", "result"}:
        log.info("主城/异常过渡恢复成功，交回自动重战阶段：%s", outcome)
    else:
        log.error("主城/异常过渡恢复失败，未确认战斗或结算状态")
    return outcome


def probe_initial_automation_screen(
    relink: Controller, timeout: float = 120.0
) -> str | None:
    """Classify the screen once before the control-panel automation starts.

    ``--auto-start`` is also used when the player is standing in town.  The
    normal battle loop intentionally starts in ``battle_wait`` and therefore
    only looks for battle/result markers; that is correct after a known
    transition, but it leaves a town screen waiting for the long unexpected
    town timeout.  Reuse the reconnect router here so town navigation keeps
    the same conservative L2 probe and quest macro as reconnect recovery.
    """
    log.info("控制面板启动：先执行一次启动画面环境识别")
    try:
        state = classify_reconnected_screen(relink, allow_town_menu=True)
    except (OSError, RuntimeError):
        state = None

    if state == "battle":
        log.info("启动画面识别为战斗，进入战斗状态检测")
        return "battle_wait"
    if state == "result":
        log.info("启动画面识别为结算，进入结算状态检测")
        return "result"
    if state == "town_collection_list":
        dismiss_town_collection_list(relink)
        state = None
    if state == "town_menu":
        if recover_last_town_quest(relink, destination_menu_open=True):
            return "battle_wait"
        return None

    log.info("启动画面尚未确认，交给重连式环境探测继续判断")
    return route_reconnected_screen(relink, timeout=timeout)


def resume_reconnected_chiaki(
    relink: Controller, stream_process: subprocess.Popen | None = None
) -> str | None:
    """Verify a live stream, deliver Cross after fresh frames, then route it."""
    stream_deadline = time() + 45.0
    fresh_frames = 0
    last_serial, _ = relink.capture_frame_state()
    while relink.running and time() < stream_deadline:
        if stream_process is not None and stream_process.poll() is not None:
            log.warning("Chiaki 串流进程已退出，放弃本次连接")
            return None
        if relink.expected_process_has_window("Session has quit"):
            log.warning("Chiaki 报告串流会话失败，将关闭本次窗口并重试")
            return None
        try:
            signature = frame_activity_signature(relink)
            if relink.background_mode and not relink.stream_binding_is_valid():
                log.error("重连捕获的窗口已不是当前 Chiaki 进程，放弃本次连接")
                return None
            serial, _ = relink.capture_frame_state()
            fresh_capture = not relink.background_mode or serial > last_serial
            if (
                float(signature.mean()) > 5.0
                and float(signature.std()) > 3.0
                and fresh_capture
            ):
                fresh_frames += 1
                last_serial = serial
                if fresh_frames >= 3:
                    break
            else:
                fresh_frames = 0
        except (OSError, RuntimeError):
            fresh_frames = 0
        sleep(1.0)
    else:
        log.error("重连失败：45 秒内未收到连续有效的新串流画面")
        return None

    log.info("串流窗口已出现，连续新帧和输入通道均已就绪")
    sleep(2.0)
    # Returning to an existing PS5 session needs two Moon/Circle presses. Each
    # press is gated by a fresh stream frame so a slow connection cannot lose
    # both inputs. Further menu closing is handled by route_reconnected_screen.
    log.info("正在恢复游戏画面：发送第 1/2 次 Moon（Backspace）")
    press_recovery_moon(relink, 1.5)
    if not relink.running or relink.paused:
        return None
    log.info("正在恢复游戏画面：发送第 2/2 次 Moon（Backspace）")
    press_recovery_moon(relink, 3.0)

    log.info("恢复画面按键完成，开始确认 OCR 和当前游戏状态")
    state = classify_reconnected_screen(relink)
    if state == "battle":
        return "battle_wait"
    if state == "result":
        return "result"
    routed = route_reconnected_screen(relink, timeout=70.0)
    if routed in {"battle_wait", "result"}:
        return routed
    log.error("两次 Moon 后仍未恢复到战斗、结算或主城状态")
    return None


def _ps5_discovery_socket(timeout: float) -> socket.socket:
    """Create the same IPv4 UDP socket and local-port fallback Chiaki uses."""
    discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    discovery.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for port in range(PS5_DISCOVERY_LOCAL_PORT_MIN, PS5_DISCOVERY_LOCAL_PORT_MAX + 1):
        try:
            discovery.bind(("", port))
            break
        except OSError:
            continue
    else:
        discovery.bind(("", 0))
    discovery.settimeout(max(0.1, timeout))
    return discovery


def _ps5_discovery_packet(command: str, credential: int | None = None) -> bytes:
    """Format a PS5 discovery packet byte-for-byte like Chiaki 2.2.0."""
    if command == "SRCH":
        text = (
            "SRCH * HTTP/1.1\n"
            f"device-discovery-protocol-version:{PS5_DISCOVERY_PROTOCOL_VERSION}\n"
        )
    elif command == "WAKEUP" and credential is not None:
        text = (
            "WAKEUP * HTTP/1.1\n"
            "client-type:vr\n"
            "auth-type:R\n"
            "model:w\n"
            "app-type:r\n"
            f"user-credential:{credential}\n"
            f"device-discovery-protocol-version:{PS5_DISCOVERY_PROTOCOL_VERSION}\n"
        )
    else:
        raise ValueError("unsupported PS5 discovery command")
    # Chiaki includes the terminating NUL in the UDP datagram.
    return text.encode("ascii") + b"\x00"


def query_ps5_state(host: str, timeout: float = 2.5) -> str:
    """Return ``ready``, ``standby`` or ``unreachable`` via directed discovery."""
    try:
        target = socket.gethostbyname(host)
        packet = _ps5_discovery_packet("SRCH")
        deadline = time() + max(0.2, timeout)
        with _ps5_discovery_socket(min(0.8, timeout)) as discovery:
            while time() < deadline:
                discovery.sendto(packet, (target, PS5_DISCOVERY_PORT))
                receive_until = min(deadline, time() + 0.8)
                while time() < receive_until:
                    discovery.settimeout(max(0.1, receive_until - time()))
                    try:
                        payload, source = discovery.recvfrom(2048)
                    except socket.timeout:
                        break
                    if source[0] != target:
                        continue
                    first_line = payload.rstrip(b"\x00").splitlines()[0].decode(
                        "ascii", "ignore"
                    )
                    fields = first_line.split()
                    if len(fields) >= 2 and fields[1] == "200":
                        return "ready"
                    if len(fields) >= 2 and fields[1] == "620":
                        return "standby"
    except (OSError, IndexError):
        pass
    return "unreachable"


def _send_ps5_wakeup_packet(host: str, credential: int) -> bool:
    """Send redundant wakeup datagrams without exposing the credential."""
    try:
        target = socket.gethostbyname(host)
        packet = _ps5_discovery_packet("WAKEUP", credential)
        with _ps5_discovery_socket(1.0) as discovery:
            for _ in range(3):
                discovery.sendto(packet, (target, PS5_DISCOVERY_PORT))
                sleep(0.25)
        return True
    except OSError:
        return False


def _ensure_ps5_ready(host: str, nickname: str, wait_seconds: float = 30.0) -> str:
    """Query state, wake standby PS5, and verify that it reaches ready.

    ``unreachable`` remains non-fatal because some routed/VPN setups permit
    Remote Play while blocking UDP discovery replies.
    """
    state = query_ps5_state(host)
    if state == "ready":
        log.info("PS5 状态：ready，可以启动串流")
        return "ready"
    if state == "standby":
        log.info("PS5 状态：standby，正在发送唤醒包")
    else:
        log.warning("未收到 PS5 状态响应，仍将尝试发送唤醒包")

    registkey = read_chiaki_registkey(nickname)
    if not registkey:
        log.warning("未读取到 Chiaki 注册键，无法自动发送 PS5 唤醒包")
        return "standby" if state == "standby" else "unreachable"
    try:
        credential = int(registkey, 16)
    except ValueError:
        log.warning("Chiaki 注册键格式无效，无法自动发送 PS5 唤醒包")
        return "standby" if state == "standby" else "unreachable"
    if not _send_ps5_wakeup_packet(host, credential):
        log.warning("发送 PS5 唤醒包失败")
        return "standby" if state == "standby" else "unreachable"

    log.info("已发送 PS5 唤醒包，正在等待状态变为 ready")
    deadline = time() + max(1.0, wait_seconds)
    while time() < deadline:
        sleep(1.5)
        current = query_ps5_state(host, timeout=1.2)
        if current == "ready":
            log.info("PS5 已唤醒：ready，开始启动串流")
            return "ready"
    if state == "standby":
        log.warning("PS5 在 %.0f 秒内仍未变为 ready，本轮不启动黑屏串流", wait_seconds)
        return "standby"
    log.warning("唤醒后仍无法查询 PS5 状态，将继续尝试串流连接")
    return "unreachable"


def recover_frozen_chiaki(
    relink: Controller, initial_process: subprocess.Popen | None = None
) -> str | None:
    """Close failed sessions, wake standby PS5, and retry a fresh stream."""
    config = RECOVERY_CONFIG
    executable = Path(str(config.get("chiaki_exe", ""))).expanduser()
    nickname = str(config.get("nickname", "")).strip()
    host = str(config.get("host", "")).strip()
    if not executable.is_file() or not nickname or not host:
        log.error("卡死恢复未执行：Chiaki 路径、主机昵称或主机地址未完整配置")
        return None

    with AUTOMATION_INPUT_LOCK:
        relink.release_automation_inputs()
    max_attempts = 8
    process = initial_process
    for attempt in range(1, max_attempts + 1):
        if not relink.running or relink.paused:
            return None
        if process is None:
            old_pid = relink.close_bound_stream_process()
            if old_pid:
                log.warning("已关闭失败的 Chiaki 串流 PID=%s", old_pid)
            ps5_state = _ensure_ps5_ready(host, nickname)
            if ps5_state == "standby":
                log.warning("PS5 尚未唤醒，跳过本次串流启动")
                sleep(min(8.0, 1.5 * attempt))
                continue
            log.info("正在尝试连接（第 %d/%d 次）", attempt, max_attempts)
            try:
                process = subprocess.Popen(
                    [str(executable), "stream", nickname, host],
                    cwd=str(executable.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                log.error("启动 Chiaki 重连命令失败", exc_info=True)
                process = None
                sleep(min(8.0, 1.5 * attempt))
                continue
        relink.set_expected_process_id(process.pid)
        outcome = resume_reconnected_chiaki(relink, process)
        if outcome == "town_recovery_failed":
            log.error("主城任务承接失败，保留当前 Chiaki 窗口，不再关闭并重启串流")
            return None
        if outcome:
            return outcome
        relink.close_bound_stream_process(timeout=3.0)
        process = None
        sleep(min(8.0, 1.5 * attempt))
    log.error("已达到重连尝试上限，停止自动输入")
    return None


def stream_window_watchdog(
    relink: Controller,
    recovery_active: Callable[[], bool],
    begin_recovery: Callable[[], None],
    finish_recovery: Callable[[str | None], None],
) -> None:
    """Recover when the bound Chiaki stream window disappears entirely."""
    config = RECOVERY_CONFIG
    executable = Path(str(config.get("chiaki_exe", ""))).expanduser()
    if not executable.is_file() or not str(config.get("nickname", "")).strip() or not str(
        config.get("host", "")
    ).strip():
        log.debug("串流窗口守护未启用：重连配置不完整")
        return

    missing_since: float | None = None
    warned = False
    flow_deferred_logged = False
    while relink.running:
        active_flow = automation_flow_name()
        if relink.paused or recovery_active() or active_flow:
            if active_flow and not flow_deferred_logged:
                log.info(
                    "串流窗口守护延后恢复：自动化流程进行中（%s），不插入按键",
                    active_flow,
                )
                flow_deferred_logged = True
            missing_since = None
            warned = False
            sleep(STREAM_WINDOW_WATCHDOG_POLL_SECONDS)
            continue
        if flow_deferred_logged:
            log.info("自动化流程已结束，串流窗口守护重新确认窗口状态")
            flow_deferred_logged = False
        try:
            valid = relink.stream_binding_is_valid()
        except Exception:
            valid = False
            log.debug("串流窗口守护检查异常", exc_info=True)
        now = time()
        if valid:
            if warned:
                log.info("Chiaki 串流窗口已恢复，继续当前自动化阶段")
            missing_since = None
            warned = False
        else:
            if missing_since is None:
                missing_since = now
                log.warning("检测到 Chiaki 串流窗口不可用，开始等待确认")
            elif now - missing_since >= STREAM_WINDOW_LOST_CONFIRM_SECONDS:
                warned = True
                missing_since = None
                log.error(
                    "Chiaki 串流窗口已连续 %.1f 秒消失，停止输入并启动自动重连",
                    STREAM_WINDOW_LOST_CONFIRM_SECONDS,
                )
                with automation_flow("stream_recovery") as acquired:
                    if not acquired:
                        log.info(
                            "串流窗口恢复延后：流程刚刚取得输入所有权（%s）",
                            automation_flow_name(),
                        )
                        continue
                    begin_recovery()
                    try:
                        next_phase = recover_frozen_chiaki(relink)
                    except Exception:
                        log.error("串流窗口丢失后的重连流程异常", exc_info=True)
                        next_phase = None
                    finish_recovery(next_phase)
        sleep(STREAM_WINDOW_WATCHDOG_POLL_SECONDS)


def frozen_stream_watchdog(
    relink: Controller,
    battle_is_active: Callable[[], bool],
    begin_recovery: Callable[[], None],
    finish_recovery: Callable[[str | None], None],
) -> None:
    """Detect a frozen battle using visual activity and historical duration."""
    timeout = float(RECOVERY_CONFIG.get("freeze_seconds", 0.0) or 0.0)
    if timeout <= 0:
        return
    previous: np.ndarray | None = None
    activity_samples: deque[tuple[float, bool]] = deque()
    duration_warning_logged = False
    flow_deferred_logged = False
    while relink.running:
        active_flow = automation_flow_name()
        if relink.paused or not battle_is_active() or active_flow:
            if active_flow and not flow_deferred_logged:
                log.info(
                    "卡死画面守护延后恢复：自动化流程进行中（%s），不插入按键",
                    active_flow,
                )
                flow_deferred_logged = True
            previous = None
            activity_samples.clear()
            duration_warning_logged = False
            sleep(1.0)
            continue
        if flow_deferred_logged:
            log.info("自动化流程已结束，卡死画面守护重新开始采样")
            flow_deferred_logged = False
        try:
            current = frame_activity_signature(relink)
            now = time()
            if previous is not None:
                difference = aligned_frame_activity_score(previous, current)
                activity_samples.append(
                    (now, difference < FREEZE_LOW_ACTIVITY_SCORE)
                )
            previous = current

            # Keep enough history for both the user-configured static timeout
            # and the shorter duration-anomaly confirmation window.
            retention = max(timeout, FREEZE_DURATION_CONFIRM_SECONDS) + 10.0
            while activity_samples and activity_samples[0][0] < now - retention:
                activity_samples.popleft()

            def low_activity_evidence(
                window_seconds: float,
                required_ratio: float,
            ) -> tuple[bool, float]:
                samples = [
                    item for item in activity_samples
                    if item[0] >= now - window_seconds
                ]
                if len(samples) < 2:
                    return False, 0.0
                covered = samples[-1][0] - samples[0][0]
                minimum_coverage = max(0.0, window_seconds - 6.0)
                ratio = sum(1 for _, low in samples if low) / len(samples)
                return covered >= minimum_coverage and ratio >= required_ratio, ratio

            visual_frozen, visual_ratio = low_activity_evidence(
                timeout,
                FREEZE_BASE_LOW_ACTIVITY_RATIO,
            )

            duration_anomaly = False
            duration_threshold: float | None = None
            timing = (
                SESSION_STATS.battle_timing_snapshot()
                if SESSION_STATS is not None
                else None
            )
            if timing is not None and int(timing["completed"] or 0) > 0:
                current_duration = float(timing["current"] or 0.0)
                average = float(timing["average"] or 0.0)
                longest = float(timing["longest"] or 0.0)
                # A normal run can exceed the average. Require a meaningful
                # margin over both average and the previous longest battle.
                duration_threshold = max(
                    120.0,
                    average * 1.75,
                    longest * 1.25,
                )
                duration_anomaly = current_duration >= duration_threshold
                if duration_anomaly and not duration_warning_logged:
                    duration_warning_logged = True
                    log.warning(
                        "本场已运行 %s，超过历史异常阈值 %s；开始结合画面活动确认卡死",
                        _format_duration(current_duration),
                        _format_duration(duration_threshold),
                    )

            anomaly_window = min(timeout, FREEZE_DURATION_CONFIRM_SECONDS)
            duration_frozen, duration_ratio = low_activity_evidence(
                anomaly_window,
                FREEZE_DURATION_LOW_ACTIVITY_RATIO,
            )
            duration_frozen = duration_anomaly and duration_frozen

            if visual_frozen or duration_frozen:
                reason = (
                    f"低活动帧占比 {visual_ratio:.0%}，持续约 {timeout:.0f} 秒"
                    if visual_frozen
                    else (
                        f"战斗耗时超过历史阈值 {_format_duration(duration_threshold)}，"
                        f"最近 {anomaly_window:.0f} 秒低活动帧占比 {duration_ratio:.0%}"
                    )
                )
                log.warning("Chiaki 卡死组合判据成立（%s），开始串流恢复", reason)
                with automation_flow("stream_recovery") as acquired:
                    if not acquired:
                        log.info(
                            "卡死恢复延后：流程刚刚取得输入所有权（%s）",
                            automation_flow_name(),
                        )
                        continue
                    begin_recovery()
                    try:
                        next_phase = recover_frozen_chiaki(relink)
                    except Exception:
                        log.error("串流恢复流程异常，已停止自动输入", exc_info=True)
                        next_phase = None
                    finish_recovery(next_phase)
                previous = None
                activity_samples.clear()
                duration_warning_logged = False
        except Exception:
            log.debug("卡死画面监控异常（已忽略）", exc_info=True)
            previous = None
            activity_samples.clear()
            duration_warning_logged = False
        sleep(5.0)


def press_verified_result_continue(relink: Controller) -> bool:
    """Press Cross only while the result-screen ``继续`` prompt is stable."""
    if relink.paused:
        return False
    if not region_has_marker(relink, "继续", "result_continue"):
        return False

    # Confirm on a second captured frame. A fixed sleep alone can read the same
    # cached frame when capture stalls, so use the capture serial as the
    # synchronization boundary.
    serial, _ = relink.capture_frame_state()
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
    ):
        log.debug("结算‘继续’未等到新捕获帧，不发送 Cross")
        return False
    if relink.paused:
        return False
    if not region_has_marker(relink, "继续", "result_continue"):
        log.debug("结算‘继续’仅单帧出现，不发送 Cross")
        return False

    with AUTOMATION_INPUT_LOCK:
        if not relink.running:
            return False
        relink.press(CROSS_KEY)
    # Japanese clients can show a second "retry this quest?" dialog after
    # 次へ. Its labels are sometimes completely lost by OCR, while the upper
    # yes row remains visibly highlighted. Keep a short, one-transition guard
    # so that the next loop can accept that row without enabling the visual
    # fallback during ordinary town HUD frames.
    if (
        getattr(relink, "ui_language_mode", None) == "ja"
        or getattr(relink, "detected_ui_language", None) == "ja"
    ):
        try:
            relink._japanese_result_confirmation_deadline = time() + 8.0
        except (AttributeError, TypeError):
            pass
    log.info("识别到右下角‘继续’，发送第 1 次 Cross")
    # On Japanese low-resolution streams the first result summary can remain
    # visible for another transition. Re-check each fresh frame and provide a
    # bounded follow-up Cross, but never press again after the retry control is
    # visible because Square/gold verification must own that page.
    if (
        "ja" in relink.ui_language_candidates()
        and not getattr(relink, "paused", False)
    ):
        for attempt in range(2, JAPANESE_RESULT_CONTINUE_MAX_PRESSES + 1):
            serial, _ = relink.capture_frame_state()
            if not relink.wait_for_fresh_capture(
                serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
            ):
                break
            if relink.paused or result_repeat_control_is_visible(relink):
                break
            if not region_has_marker(relink, "继续", "result_continue"):
                break
            with AUTOMATION_INPUT_LOCK:
                if not relink.running:
                    break
                relink.press(CROSS_KEY)
            log.info("结算页仍未进入重战页，发送第 %d 次 Cross", attempt)
            sleep(0.75)
    else:
        sleep(0.75)
    return True


def press_confirmed_repeat_continue(relink: Controller) -> bool:
    """Advance a confirmed retry page through the shared result-progress step."""
    if relink.paused or not relink.running:
        return False
    serial, _ = relink.capture_frame_state()
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
    ):
        log.debug("自动重战确认后未等到新捕获帧，暂不发送 Cross")
        return False
    if not result_progress_prompt_is_visible(relink):
        log.debug("自动重战确认后尚未识别到右下推进提示，保持结算页等待")
        return False
    with AUTOMATION_INPUT_LOCK:
        if relink.paused or not relink.running:
            return False
        relink.press(CROSS_KEY)
    log.info("自动重战已确认开启，发送一次 Cross 进入下一轮")
    sleep(0.75)

    # On the Japanese low-resolution page the first Cross can be consumed by
    # the result transition without opening the retry confirmation. If the
    # same verified progress prompt and confirmed retry page remain visible,
    # send exactly one follow-up Cross. The normal highlight flow owns the
    # subsequent yes/no selection.
    if "ja" not in relink.ui_language_candidates():
        return True
    serial, _ = relink.capture_frame_state()
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.6
    ):
        return True
    if relink.paused or not relink.running or detect_stable_battle_hud(relink):
        return True
    if japanese_settlement_highlight_dialog_active(relink):
        return True
    if (
        result_progress_prompt_is_visible(relink)
        and
        japanese_retry_page_is_visible(relink)
        and result_retry_state(relink) == "enabled"
    ):
        with AUTOMATION_INPUT_LOCK:
            if relink.paused or not relink.running:
                return True
            relink.press(CROSS_KEY)
        try:
            relink._japanese_result_confirmation_deadline = time() + 8.0
        except (AttributeError, TypeError):
            pass
        log.info("重战页首次 Cross 后尚未出现续战确认，补发第 2 次 Cross")
        sleep(0.75)
    return True


def detect_stable_result_ui(relink: Controller) -> str | None:
    """Detect the real result controls using bottom-left/right UI prompts."""
    if relink.paused:
        return None

    continue_found = region_has_marker(relink, "继续", "result_continue")
    repeat_found = region_has_marker(relink, "再次", "result_retry_any")

    # Two independent controls on the same result frame are already strong
    # evidence.  If only one OCR crop matches, verify it on a fresh frame.
    if continue_found and repeat_found:
        return "继续+再次挑战"
    if not continue_found and not repeat_found:
        return None

    serial, _ = relink.capture_frame_state()
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
    ):
        return None
    if relink.paused:
        return None
    if continue_found and region_has_marker(
        relink, "继续", "result_continue"
    ):
        return "继续"
    if repeat_found:
        if region_has_marker(relink, "再次", "result_retry_any"):
            return "再次挑战/撤销"
    return None


def failed_repeat_has_left_result_screen(relink: Controller) -> bool:
    """Confirm a failed repeat toggle has genuinely left result processing.

    This narrow exception is used only after the repeat toggle exhausted its
    bounded attempts. It avoids making a manual battle exit wait three minutes
    before the existing town recovery can inspect the screen.
    """
    if detect_stable_result_ui(relink) is not None:
        return False
    serial, _ = relink.capture_frame_state()
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
    ):
        return False
    if detect_stable_result_ui(relink) is not None:
        return False
    return unexpected_town_recovery_signal(relink) == "timer_missing_no_battle_hud"


def read_japanese_result_retry_text(relink: Controller) -> str:
    """Read the lower-left quarter containing the Japanese retry button.

    The retry page is stable by layout, but a narrow recognition-only line can
    return glyph variants such as 再排殺する. Use the lower-left quarter with
    text detection so the button is isolated from the right-side 次へ prompt.
    """
    frame = relink.screenshot().convert("RGB")
    width, height = frame.size
    crop = frame.crop(
        (
            0,
            int(height * 0.72),
            int(width * 0.50),
            height,
        )
    )
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    # This rare result-only OCR pass benefits from a larger recognition
    # canvas on 540p streams. Three-times Lanczos preserves the gold outlined
    # Japanese glyphs; sharpening was tested and made them less reliable.
    crop = crop.resize((max(1, crop.width * 3), max(1, crop.height * 3)), resampling)
    result = relink.ocr(crop, confidence=0.40, language="ja")
    if not isinstance(result, list):
        return ""
    return "".join(str(item.get("text", "")) for item in result)


def _japanese_retry_text_state(text: str) -> str | None:
    """Classify noisy Japanese retry OCR after the page guard is satisfied."""
    compact = "".join(str(text or "").split())
    if not compact:
        return None
    # The cancel form is the strongest signal that Square already enabled the
    # toggle. Include common 540P katakana/kanji substitutions.
    if any(
        marker in compact
        for marker in (
            "キャンセル",
            "キヤンセル",
            "ャンセル",
            "撤销",
            "取消",
            "撤",
        )
    ):
        return "enabled"
    # The result-only crop is already proven to be the lower-left retry bar.
    # Within that crop, a noisy 再 + challenge glyph is sufficient to classify
    # the available form without requiring the entire Japanese sentence.
    if "再" in compact and any(
        glyph in compact for glyph in ("挑", "戦", "规", "規", "排", "製", "殺", "杀")
    ):
        return "available"
    if len(compact) >= 2 and "戦" in compact:
        return "available"
    return None


def result_retry_state(relink: Controller) -> str | None:
    """Return whether result-page auto-repeat is available or already enabled."""
    # On a real Japanese Controller, the fixed gold crop is meaningful only
    # after the lower-left retry control is visible. Before that page, the
    # same coordinates contain result rewards and background highlights.
    if (
        isinstance(relink, Controller)
        and "ja" in relink.ui_language_candidates()
        and not (
            japanese_retry_page_is_visible(relink)
        )
    ):
        log.debug("日文总评页尚未出现左下 PS5 圆形按钮或 MSP 兜底标记，跳过重战 OCR/金色检测")
        return None
    ocr_state: str | None = None
    texts = read_region_texts(relink, "再次")
    if "ja" in relink.ui_language_candidates():
        japanese_text = texts.get("ja", "")
        if not (
            _text_matches_marker(japanese_text, "ja", "result_retry_available")
            or _text_matches_marker(japanese_text, "ja", "result_retry_cancel")
        ):
            try:
                fallback_text = read_japanese_result_retry_text(relink)
                if fallback_text:
                    texts["ja"] = fallback_text
                    log.debug("日文重战按钮切换到中央底部 OCR 兜底：%r", fallback_text)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                log.debug("日文中央底部重战按钮 OCR 失败", exc_info=True)
    for language in relink.ui_language_candidates():
        text = texts.get(language, "")
        # Enabled text contains much of the available label in both languages,
        # so the cancel form must always be evaluated first.
        if _text_matches_marker(text, language, "result_retry_cancel"):
            relink.confirm_ui_language(language, "result_retry_cancel")
            ocr_state = "enabled"
            break
        if _text_matches_marker(text, language, "result_retry_available"):
            relink.confirm_ui_language(language, "result_retry_available")
            ocr_state = "available"
            break
        if language == "ja":
            fuzzy_state = _japanese_retry_text_state(text)
            if fuzzy_state is not None:
                relink.confirm_ui_language(language, "result_retry_fuzzy")
                ocr_state = fuzzy_state
                log.info("日文重战按钮宽匹配命中：%r -> %s", text, fuzzy_state)
                break

    try:
        indicator_gold = result_repeat_indicator_is_stably_gold(relink)
    except Exception:
        log.debug("自动重战金色标记检测暂时不可用，沿用 OCR 状态", exc_info=True)
        indicator_gold = False

    if ocr_state == "enabled":
        if indicator_gold:
            log.info("OCR 与金色自动重战标记均确认自动重战已开启")
            return "enabled"
        # OCR can switch to キャンセル before the game finishes rendering the
        # gold state. Never let text alone advance the result page: Square is
        # a toggle, and a false positive here sends the player back to town.
        log.debug("自动重战 OCR 已显示撤销/キャンセル，但金色标记尚未连续确认")
        return None
    if ocr_state == "available":
        if indicator_gold:
            log.info("OCR 显示可开启但金色标记已存在，按补充视觉证据判定已开启")
            return "enabled"
        return "available"

    # The icon never identifies a result page by itself. At this point the
    # caller is already in the result phase, so it may supplement a transient
    # OCR miss by confirming the enabled state without sending a toggle.
    # Gold is only an on-page verification signal. It must never discover the
    # retry page when OCR has not identified a retry label.
    return None


def ensure_auto_repeat_enabled(
    relink: Controller, initial_state: str | None = None
) -> bool:
    """Enable auto-repeat with one verified retry for a dropped Square input."""
    state = initial_state if initial_state is not None else result_retry_state(relink)
    if state == "enabled":
        return True
    if state != "available":
        return False

    for attempt in range(1, RESULT_REPEAT_MAX_TOGGLE_ATTEMPTS + 1):
        serial, _ = relink.capture_frame_state()
        with AUTOMATION_INPUT_LOCK:
            if relink.paused or not relink.running:
                return False
            relink.press(SQUARE_KEY, interval=0.3)
        log.info(
            "识别到自动重战尚未开启，发送第 %d/%d 次 Square 并等待状态确认",
            attempt,
            RESULT_REPEAT_MAX_TOGGLE_ATTEMPTS,
        )
        relink.wait_for_fresh_capture(serial, timeout=1.5)

        # Require two enabled samples. A single gold animation frame must not
        # be enough to advance the result page.
        enabled_samples = 0
        deadline = monotonic() + RESULT_REPEAT_CONFIRM_TIMEOUT_SECONDS
        while monotonic() < deadline:
            if relink.paused or not relink.running:
                return False
            state = result_retry_state(relink)
            if state == "enabled":
                enabled_samples += 1
                if enabled_samples >= RESULT_REPEAT_CONFIRM_SAMPLES:
                    log.info("已连续确认左下角金色/撤销/キャンセル状态，自动重战开启成功")
                    return True
            else:
                enabled_samples = 0
            sleep(BATTLE_HUD_CONFIRM_INTERVAL_SECONDS)

        if attempt >= RESULT_REPEAT_MAX_TOGGLE_ATTEMPTS:
            break

        # The only safe basis for a retry is two fresh observations that the
        # same result control remains available. If the first Square actually
        # worked but OCR is transient, this check prevents a toggle-back.
        sleep(RESULT_REPEAT_RETRY_SETTLE_SECONDS)
        first_retry_state = result_retry_state(relink)
        sleep(BATTLE_HUD_CONFIRM_INTERVAL_SECONDS)
        second_retry_state = result_retry_state(relink)
        if first_retry_state != "available" or second_retry_state != "available":
            log.warning("Square 后状态不稳定，取消补发以避免反向关闭自动重战")
            return False
        log.warning("Square 后连续两帧仍显示未开启，补发一次受控 Square")

    log.error("两次 Square 后仍未连续确认自动重战已开启；停止结算推进等待人工处理")
    return False


def settlement_confirmation_selection(relink: Controller) -> str | None:
    """Read the selected row in the result confirmation dialog by its blue bar."""
    frame = relink.screenshot()
    pixels = np.asarray(frame, dtype=np.float32)
    height, width = pixels.shape[:2]
    row_half = max(5, int(height * 0.018))
    x0, x1 = int(width * 0.35), int(width * 0.65)

    def blue_score(center_y: float) -> float:
        center = int(height * center_y)
        y0 = max(0, center - row_half)
        y1 = min(height, center + row_half + 1)
        band = pixels[y0:y1, x0:x1]
        return float((band[:, :, 2] - (band[:, :, 0] + band[:, :, 1]) * 0.5).mean())

    # The Japanese 540P confirmation modal is rendered slightly lower than
    # the old fixed ratios.  Keep the bands narrow, but sample the actual
    # row centers used by both the 540P stream and the larger client sizes.
    # The fallback pair is intentionally close to the original geometry so
    # this remains scoped to the two-row result confirmation dialog.
    candidates = (
        (blue_score(0.625), blue_score(0.677)),
        (blue_score(0.640), blue_score(0.700)),
    )
    # Select one complete row-pair, rather than mixing the best individual
    # bands from different geometries.
    delta = max((yes - no for yes, no in candidates), key=abs)
    if delta >= 6.0:
        return "yes"
    if delta <= -6.0:
        return "no"
    return None


# ============================================================
#  Relink 战斗逻辑
# ============================================================
def relink_battle(relink: Controller) -> None:
    """Run the normal rebattle state machine with unconditional cleanup."""
    try:
        _relink_battle_impl(relink)
    except Exception:
        log.error("自动重战状态机异常，停止自动输入", exc_info=True)
        try:
            relink.request_shutdown("state_machine_exception")
        except Exception:
            log.debug("记录状态机异常停止原因失败", exc_info=True)
    finally:
        with AUTOMATION_INPUT_LOCK:
            try:
                relink.release_automation_inputs()
            except Exception:
                log.error("自动重战退出时释放输入失败", exc_info=True)
        clear_automation_flow_state("自动重战状态机退出")


def _relink_battle_impl(relink: Controller) -> None:
    """单次战斗 → 结算 → 再次挑战 的完整循环"""
    global INITIAL_AUTOMATION_PHASE
    battle_active = False
    phase = INITIAL_AUTOMATION_PHASE if INITIAL_AUTOMATION_PHASE in {
        "battle_wait",
        "result",
        "startup_probe",
    } else "startup_probe"
    INITIAL_AUTOMATION_PHASE = "startup_probe"
    battle_number = 1
    repeat_armed = False
    repeat_verification_required = False
    repeat_activation_failed = False
    # Square is a toggle. ``ensure_auto_repeat_enabled`` owns its bounded,
    # state-verified retry; this guard prevents restarting that sequence on
    # every result-loop poll.
    repeat_toggle_sent = False
    repeat_continue_sent = False
    # Resume is a synchronization boundary: the OCR/state loop must get one
    # chance to classify a result screen before the movement worker can send
    # another forward pulse.  A dict keeps this shared value writable from the
    # worker thread without introducing another lock around every poll.
    resume_guard = {"until": 0.0}
    settlement_navigation = {"no_to_yes_sent": False}
    recovery_state = {"active": False}
    unrecognized_since: float | None = None
    timer_missing_since: float | None = None
    town_recovery_attempts = 0
    main_pause_generation = relink.pause_generation
    log.info("自动重战状态机已启动，初始阶段: %s", phase)

    def begin_recovery() -> None:
        nonlocal battle_active, phase
        battle_active = False
        phase = "recovery"
        recovery_state["active"] = True
        resume_guard["until"] = float("inf")
        with AUTOMATION_INPUT_LOCK:
            relink.release_automation_inputs()
        if SESSION_STATS is not None:
            SESSION_STATS.discard_interrupted_battle()
        log.warning("阶段切换: battle_active -> recovery；已释放全部自动化输入")

    def finish_recovery(next_phase: str | None) -> None:
        nonlocal phase
        with AUTOMATION_INPUT_LOCK:
            relink.release_automation_inputs()
        recovery_state["active"] = False
        if next_phase in ("battle_wait", "result"):
            phase = next_phase
            resume_guard["until"] = time() + 2.0
            log.info("串流恢复完成，进入阶段: %s", next_phase)
            return
        phase = "recovery_failed"
        resume_guard["until"] = float("inf")
        log.error("串流恢复未确认安全画面，自动输入保持停止；请按 F2 停止后人工处理")

    def reset_unrecognized_transition() -> None:
        nonlocal unrecognized_since, timer_missing_since
        unrecognized_since = None
        timer_missing_since = None

    def recover_after_unrecognized_transition(reason: str) -> bool:
        """Probe the town only after a sustained missing battle timer.

        An unclassified frame is not enough: loading and fast result
        transitions can temporarily contain neither battle nor result text.
        The recovery clock starts only when the right-half battle countdown
        has also been absent, and any visible countdown cancels that clock.
        """

        nonlocal phase, unrecognized_since, timer_missing_since
        nonlocal town_recovery_attempts
        if unexpected_town_recovery_signal(relink) != (
            "timer_missing_no_battle_hud"
        ):
            reset_unrecognized_transition()
            return False
        now = time()
        if unrecognized_since is None:
            unrecognized_since = now
        if timer_missing_since is None:
            timer_missing_since = now
        if now - timer_missing_since < UNEXPECTED_TOWN_RECOVERY_DELAY_SECONDS:
            return False
        unrecognized_since = None
        timer_missing_since = None
        town_recovery_attempts += 1
        if town_recovery_attempts > UNEXPECTED_TOWN_RECOVERY_MAX_ATTEMPTS:
            phase = "recovery_failed"
            log.error("主城续战已连续失败 %d 次，停止自动输入", town_recovery_attempts - 1)
            return True
        phase = "recovery"
        outcome = recover_unexpected_town_state(relink, reason=reason)
        if outcome in {"battle_wait", "result"}:
            phase = outcome
            town_recovery_attempts = 0
            resume_guard["until"] = time() + 2.0
            reset_unrecognized_transition()
        else:
            phase = "recovery_failed"
            resume_guard["until"] = float("inf")
        return True

    def enter_battle() -> None:
        nonlocal battle_active, phase, repeat_armed, repeat_continue_sent
        nonlocal repeat_verification_required, repeat_toggle_sent
        nonlocal repeat_activation_failed
        nonlocal town_recovery_attempts
        with AUTOMATION_INPUT_LOCK:
            # A previous result/stop transition must never leak a held axis
            # into the next battle entry.
            relink.release_automation_inputs()
        battle_active = True
        phase = "battle_active"
        reset_unrecognized_transition()
        town_recovery_attempts = 0
        repeat_armed = False
        repeat_continue_sent = False
        repeat_verification_required = False
        repeat_toggle_sent = False
        repeat_activation_failed = False
        _stats_start_battle()
        log.info("阶段切换: battle_wait/result -> battle_active")
        with AUTOMATION_INPUT_LOCK:
            relink.press(L2_KEY)

    def transition_to_result() -> None:
        """Move to result handling exactly once and finish the battle stats."""
        nonlocal battle_active, phase, battle_number, repeat_armed, repeat_continue_sent
        nonlocal repeat_verification_required, repeat_toggle_sent
        nonlocal repeat_activation_failed
        settlement_navigation["cross_sent"] = False
        settlement_navigation["no_to_yes_sent"] = False
        if phase == "battle_active":
            battle_active = False
            phase = "result"
            repeat_toggle_sent = False
            repeat_verification_required = False
            repeat_activation_failed = False
            repeat_continue_sent = False
            reset_unrecognized_transition()
            with AUTOMATION_INPUT_LOCK:
                relink.release_automation_inputs()
            duration = _stats_finish_battle()
            log.info("阶段切换: battle_active -> result")
            log.info(
                "--- 第 %d 场战斗结算%s ---",
                battle_number,
                f"，本场耗时 {_format_duration(duration)}" if duration is not None else "",
            )
            battle_number += 1
            repeat_armed = False
            repeat_continue_sent = False
        elif phase != "result":
            phase = "result"
            repeat_toggle_sent = False
            repeat_verification_required = False
            repeat_activation_failed = False
            repeat_continue_sent = False
            with AUTOMATION_INPUT_LOCK:
                relink.release_automation_inputs()

    def battle_loop() -> None:
        """后台线程：战斗中分段保持向前接近敌人。

        The upstream local-PC script used a middle-mouse click for lock-on.
        Its controller equivalent is L2, not the DS4 Touchpad. Target locking
        is handled by L2 at battle entry and by the focus watchdog instead.
        """
        worker_pause_generation = relink.pause_generation
        while True:
            current_generation = relink.pause_generation
            if current_generation != worker_pause_generation:
                worker_pause_generation = current_generation
                if not relink.paused:
                    # Handle a rapid pause/resume before the OCR loop gets a
                    # scheduling turn.  The main loop will still perform the
                    # result/continue probe; this only blocks movement here.
                    resume_guard["until"] = max(
                        resume_guard["until"], time() + 1.25
                    )
                    with AUTOMATION_INPUT_LOCK:
                        relink.release_automation_inputs()
            if (
                relink.paused
                or time() < resume_guard["until"]
                or not battle_active
                or not relink.running
            ):
                sleep(0.1)
                continue
            try:
                # Use short press/release pulses instead of a two-second held
                # axis. A missed phase frame can then never leave W/Up latched
                # while the result screen or town is already visible.
                with AUTOMATION_INPUT_LOCK:
                    if relink.paused or not battle_active or not relink.running:
                        continue
                    relink.press(LEFT_STICK_UP_KEY)
                sleep(0.15)
            except Exception:
                log.debug("战斗推进按键异常（已释放前进）", exc_info=True)
                with AUTOMATION_INPUT_LOCK:
                    relink.release_automation_inputs()

    battle_thread = threading.Thread(target=battle_loop, daemon=True)
    battle_thread.start()
    focus_watchdog_thread = threading.Thread(
        target=focus_watchdog,
        args=(relink, lambda: battle_active),
        daemon=True,
    )
    focus_watchdog_thread.start()
    recovery_thread = threading.Thread(
        target=frozen_stream_watchdog,
        args=(
            relink,
            lambda: battle_active and not recovery_state["active"],
            begin_recovery,
            finish_recovery,
        ),
        daemon=True,
    )
    recovery_thread.start()
    window_recovery_thread = threading.Thread(
        target=stream_window_watchdog,
        args=(
            relink,
            lambda: recovery_state["active"],
            begin_recovery,
            finish_recovery,
        ),
        daemon=True,
    )
    window_recovery_thread.start()

    while relink.running:
        if relink.paused:
            sleep(0.2)
            continue
        if phase == "startup_probe":
            outcome = probe_initial_automation_screen(relink)
            if outcome in {"battle_wait", "result"}:
                phase = outcome
                log.info("启动环境识别完成，进入阶段: %s", phase)
            else:
                # A minimized/recreated stream has no reliable scene to
                # classify yet. Keep probing so the capture watchdog can
                # restore the window before town/battle/result routing.
                log.warning("启动环境暂未确认，保持启动探测并等待新画面/窗口恢复")
                sleep(1.0)
            continue
        if phase in ("recovery", "recovery_failed"):
            sleep(0.2)
            continue

        current_generation = relink.pause_generation
        if current_generation != main_pause_generation:
            # Do not let a stale pre-pause phase resume directly into movement.
            # This also covers a result screen that appeared while the user
            # paused: ``继续`` is checked before the next W pulse is allowed.
            main_pause_generation = current_generation
            resume_guard["until"] = time() + 1.25
            with AUTOMATION_INPUT_LOCK:
                relink.release_automation_inputs()
            log.info("暂停已解除：先重新检查结算标记，再恢复战斗推进")
            if phase == "battle_active":
                result_marker = detect_stable_result_ui(relink)
                if result_marker:
                    log.info("恢复后识别到结算控件：%s", result_marker)
                    transition_to_result()
                    continue
            # For the normal result phase, fall through immediately so the
            # verified two-frame ``继续`` check can run without waiting a tick.
            if phase == "result":
                if press_verified_result_continue(relink):
                    # The next confirmation window belongs to a new result
                    # cycle; do not inherit the previous window's Cross guard.
                    settlement_navigation["cross_sent"] = False
                    settlement_navigation["no_to_yes_sent"] = False
                    continue
        if phase == "battle_wait":
            if detect_stable_battle_hud(relink):
                enter_battle()
                continue
            # Allow starting/restarting the tool while a result screen is open.
            result_marker = detect_stable_result_ui(relink)
            if result_marker:
                phase = "result"
                reset_unrecognized_transition()
                with AUTOMATION_INPUT_LOCK:
                    relink.release_automation_inputs()
                log.info("识别到结算控件 %s，恢复到结算阶段", result_marker)
                continue
            if recover_after_unrecognized_transition(
                "battle_wait 阶段持续没有识别到战斗或结算，且无战斗倒计时"
            ):
                continue
            sleep(1.0)
            continue

        if phase == "battle_active":
            result_marker = detect_stable_result_ui(relink)
            if result_marker:
                log.info("识别到结算控件：%s", result_marker)
                transition_to_result()
                continue
            if recover_after_unrecognized_transition(
                "battle_active 阶段持续没有识别到战斗或结算，且无战斗倒计时"
            ):
                continue
            sleep(1.0)
            continue

        # Auto-repeat can transition directly into the next battle.  Once the
        # jump marker appears, result input is disabled before any other action.
        if detect_stable_battle_hud(relink):
            enter_battle()
            continue

        # This ten-battle modal overlays the retry page and leaves its gold
        # control visible underneath.  It must win over every retry-state
        # probe, otherwise the enabled control masks the required Up + Cross.
        if japanese_retry_confirmation_present(relink):
            handle_japanese_retry_confirmation(relink)
            log.info("识别到日文十场再挑战确认，优先选择‘はい’后继续")
            sleep(1.0)
            continue

        # After 次へ, the ten-battle confirmation can cover the lower-left
        # retry control. If its Japanese title is lost by 540P OCR, the
        # short post-Cross window plus the blue selected row is the stronger
        # signal. Handle it before the Square/toggle probe.
        if (
            getattr(relink, "_japanese_result_confirmation_deadline", 0.0) > time()
            and japanese_settlement_highlight_dialog_active(relink)
        ):
            handle_japanese_settlement_highlight(relink)
            log.info("日文结算 Cross 后确认窗口已按高亮处理，跳过重战页 Square 分支")
            sleep(1.0)
            continue

        if chinese_settlement_confirmation_present(relink):
            handle_settlement_confirmation(relink, settlement_navigation)
            log.info("严格识别到中文结算确认窗口，按‘是/否’高亮处理确认")
            sleep(1.0)
            continue

        # Low-resolution Japanese retry pages can also produce OCR that looks
        # like the generic bottom-right Continue prompt. Probe the lower-left
        # retry bar before any generic settlement/Cross branch, so Square is
        # never bypassed by a false Continue match.
        retry_state = result_retry_state(relink)
        if (
            "ja" in relink.ui_language_candidates()
            and not repeat_armed
            and not repeat_activation_failed
            and retry_state is None
            and japanese_retry_page_is_visible(relink)
        ):
            # The visual fallback owns pages whose retry text remains unreadable.
            # After Square, keep polling the gold state instead of waiting for
            # OCR to discover キャンセル. Otherwise a successful toggle can
            # leave this branch stalled until the three-minute town fallback.
            if result_repeat_indicator_is_stably_gold(relink):
                repeat_armed = True
                repeat_verification_required = False
                log.info("日文低分辨率重战页红框已确认金色，进入受控 Cross 推进")
                if not repeat_continue_sent:
                    repeat_continue_sent = press_confirmed_repeat_continue(relink)
                    if repeat_continue_sent:
                        settlement_navigation["cross_sent"] = False
                        settlement_navigation["no_to_yes_sent"] = False
                        reset_unrecognized_transition()
                sleep(0.5)
                continue
            if not repeat_toggle_sent:
                serial, _ = relink.capture_frame_state()
                with AUTOMATION_INPUT_LOCK:
                    if relink.paused or not relink.running:
                        continue
                    relink.press(SQUARE_KEY, interval=0.3)
                repeat_toggle_sent = True
                repeat_verification_required = True
                log.info(
                    "日文低分辨率重战页已由左下视觉区域确认，未依赖文字 OCR，发送一次 Square"
                )
                relink.wait_for_fresh_capture(serial, timeout=1.5)
            else:
                log.debug("日文低分辨率重战页已发送 Square，继续等待金色状态")
            sleep(0.5)
            continue

        japanese_retry_precheck = None
        if "ja" in relink.ui_language_candidates():
            try:
                japanese_retry_precheck = result_retry_state(relink)
                if japanese_retry_precheck in {"available", "enabled"}:
                    log.debug(
                        "日文结算预检已命中自动重战状态：%s，跳过通用结算确认分支",
                        japanese_retry_precheck,
                    )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                log.debug("日文结算自动重战预检失败", exc_info=True)
        center_texts = (
            {}
            if japanese_retry_precheck in {"available", "enabled"}
            else read_region_texts(relink, "结算")
        )
        challenge_language = _match_marker_language(
            relink, center_texts, "challenge_confirmation"
        )
        if challenge_language:
            if challenge_language == "ja":
                if _text_matches_marker(
                    center_texts.get("ja", ""), "ja", "challenge_confirmation_retry"
                ):
                    handle_japanese_retry_confirmation(relink)
                    log.info("识别到日文再挑戦確認界面，按上后确认‘はい’")
                    sleep(1.0)
                    continue
                # Japanese labels vary (はい/いいえ, 決定/キャンセル, etc.).
                # The blue highlight position is language-independent, so use
                # the same fresh-frame no -> yes verification as the normal
                # result confirmation instead of blindly accepting the top row.
                handle_settlement_confirmation(relink, settlement_navigation)
                log.info("识别到日文挑战确认界面，按高亮位置处理确认")
            else:
                settlement_navigation["no_to_yes_sent"] = False
                with AUTOMATION_INPUT_LOCK:
                    relink.press(LEFT_STICK_UP_KEY)
                    relink.press(CROSS_KEY)
                log.info("识别到挑战确认界面，选择并确认")
            sleep(1.0)
            continue
        if _match_marker_language(relink, center_texts, "settlement"):
            if _match_marker_language(relink, center_texts, "confirmation"):
                handle_settlement_confirmation(relink, settlement_navigation)
            else:
                settlement_navigation["no_to_yes_sent"] = False
                with AUTOMATION_INPUT_LOCK:
                    relink.press(CROSS_KEY)
                log.info("识别到结算确认界面，发送一次 Cross")
            sleep(1.0)
            continue

        # Enable repeat before advancing the result page.  The video shows
        # both controls at once: left-bottom '再次挑战' and right-bottom
        # '继续'.  Toggling repeat first prevents the next page from returning
        # to town before the automatic rematch is armed.
        retry_state = result_retry_state(relink)
        if retry_state == "available":
            if not repeat_toggle_sent:
                repeat_toggle_sent = True
                repeat_verification_required = True
                if ensure_auto_repeat_enabled(relink, retry_state):
                    repeat_armed = True
                    repeat_verification_required = False
                else:
                    # Do not leave the result loop locked forever. The game
                    # may already have returned to town after the failed
                    # toggle, so the normal no-HUD/no-timer recovery probe
                    # must be allowed to run.
                    # Do not advance this Japanese result page without the
                    # required gold confirmation. Keep polling the visible
                    # control instead of silently falling through to Cross.
                    repeat_activation_failed = False
                    repeat_verification_required = True
                    log.error(
                        "自动重战开关未确认金色状态，保持结算页并继续等待"
                    )
            else:
                log.debug("本页自动重战开关已完成受控尝试，不再重启切换序列")
            if not repeat_armed and not repeat_activation_failed:
                # Never advance this result page before the one toggle has been
                # positively verified. A stale available label cannot justify a
                # second Square.
                sleep(0.5)
                continue
        if retry_state == "enabled":
            if not repeat_armed:
                log.info("检测到自动重战已处于开启状态")
            repeat_armed = True
            repeat_verification_required = False
            if "ja" in relink.ui_language_candidates() and not repeat_continue_sent:
                repeat_continue_sent = press_confirmed_repeat_continue(relink)
                if repeat_continue_sent:
                    settlement_navigation["cross_sent"] = False
                    settlement_navigation["no_to_yes_sent"] = False
                    reset_unrecognized_transition()
                    continue

        if repeat_verification_required and not repeat_armed and not repeat_activation_failed:
            # This page has already exposed its auto-repeat control. Do not let
            # a transient unreadable frame advance to town before activation is
            # positively verified.  It must still join the long no-HUD watchdog:
            # if the game has already returned to town underneath a stale
            # result frame, resume the normal state search instead of waiting
            # in this branch forever.
            if recover_after_unrecognized_transition(
                "结算自动重战状态确认长期无进展，巡检战斗/结算/主城状态"
            ):
                continue
            sleep(0.5)
            continue

        # On Japanese result pages, never let the bottom-right 次へ path skip
        # the lower-left 再挑戦する control. If this narrow OCR is transiently
        # unreadable, stay on the page and retry rather than silently falling
        # back to the three-minute town recovery route.
        if (
            "ja" in relink.ui_language_candidates()
            and not repeat_armed
            and not repeat_activation_failed
            and retry_state is None
            and japanese_retry_page_is_visible(relink)
        ):
            if not repeat_toggle_sent:
                serial, _ = relink.capture_frame_state()
                with AUTOMATION_INPUT_LOCK:
                    if relink.paused or not relink.running:
                        continue
                    relink.press(SQUARE_KEY, interval=0.3)
                repeat_toggle_sent = True
                repeat_verification_required = True
                log.info(
                    "日文低分辨率重战页已由左下视觉区域确认，未依赖文字 OCR，发送一次 Square"
                )
                relink.wait_for_fresh_capture(serial, timeout=1.5)
            else:
                log.debug("日文低分辨率重战页已发送 Square，继续等待金色状态")
            now = time()
            last_notice = float(
                getattr(relink, "_japanese_retry_wait_notice_at", 0.0) or 0.0
            )
            if now - last_notice >= 5.0:
                log.warning(
                    "日文结算页尚未识别左下自动重战状态，暂不发送 Cross，继续检测 再挑戦する/キャンセル"
                )
                setattr(relink, "_japanese_retry_wait_notice_at", now)
            if recover_after_unrecognized_transition(
                "日文结算重战控件长期无进展，巡检战斗/结算/主城状态"
            ):
                continue
            sleep(0.5)
            continue

        # Result phase: if Japanese OCR misses 次へ, use the visual prompt.
        # The helper blocks this path on the retry page until gold is armed.
        if "ja" in relink.ui_language_candidates() and press_visual_result_continue(
            relink, repeat_armed
        ):
            settlement_navigation["cross_sent"] = False
            settlement_navigation["no_to_yes_sent"] = False
            reset_unrecognized_transition()
            continue

        # Result phase: Cross is permitted only after two consecutive OCR
        # matches of the bottom-right '继续' prompt.
        if press_verified_result_continue(relink):
            settlement_navigation["cross_sent"] = False
            settlement_navigation["no_to_yes_sent"] = False
            reset_unrecognized_transition()
            continue

        if repeat_activation_failed and failed_repeat_has_left_result_screen(relink):
            log.warning(
                "自动重战开关失败后已离开结算且无战斗倒计时，立即巡检主城并尝试续战"
            )
            phase = "recovery"
            outcome = recover_unexpected_town_state(
                relink, reason="结算自动重战开关失败后离开结算页面"
            )
            if outcome in {"battle_wait", "result"}:
                phase = outcome
                repeat_activation_failed = False
                town_recovery_attempts = 0
                reset_unrecognized_transition()
            else:
                phase = "recovery_failed"
            continue

        if recover_after_unrecognized_transition(
            "结算阶段未识别到继续/再次挑战控件，且无战斗倒计时"
        ):
            continue
        sleep(1.0)

    battle_active = False
    with AUTOMATION_INPUT_LOCK:
        relink.release_automation_inputs()


def relink_battle_silent(relink: Controller):
    """Run the silent rebattle state machine with unconditional cleanup."""
    try:
        _relink_battle_silent_impl(relink)
    except Exception:
        log.error("静默自动重战状态机异常，停止自动输入", exc_info=True)
        try:
            relink.request_shutdown("state_machine_exception")
        except Exception:
            log.debug("记录静默状态机异常停止原因失败", exc_info=True)
    finally:
        with AUTOMATION_INPUT_LOCK:
            try:
                relink.release_automation_inputs()
            except Exception:
                log.error("静默自动重战退出时释放输入失败", exc_info=True)
        clear_automation_flow_state("静默自动重战状态机退出")


def _relink_battle_silent_impl(relink: Controller):
    global INITIAL_AUTOMATION_PHASE
    battle_active = False
    phase = INITIAL_AUTOMATION_PHASE if INITIAL_AUTOMATION_PHASE in {
        "battle_wait",
        "result",
        "startup_probe",
    } else "startup_probe"
    INITIAL_AUTOMATION_PHASE = "startup_probe"
    repeat_armed = False
    repeat_verification_required = False
    repeat_toggle_sent = False
    repeat_continue_sent = False
    repeat_activation_failed = False
    settlement_navigation = {"no_to_yes_sent": False}
    recovery_state = {"active": False}
    unrecognized_since: float | None = None
    timer_missing_since: float | None = None
    town_recovery_attempts = 0
    log.info("静默自动重战状态机已启动，初始阶段: %s", phase)

    def recover_after_unrecognized_transition(reason: str) -> bool:
        nonlocal phase, unrecognized_since, timer_missing_since
        nonlocal town_recovery_attempts
        if unexpected_town_recovery_signal(relink) != (
            "timer_missing_no_battle_hud"
        ):
            unrecognized_since = None
            timer_missing_since = None
            return False
        now = time()
        if unrecognized_since is None:
            unrecognized_since = now
        if timer_missing_since is None:
            timer_missing_since = now
        if now - timer_missing_since < UNEXPECTED_TOWN_RECOVERY_DELAY_SECONDS:
            return False
        unrecognized_since = None
        timer_missing_since = None
        town_recovery_attempts += 1
        if town_recovery_attempts > UNEXPECTED_TOWN_RECOVERY_MAX_ATTEMPTS:
            log.error("静默模式主城续战已连续失败 %d 次，停止自动输入", town_recovery_attempts - 1)
            relink.running = False
            return True
        outcome = recover_unexpected_town_state(relink, reason=reason)
        if outcome in {"battle_wait", "result"}:
            phase = outcome
            town_recovery_attempts = 0
            unrecognized_since = None
        else:
            log.error("静默模式未能从主城或异常过渡状态恢复，停止自动输入")
            relink.running = False
        return True
    focus_watchdog_thread = threading.Thread(
        target=focus_watchdog,
        args=(relink, lambda: battle_active),
        daemon=True,
    )
    focus_watchdog_thread.start()

    def begin_window_recovery() -> None:
        nonlocal battle_active, phase
        battle_active = False
        phase = "recovery"
        recovery_state["active"] = True
        with AUTOMATION_INPUT_LOCK:
            relink.release_automation_inputs()
        log.warning("串流窗口丢失：静默模式停止输入并开始自动重连")

    def finish_window_recovery(next_phase: str | None) -> None:
        nonlocal phase
        with AUTOMATION_INPUT_LOCK:
            relink.release_automation_inputs()
        recovery_state["active"] = False
        if next_phase in {"battle_wait", "result"}:
            phase = next_phase
            log.info("串流窗口恢复完成，静默模式回到阶段: %s", next_phase)
        else:
            log.error("串流窗口恢复失败，静默模式停止自动输入")
            relink.running = False

    window_recovery_thread = threading.Thread(
        target=stream_window_watchdog,
        args=(
            relink,
            lambda: recovery_state["active"],
            begin_window_recovery,
            finish_window_recovery,
        ),
        daemon=True,
    )
    window_recovery_thread.start()

    while relink.running:
        if phase == "recovery":
            sleep(0.2)
            continue
        if relink.paused:
            sleep(0.2)
            continue
        if phase == "startup_probe":
            outcome = probe_initial_automation_screen(relink)
            if outcome in {"battle_wait", "result"}:
                phase = outcome
                log.info("静默模式启动环境识别完成，进入阶段: %s", phase)
            else:
                log.warning("静默模式启动环境暂未确认，保持启动探测并等待新画面/窗口恢复")
                sleep(1.0)
            continue
        if phase == "battle_wait":
            if detect_stable_battle_hud(relink):
                battle_active = True
                phase = "battle_active"
                unrecognized_since = None
                timer_missing_since = None
                town_recovery_attempts = 0
                repeat_armed = False
                repeat_verification_required = False
                repeat_toggle_sent = False
                repeat_activation_failed = False
                _stats_start_battle()
                with AUTOMATION_INPUT_LOCK:
                    relink.press(L2_KEY)
                continue
            if detect_stable_result_ui(relink):
                phase = "result"
                unrecognized_since = None
                timer_missing_since = None
                repeat_toggle_sent = False
                repeat_activation_failed = False
                continue
            if recover_after_unrecognized_transition(
                "静默模式 battle_wait 阶段持续没有识别到战斗或结算，且无战斗倒计时"
            ):
                continue
            sleep(1.0)
            continue

        if phase == "battle_active":
            if detect_stable_result_ui(relink):
                battle_active = False
                phase = "result"
                unrecognized_since = None
                timer_missing_since = None
                repeat_armed = False
                repeat_verification_required = False
                repeat_toggle_sent = False
                repeat_activation_failed = False
                _stats_finish_battle()
                continue
            if recover_after_unrecognized_transition(
                "静默模式 battle_active 阶段持续没有识别到战斗或结算，且无战斗倒计时"
            ):
                continue
            sleep(1.0)
            continue

        # The Japanese ten-battle confirmation is an overlay above the retry
        # controls.  Detect it before the enabled-state probe so the retained
        # gold icon cannot hide the required Up + Cross action.
        if japanese_retry_confirmation_present(relink):
            handle_japanese_retry_confirmation(relink)
            log.info("静默模式识别到日文十场再挑战确认，优先选择‘はい’后继续")
            sleep(1.0)
            continue

        if (
            getattr(relink, "_japanese_result_confirmation_deadline", 0.0) > time()
            and japanese_settlement_highlight_dialog_active(relink)
        ):
            handle_japanese_settlement_highlight(relink)
            log.info("静默模式日文结算 Cross 后确认窗口已按高亮处理，跳过重战页 Square 分支")
            sleep(1.0)
            continue

        if chinese_settlement_confirmation_present(relink):
            handle_settlement_confirmation(relink, settlement_navigation)
            log.info("静默模式严格识别到中文结算确认窗口，按‘是/否’高亮处理确认")
            sleep(1.0)
            continue

        # In silent mode, the visual retry bar must win over generic Continue
        # OCR. A false Continue match must never consume the page before Square.
        retry_state = result_retry_state(relink)
        if (
            "ja" in relink.ui_language_candidates()
            and not repeat_armed
            and not repeat_activation_failed
            and retry_state is None
            and japanese_retry_page_is_visible(relink)
        ):
            if result_repeat_indicator_is_stably_gold(relink):
                repeat_armed = True
                repeat_verification_required = False
                log.info("静默模式日文低分辨率重战页红框已确认金色，进入受控 Cross 推进")
                if not repeat_continue_sent:
                    repeat_continue_sent = press_confirmed_repeat_continue(relink)
                    if repeat_continue_sent:
                        settlement_navigation["cross_sent"] = False
                        settlement_navigation["no_to_yes_sent"] = False
                        unrecognized_since = None
                        timer_missing_since = None
                sleep(0.5)
                continue
            if not repeat_toggle_sent:
                serial, _ = relink.capture_frame_state()
                with AUTOMATION_INPUT_LOCK:
                    if relink.paused or not relink.running:
                        continue
                    relink.press(SQUARE_KEY, interval=0.3)
                repeat_toggle_sent = True
                repeat_verification_required = True
                log.info(
                    "静默模式日文低分辨率重战页已由左下视觉区域确认，未依赖文字 OCR，发送一次 Square"
                )
                relink.wait_for_fresh_capture(serial, timeout=1.5)
            else:
                log.debug("静默模式日文低分辨率重战页已发送 Square，继续等待金色状态")
            sleep(0.5)
            continue

        japanese_retry_precheck = None
        if "ja" in relink.ui_language_candidates():
            try:
                japanese_retry_precheck = result_retry_state(relink)
                if japanese_retry_precheck in {"available", "enabled"}:
                    log.debug(
                        "静默模式日文结算预检已命中自动重战状态：%s，跳过通用结算确认分支",
                        japanese_retry_precheck,
                    )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                log.debug("静默模式日文结算自动重战预检失败", exc_info=True)
        center_texts = (
            {}
            if japanese_retry_precheck in {"available", "enabled"}
            else read_region_texts(relink, "结算")
        )
        challenge_language = _match_marker_language(
            relink, center_texts, "challenge_confirmation"
        )
        if challenge_language:
            if challenge_language == "ja":
                if _text_matches_marker(
                    center_texts.get("ja", ""), "ja", "challenge_confirmation_retry"
                ):
                    handle_japanese_retry_confirmation(relink)
                    sleep(1.0)
                    continue
                handle_settlement_confirmation(relink, settlement_navigation)
            else:
                settlement_navigation["no_to_yes_sent"] = False
                with AUTOMATION_INPUT_LOCK:
                    relink.press(LEFT_STICK_UP_KEY)
                    relink.press(CROSS_KEY)
            sleep(1.0)
            continue
        if _match_marker_language(relink, center_texts, "settlement"):
            if _match_marker_language(relink, center_texts, "confirmation"):
                handle_settlement_confirmation(relink, settlement_navigation)
            else:
                settlement_navigation["no_to_yes_sent"] = False
                with AUTOMATION_INPUT_LOCK:
                    relink.press(CROSS_KEY)
            sleep(1.0)
            continue

        retry_state = result_retry_state(relink)
        if retry_state == "available":
            if not repeat_toggle_sent:
                repeat_toggle_sent = True
                repeat_verification_required = True
                if ensure_auto_repeat_enabled(relink, retry_state):
                    repeat_armed = True
                    repeat_verification_required = False
                else:
                    repeat_activation_failed = False
                    repeat_verification_required = True
                    log.error(
                        "静默模式自动重战开关未确认金色状态，保持结算页并继续等待"
                    )
            if not repeat_armed and not repeat_activation_failed:
                sleep(0.5)
                continue
        if retry_state == "enabled":
            repeat_armed = True
            repeat_verification_required = False
            if "ja" in relink.ui_language_candidates() and not repeat_continue_sent:
                repeat_continue_sent = press_confirmed_repeat_continue(relink)
                if repeat_continue_sent:
                    settlement_navigation["cross_sent"] = False
                    settlement_navigation["no_to_yes_sent"] = False
                    unrecognized_since = None
                    timer_missing_since = None
                    continue

        if repeat_verification_required and not repeat_armed and not repeat_activation_failed:
            if recover_after_unrecognized_transition(
                "静默模式结算自动重战状态确认长期无进展，巡检战斗/结算/主城状态"
            ):
                continue
            sleep(0.5)
            continue

        if (
            "ja" in relink.ui_language_candidates()
            and not repeat_armed
            and not repeat_activation_failed
            and retry_state is None
            and japanese_retry_page_is_visible(relink)
        ):
            if not repeat_toggle_sent:
                serial, _ = relink.capture_frame_state()
                with AUTOMATION_INPUT_LOCK:
                    if relink.paused or not relink.running:
                        continue
                    relink.press(SQUARE_KEY, interval=0.3)
                repeat_toggle_sent = True
                repeat_verification_required = True
                log.info(
                    "静默模式日文低分辨率重战页已由左下视觉区域确认，未依赖文字 OCR，发送一次 Square"
                )
                relink.wait_for_fresh_capture(serial, timeout=1.5)
            else:
                log.debug("静默模式日文低分辨率重战页已发送 Square，继续等待金色状态")
            now = time()
            last_notice = float(
                getattr(relink, "_japanese_retry_wait_notice_at", 0.0) or 0.0
            )
            if now - last_notice >= 5.0:
                log.warning(
                    "静默模式日文结算页尚未识别左下自动重战状态，暂不发送 Cross，继续检测 再挑戦する/キャンセル"
                )
                setattr(relink, "_japanese_retry_wait_notice_at", now)
            if recover_after_unrecognized_transition(
                "静默模式日文结算重战控件长期无进展，巡检战斗/结算/主城状态"
            ):
                continue
            sleep(0.5)
            continue

        # Keep the language-independent blue-highlight fallback after the
        # repeat-control probe. The centered Japanese 再挑戦する button is
        # blue as well and must not advance the page before Square is checked.
        if japanese_settlement_highlight_dialog_active(relink):
            handle_japanese_settlement_highlight(relink)
            log.info("静默模式识别到日文结算双选项高亮，按高亮位置处理确认")
            sleep(1.0)
            continue

        if "ja" in relink.ui_language_candidates() and press_visual_result_continue(
            relink, repeat_armed
        ):
            settlement_navigation["cross_sent"] = False
            settlement_navigation["no_to_yes_sent"] = False
            unrecognized_since = None
            timer_missing_since = None
            continue
        if press_verified_result_continue(relink):
            settlement_navigation["cross_sent"] = False
            settlement_navigation["no_to_yes_sent"] = False
            unrecognized_since = None
            timer_missing_since = None
            continue
        if repeat_activation_failed and failed_repeat_has_left_result_screen(relink):
            log.warning(
                "静默模式自动重战开关失败后已离开结算且无战斗倒计时，立即巡检主城并尝试续战"
            )
            outcome = recover_unexpected_town_state(
                relink, reason="静默模式结算自动重战开关失败后离开结算页面"
            )
            if outcome in {"battle_wait", "result"}:
                phase = outcome
                repeat_activation_failed = False
                town_recovery_attempts = 0
                unrecognized_since = None
                timer_missing_since = None
            else:
                log.error("静默模式异常主城巡检失败，停止自动输入")
                relink.running = False
            continue
        if recover_after_unrecognized_transition(
            "静默模式结算阶段未识别到继续/再次挑战控件，且无战斗倒计时"
        ):
            continue
        sleep(1.0)

    battle_active = False


def _config_bool(value: object, fallback: bool = False) -> bool:
    """Parse persisted checkbox values without treating ``"false"`` as true."""

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


def _normalize_attribute_groups(
    raw_groups: object,
    *,
    legacy_thresholds: dict[str, int],
    legacy_thresholds_enabled: bool,
    legacy_sum_enabled: bool,
    legacy_sum_min: int,
) -> list[dict[str, object]]:
    """Normalize new multi-combination settings and synthesize old group 1."""

    candidates = raw_groups if isinstance(raw_groups, list) else None
    if not candidates:
        candidates = [
            {
                "name": "组合 1",
                "enabled": bool(legacy_thresholds),
                "thresholds": legacy_thresholds,
                "attribute_thresholds_enabled": legacy_thresholds_enabled,
                "attribute_sum_enabled": legacy_sum_enabled,
                "attribute_sum_min": legacy_sum_min,
            }
        ]

    normalized: list[dict[str, object]] = []
    for index, raw_group in enumerate(candidates[:4], start=1):
        if not isinstance(raw_group, dict):
            continue
        raw_thresholds = raw_group.get("thresholds", {})
        group_thresholds: dict[str, int] = {}
        if isinstance(raw_thresholds, dict):
            for name, value in raw_thresholds.items():
                canonical = normalize_ability_name(str(name))
                if canonical is None:
                    continue
                try:
                    minimum = int(value)
                except (TypeError, ValueError):
                    continue
                if minimum >= 0:
                    group_thresholds[canonical] = minimum

        rows: list[dict[str, object]] = []
        raw_rows = raw_group.get("rows")
        if isinstance(raw_rows, list):
            for row in raw_rows[:4]:
                if not isinstance(row, dict):
                    continue
                name = normalize_ability_name(str(row.get("name", ""))) or ""
                try:
                    minimum = max(0, int(row.get("min", 8)))
                except (TypeError, ValueError):
                    minimum = 8
                rows.append(
                    {
                        "enabled": _config_bool(row.get("enabled"), bool(name)),
                        "name": name,
                        "min": minimum,
                    }
                )
        if not rows:
            for row_index in range(4):
                raw_name = raw_thresholds.get(f"_row{row_index}_name", "") if isinstance(raw_thresholds, dict) else ""
                name = normalize_ability_name(str(raw_name)) or ""
                raw_min = raw_thresholds.get(f"_row{row_index}_min", 8) if isinstance(raw_thresholds, dict) else 8
                try:
                    minimum = max(0, int(raw_min))
                except (TypeError, ValueError):
                    minimum = 8
                if not name and row_index < len(group_thresholds):
                    name = list(group_thresholds)[row_index]
                    minimum = group_thresholds[name]
                rows.append({"enabled": bool(name), "name": name, "min": minimum})
        while len(rows) < 4:
            rows.append({"enabled": False, "name": "", "min": 8})

        try:
            sum_min = max(0, int(raw_group.get("attribute_sum_min", 0)))
        except (TypeError, ValueError):
            sum_min = 0
        normalized.append(
            {
                "name": str(raw_group.get("name") or f"组合 {index}"),
                "enabled": _config_bool(raw_group.get("enabled"), bool(group_thresholds)),
                "thresholds": group_thresholds,
                "attribute_thresholds_enabled": _config_bool(
                    raw_group.get("attribute_thresholds_enabled"), bool(group_thresholds)
                ),
                "attribute_sum_enabled": _config_bool(
                    raw_group.get("attribute_sum_enabled"), False
                ),
                "attribute_sum_min": sum_min,
                "rows": rows,
            }
        )
    return normalized


def _load_ability_config(path: Path | None) -> dict[str, object]:
    defaults: dict[str, object] = {
        "total_enabled": True,
        "total_min": 36,
        "thresholds": {},
        # ``None`` preserves the pre-V48 behavior for old JSON files that
        # contain selected thresholds but no explicit condition switch.
        "attribute_thresholds_enabled": None,
        "attribute_sum_enabled": False,
        "attribute_sum_min": 0,
        "stop_mode": ABILITY_STOP_MODE_ATTRIBUTES,
        "msp_spent_limit": 0,
        "msp_remaining_limit": 0,
        "auto_overwrite": False,
        "auto_overwrite_if_all_better": False,
        "stop_after_completion": True,
        "offer_navigation_settle_seconds": ABILITY_NAVIGATION_SETTLE_SECONDS,
        "success_settle_seconds": ABILITY_SUCCESS_SETTLE_SECONDS,
        "success_continue_interval_seconds": ABILITY_SUCCESS_CONTINUE_INTERVAL_SECONDS,
        "reroll_settle_seconds": ABILITY_REROLL_SETTLE_SECONDS,
        "accept_highlight_settle_seconds": ABILITY_ACCEPT_HIGHLIGHT_SETTLE_SECONDS,
        "result_timeout_seconds": ABILITY_RESULT_TIMEOUT_SECONDS,
    }
    if path is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
        except (OSError, UnicodeError, json.JSONDecodeError):
            log.warning("能力提升配置读取失败，使用默认配置：%s", path, exc_info=True)
    raw_thresholds = defaults.get("thresholds", {})
    thresholds: dict[str, int] = {}
    if isinstance(raw_thresholds, dict):
        for name, value in raw_thresholds.items():
            canonical = normalize_ability_name(str(name)) or str(name)
            try:
                minimum = int(value)
            except (TypeError, ValueError):
                continue
            if minimum >= 0:
                thresholds[canonical] = minimum
    defaults["thresholds"] = thresholds
    defaults["attribute_thresholds_enabled"] = _config_bool(
        defaults.get("attribute_thresholds_enabled"), bool(thresholds)
    )
    defaults["attribute_sum_enabled"] = _config_bool(
        defaults.get("attribute_sum_enabled"), False
    )
    try:
        defaults["attribute_sum_min"] = max(0, int(defaults.get("attribute_sum_min", 0)))
    except (TypeError, ValueError):
        defaults["attribute_sum_min"] = 0
    defaults["auto_overwrite"] = _config_bool(defaults.get("auto_overwrite"), False)
    defaults["auto_overwrite_if_all_better"] = _config_bool(
        defaults.get("auto_overwrite_if_all_better"), False
    )
    defaults["stop_after_completion"] = _config_bool(
        defaults.get("stop_after_completion"), True
    )
    defaults["attribute_groups"] = _normalize_attribute_groups(
        defaults.get("attribute_groups"),
        legacy_thresholds=thresholds,
        legacy_thresholds_enabled=bool(defaults["attribute_thresholds_enabled"]),
        legacy_sum_enabled=bool(defaults["attribute_sum_enabled"]),
        legacy_sum_min=int(defaults["attribute_sum_min"]),
    )
    defaults["stop_mode"] = normalize_ability_stop_mode(defaults.get("stop_mode"))
    for key in ("msp_spent_limit", "msp_remaining_limit"):
        try:
            defaults[key] = max(0, int(defaults.get(key, 0)))
        except (TypeError, ValueError):
            defaults[key] = 0
    if (
        defaults["stop_mode"] == ABILITY_STOP_MODE_SPENT_MSP
        and defaults["msp_spent_limit"] == 0
    ):
        defaults["msp_spent_limit"] = 1
    try:
        defaults["total_min"] = max(0, int(defaults.get("total_min", 36)))
    except (TypeError, ValueError):
        defaults["total_min"] = 36
    timing_limits = {
        "offer_navigation_settle_seconds": (ABILITY_NAVIGATION_SETTLE_SECONDS, 0.1, 10.0),
        "success_settle_seconds": (ABILITY_SUCCESS_SETTLE_SECONDS, 0.1, 30.0),
        "success_continue_interval_seconds": (
            ABILITY_SUCCESS_CONTINUE_INTERVAL_SECONDS,
            0.1,
            30.0,
        ),
        "reroll_settle_seconds": (ABILITY_REROLL_SETTLE_SECONDS, 0.1, 30.0),
        "accept_highlight_settle_seconds": (
            ABILITY_ACCEPT_HIGHLIGHT_SETTLE_SECONDS,
            0.1,
            10.0,
        ),
        "result_timeout_seconds": (ABILITY_RESULT_TIMEOUT_SECONDS, 10.0, 600.0),
    }
    for key, (fallback, minimum, maximum) in timing_limits.items():
        try:
            value = float(defaults.get(key, fallback))
            if not math.isfinite(value):
                raise ValueError
        except (TypeError, ValueError):
            value = fallback
        defaults[key] = round(min(max(value, minimum), maximum), 1)
    return defaults


def _ability_language_marker_score(items: list[dict[str, object]], language: str) -> int:
    """Score language-exclusive ability-screen labels in one OCR result."""

    text = "".join(str(item.get("text", "")) for item in items)
    return sum(marker in text for marker in ABILITY_LANGUAGE_MARKERS.get(language, ()))


def _read_ability_frame(relink: Controller) -> tuple[Image.Image, list[dict[str, object]]]:
    """Capture the ability screen and lock its OCR language once it is proven.

    Auto mode needs a Chinese/Japanese probe only until an exclusive ability
    label is observed.  Keeping both full-frame OCR passes on every stable
    screen roughly doubles a normal 1080p reroll cycle, while lock-on remains
    safe because generic labels such as ``上限突破`` cannot establish it.
    """

    frame = relink.screenshot_text("能力提升")
    best_items: list[dict[str, object]] = []
    best_score = (-1, -1, -1, -1)

    def score_items(items: list[dict[str, object]], pass_index: int) -> tuple[int, int, int, int]:
        canonical_count = sum(
            normalize_ability_name(str(item.get("text", ""))) is not None
            for item in items
        )
        marker_count = sum(
            any(
                marker in str(item.get("text", ""))
                for marker in (
                    *ABILITY_RESULT_MARKERS,
                    *ABILITY_SUCCESS_MARKERS,
                    *ABILITY_CONFIRM_MARKERS,
                    *ABILITY_CURRENT_EFFECT_MARKERS,
                )
            )
            for item in items
        )
        return (canonical_count, marker_count, min(len(items), 20), -pass_index)

    language_results: list[tuple[str, list[dict[str, object]]]] = []
    languages = relink.ui_language_candidates()
    for language in languages:
        result = relink.ocr(frame, confidence=0.40, language=language)
        items = result if isinstance(result, list) else []
        language_results.append((language, items))
        score = score_items(items, 1)
        if score > best_score:
            best_score = score
            best_items = items

    # This worker is independent from the normal rebattle process, so it must
    # establish its own automatic language choice.  On explicit language
    # settings ``ui_language_candidates`` already contains only one model.
    if getattr(relink, "detected_ui_language", None) is None:
        language_evidence = [
            (_ability_language_marker_score(items, language), language)
            for language, items in language_results
        ]
        language_evidence.sort(reverse=True)
        if language_evidence and language_evidence[0][0] > 0:
            evidence, language = language_evidence[0]
            relink.confirm_ui_language(language, f"ability_marker:{evidence}")

    # Four rows are enough for a normal offer/result page. A page marker plus
    # two rows is enough while a confirmation/success page is animating in.
    # The execute confirmation has two independent fixed labels even before
    # its rows finish fading in; those labels are sufficient to avoid paying
    # for enhanced OCR, while the later stage classifier still refuses input
    # until the required rows are actually present.
    base_is_sufficient = best_score[0] >= 4 or (
        best_score[0] >= 2 and best_score[1] >= 1
    ) or best_score[1] >= 2
    fallback_passes = 0
    if not base_is_sufficient:
        variants = (
            ImageEnhance.Contrast(frame).enhance(1.35),
            ImageEnhance.Sharpness(
                ImageEnhance.Contrast(frame).enhance(1.15)
            ).enhance(1.8),
        )
        # Once language is locked this stays on the single proven model.  Do
        # not reopen the other language merely because an animation frame has
        # fewer visible rows than usual.
        fallback_languages = relink.ui_language_candidates()
        for language in fallback_languages:
            for pass_index, candidate_frame in enumerate(variants, start=2):
                result = relink.ocr(candidate_frame, confidence=0.40, language=language)
                items = result if isinstance(result, list) else []
                fallback_passes += 1
                score = score_items(items, pass_index)
                if score > best_score:
                    best_score = score
                    best_items = items
        log.debug("能力提升基础 OCR 未充分命中，已启用增强兜底")
    else:
        log.debug("能力提升基础 OCR 已充分命中，跳过增强通道")
    log.debug(
        "能力提升 OCR 选择：语言=%s，基础通道=%d，增强通道=%d，词条数=%d，页面标记=%d，总文本=%d",
        "/".join(languages),
        len(language_results),
        fallback_passes,
        best_score[0],
        best_score[1],
        best_score[2],
    )
    return frame, best_items


def _ability_result_ready(items: list[dict[str, object]], frame: Image.Image) -> bool:
    texts = [str(item.get("text", "")) for item in items]
    old_rolls, _ = extract_ability_rolls(items, frame, side="old")
    new_rolls, _ = extract_ability_rolls(items, frame, side="new")
    has_result_title = any(marker in "".join(texts) for marker in ABILITY_RESULT_MARKERS)
    # The title and some rows fade in before the complete result. A partial
    # two-row frame is not safe for either total-star checks or old/new
    # comparison, so wait until all four rows in both columns are visible.
    return has_result_title and len(old_rolls) == 4 and len(new_rolls) == 4


def _ability_success_ready(items: list[dict[str, object]], frame: Image.Image) -> bool:
    """Detect the short ``Over the Limit!`` success screen before coverage."""

    texts = "".join(str(item.get("text", "")) for item in items)
    if not any(marker in texts for marker in ABILITY_SUCCESS_MARKERS):
        return False
    # The success screen has one column with four new effects. It must not be
    # treated as the final two-column coverage confirmation page.
    return _ability_offer_side(items, frame) is not None


def _ability_offer_ready(items: list[dict[str, object]], frame: Image.Image) -> bool:
    """Detect the animated ``Over the Limit`` candidate-ability page.

    This page has one column containing four candidate rows and no execution
    buttons. It must be confirmed once before the two-column overwrite dialog
    appears, so treating it as the final result would advance the wrong state.
    """

    return _ability_offer_side(items, frame) is not None


def _ability_offer_side(
    items: list[dict[str, object]], frame: Image.Image
) -> str | None:
    """Return the column containing the four candidate rows.

    The game can render the single-column candidate panel on either side of
    the client area depending on the selected character/menu layout. The
    final result page remains a two-column screen, so an offer is accepted
    only when exactly one parser column contains all four rows.
    """

    texts = "".join(str(item.get("text", "")) for item in items)
    if any(marker in texts for marker in ABILITY_CONFIRM_MARKERS):
        return None
    matching_sides: list[str] = []
    for side in ("old", "new"):
        rolls, _ = extract_ability_rolls(items, frame, side=side)
        if len(rolls) == 4:
            matching_sides.append(side)
    return matching_sides[0] if len(matching_sides) == 1 else None


def _ability_confirmation_ready(items: list[dict[str, object]], frame: Image.Image) -> bool:
    """Detect the MSP ``执行 / 取消`` confirmation dialog."""

    texts = "".join(str(item.get("text", "")) for item in items)
    has_execute = any(marker in texts for marker in ABILITY_CONFIRM_MARKERS)
    has_current_effects = any(marker in texts for marker in ABILITY_CURRENT_EFFECT_MARKERS)
    if not has_execute or not has_current_effects:
        return False
    old_rolls, _ = extract_ability_rolls(items, frame, side="old")
    new_rolls, _ = extract_ability_rolls(items, frame, side="new")
    return len(old_rolls) + len(new_rolls) >= 2


def _ability_result_highlight(frame: Image.Image) -> str | None:
    """Detect the blue focus bar on the ability result page's yes/no menu.

    The result dialog uses two fixed, vertically stacked choices. The
    selected choice has a blue horizontal glow, while the other row remains
    dark. Use normalized coordinates so the test remains valid for the
    different stream resolutions supported by the capture backend.
    """

    try:
        pixels = np.asarray(frame.convert("RGB"), dtype=np.float32)
    except (AttributeError, TypeError, ValueError):
        return None
    if pixels.ndim != 3 or pixels.shape[2] < 3:
        return None
    height, width = pixels.shape[:2]
    row_half = max(5, int(height * 0.018))
    x0, x1 = int(width * 0.35), int(width * 0.65)

    def blue_score(center_fraction: float) -> float:
        center = int(height * center_fraction)
        y0 = max(0, center - row_half)
        y1 = min(height, center + row_half + 1)
        band = pixels[y0:y1, x0:x1]
        if band.size == 0:
            return 0.0
        red, green, blue = band[:, :, 0], band[:, :, 1], band[:, :, 2]
        return float((blue - (red + green) * 0.5).mean())

    yes_score = blue_score(0.733)
    no_score = blue_score(0.783)
    delta = yes_score - no_score
    if delta >= 6.0:
        return "yes"
    if delta <= -6.0:
        return "no"
    return None


def chinese_settlement_confirmation_present(relink: Controller) -> bool:
    """Strictly identify the Chinese settlement confirmation dialog by text."""
    configured_language = getattr(relink, "ui_language_mode", "auto")
    detected_language = getattr(relink, "detected_ui_language", None)
    if configured_language not in {"auto", "zh"} and detected_language != "zh":
        return False
    try:
        frame = relink.screenshot()
        width, height = frame.size
        crop = frame.crop(
            (int(width * 0.24), int(height * 0.25), int(width * 0.76), int(height * 0.76))
        )
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        crop = crop.resize((crop.width * 2, crop.height * 2), resampling)
        result = relink.ocr(crop, confidence=0.48, language="zh")
        text = "".join(
            str(item.get("text", "")) for item in result
        ) if isinstance(result, list) else ""
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return "结算" in text and "确认" in text


def handle_settlement_confirmation(
    relink: Controller, navigation_state: dict[str, bool]
) -> str:
    """Handle a settlement yes/no dialog without repeating stale navigation.

    The highlight is read from a fresh frame after moving from ``否`` to
    ``是``. The caller must run this again before sending Cross; a stale frame
    therefore cannot confirm the wrong row.
    """
    selection = settlement_confirmation_selection(relink)
    if selection == "no":
        # A visible ``否`` proves that this is a new confirmation dialog after
        # any previous Cross action; allow one fresh up navigation for it.
        navigation_state["cross_sent"] = False
        if navigation_state.get("no_to_yes_sent", False):
            log.debug("结算确认仍识别为‘否’，已发送过上键，等待新画面")
            return "waiting"
        navigation_state["no_to_yes_sent"] = True
        serial, _ = relink.capture_frame_state()
        with AUTOMATION_INPUT_LOCK:
            if not relink.running:
                return "waiting"
            press_result = relink.press(D_PAD_UP_KEY)
        if press_result is False:
            navigation_state["no_to_yes_sent"] = False
            log.warning("结算确认导航按键未发送成功，保留当前高亮并等待重试")
            return "waiting"
        if relink.wait_for_fresh_capture(
            serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
        ):
            refreshed = settlement_confirmation_selection(relink)
            if refreshed == "yes":
                log.info("结算确认已通过新捕获帧确认切换到‘是’，等待下一轮确认")
            else:
                log.warning("发送上键后未确认结算高亮为‘是’，暂不发送 Cross")
        else:
            log.warning("结算确认导航后未等到新捕获帧，暂不发送 Cross")
        return "navigated"
    if selection == "yes":
        navigation_state["no_to_yes_sent"] = False
        if navigation_state.get("cross_sent", False):
            log.debug("结算确认仍停留在‘是’，已发送过 Cross，等待新画面")
            return "waiting"
        navigation_state["cross_sent"] = True
        serial, _ = relink.capture_frame_state()
        with AUTOMATION_INPUT_LOCK:
            if not relink.running:
                return "waiting"
            press_result = relink.press(CROSS_KEY)
        if press_result is False:
            navigation_state["cross_sent"] = False
            log.warning("结算确认 Cross 未发送成功，等待窗口恢复后重试")
            return "waiting"
        if not relink.wait_for_fresh_capture(
            serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
        ):
            log.warning("结算确认发送 Cross 后未等到新捕获帧，暂不重复确认")
        else:
            log.info("结算确认已在新一轮识别中选中‘是’，发送一次 Cross")
        return "confirmed"
    return "waiting"


def handle_japanese_settlement_highlight(relink: Controller) -> str:
    """Confirm the Japanese result dialog from the current highlight only.

    This dialog is a regular result-page fast-confirm prompt. It must not
    reuse the cross-dialog ``settlement_navigation`` memory used by the
    Chinese text-confirmation path: a later Japanese prompt may appear with
    the same selected row after an earlier Cross was already sent.
    """
    selection = settlement_confirmation_selection(relink)
    if selection == "yes":
        with AUTOMATION_INPUT_LOCK:
            if not relink.running:
                return "waiting"
            if relink.press(CROSS_KEY) is False:
                return "waiting"
            try:
                relink._japanese_result_confirmation_deadline = 0.0
            except (AttributeError, TypeError):
                pass
        log.info("日文结算上方确定项已高亮，直接发送一次 Cross")
        return "confirmed"
    if selection == "no":
        with AUTOMATION_INPUT_LOCK:
            if not relink.running:
                return "waiting"
            if relink.press(D_PAD_UP_KEY) is False:
                return "waiting"
        log.info("日文结算下方取消项已高亮，发送一次上键切换到确定")
        return "navigated"
    return "waiting"


def handle_japanese_retry_confirmation(relink: Controller) -> str:
    """Move Japanese ``再挑戦確認`` from いいえ to はい, then Cross."""
    serial, _ = relink.capture_frame_state()
    with AUTOMATION_INPUT_LOCK:
        if not relink.running:
            return "waiting"
        if relink.press(D_PAD_UP_KEY) is False:
            return "waiting"
    if not relink.wait_for_fresh_capture(
        serial, timeout=BATTLE_HUD_CONFIRM_INTERVAL_SECONDS + 0.35
    ):
        log.warning("日文再挑戦確認上移后未等到新捕获帧，暂不发送 Cross")
        return "waiting"
    if settlement_confirmation_selection(relink) != "yes":
        log.warning("日文再挑戦确认上移后未确认‘はい’高亮，暂不发送 Cross")
        return "waiting"
    with AUTOMATION_INPUT_LOCK:
        if not relink.running or relink.press(CROSS_KEY) is False:
            return "waiting"
    log.info("识别到日文再挑戦確認，已上移到‘はい’并发送一次 Cross")
    return "confirmed"


def read_japanese_retry_confirmation_title(relink: Controller) -> str:
    """Read only the centered Japanese retry-confirmation title.

    The full result-center OCR is intentionally broad so it can also process
    ordinary result prompts. This modal has a stable, highly legible title;
    a dedicated crop avoids losing that title to reward text, glow effects,
    or the lower retry control on 540P streams.
    """
    frame = relink.screenshot().convert("RGB")
    width, height = frame.size
    crop = frame.crop(
        (
            int(width * 0.32),
            int(height * 0.28),
            int(width * 0.68),
            int(height * 0.47),
        )
    )
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    crop = crop.resize((max(1, crop.width * 2), max(1, crop.height * 2)), resampling)
    result = relink.ocr(crop, confidence=0.40, language="ja")
    if not isinstance(result, list):
        return ""
    return "".join(str(item.get("text", "")) for item in result)


def japanese_retry_confirmation_present(
    relink: Controller, texts: dict[str, str] | None = None
) -> bool:
    """Recognize the ten-battle Japanese retry confirmation before page controls.

    The modal preserves much of the enabled auto-repeat page underneath it.
    Therefore this must run before probing the lower-left retry toggle: the
    underlying ``キャンセル`` and gold icon are not evidence that it is safe
    to advance.  A dedicated center crop keeps the tolerant low-resolution
    fallback scoped to this one result confirmation dialog.
    """
    # This exact title is the highest-confidence signal for the modal shown
    # after the result page. It must precede every Square/retry-control probe.
    if "ja" in relink.ui_language_candidates():
        try:
            title = read_japanese_retry_confirmation_title(relink)
            if _text_matches_marker(title, "ja", "challenge_confirmation_retry"):
                relink.confirm_ui_language("ja", "retry_confirmation_title")
                log.info("日文再挑戦確認标题专用 OCR 命中：%r", title)
                return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            log.debug("日文再挑戦確認标题专用 OCR 暂不可用", exc_info=True)

    if texts is None:
        try:
            texts = read_settlement_center_texts(relink)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False
    text = str(texts.get("ja", ""))
    if _text_matches_marker(text, "ja", "challenge_confirmation_retry"):
        relink.confirm_ui_language("ja", "retry_confirmation")
        return True
    compact = "".join(text.split())
    # 540P OCR can lose the final characters of 再挑戦確認, but the modal title
    # still keeps the retry and confirmation stems.  Do not accept a lone 再戦
    # here: regular result controls may contain it behind the modal.
    if (
        "再" in compact
        and any(glyph in compact for glyph in ("挑", "規", "排", "戦"))
        and "確" in compact
    ):
        relink.confirm_ui_language("ja", "retry_confirmation_fuzzy")
        log.info("日文再挑戦確認中心 OCR 宽匹配命中：%r", text)
        return True
    return False


def japanese_settlement_highlight_dialog_active(relink: Controller) -> bool:
    """Return whether a Japanese result dialog has a usable yes/no highlight.

    Japanese confirmation wording varies between game prompts and can be lost
    entirely to compressed-stream OCR. Once the client language is known to
    be Japanese, the two-row blue highlight is the authoritative input guard:
    it lets the normal confirmation state machine move ``no`` to ``yes`` or
    accept an already-selected ``yes``. Do not enable this visual fallback
    while language auto-detection is unresolved, because a Chinese result
    dialog can share the same geometry.
    """
    try:
        center_texts = read_region_texts(relink, "结算")
        japanese_text = center_texts.get("ja", "")
        settlement_dialog = (
            _text_matches_marker(japanese_text, "ja", "settlement")
            and _text_matches_marker(japanese_text, "ja", "confirmation")
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False

    pending_deadline = float(
        getattr(relink, "_japanese_result_confirmation_deadline", 0.0) or 0.0
    )
    pending_confirmation = pending_deadline > time()

    # The centered Japanese ``再挑戦する`` button is also blue. It must be
    # classified before the generic two-row highlight fallback, otherwise the
    # fallback sends Cross and the result loop never reaches Box/repeat-state
    # verification. The caller will process this page through result_retry_state.
    try:
        retry_text = read_japanese_result_retry_text(relink)
        if not pending_confirmation and (
            _text_matches_marker(retry_text, "ja", "result_retry_available") or _text_matches_marker(
            retry_text, "ja", "result_retry_cancel"
            )
        ):
            log.debug("日文高亮兜底跳过：当前是中央底部再挑戦按钮，应先检测自动重战状态")
            return False
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        pass

    configured_language = getattr(relink, "ui_language_mode", None)
    detected_language = getattr(relink, "detected_ui_language", None)
    if configured_language != "ja" and detected_language != "ja":
        # Auto language detection can still be unresolved on the first frame
        # of this dialog. Establish Japanese context from the central result
        # crop, then let the blue selection bar decide the action. The exact
        # はい/いいえ OCR is intentionally not required here.
        if not settlement_dialog:
            return False
        relink.confirm_ui_language("ja", "settlement_confirmation")
    # ``再挑戦確認`` has its own initial focus rule: Japanese clients open
    # this dialog on いいえ and must move up before Cross. Check its title
    # before the generic blue-bar fallback, otherwise a broad glow can be
    # mistaken for はい and bypass the required Up action.
    try:
        if _text_matches_marker(
            japanese_text, "ja", "challenge_confirmation_retry"
        ):
            log.debug("日文通用高亮兜底跳过：当前是再挑戦確認专用页面")
            return False
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    # A blue horizontal strip also exists in the town HUD.  Japanese OCR can
    # miss the dialog labels entirely on a small/compressed client window, but
    # the visual fallback must still be anchored to an independently detected
    # result page; otherwise a stale result phase can repeatedly send Up in
    # town, exactly as seen after an interrupted rematch.
    try:
        result_marker = detect_stable_result_ui(relink)
        # A center-dialog match remains a result-page anchor after the
        # bottom-right prompt has already disappeared.
        if result_marker is None and not pending_confirmation and not settlement_dialog:
            log.debug("日文高亮兜底跳过：当前画面没有结算页、中央确认框或续战过渡证据")
            return False
        if pending_confirmation:
            log.debug("日文续战 Cross 后进入确认过渡窗口，允许检查默认‘是’高亮")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    # The normal Japanese result page also has a blue element near the lower
    # part of the frame. Do not let that visual fallback steal the ordinary
    # bottom-right ``次へ`` prompt shown in the supplied screenshot. The
    # verified result-continue path will send Cross after two fresh OCR hits.
    try:
        if not pending_confirmation and region_has_marker(relink, "继续", "result_continue"):
            log.debug("日文高亮兜底跳过：当前已识别右下角次へ继续提示")
            return False
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        # Lightweight test doubles and a transient capture failure should not
        # disable the established visual fallback.
        pass
    return settlement_confirmation_selection(relink) is not None


def _wait_for_ability_yes_highlight(
    relink: Controller,
    timeout_seconds: float,
    settle_seconds: float = ABILITY_ACCEPT_HIGHLIGHT_SETTLE_SECONDS,
) -> tuple[Image.Image, list[dict[str, object]]] | None:
    """Confirm a stable ``是`` focus before an automatic overwrite Cross.

    A previous Moon cancellation can leave ``否`` focused. Move up only after
    detecting that state, then require two consecutive ``是`` samples. Any
    ambiguous focus stops the automatic action rather than guessing.
    """

    deadline = time() + min(timeout_seconds, max(3.0, settle_seconds * 4.0 + 2.0))
    settle_seconds = max(0.1, float(settle_seconds))
    moved_to_yes = False
    yes_samples = 0
    sleep(min(settle_seconds, max(0.0, deadline - time())))
    while relink.running and not relink.paused and time() < deadline:
        try:
            frame, items = _read_ability_frame(relink)
            if _ability_stage(items, frame) != "result":
                sleep(0.2)
                continue
            selection = _ability_result_highlight(frame)
        except (OSError, RuntimeError):
            sleep(0.2)
            continue

        log.info("能力提升自动覆盖高亮复核：当前选项=%s", selection or "无法确认")
        if selection == "yes":
            yes_samples += 1
            if yes_samples >= 2:
                return frame, items
        elif selection == "no" and not moved_to_yes:
            with AUTOMATION_INPUT_LOCK:
                relink.press(D_PAD_UP_KEY)
            moved_to_yes = True
            yes_samples = 0
            log.info("能力提升自动覆盖检测到‘否’高亮，发送上方向键切换到‘是’")
        else:
            yes_samples = 0
            if selection is None:
                log.warning("能力提升自动覆盖无法确认‘是/否’高亮，停止自动确认")
                return None
            log.warning("能力提升自动覆盖切换后仍未处于‘是’高亮，停止自动确认")
            return None
        sleep(min(0.25, max(0.0, deadline - time())))
    return None


def _ability_stage(items: list[dict[str, object]], frame: Image.Image) -> str | None:
    """Classify one stable ability screen without sending input."""

    if _ability_result_ready(items, frame):
        return "result"
    if _ability_confirmation_ready(items, frame):
        return "confirmation"
    if _ability_success_ready(items, frame):
        return "success"
    if _ability_offer_ready(items, frame):
        return "offer"
    return None


def _ability_level_locations(
    items: list[dict[str, object]],
) -> dict[int, tuple[float, float]]:
    """Find the three LV1/LV2/LV3 card labels from OCR coordinates."""

    locations: dict[int, tuple[float, float]] = {}
    for item in items:
        raw_text = re.sub(r"\s+", "", str(item.get("text", ""))).upper()
        match = re.search(r"L[VY](1|2|3)", raw_text)
        if match:
            location = item.get("location")
            if isinstance(location, (tuple, list)) and len(location) == 4:
                try:
                    x0, y0, x1, y1 = (float(value) for value in location)
                except (TypeError, ValueError):
                    continue
                locations[int(match.group(1))] = ((x0 + x1) * 0.5, (y0 + y1) * 0.5)
    return locations


def _ability_selected_level(
    items: list[dict[str, object]], frame: Image.Image
) -> int | None:
    """Detect which breakthrough card is highlighted by its fill color.

    The selected card occupies much more saturated color area than the two
    unselected white cards. Blue, yellow, and purple correspond to LV1, LV2,
    and LV3 respectively. Return ``None`` when OCR labels or color evidence
    are incomplete so the caller can avoid sending Cross blindly.
    """

    locations = _ability_level_locations(items)
    if set(locations) != {1, 2, 3}:
        return None
    try:
        pixels = np.asarray(frame.convert("RGB"), dtype=np.float32) / 255.0
    except (AttributeError, TypeError, ValueError):
        return None
    height, width = pixels.shape[:2]
    centers = [locations[level][0] for level in (1, 2, 3)]
    spacing = min(
        abs(centers[1] - centers[0]),
        abs(centers[2] - centers[1]),
    )
    if spacing < 40:
        return None
    x_radius = max(55, min(150, spacing * 0.38))
    y_radius = x_radius
    color_scores: dict[int, dict[str, int]] = {}
    for level, (center_x, center_y) in locations.items():
        x0 = max(0, int(center_x - x_radius))
        x1 = min(width, int(center_x + x_radius))
        y0 = max(0, int(center_y - y_radius))
        y1 = min(height, int(center_y + y_radius))
        crop = pixels[y0:y1, x0:x1]
        red, green, blue = crop[..., 0], crop[..., 1], crop[..., 2]
        maximum = crop.max(axis=2)
        minimum = crop.min(axis=2)
        difference = maximum - minimum
        saturation = np.where(maximum > 1e-6, difference / maximum * 255.0, 0.0)
        # Convert hue only for sufficiently saturated pixels. The formula is
        # kept local to avoid making color detection depend on OpenCV at run.
        hue = np.zeros_like(maximum)
        valid = difference > 1e-5
        red_max = (maximum == red) & valid
        green_max = (maximum == green) & valid
        blue_max = (maximum == blue) & valid
        hue[red_max] = ((green[red_max] - blue[red_max]) / difference[red_max]) % 6
        hue[green_max] = ((blue[green_max] - red[green_max]) / difference[green_max]) + 2
        hue[blue_max] = ((red[blue_max] - green[blue_max]) / difference[blue_max]) + 4
        hue *= 60.0
        vivid = (saturation >= 80.0) & (maximum >= 70.0 / 255.0)
        color_scores[level] = {
            "blue": int(np.sum(vivid & (hue >= 175.0) & (hue <= 250.0))),
            "yellow": int(np.sum(vivid & (hue >= 20.0) & (hue <= 75.0))),
            "purple": int(np.sum(vivid & (hue >= 250.0) & (hue <= 330.0))),
            "area": int(vivid.size),
        }
    selected_level: int | None = None
    selected_score = 0
    color_to_level = {"blue": 1, "yellow": 2, "purple": 3}
    for level, scores in color_scores.items():
        color = max(color_to_level, key=lambda name: scores[name])
        score = scores[color]
        if score < scores["area"] * 0.45 or score <= selected_score:
            continue
        selected_level = color_to_level[color]
        selected_score = score
    return selected_level


def _wait_for_ability_stage(
    relink: Controller,
    timeout_seconds: float,
    expected_stage: str | set[str] | tuple[str, ...] | None = None,
) -> tuple[str, Image.Image, list[dict[str, object]]] | None:
    """Wait for a structurally complete ability stage.

    Ability reroll owns this state machine.  It must not run normal rebattle
    settlement probes here: those probes make unrelated OCR calls and can
    inject combat inputs into a protected reroll flow.  Only the final
    overwrite result is read twice because its rows drive the keep/reroll
    decision; the other stages have strict non-destructive classifiers and
    return from their first complete frame.
    """

    expected = None
    if expected_stage is not None:
        expected = {expected_stage} if isinstance(expected_stage, str) else set(expected_stage)
    deadline = time() + timeout_seconds
    while relink.running and not relink.paused and time() < deadline:
        try:
            frame, items = _read_ability_frame(relink)
            stage = _ability_stage(items, frame)
        except (OSError, RuntimeError):
            sleep(0.5)
            continue

        if stage is not None and (expected is None or stage in expected):
            if stage not in ABILITY_STAGE_DOUBLE_CHECK:
                return stage, frame, items
            sleep(0.65)
            try:
                second_frame, second_items = _read_ability_frame(relink)
                second_stage = _ability_stage(second_items, second_frame)
            except (OSError, RuntimeError):
                continue
            if second_stage == stage and (expected is None or second_stage in expected):
                return stage, second_frame, second_items
        sleep(0.45)
    return None


def _wait_for_ability_result(
    relink: Controller, timeout_seconds: float
) -> tuple[Image.Image, list[dict[str, object]]] | None:
    stage = _wait_for_ability_stage(relink, timeout_seconds, expected_stage="result")
    if stage is not None and stage[0] == "result":
        return stage[1], stage[2]
    return None


def _wait_for_ability_success(
    relink: Controller, timeout_seconds: float
) -> tuple[Image.Image, list[dict[str, object]]] | None:
    stage = _wait_for_ability_stage(relink, timeout_seconds, expected_stage="success")
    if stage is not None and stage[0] == "success":
        return stage[1], stage[2]
    return None


def _advance_ability_success_to_result(
    relink: Controller,
    timeout_seconds: float,
    continue_interval_seconds: float = ABILITY_SUCCESS_CONTINUE_INTERVAL_SECONDS,
) -> tuple[Image.Image, list[dict[str, object]]] | None:
    """Press Cross until the success page becomes the complete result page.

    The success animation can remain visible after its four attributes have
    been read. A single Cross is therefore not reliable: keep a deliberate
    interval between presses, and stop immediately when the two-column result
    page is stable. The stage classifier checks ``result`` before ``success``
    so a result page can never trigger another Cross from this loop.
    """

    deadline = time() + timeout_seconds
    press_count = 0
    while relink.running and not relink.paused and time() < deadline:
        remaining = deadline - time()
        if remaining <= 0:
            break
        stage = _wait_for_ability_stage(
            relink,
            remaining,
            expected_stage=("success", "result"),
        )
        if stage is None:
            break
        if stage[0] == "result":
            return stage[1], stage[2]

        with AUTOMATION_INPUT_LOCK:
            relink.press(CROSS_KEY)
        press_count += 1
        log.info("能力提升成功页仍在停留，发送第 %d 次 Cross 等待覆盖页", press_count)

        remaining = deadline - time()
        if remaining > 0:
            sleep(min(continue_interval_seconds, remaining))
    return None


def _clear_ability_reroll_state(relink: Controller, state: dict[str, object]) -> None:
    """Release input and invalidate every transient ability-reroll phase.

    The independent reroll worker can be stopped by F2, the GUI, a timeout,
    or a successful result. All exits must leave the controller in the same
    neutral state so a later normal rebattle worker cannot observe a stale
    offer/confirmation/result phase.
    """

    state.clear()
    state.update({"phase": "stopped", "round": 0})
    try:
        with AUTOMATION_INPUT_LOCK:
            relink.release_automation_inputs()
    except (AttributeError, RuntimeError):
        pass
    try:
        relink.request_shutdown()
    except AttributeError:
        relink.running = False


def _move_ability_offer_to_lv3(
    relink: Controller,
    stage: tuple[str, Image.Image, list[dict[str, object]]],
    timeout_seconds: float,
    navigation_settle_seconds: float = ABILITY_NAVIGATION_SETTLE_SECONDS,
) -> tuple[Image.Image, list[dict[str, object]]] | None:
    """Move the selected card until LV3 is purple, then return its frame."""

    current = stage
    for attempt in range(ABILITY_NAVIGATION_MAX_STEPS + 1):
        selected_level = _ability_selected_level(current[2], current[1])
        if selected_level == 3:
            return current[1], current[2]
        if selected_level not in {1, 2}:
            log.error(
                "能力提升候选页无法确认当前高亮等级（第 %d 次检查），禁止盲按 Cross",
                attempt + 1,
            )
            return None
        if attempt >= ABILITY_NAVIGATION_MAX_STEPS:
            log.error("能力提升候选页调整超过 %d 次，仍未到达 LV3", ABILITY_NAVIGATION_MAX_STEPS)
            return None
        direction_key = LEFT_STICK_LEFT_KEY if selected_level == 1 else LEFT_STICK_RIGHT_KEY
        direction_name = "左" if selected_level == 1 else "右"
        log.info(
            "能力提升候选页当前为 LV%d，向%s移动，等待 LV3 紫色高亮（第 %d 次）",
            selected_level,
            direction_name,
            attempt + 1,
        )
        with AUTOMATION_INPUT_LOCK:
            relink.press(direction_key)
        sleep(navigation_settle_seconds)
        refreshed = _wait_for_ability_stage(
            relink,
            min(timeout_seconds, 8.0),
            expected_stage="offer",
        )
        if refreshed is None:
            return None
        current = refreshed
    return None


def _cancel_ability_result(relink: Controller) -> None:
    """Leave an unqualified coverage page without confirming its choice.

    The result dialog remembers the previously focused row. Navigating to
    ``否`` and pressing Cross is therefore unsafe: a stale or delayed frame can
    leave ``是`` focused and overwrite the existing abilities. Moon is the
    game's cancel/back action and exits this page without submitting either
    choice.
    """

    with AUTOMATION_INPUT_LOCK:
        relink.press(MOON_KEY)
    log.info("能力提升结果不符合条件，发送 Moon 退出覆盖确认页，不发送 Cross")


def ability_reroll_loop(relink: Controller, config: dict[str, object], journal: AbilityJournal) -> None:
    """Run one independent ability reroll session until a good roll is found."""
    with automation_flow("ability_reroll") as acquired:
        if not acquired:
            log.error(
                "能力提升重抽未启动：其它自动化流程仍占用输入：%s",
                automation_flow_name(),
            )
            relink.show_toast("能力提升重抽", "其它自动化流程尚未退出，未发送任何按键")
            return
        _ability_reroll_loop_impl(relink, config, journal)


def _ability_reroll_loop_impl(
    relink: Controller, config: dict[str, object], journal: AbilityJournal
) -> None:
    """Implementation of the protected independent ability reroll session."""

    total_enabled = bool(config.get("total_enabled", True))
    total_min = int(config.get("total_min", 36))
    raw_thresholds = config.get("thresholds", {})
    thresholds = raw_thresholds if isinstance(raw_thresholds, dict) else {}
    raw_attribute_groups = config.get("attribute_groups")
    attribute_groups = (
        raw_attribute_groups if isinstance(raw_attribute_groups, list) else None
    )
    attribute_thresholds_enabled = _config_bool(
        config.get("attribute_thresholds_enabled"), bool(thresholds)
    )
    attribute_sum_enabled = _config_bool(
        config.get("attribute_sum_enabled"), False
    )
    attribute_sum_min = max(0, int(config.get("attribute_sum_min", 0)))
    auto_overwrite = _config_bool(config.get("auto_overwrite"), False)
    compare_enabled = _config_bool(
        config.get("auto_overwrite_if_all_better"), False
    )
    stop_after_completion = _config_bool(
        config.get("stop_after_completion"), True
    )
    navigation_settle_seconds = float(
        config.get("offer_navigation_settle_seconds", ABILITY_NAVIGATION_SETTLE_SECONDS)
    )
    success_settle_seconds = float(
        config.get("success_settle_seconds", ABILITY_SUCCESS_SETTLE_SECONDS)
    )
    success_continue_interval_seconds = float(
        config.get(
            "success_continue_interval_seconds",
            ABILITY_SUCCESS_CONTINUE_INTERVAL_SECONDS,
        )
    )
    reroll_settle_seconds = float(
        config.get("reroll_settle_seconds", ABILITY_REROLL_SETTLE_SECONDS)
    )
    accept_highlight_settle_seconds = float(
        config.get(
            "accept_highlight_settle_seconds",
            ABILITY_ACCEPT_HIGHLIGHT_SETTLE_SECONDS,
        )
    )
    timeout_seconds = float(config.get("result_timeout_seconds", ABILITY_RESULT_TIMEOUT_SECONDS))
    stop_mode = normalize_ability_stop_mode(config.get("stop_mode"))
    msp_spent_limit = max(0, int(config.get("msp_spent_limit", 0)))
    msp_remaining_limit = max(0, int(config.get("msp_remaining_limit", 0)))
    msp_limit = (
        msp_spent_limit
        if stop_mode == ABILITY_STOP_MODE_SPENT_MSP
        else msp_remaining_limit
    )
    initial_msp: int | None = None
    current_msp: int | None = None
    round_index = 0
    state: dict[str, object] = {"phase": "idle", "round": 0}
    pending_stage: tuple[str, Image.Image, list[dict[str, object]]] | None = None

    def observe_msp(frame: Image.Image, items: list[dict[str, object]], context: str) -> int | None:
        nonlocal initial_msp, current_msp
        observed = extract_msp_from_ocr(items, frame.size)
        if observed is None:
            log.warning("能力提升 %s 未识别右上角 MSP", context)
            return None
        if initial_msp is None:
            initial_msp = observed
            log.info("能力提升 MSP 起始值：%d", observed)
        if current_msp != observed:
            current_msp = observed
            spent = max(0, initial_msp - observed) if initial_msp is not None else 0
            log.info("能力提升 %s MSP：当前=%d，已使用=%d", context, observed, spent)
        return observed

    def msp_limit_reached(observed: int | None, context: str) -> bool:
        if stop_mode == ABILITY_STOP_MODE_ATTRIBUTES:
            return False
        if observed is None:
            log.error("能力提升 %s 无法确认 MSP，停止以避免突破预算失控", context)
            relink.show_toast("能力提升重抽", "无法识别右上角 MSP，已安全停止，请检查日志")
            return True
        reached, reason = msp_stop_status(
            stop_mode,
            current_msp=observed,
            initial_msp=initial_msp,
            limit=msp_limit,
        )
        if reached:
            log.info("能力提升触发停止条件：%s", reason)
            relink.show_toast("能力提升重抽", f"{reason}，已停止")
        return reached

    log.info(
        "能力提升重抽已启动 | 停止方式=%s | 总星数条件=%s%d | 属性组合=%s | 属性逐项条件=%s | 属性星数之和条件=%s%d | 达标自动覆盖=%s | 自动覆盖完成后停止=%s | 新词条逐项优于旧词条自动覆盖=%s",
        {
            ABILITY_STOP_MODE_ATTRIBUTES: "属性",
            ABILITY_STOP_MODE_SPENT_MSP: f"已使用 {msp_spent_limit} MSP",
            ABILITY_STOP_MODE_REMAINING_MSP: f"剩余 <= {msp_remaining_limit} MSP",
        }.get(stop_mode, stop_mode),
        ">=" if total_enabled else "未启用 ",
        total_min,
        attribute_groups if attribute_groups is not None else "未启用",
        thresholds if attribute_thresholds_enabled else "未启用",
        ">=" if attribute_sum_enabled else "未启用 ",
        attribute_sum_min,
        "是" if auto_overwrite else "否",
        "是" if stop_after_completion else "否",
        "是" if compare_enabled else "否",
    )
    log.info(
        "能力提升有效动作配置 | 自动覆盖=%s | 逐项优于旧词条自动覆盖=%s | Cross键=%r | Moon键=%r",
        auto_overwrite,
        compare_enabled,
        CROSS_KEY,
        MOON_KEY,
    )
    try:
        while relink.running and not relink.paused:
            round_index += 1
            state["round"] = round_index
            state["phase"] = "waiting"
            stage = pending_stage or _wait_for_ability_stage(relink, timeout_seconds)
            pending_stage = None
            if stage is None:
                log.error(
                    "能力提升第 %d 轮在 %.0f 秒内未识别到突破候选页、执行确认页或结果页，已停止",
                    round_index,
                    timeout_seconds,
                )
                relink.show_toast("能力提升重抽", "超时未识别突破页面，已停止，请检查日志")
                break

            observed_msp = observe_msp(stage[1], stage[2], f"第 {round_index} 轮页面")
            if msp_limit_reached(observed_msp, f"第 {round_index} 轮开始前"):
                break

            if stage[0] == "offer":
                state["phase"] = "offer_navigation"
                offer_frame = _move_ability_offer_to_lv3(
                    relink,
                    stage,
                    timeout_seconds,
                    navigation_settle_seconds=navigation_settle_seconds,
                )
                if offer_frame is None:
                    log.error("能力提升第 %d 轮未能把高亮调整到 LV3 紫色，已停止", round_index)
                    relink.show_toast("能力提升重抽", "未确认 LV3 紫色高亮，已停止，请检查日志")
                    break
                offer_image, offer_items = offer_frame
                observed_msp = observe_msp(offer_image, offer_items, f"第 {round_index} 轮候选页")
                if msp_limit_reached(observed_msp, f"第 {round_index} 轮候选页"):
                    break
                offer_side = _ability_offer_side(offer_items, offer_image)
                if offer_side is None:
                    log.error("能力提升第 %d 轮候选属性列不明确，已停止", round_index)
                    relink.show_toast("能力提升重抽", "候选属性列无法确认，已停止，请检查日志")
                    break
                offer_rolls, _ = extract_ability_rolls(
                    offer_items, offer_image, side=offer_side
                )
                log.info(
                    "能力提升第 %d 轮：已确认 LV3 紫色，候选属性=%s，发送 Cross 进入执行确认页",
                    round_index,
                    [(roll.attribute, roll.value) for roll in offer_rolls],
                )
                state["phase"] = "offer_confirming"
                with AUTOMATION_INPUT_LOCK:
                    relink.press(CROSS_KEY)
                stage = _wait_for_ability_stage(
                    relink, timeout_seconds, expected_stage="confirmation"
                )
                if stage is None:
                    log.error(
                        "能力提升第 %d 轮发送候选页 Cross 后未识别到执行确认页，已停止",
                        round_index,
                    )
                    relink.show_toast("能力提升重抽", "未识别执行确认页，已停止，请检查日志")
                    break

            if stage[0] == "confirmation":
                state["phase"] = "confirmation_submitting"
                with AUTOMATION_INPUT_LOCK:
                    # The dialog remembers the previous focus and commonly opens
                    # with ``取消`` selected. Up selects ``执行`` deterministically.
                    relink.press(D_PAD_UP_KEY)
                    relink.press(CROSS_KEY)
                log.info("能力提升第 %d 轮：已定位‘执行’并发送 Cross，等待突破成功页", round_index)
                state["phase"] = "success_wait"
                success = _wait_for_ability_success(relink, timeout_seconds)
                if success is None:
                    log.error(
                        "能力提升第 %d 轮在执行后未识别到 Over the Limit 成功页，已停止",
                        round_index,
                    )
                    relink.show_toast("能力提升重抽", "未识别突破成功页，已停止，请检查日志")
                    break
                success_frame, success_items = success
                success_side = _ability_offer_side(success_items, success_frame)
                success_rolls = []
                if success_side is not None:
                    success_rolls, _ = extract_ability_rolls(
                        success_items,
                        success_frame,
                        side=success_side,
                        star_evidence=journal.star_evidence(),
                    )
                log.info(
                    "能力提升第 %d 轮：识别到突破成功页，等待 %.1f 秒后读取成功属性并发送 Cross；成功页词条=%s",
                    round_index,
                    success_settle_seconds,
                    [(roll.attribute, roll.stars, roll.value) for roll in success_rolls],
                )
                sleep(success_settle_seconds)
                state["phase"] = "success_advancing"
                result = _advance_ability_success_to_result(
                    relink,
                    timeout_seconds,
                    continue_interval_seconds=success_continue_interval_seconds,
                )
            elif stage[0] == "result":
                # Accept an already-open result page after a manual recovery or
                # restart, without repeating a destructive confirmation input.
                state["phase"] = "result_evaluate"
                result = (stage[1], stage[2])
            else:
                result = None

            if result is None:
                log.error("能力提升第 %d 轮在执行后未识别到完整覆盖页，已停止", round_index)
                relink.show_toast("能力提升重抽", "超时未识别完整覆盖页，已停止，请检查日志")
                break
            frame, items = result
            observed_msp = observe_msp(frame, items, f"第 {round_index} 轮结果页")
            if msp_limit_reached(observed_msp, f"第 {round_index} 轮结果页"):
                break
            state["phase"] = "result_evaluate"
            star_evidence = journal.star_evidence()
            old_rolls, old_unknown = extract_ability_rolls(
                items, frame, side="old", star_evidence=star_evidence
            )
            new_rolls, new_unknown = extract_ability_rolls(
                items, frame, side="new", star_evidence=star_evidence
            )
            raw_ocr = [
                str(item.get("text", "")).strip()
                for item in items
                if str(item.get("text", "")).strip()
            ]
            unknown = [*old_unknown, *new_unknown]
            evaluation = evaluate_ability_rolls(
                old_rolls,
                new_rolls,
                total_enabled=total_enabled and stop_mode == ABILITY_STOP_MODE_ATTRIBUTES,
                total_min=total_min,
                thresholds=thresholds if stop_mode == ABILITY_STOP_MODE_ATTRIBUTES else {},
                compare_enabled=compare_enabled and stop_mode == ABILITY_STOP_MODE_ATTRIBUTES,
                auto_overwrite=auto_overwrite and stop_mode == ABILITY_STOP_MODE_ATTRIBUTES,
                attribute_thresholds_enabled=(
                    attribute_thresholds_enabled
                    and stop_mode == ABILITY_STOP_MODE_ATTRIBUTES
                ),
                attribute_sum_enabled=(
                    attribute_sum_enabled
                    and stop_mode == ABILITY_STOP_MODE_ATTRIBUTES
                ),
                attribute_sum_min=attribute_sum_min,
                attribute_groups=(
                    attribute_groups
                    if stop_mode == ABILITY_STOP_MODE_ATTRIBUTES
                    else None
                ),
            )
            reason = (
                f"整体条件：{evaluation.overall_reason}（{evaluation.total_reason}；"
                f"{evaluation.attributes_reason}；{evaluation.attribute_sum_reason}）；"
                f"逐项条件：{evaluation.comparison_reason}"
            )
            validations = [
                f"{roll.attribute}: {roll.star_validation}"
                for roll in [*old_rolls, *new_rolls]
                if roll.star_validation
            ]
            if validations:
                reason += "；星数交叉验证：" + " | ".join(validations)
            log.info(
                "能力提升第 %d 轮 | 新词条=%s | 旧词条=%s | %s | 星数来源=%s | OCR未归类=%s",
                round_index,
                [
                    (roll.attribute, roll.stars, roll.value, roll.stars_source)
                    for roll in new_rolls
                ],
                [
                    (roll.attribute, roll.stars, roll.value, roll.stars_source)
                    for roll in old_rolls
                ],
                reason,
                {
                    roll.attribute: roll.stars_source
                    for roll in [*old_rolls, *new_rolls]
                    if roll.attribute
                },
                unknown or "无",
            )
            decision = (
                "accept"
                if evaluation.auto_accept
                else "stop"
                if evaluation.should_accept
                else "reroll"
            )
            planned_action = {
                "accept": "cross",
                "stop": "wait_manual",
                "reroll": "moon",
            }[decision]
            planned_action_label = {
                "cross": "自动发送 Cross",
                "wait_manual": "停在覆盖确认页，等待手动选择",
                "moon": "发送 Moon 继续重抽",
            }[planned_action]
            reason += f"；计划处理：{planned_action_label}"
            log.info(
                "能力提升第 %d 轮计划处理=%s | decision=%s | auto_overwrite=%s | overall_ok=%s | comparison_ok=%s",
                round_index,
                planned_action_label,
                decision,
                auto_overwrite,
                evaluation.overall_ok,
                evaluation.comparison_ok,
            )
            # Dispatch from the single persisted decision. Re-evaluating the
            # two booleans below independently made it too easy for a future
            # change to record one outcome and send another input.
            if decision == "accept":
                state["phase"] = "accepting"
                confirmed_result = _wait_for_ability_yes_highlight(
                    relink,
                    timeout_seconds,
                    settle_seconds=accept_highlight_settle_seconds,
                )
                if confirmed_result is None:
                    actual_action = "highlight_unknown"
                    reason += "；实际动作：未确认‘是’高亮，未发送 Cross/Moon"
                    log.warning(
                        "能力提升第 %d 轮满足自动覆盖条件，但未确认‘是’高亮，安全停止",
                        round_index,
                    )
                    relink.show_toast(
                        "能力提升重抽",
                        "已达标但未确认‘是’高亮，已停止，请人工确认",
                    )
                    journal.record_round(
                        old_rolls=old_rolls,
                        new_rolls=new_rolls,
                        raw_ocr=raw_ocr,
                        unknown_ocr=unknown,
                        decision="stop",
                        reason=reason,
                        action=actual_action,
                    )
                    break
                play_ability_qualified_alert()
                with AUTOMATION_INPUT_LOCK:
                    relink.press(CROSS_KEY)
                actual_action = "cross_sent"
                log.info(
                    "能力提升第 %d 轮达标，已发送 Cross（键=%r）并执行覆盖",
                    round_index,
                    CROSS_KEY,
                )
                relink.show_toast("能力提升重抽", f"第 {round_index} 轮已达标并自动覆盖")
                journal.record_round(
                    old_rolls=old_rolls,
                    new_rolls=new_rolls,
                    raw_ocr=raw_ocr,
                    unknown_ocr=unknown,
                    decision=decision,
                    reason=reason + f"；实际动作：{actual_action}",
                    action=actual_action,
                )
                if stop_after_completion:
                    break
                state["phase"] = "post_accept_wait"
                log.info(
                    "能力提升第 %d 轮覆盖已完成，配置为继续重抽；等待下一张稳定候选页",
                    round_index,
                )
                relink.show_toast(
                    "能力提升重抽",
                    f"第 {round_index} 轮已达标并覆盖，等待下一轮候选页",
                )
                next_offer = _wait_for_ability_stage(
                    relink,
                    timeout_seconds,
                    expected_stage="offer",
                )
                if next_offer is None:
                    log.error(
                        "能力提升第 %d 轮覆盖完成后未识别到下一张候选页，安全停止",
                        round_index,
                    )
                    relink.show_toast(
                        "能力提升重抽",
                        "覆盖完成后未识别下一轮候选页，已安全停止",
                    )
                    break
                pending_stage = next_offer
                continue
            if decision == "stop":
                state["phase"] = "waiting_manual"
                play_ability_qualified_alert()
                actual_action = "wait_manual"
                log.info(
                    "能力提升第 %d 轮达标，已停在覆盖确认页；不发送 Cross/Moon，等待手动选择‘是’或‘否’",
                    round_index,
                )
                relink.show_toast(
                    "能力提升重抽",
                    "发现符合条件的词条，已停在覆盖确认页，请手动选择‘是’或‘否’",
                )
                journal.record_round(
                    old_rolls=old_rolls,
                    new_rolls=new_rolls,
                    raw_ocr=raw_ocr,
                    unknown_ocr=unknown,
                    decision=decision,
                    reason=reason + f"；实际动作：{actual_action}",
                    action=actual_action,
                )
                # Keep the worker alive in a real waiting state. The user's
                # manual choice is inferred from the next stable page and the
                # MSP delta: returning without spending resumes rerolling,
                # while a spent MSP means the user accepted the overwrite.
                paused_msp = current_msp
                manual_outcome = "waiting"
                while relink.running:
                    if relink.paused:
                        sleep(0.5)
                        continue
                    manual_stage = _wait_for_ability_stage(
                        relink,
                        min(5.0, timeout_seconds),
                        expected_stage=("offer", "confirmation", "success", "result"),
                    )
                    if manual_stage is None:
                        continue
                    manual_frame, manual_items = manual_stage[1], manual_stage[2]
                    manual_msp = observe_msp(
                        manual_frame,
                        manual_items,
                        f"第 {round_index} 轮人工处理后",
                    )
                    if manual_stage[0] == "result":
                        continue
                    if (
                        paused_msp is not None
                        and manual_msp is not None
                        and manual_msp < paused_msp
                    ):
                        manual_outcome = "accepted"
                        log.info(
                            "能力提升第 %d 轮检测到人工确认覆盖：MSP %d -> %d，结束自动重抽",
                            round_index,
                            paused_msp,
                            manual_msp,
                        )
                        break
                    if manual_stage[0] == "offer":
                        manual_outcome = "reroll"
                        log.info(
                            "能力提升第 %d 轮检测到人工取消覆盖且 MSP 未减少，恢复自动重抽",
                            round_index,
                        )
                        break
                if manual_outcome == "accepted" or not relink.running:
                    break
                if manual_outcome == "reroll":
                    continue
                continue
            state["phase"] = "rerolling"
            _cancel_ability_result(relink)
            actual_action = "moon_sent"
            journal.record_round(
                old_rolls=old_rolls,
                new_rolls=new_rolls,
                raw_ocr=raw_ocr,
                unknown_ocr=unknown,
                decision=decision,
                reason=reason + "；实际动作：moon_sent",
                action=actual_action,
            )
            sleep(reroll_settle_seconds)
    finally:
        _clear_ability_reroll_state(relink, state)
        log.info("能力提升重抽状态机已清除，自动化输入已归零")


def run_ability_reroll(args) -> int:
    global log
    log = Log("GBFR-Ability", "i").logger
    config = _load_ability_config(Path(args.ability_config_file) if args.ability_config_file else None)
    journal_path = Path(args.ability_stats_file) if args.ability_stats_file else Path(get_runtime_log_dir()) / "ability-journal.json"
    journal = AbilityJournal(journal_path)
    if not args.background and args.input_profile:
        try:
            profile_data = json.loads(Path(args.input_profile).read_text(encoding="utf-8"))
            mapping = profile_data.get("foreground_keys", {})
            if isinstance(mapping, dict) and all(field in mapping for field in AUTOMATION_KEY_FIELDS):
                candidate = {field: str(mapping[field]).lower() for field in AUTOMATION_KEY_FIELDS}
                if all(value in Controller.KEY_MAP for value in candidate.values()) and len(set(candidate.values())) == len(candidate):
                    apply_foreground_keymap(candidate)
                    log.info("能力提升重抽已应用现有 Chiaki Cross 键位：%s", candidate["cross"])
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            log.warning("能力提升输入配置读取失败，沿用默认键位", exc_info=True)
    relink = Controller(
        args.window_title,
        "GBFR 能力提升重抽",
        ABILITY_RELINK_DICT,
        background=args.background,
        ui_language=args.ui_language,
        recognition_profile=args.recognition_profile,
    )
    relink.set_battle_stop_key("f2")
    relink.activate_automation("能力提升重抽")
    try:
        ability_reroll_loop(relink, config, journal)
    finally:
        _clear_ability_reroll_state(relink, {})
        clear_automation_flow_state("能力提升重抽退出")
        relink.stop()
    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        prog="GBFR_AutoReBattle",
        description="GBFR 自动重战工具",
    )
    parser.add_argument("--silent", action="store_true", help="兼容原参数；Chiaki 窗口仍必须可见")
    parser.add_argument(
        "--background",
        action="store_true",
        help="后台模式：捕获 Chiaki 窗口并向其发送按键，不抢占当前前台窗口",
    )
    parser.add_argument(
        "--start-chiaki",
        action="store_true",
        help="由本工具启动同目录的 Chiaki，再等待串流窗口",
    )
    parser.add_argument(
        "--chiaki-exe",
        default=None,
        help="Chiaki 可执行文件路径；省略时查找同目录 Chiaki\\chiaki.exe",
    )
    parser.add_argument(
        "--auto-recover",
        action="store_true",
        help="战斗中画面长时间静止时重启当前绑定的 Chiaki 串流",
    )
    parser.add_argument(
        "--reconnect-nickname",
        default="",
        help="Chiaki 已注册主机的昵称，用于精确重连",
    )
    parser.add_argument(
        "--reconnect-host",
        default="",
        help="重连主机地址或 IP；不会按窗口标题猜测主机",
    )
    parser.add_argument(
        "--freeze-timeout-seconds",
        type=float,
        default=CHIAKI_FREEZE_TIMEOUT_SECONDS,
        help="画面连续静止多少秒后触发重连，默认 600",
    )
    parser.add_argument(
        "--reconnect-once",
        action="store_true",
        help="先执行一次真实串流重连与续战/主城流程，再进入完整挂机循环",
    )
    parser.add_argument(
        "--account-id",
        action="store_true",
        help="打开 Sony 登录并获取 PSN AccountID，然后退出",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="打开统一控制面板（Chiaki + 自动重战 + AccountID）",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="检查打包后的后台截图与虚拟手柄组件，然后退出",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="开启恢复诊断日志：记录配置、窗口尺寸、HUD/OCR 判定，不改变自动按键逻辑",
    )
    parser.add_argument("--window-title", default=CHIAKI_WINDOW_TITLE, help="Chiaki 串流窗口标题")
    parser.add_argument(
        "--launcher-pid",
        type=int,
        default=0,
        help="由统一控制面板传入；该控制面板退出时自动停止本子进程",
    )
    parser.add_argument(
        "--ui-language",
        choices=("auto", "zh", "ja"),
        default="auto",
        help="游戏界面语言：auto 自动识别，zh 简体中文，ja 日文",
    )
    parser.add_argument(
        "--recognition-profile",
        choices=tuple(RECOGNITION_PROFILES),
        default="auto",
        help="识别画面适配档位；实际客户区会自动裁剪黑边并按比例缩放",
    )
    parser.add_argument("--l2-key", default=L2_KEY, help="Chiaki 中 L2 对应的键盘按键，默认 L")
    parser.add_argument(
        "--input-profile",
        default=None,
        help="一键同步生成的前台 Chiaki 键位与后台 DS4 选项 JSON",
    )
    parser.add_argument(
        "--refocus-seconds",
        type=float,
        default=REFOCUS_SECONDS,
        help="上方或右侧技能持续高亮多少秒后恢复索敌，默认 15",
    )
    parser.add_argument(
        "--refocus-mode",
        choices=(
            REFOCUS_MODE_MELEE,
            REFOCUS_MODE_RANGED,
            REFOCUS_MODE_BOSS_RING,
            REFOCUS_MODE_L2_RING,
            REFOCUS_MODE_L2_SBA,
            REFOCUS_MODE_SBA_RING_GUARDED,
            REFOCUS_MODE_RING_ARC_EXPERIMENT,
        ),
        default=REFOCUS_MODE_DEFAULT,
        help="索敌方案：近战/法系、远程、BOSS环实验、目标环实验、持续L2、奥义保护或部分圆弧实验",
    )
    parser.add_argument(
        "--max-battles",
        type=int,
        default=0,
        help="完成指定场数后自动停止并关闭 Chiaki；0 表示不限制",
    )
    parser.add_argument(
        "--max-runtime-minutes",
        type=float,
        default=0.0,
        help="运行指定分钟后自动停止并关闭 Chiaki；0 表示不限制",
    )
    parser.add_argument(
        "--stop-at",
        default="",
        help="本机时钟到达 HH:MM 后自动停止并关闭 Chiaki，例如 23:30",
    )
    parser.add_argument(
        "--schedule-file",
        default=None,
        help="运行中读取自动结束设置的 JSON 文件；统一界面会自动传入",
    )
    parser.add_argument(
        "--stats-file",
        default=None,
        help="战斗统计 JSON 路径；统一界面会自动传入",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="启动进程后直接开始自动重战，不等待 F1",
    )
    parser.add_argument(
        "--invert-movement",
        action="store_true",
        help="后台虚拟手柄反向移动轴；仅客户机方向相反时启用",
    )
    parser.add_argument(
        "--ability-reroll",
        action="store_true",
        help="启动独立的能力提升重抽功能，不进入自动重战状态机",
    )
    parser.add_argument(
        "--ability-config-file",
        default=None,
        help="能力提升重抽配置 JSON 路径",
    )
    parser.add_argument(
        "--ability-stats-file",
        default=None,
        help="能力提升词条统计 JSON 路径",
    )
    return parser.parse_args()


def _find_stream_window_with_title(
    title: str, *, require_stream_marker: bool = True, strict_title: bool = False
) -> tuple[int, str] | None:
    """Find a visible Chiaki stream window and return ``(hwnd, caption)``.

    The GUI's explicit auto-find action may use a conservative fallback to
    discover a title. Runtime binding passes ``strict_title=True`` so the
    configured title field remains authoritative.
    """
    import win32api
    import win32gui
    import win32process

    requested = (title or "").strip().lower()
    exact: list[tuple[int, str]] = []
    candidates: list[tuple[int, int, int, str]] = []

    def process_name(process_id: int) -> str:
        try:
            handle = win32api.OpenProcess(0x1000 | 0x0010, False, process_id)
            if not handle:
                return ""
            try:
                return os.path.basename(
                    win32process.GetModuleFileNameEx(handle, 0)
                ).lower()
            finally:
                win32api.CloseHandle(handle)
        except Exception:
            return ""

    def callback(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        caption = win32gui.GetWindowText(hwnd).strip()
        if not caption:
            return True
        lowered = caption.lower()
        # Never treat this control panel as the Chiaki stream. A stale title
        # saved from an earlier faulty capture can otherwise match exactly and
        # prevent the real Chiaki process from being launched.
        if (
            "gbfr 自动重战" in caption
            or "gbfr autorebattle" in lowered
            or "控制台" in caption
        ):
            return True
        try:
            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            process_id = int(process_id)
        except Exception:
            return True
        if any(
            marker in lowered
            for marker in (
                "settings", "configuration", "register", "registration", "设置", "注册"
            )
        ):
            return True
        process_is_chiaki = process_name(process_id).startswith("chiaki")
        title_is_stream = "stream" in lowered or "串流" in caption
        title_is_chiaki = "chiaki" in lowered
        title_matches_request = bool(requested and requested in lowered)
        # A title fragment such as "Stream" or "串流" is not enough: browser
        # tabs and forum pages can contain it too.  Automated binding and
        # capture must only accept a window owned by Chiaki itself.
        if title_matches_request and process_is_chiaki:
            exact.append((hwnd, caption))
            return True
        if strict_title and requested:
            return True
        if require_stream_marker and not title_is_stream:
            return True
        if not process_is_chiaki:
            return True
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            area = max(0, right - left) * max(0, bottom - top)
        except Exception:
            area = 0
        score = 0
        if process_is_chiaki:
            score += 70
        if title_is_stream:
            score += 50
        if title_is_chiaki:
            score += 25
        if area >= 400_000:
            score += 25
        if score >= 70:
            candidates.append((score, area, hwnd, caption))
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return None
    if exact:
        return exact[0]
    if not candidates:
        return None
    _, _, hwnd, caption = max(candidates, key=lambda item: (item[0], item[1]))
    return hwnd, caption


def _find_window_handle(title: str) -> int | None:
    """Return a visible Chiaki stream window handle."""
    found = _find_stream_window_with_title(title, strict_title=True)
    return found[0] if found else None


def _resize_window_client_area(hwnd: int, width: int, height: int) -> bool:
    """Set a Chiaki HWND's client area to an exact 16:9 size."""
    import win32con
    import win32gui

    if not hwnd or not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
        return False
    client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    adjusted = adjust_window_rect_ex(
        (0, 0, int(width), int(height)), style, False, ex_style
    )
    outer_width = adjusted[2] - adjusted[0]
    outer_height = adjusted[3] - adjusted[1]
    flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
    win32gui.SetWindowPos(
        hwnd,
        0,
        client_left + adjusted[0],
        client_top + adjusted[1],
        outer_width,
        outer_height,
        flags,
    )
    c_left, c_top, c_right, c_bottom = win32gui.GetClientRect(hwnd)
    return (c_right - c_left, c_bottom - c_top) == (int(width), int(height))


def _find_window(title: str) -> bool:
    return _find_window_handle(title) is not None


def _start_chiaki(args) -> subprocess.Popen | None:
    """Start the bundled Chiaki GUI when the unified launcher requests it."""
    if not args.start_chiaki:
        return None
    if _find_window(args.window_title):
        log.info("已检测到 Chiaki 窗口，不重复启动")
        return None

    base_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = []
    if args.chiaki_exe:
        candidates.append(Path(args.chiaki_exe).expanduser())
    candidates.extend((base_dir / "Chiaki" / "chiaki.exe", base_dir / "chiaki.exe"))
    chiaki_path = next((path for path in candidates if path.is_file()), None)
    if chiaki_path is None:
        raise FileNotFoundError(
            "未找到 Chiaki。请把 Chiaki 文件夹放到工具目录，或使用 --chiaki-exe 指定 chiaki.exe"
        )

    log.info("启动内置 Chiaki: %s", chiaki_path)
    return subprocess.Popen([str(chiaki_path)], cwd=str(chiaki_path.parent))


def _self_command(*extra: str) -> list[str]:
    """Build a child command for source and Nuitka-frozen execution."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra]
    return [sys.executable, str(Path(__file__).resolve()), *extra]


_SHELL_EXECUTE_ERRORS = {
    2: "找不到启动文件",
    3: "找不到启动路径",
    5: "访问被拒绝，可能是 UAC/安全策略阻止了提权",
    8: "内存不足",
    11: "启动文件格式无效",
    12: "启动文件不是有效的 Windows 应用程序",
    26: "发生共享冲突",
    27: "文件关联不完整",
    28: "DDE 超时",
    29: "DDE 失败",
    30: "DDE 忙",
    31: "没有可用的文件关联",
    32: "无法启动关联程序",
    1223: "用户取消了 UAC 确认",
}


def _shell_execute_error_text(result: int) -> str:
    """Translate the small ShellExecuteW failure code set for diagnostics."""
    return _SHELL_EXECUTE_ERRORS.get(int(result), f"Windows 返回码 {int(result)}")


def _ensure_gui_admin() -> bool:
    """Relaunch the control panel once with elevation so children inherit it."""
    if ctypes.windll.shell32.IsUserAnAdmin():
        return True

    compiled = bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))
    executable_path = Path(sys.executable).resolve()
    executable = str(executable_path)
    parameters = subprocess.list2cmdline(
        sys.argv[1:]
        if compiled
        else [str(Path(__file__).resolve()), *sys.argv[1:]]
    )
    working_directory = executable_path.parent
    if not executable_path.is_file():
        message = (
            "无法获取管理员权限：找不到当前启动文件。\n\n"
            f"启动文件：{executable_path}"
        )
        log.error(message.replace("\n", " | "))
        ctypes.windll.user32.MessageBoxW(None, message, "Chiaki + GBFR 自动重战", 0x10)
        return False

    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", executable, parameters, str(working_directory), 1
    )
    if result <= 32:
        detail = _shell_execute_error_text(result)
        log.error(
            "管理员提权失败 | ShellExecuteW=%s (%s) | exe=%s | cwd=%s | 参数=%s",
            result, detail, executable_path, working_directory, parameters or "<无>",
        )
        ctypes.windll.user32.MessageBoxW(
            None,
            "无法获取管理员权限，自动重战无法启动。\n\n"
            f"原因：{detail}\n启动文件：{executable_path}",
            "Chiaki + GBFR 自动重战",
            0x10,
        )
    return False


def run_unified_gui(args) -> int:
    """Run a small controller panel while keeping Chiaki's native UI intact."""
    root = tk.Tk()
    root.title("GBFR 自动重战 · Chiaki 控制台")
    # Keep enough horizontal room for the Chinese labels, but allow the panel
    # to be resized down without making fixed-width controls overlap.
    root.geometry("980x960")
    root.minsize(860, 820)
    root.columnconfigure(0, weight=0, minsize=220)
    root.columnconfigure(1, weight=1)
    root.columnconfigure(2, weight=0, minsize=220)

    palette = {
        "surface": "#f3f7fb",
        "panel": "#f8fbff",
        "ink": "#17324d",
        "muted": "#5f7080",
        "blue": "#0c72b8",
        "blue_hover": "#075b95",
        "ice": "#d9effb",
        "gold": "#e0b84f",
        "gold_soft": "#f5e7b5",
        "danger": "#8a3b2c",
    }
    root.configure(bg=palette["surface"])
    root.option_add("*Font", ("Segoe UI", 9))
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Treeview",
        background="#ffffff",
        fieldbackground="#ffffff",
        foreground=palette["ink"],
        bordercolor="#bdd4e5",
        rowheight=24,
    )
    style.configure(
        "Treeview.Heading",
        background=palette["ice"],
        foreground=palette["ink"],
        relief="flat",
    )
    style.configure(
        "TCombobox",
        fieldbackground="#ffffff",
        background="#ffffff",
        foreground=palette["ink"],
        bordercolor="#9dbdd3",
        arrowsize=14,
    )

    app_icon = None
    window_icon = None
    header_icon = None
    icon_path = Path(__file__).resolve().parent / "assets" / "gbfr-crystal-icon.png"
    try:
        app_icon = tk.PhotoImage(file=str(icon_path))
        window_icon = app_icon.subsample(4, 4)
        root.iconphoto(True, window_icon)
        header_icon = app_icon.subsample(20, 20)
    except (OSError, tk.TclError):
        pass

    chiaki_process = {"value": None}
    automation_process = {"value": None}
    active_run_kind = {"value": None}
    automation_output = {"value": None}
    active_background_mode = {"value": None}
    # Keep a byte offset rather than a TextIO cookie. The automation child may
    # write a partial multi-byte character between two UI polling intervals.
    existing_console_log = Path(get_runtime_log_dir()) / "automation-console.log"
    try:
        initial_log_offset = (
            existing_console_log.stat().st_size
            if existing_console_log.is_file()
            else 0
        )
    except OSError:
        initial_log_offset = 0
    # Historical output may have been produced under a different console code
    # page. Start at EOF and show only output generated after this GUI opens.
    log_cursor = {"value": initial_log_offset}
    log_pending = {"value": b""}
    settings_path = Path(get_runtime_log_dir()).parent / "settings.json"
    stats_path = Path(get_runtime_log_dir()) / "session-stats.json"
    schedule_path = Path(get_runtime_log_dir()) / "schedule.json"
    input_profile_path = Path(get_runtime_log_dir()).parent / "input-profile.json"

    def load_gui_settings() -> dict[str, object]:
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    saved_settings = load_gui_settings()
    runtime_root = Path(
        sys.executable if getattr(sys, "frozen", False) else __file__
    ).resolve().parent
    saved_chiaki = str(saved_settings.get("chiaki_exe", "")).strip()
    requested_chiaki = str(args.chiaki_exe or "").strip()
    detected_chiaki = find_chiaki_executables(runtime_root)
    initial_chiaki_path = (
        saved_chiaki
        or requested_chiaki
        or (str(detected_chiaki[0]) if detected_chiaki else "Chiaki\\chiaki.exe")
    )
    saved_background = (
        True
        if args.background
        else saved_settings.get("background_mode", False)
    )
    if not isinstance(saved_background, bool):
        saved_background = bool(args.background)

    background = tk.BooleanVar(value=saved_background)
    background_label = tk.StringVar(
        value=(
            "后台运行（已勾选；启动后会锁定，停止后可取消）"
            if saved_background
            else "后台运行（勾选后启用；运行中锁定，停止后可取消）"
        )
    )
    status = tk.StringVar(
        value=(
            "就绪：已选择后台模式（启动前将检查环境）"
            if saved_background
            else "就绪：已选择前台模式"
        )
    )
    invert_movement = tk.BooleanVar(
        value=bool(saved_settings.get("invert_movement", False))
    )
    saved_window_title = str(
        saved_settings.get("window_title", args.window_title)
    ).strip()
    # Older builds could persist this application's own window title after an
    # over-eager automatic capture. Never let that stale value block Chiaki
    # startup; users who need a custom stream caption can capture it manually
    # after the stream is connected.
    if (
        not saved_window_title
        or "gbfr 自动重战" in saved_window_title
        or "gbfr autorebattle" in saved_window_title.lower()
        or "控制台" in saved_window_title
    ):
        saved_window_title = CHIAKI_WINDOW_TITLE
    title_var = tk.StringVar(value=saved_window_title)
    saved_app_language = str(saved_settings.get("app_language", "zh")).strip().lower()
    if saved_app_language not in APP_LANGUAGE_LABELS:
        saved_app_language = "zh"
    app_language_var = tk.StringVar(value=APP_LANGUAGE_LABELS[saved_app_language])
    app_language_label_to_code = {
        label: code for code, label in APP_LANGUAGE_LABELS.items()
    }
    saved_language = str(saved_settings.get("ui_language", args.ui_language)).strip().lower()
    if saved_language not in UI_LANGUAGE_LABELS:
        saved_language = "auto"
    game_language_label_to_code = {}
    ui_language_var = tk.StringVar()
    language_combo_holder: dict[str, ttk.Combobox | None] = {"value": None}

    def selected_game_language_code() -> str:
        return game_language_label_to_code.get(ui_language_var.get(), "auto")

    def set_game_language_labels(app_code: str) -> None:
        nonlocal game_language_label_to_code
        labels = GAME_LANGUAGE_LABELS.get(app_code, GAME_LANGUAGE_LABELS["zh"])
        game_language_label_to_code = {label: code for code, label in labels.items()}
        ui_language_var.set(labels.get(saved_language, labels["auto"]))
        language_combo_widget = language_combo_holder["value"]
        if language_combo_widget is not None:
            language_combo_widget.configure(values=tuple(labels.values()))

    set_game_language_labels(saved_app_language)
    recognition_profile_var = tk.StringVar(
        value=str(saved_settings.get("recognition_profile", "auto"))
    )
    if recognition_profile_var.get() not in RECOGNITION_PROFILES:
        recognition_profile_var.set("auto")
    recognition_profile_labels = {
        key: str(value["label"]) for key, value in RECOGNITION_PROFILES.items()
    }
    recognition_profile_label_to_code = {
        label: key for key, label in recognition_profile_labels.items()
    }
    def selected_recognition_profile_code() -> str:
        value = recognition_profile_var.get()
        return recognition_profile_label_to_code.get(value, value if value in RECOGNITION_PROFILES else "auto")
    path_var = tk.StringVar(value=initial_chiaki_path)
    auto_recover = tk.BooleanVar(value=bool(saved_settings.get("auto_recover", False)))
    reconnect_nickname_var = tk.StringVar(
        value=str(saved_settings.get("reconnect_nickname", ""))
    )
    reconnect_host_var = tk.StringVar(
        value=str(saved_settings.get("reconnect_host", ""))
    )
    freeze_minutes_var = tk.StringVar(
        value=str(saved_settings.get("freeze_minutes", "10"))
    )
    max_battles_var = tk.StringVar(value=str(saved_settings.get("max_battles", "")))
    max_runtime_var = tk.StringVar(value=str(saved_settings.get("max_runtime_minutes", "")))
    stop_at_var = tk.StringVar(value=str(saved_settings.get("stop_at", "")))
    saved_refocus_mode = str(
        saved_settings.get("refocus_mode", REFOCUS_MODE_DEFAULT)
    ).strip().lower()
    if saved_refocus_mode not in REFOCUS_MODE_LABELS:
        saved_refocus_mode = REFOCUS_MODE_DEFAULT
    refocus_mode_label_to_code = {
        label: code for code, label in REFOCUS_MODE_LABELS.items()
    }
    refocus_mode_var = tk.StringVar(
        value=REFOCUS_MODE_LABELS[saved_refocus_mode]
    )
    stats_summary = tk.StringVar(value="等待自动重战启动")
    stats_detail = tk.StringVar(value="当前场耗时：--:--")
    automation_state = tk.StringVar(value="自动战斗：未启动")
    automation_state_widget: dict[str, tk.Label | None] = {"value": None}
    automation_state_current = {"tone": "idle"}
    automation_primary_text = tk.StringVar(value="启动自动重战")
    automation_primary_widget: dict[str, tk.Button | None] = {"value": None}
    log_follow = {"value": True}
    log_follow_text = tk.StringVar(value="暂停自动滚动")
    ability_config_path = Path(get_runtime_log_dir()).parent / "ability-reroll.json"
    ability_journal_path = Path(get_runtime_log_dir()) / "ability-journal.json"
    saved_ability = saved_settings.get("ability_reroll", {})
    if not isinstance(saved_ability, dict):
        saved_ability = {}
    ability_total_enabled = tk.BooleanVar(
        value=_config_bool(saved_ability.get("total_enabled"), True)
    )
    ability_total_min = tk.StringVar(value=str(saved_ability.get("total_min", 36)))
    ability_stop_mode = tk.StringVar(
        value=normalize_ability_stop_mode(saved_ability.get("stop_mode"))
    )
    ability_msp_spent_limit = tk.StringVar(
        value=str(saved_ability.get("msp_spent_limit", 0))
    )
    ability_msp_remaining_limit = tk.StringVar(
        value=str(saved_ability.get("msp_remaining_limit", 0))
    )
    ability_auto_overwrite = tk.BooleanVar(
        value=_config_bool(saved_ability.get("auto_overwrite"), False)
    )
    ability_compare_enabled = tk.BooleanVar(
        value=_config_bool(saved_ability.get("auto_overwrite_if_all_better"), False)
    )
    ability_stop_after_completion = tk.BooleanVar(
        value=_config_bool(saved_ability.get("stop_after_completion"), True)
    )
    ability_timing_defaults = _load_ability_config(None)
    ability_timing_specs = (
        (
            "offer_navigation_settle_seconds",
            "候选页移动后等待",
            0.1,
            10.0,
            ABILITY_NAVIGATION_SETTLE_SECONDS,
        ),
        (
            "success_settle_seconds",
            "成功页首次等待",
            0.1,
            30.0,
            ABILITY_SUCCESS_SETTLE_SECONDS,
        ),
        (
            "success_continue_interval_seconds",
            "成功页重复 Cross 间隔",
            0.1,
            30.0,
            ABILITY_SUCCESS_CONTINUE_INTERVAL_SECONDS,
        ),
        (
            "reroll_settle_seconds",
            "覆盖页取消后等待",
            0.1,
            30.0,
            ABILITY_REROLL_SETTLE_SECONDS,
        ),
        (
            "accept_highlight_settle_seconds",
            "自动覆盖高亮复核等待",
            0.1,
            10.0,
            ABILITY_ACCEPT_HIGHLIGHT_SETTLE_SECONDS,
        ),
        (
            "result_timeout_seconds",
            "单轮页面识别超时",
            10.0,
            600.0,
            ABILITY_RESULT_TIMEOUT_SECONDS,
        ),
    )
    ability_timing_vars: dict[str, tk.StringVar] = {}
    for key, _label, _minimum, _maximum, fallback in ability_timing_specs:
        raw_value = saved_ability.get(key, ability_timing_defaults.get(key, fallback))
        try:
            timing_value = float(raw_value)
            if not math.isfinite(timing_value):
                raise ValueError
        except (TypeError, ValueError):
            timing_value = float(fallback)
        ability_timing_vars[key] = tk.StringVar(value=f"{timing_value:.1f}")
    ability_attribute_names = tuple(name for name, _ in ABILITY_ATTRIBUTE_ALIASES)
    saved_thresholds = saved_ability.get("thresholds", {})
    if not isinstance(saved_thresholds, dict):
        saved_thresholds = {}
    legacy_thresholds: dict[str, int] = {}
    for raw_name, raw_min in saved_thresholds.items():
        canonical = normalize_ability_name(str(raw_name))
        if canonical is None:
            continue
        try:
            legacy_thresholds[canonical] = max(0, int(raw_min))
        except (TypeError, ValueError):
            continue
    try:
        legacy_sum_min = max(0, int(saved_ability.get("attribute_sum_min", 0) or 0))
    except (TypeError, ValueError):
        legacy_sum_min = 0
    saved_groups = _normalize_attribute_groups(
        saved_ability.get("attribute_groups"),
        legacy_thresholds=legacy_thresholds,
        legacy_thresholds_enabled=_config_bool(
            saved_ability.get("attribute_thresholds_enabled"), bool(legacy_thresholds)
        ),
        legacy_sum_enabled=_config_bool(saved_ability.get("attribute_sum_enabled"), False),
        legacy_sum_min=legacy_sum_min,
    )
    while len(saved_groups) < 4:
        saved_groups.append(
            {
                "name": f"组合 {len(saved_groups) + 1}",
                "enabled": False,
                "thresholds": {},
                "attribute_thresholds_enabled": True,
                "attribute_sum_enabled": False,
                "attribute_sum_min": 0,
                "rows": [],
            }
        )
    ability_group_states: list[dict[str, object]] = []
    for group_index, saved_group in enumerate(saved_groups[:4]):
        rows = saved_group.get("rows", [])
        if not isinstance(rows, list):
            rows = []
        row_states: list[dict[str, tk.Variable]] = []
        thresholds = saved_group.get("thresholds", {})
        thresholds = thresholds if isinstance(thresholds, dict) else {}
        for row_index in range(4):
            row = rows[row_index] if row_index < len(rows) and isinstance(rows[row_index], dict) else {}
            saved_name = normalize_ability_name(str(row.get("name", ""))) or ""
            if not saved_name:
                candidates = list(thresholds)
                saved_name = candidates[row_index] if row_index < len(candidates) else ability_attribute_names[row_index]
            try:
                saved_min = max(0, int(row.get("min", thresholds.get(saved_name, 8))))
            except (TypeError, ValueError):
                saved_min = 8
            row_states.append(
                {
                    "enabled": tk.BooleanVar(
                        value=_config_bool(row.get("enabled"), bool(saved_name in thresholds))
                    ),
                    "name": tk.StringVar(value=saved_name),
                    "min": tk.StringVar(value=str(saved_min)),
                }
            )
        ability_group_states.append(
            {
                "name": tk.StringVar(value=str(saved_group.get("name") or f"组合 {group_index + 1}")),
                "enabled": tk.BooleanVar(value=_config_bool(saved_group.get("enabled"), False)),
                "thresholds_enabled": tk.BooleanVar(
                    value=_config_bool(saved_group.get("attribute_thresholds_enabled"), bool(thresholds))
                ),
                "sum_enabled": tk.BooleanVar(
                    value=_config_bool(saved_group.get("attribute_sum_enabled"), False)
                ),
                "sum_min": tk.StringVar(value=str(saved_group.get("attribute_sum_min", 0))),
                "rows": row_states,
            }
        )
    ability_run_status = tk.StringVar(value="能力提升重抽：未启动")
    ability_primary_text = tk.StringVar(value="启动能力提升重抽")
    ability_primary_widget: dict[str, tk.Button | None] = {"value": None}
    ability_manual_notice_shown = {"value": False}
    ability_config_window = {"value": None}
    ability_journal_window = {"value": None}
    settings_window = {"value": None}

    def set_status(text: str) -> None:
        # The status bar is a one-line summary. Never allow a traceback or a
        # copied multi-line log record to expand it into the log area.
        compact = " ".join(str(text).replace("\r", "\n").splitlines()).strip()
        status.set(compact[:320])

    def set_automation_state(text: str, tone: str = "idle") -> None:
        """Show the actual automation lifecycle in one prominent GUI banner."""
        styles = {
            "idle": ("#e8eef2", "#334155"),
            "running": ("#dff3e5", "#17633a"),
            "paused": ("#fff1d6", "#915b00"),
            "recovering": ("#e3effa", "#155a92"),
            "stopped": ("#f3e4e1", "#9b2c21"),
        }
        automation_state.set(text)
        automation_state_current["tone"] = tone
        widget = automation_state_widget["value"]
        if widget is not None:
            background, foreground = styles[tone]
            widget.configure(bg=background, fg=foreground)
        process = automation_process["value"]
        primary = automation_primary_widget["value"]
        if primary is not None:
            if (
                process is not None
                and process.poll() is None
                and active_run_kind["value"] == "rebattle"
            ):
                automation_primary_text.set("自动重战生效\n点击停止")
                primary.configure(bg="#dff3e5", fg="#17633a", activebackground="#c7e8d1")
            else:
                automation_primary_text.set("启动自动重战")
                primary.configure(bg=palette["blue"], fg="#ffffff", activebackground=palette["blue_hover"])

    def set_ability_primary_state() -> None:
        """Keep the independent ability action visibly synchronized with its child."""
        process = automation_process["value"]
        is_running = (
            process is not None
            and process.poll() is None
            and active_run_kind["value"] == "ability"
        )
        button = ability_primary_widget["value"]
        if button is None:
            return
        if is_running:
            if ability_manual_notice_shown["value"]:
                ability_primary_text.set("能力提升重抽已暂停\n点击停止")
            else:
                ability_primary_text.set("能力提升重抽生效\n点击停止")
            button.configure(
                bg="#dff3e5",
                fg="#17633a",
                activebackground="#c7e8d1",
            )
        else:
            ability_primary_text.set("启动能力提升重抽")
            button.configure(
                bg=palette["blue"],
                fg="#ffffff",
                activebackground=palette["blue_hover"],
            )

    def toggle_ability_reroll() -> None:
        """Use one prominent action for the independent reroll workflow."""
        process = automation_process["value"]
        if (
            process is not None
            and process.poll() is None
            and active_run_kind["value"] == "ability"
        ):
            stop_automation()
        elif process is not None and process.poll() is None:
            set_status("当前正在运行自动重战，请先停止后再启动能力提升重抽")
        else:
            start_ability_reroll()

    def ability_config_payload(*, validate: bool) -> dict[str, object] | None:
        try:
            total_min = int(ability_total_min.get().strip() or "0")
        except ValueError:
            if validate:
                messagebox.showerror("能力提升设置无效", "总星数阈值必须是非负整数。", parent=root)
                return None
            total_min = 36
        if total_min < 0:
            if validate:
                messagebox.showerror("能力提升设置无效", "总星数阈值必须是非负整数。", parent=root)
                return None
            total_min = 0
        stop_mode = normalize_ability_stop_mode(ability_stop_mode.get())

        def read_msp_limit(variable: tk.StringVar, label: str, *, allow_zero: bool) -> int | None:
            try:
                value = int(variable.get().strip() or "0")
            except ValueError:
                if validate:
                    messagebox.showerror("能力提升设置无效", f"{label}必须是非负整数。", parent=root)
                    return None
                value = 0
            if value < 0 or (not allow_zero and value == 0):
                if validate:
                    suffix = "且不能为 0" if not allow_zero else ""
                    messagebox.showerror(
                        "能力提升设置无效",
                        f"{label}必须是非负整数{suffix}。",
                        parent=root,
                    )
                    return None
                value = 1 if not allow_zero else 0
            variable.set(str(value))
            return value

        msp_spent_limit = read_msp_limit(
            ability_msp_spent_limit,
            "已使用 MSP 停止阈值",
            allow_zero=stop_mode != ABILITY_STOP_MODE_SPENT_MSP,
        )
        msp_remaining_limit = read_msp_limit(
            ability_msp_remaining_limit,
            "剩余 MSP 停止阈值",
            allow_zero=True,
        )
        if msp_spent_limit is None or msp_remaining_limit is None:
            return None
        group_payloads: list[dict[str, object]] = []
        for group_index, group in enumerate(ability_group_states, start=1):
            name_var = group["name"]
            enabled_var = group["enabled"]
            thresholds_enabled_var = group["thresholds_enabled"]
            sum_enabled_var = group["sum_enabled"]
            sum_min_var = group["sum_min"]
            rows = group["rows"]
            assert isinstance(name_var, tk.StringVar)
            assert isinstance(enabled_var, tk.BooleanVar)
            assert isinstance(thresholds_enabled_var, tk.BooleanVar)
            assert isinstance(sum_enabled_var, tk.BooleanVar)
            assert isinstance(sum_min_var, tk.StringVar)
            assert isinstance(rows, list)
            try:
                sum_minimum = int(sum_min_var.get().strip() or "0")
            except ValueError:
                if validate and sum_enabled_var.get():
                    messagebox.showerror(
                        "能力提升设置无效",
                        f"组合 {group_index} 的指定属性星数之和必须是非负整数。",
                        parent=root,
                    )
                    return None
                sum_minimum = 0
            if sum_minimum < 0 or (sum_enabled_var.get() and sum_minimum == 0):
                if validate and sum_enabled_var.get():
                    messagebox.showerror(
                        "能力提升设置无效",
                        f"组合 {group_index} 启用星数之和时，阈值必须大于 0。",
                        parent=root,
                    )
                    return None
                sum_minimum = 1 if sum_enabled_var.get() else 0
            sum_min_var.set(str(sum_minimum))

            thresholds: dict[str, int] = {}
            saved_rows: list[dict[str, object]] = []
            for row_index, row in enumerate(rows):
                assert isinstance(row, dict)
                enabled = row["enabled"]
                name_var = row["name"]
                minimum_var = row["min"]
                assert isinstance(enabled, tk.BooleanVar)
                assert isinstance(name_var, tk.StringVar)
                assert isinstance(minimum_var, tk.StringVar)
                name = name_var.get().strip()
                try:
                    minimum = int(minimum_var.get().strip() or "0")
                except ValueError:
                    if validate and enabled.get():
                        messagebox.showerror(
                            "能力提升设置无效",
                            f"组合 {group_index} 第 {row_index + 1} 项星数必须是非负整数。",
                            parent=root,
                        )
                        return None
                    minimum = 0
                if enabled.get():
                    if name not in ability_attribute_names or minimum < 0:
                        if validate:
                            messagebox.showerror(
                                "能力提升设置无效",
                                f"组合 {group_index} 第 {row_index + 1} 项属性或最低星数无效。",
                                parent=root,
                            )
                            return None
                    else:
                        thresholds[name] = minimum
                saved_rows.append(
                    {"enabled": bool(enabled.get()), "name": name, "min": minimum}
                )
            if validate and enabled_var.get() and not (
                (thresholds_enabled_var.get() and thresholds)
                or (sum_enabled_var.get() and thresholds)
            ):
                messagebox.showerror(
                    "能力提升设置无效",
                    f"组合 {group_index} 启用后，请至少选择一项属性并启用逐项或星数之和条件。",
                    parent=root,
                )
                return None
            group_payloads.append(
                {
                    "name": name_var.get().strip() or f"组合 {group_index}",
                    "enabled": bool(enabled_var.get()),
                    "thresholds": thresholds,
                    "attribute_thresholds_enabled": bool(thresholds_enabled_var.get()),
                    "attribute_sum_enabled": bool(sum_enabled_var.get()),
                    "attribute_sum_min": sum_minimum,
                    "rows": saved_rows,
                }
            )
        first_group = group_payloads[0]
        if (
            validate
            and stop_mode == ABILITY_STOP_MODE_ATTRIBUTES
            and not ability_total_enabled.get()
            and not any(
                group.get("enabled")
                and (
                    (group.get("attribute_thresholds_enabled") and group.get("thresholds"))
                    or (group.get("attribute_sum_enabled") and group.get("thresholds"))
                )
                for group in group_payloads
            )
            and not ability_compare_enabled.get()
        ):
            messagebox.showerror(
                "能力提升设置无效",
                "请至少启用总星数、选择一个属性，或启用逐项优于旧词条条件。",
                parent=root,
            )
            return None
        timing_values: dict[str, float] = {}
        for key, label, minimum, maximum, fallback in ability_timing_specs:
            try:
                value = float(ability_timing_vars[key].get().strip())
                if not math.isfinite(value):
                    raise ValueError
            except (TypeError, ValueError):
                if validate:
                    messagebox.showerror(
                        "能力提升设置无效",
                        f"{label}必须是 {minimum:.1f} 到 {maximum:.1f} 秒。",
                        parent=root,
                    )
                    return None
                value = float(fallback)
            if value < minimum or value > maximum:
                if validate:
                    messagebox.showerror(
                        "能力提升设置无效",
                        f"{label}必须是 {minimum:.1f} 到 {maximum:.1f} 秒。",
                        parent=root,
                    )
                    return None
                value = min(max(value, minimum), maximum)
            value = round(value, 1)
            timing_values[key] = value
            ability_timing_vars[key].set(f"{value:.1f}")
        return {
            "total_enabled": bool(ability_total_enabled.get()),
            "total_min": total_min,
            "thresholds": dict(first_group["thresholds"]),
            "attribute_thresholds_enabled": bool(first_group["attribute_thresholds_enabled"]),
            "attribute_sum_enabled": bool(first_group["attribute_sum_enabled"]),
            "attribute_sum_min": int(first_group["attribute_sum_min"]),
            "attribute_groups": group_payloads,
            "stop_mode": stop_mode,
            "msp_spent_limit": msp_spent_limit,
            "msp_remaining_limit": msp_remaining_limit,
            "auto_overwrite": bool(ability_auto_overwrite.get()),
            "auto_overwrite_if_all_better": bool(ability_compare_enabled.get()),
            "stop_after_completion": bool(ability_stop_after_completion.get()),
            **timing_values,
        }

    def apply_ability_configuration() -> bool:
        payload = ability_config_payload(validate=True)
        if payload is None:
            return False
        settings = load_gui_settings()
        settings["ability_reroll"] = dict(payload)
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, settings_path)
            ability_config_path.parent.mkdir(parents=True, exist_ok=True)
            config_payload = dict(payload)
            config_temp = ability_config_path.with_suffix(".tmp")
            config_temp.write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(config_temp, ability_config_path)
        except OSError as exc:
            messagebox.showerror("能力提升设置未保存", str(exc), parent=root)
            return False
        enabled_groups = [
            group for group in payload.get("attribute_groups", [])
            if isinstance(group, dict) and group.get("enabled")
        ]
        stop_mode = str(payload.get("stop_mode", ABILITY_STOP_MODE_ATTRIBUTES))
        stop_summary = {
            ABILITY_STOP_MODE_ATTRIBUTES: "按属性条件停止",
            ABILITY_STOP_MODE_SPENT_MSP: f"已使用 {payload['msp_spent_limit']} MSP 后停止",
            ABILITY_STOP_MODE_REMAINING_MSP: f"剩余 {payload['msp_remaining_limit']} MSP 后停止",
        }.get(stop_mode, "按属性条件停止")
        condition_summary: list[str] = []
        if payload.get("total_enabled"):
            condition_summary.append(f"总星数>={payload['total_min']}")
        if enabled_groups:
            condition_summary.append(f"{len(enabled_groups)} 组指定属性组合（任一满足）")
        set_status(
            "能力提升设置已保存：" + stop_summary + "；"
            + ("、".join(condition_summary) if condition_summary else "未配置覆盖条件")
            + (
                "；符合条件后自动选择‘是’并覆盖"
                if payload["auto_overwrite"]
                else "；符合条件后停在覆盖确认页等待手动选择"
            )
            + ("；覆盖完成后停止" if payload["stop_after_completion"] else "；覆盖完成后继续重抽")
        )
        return True

    def save_background_choice() -> None:
        """Persist only the selected mode; environment checks never alter it."""
        settings = load_gui_settings()
        settings["app_language"] = app_language_label_to_code.get(
            app_language_var.get(), "zh"
        )
        settings["background_mode"] = bool(background.get())
        settings["invert_movement"] = bool(invert_movement.get())
        settings["window_title"] = title_var.get().strip()
        settings["ui_language"] = selected_game_language_code()
        settings["recognition_profile"] = selected_recognition_profile_code()
        settings["chiaki_exe"] = path_var.get().strip()
        settings["auto_recover"] = bool(auto_recover.get())
        settings["reconnect_nickname"] = reconnect_nickname_var.get().strip()
        settings["reconnect_host"] = reconnect_host_var.get().strip()
        settings["freeze_minutes"] = freeze_minutes_var.get().strip()
        settings["max_battles"] = max_battles_var.get().strip()
        settings["max_runtime_minutes"] = max_runtime_var.get().strip()
        settings["stop_at"] = stop_at_var.get().strip()
        settings["refocus_mode"] = refocus_mode_label_to_code.get(
            refocus_mode_var.get(), REFOCUS_MODE_DEFAULT
        )
        ability_payload = ability_config_payload(validate=False)
        if ability_payload is not None:
            settings["ability_reroll"] = dict(ability_payload)
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = settings_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, settings_path)
        except OSError as exc:
            set_status(f"无法保存后台模式选择：{exc}")
            return

        set_status(
            "已选择后台模式；启动时将使用 ViGEm DS4"
            if background.get()
            else "已选择前台模式；自动按键会激活 Chiaki"
        )

    def apply_ui_language() -> None:
        """Persist the game OCR language selection and make it explicit."""
        code = selected_game_language_code()
        save_background_choice()
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            set_status(
                f"游戏识别语言已应用：{UI_LANGUAGE_LABELS[code]}；请停止并重新启动自动重战后生效"
            )
        else:
            set_status(f"游戏识别语言已应用：{UI_LANGUAGE_LABELS[code]}")

    def apply_display_language() -> None:
        """Apply translations to the visible control panel without changing OCR."""
        code = app_language_label_to_code.get(app_language_var.get(), "zh")
        settings = load_gui_settings()
        settings["app_language"] = code
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = settings_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, settings_path)
        except OSError as exc:
            set_status(f"无法保存工具界面语言：{exc}")
            return
        set_game_language_labels(code)
        translation = APP_TRANSLATIONS.get(code, {})

        def translate_tree(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                if isinstance(child, (tk.LabelFrame, tk.Label, tk.Button)):
                    if not str(child.cget("textvariable")):
                        source = getattr(child, "_gbfr_i18n_source", None)
                        if source is None:
                            source = str(child.cget("text"))
                            setattr(child, "_gbfr_i18n_source", source)
                        if source in translation:
                            child.configure(text=translation[source])
                translate_tree(child)

        translate_tree(root)
        root.title(
            "GBFR Auto ReBattle · Chiaki Console"
            if code == "en"
            else "GBFR 自動再戦 · Chiaki コンソール"
            if code == "ja"
            else "GBFR 自动重战 · Chiaki 控制台"
        )
        set_status(
            {"zh": "工具界面语言已应用：简体中文", "ja": "ツール表示言語を適用しました", "en": "Tool UI language applied"}[code]
        )

    def restore_chiaki_resolution(profile: str) -> None:
        """Restore one of the supported 16:9 Chiaki client-area presets."""
        sizes = {
            "chiaki_360p": (640, 360),
            "chiaki_540p": (960, 540),
            "chiaki_720p": (1280, 720),
            "chiaki_1080p": (1920, 1080),
        }
        size = sizes[profile]
        # Resolve the live stream window before changing its geometry.  This
        # also refreshes custom Chiaki titles instead of relying on a stale
        # title field from a previous session.
        if not capture_stream_window_title(show_feedback=False):
            messagebox.showwarning(
                "未捕获到 Chiaki 串流窗口",
                "请先打开并连接 Chiaki 串流，再恢复分辨率。",
                parent=root,
            )
            return
        hwnd = _find_window_handle(title_var.get().strip())
        if hwnd is None:
            messagebox.showwarning(
                "未找到 Chiaki 串流窗口",
                "请先打开 Chiaki 串流，并确认窗口标题与上方配置一致。",
                parent=root,
            )
            return
        if _resize_window_client_area(hwnd, *size):
            recognition_profile_var.set(recognition_profile_labels[profile])
            save_background_choice()
            set_status(
                f"Chiaki 客户区已恢复为 {size[0]}×{size[1]}；识别档位已同步，等待画面稳定后再启动自动化"
            )
        else:
            messagebox.showwarning(
                "Chiaki 分辨率恢复失败",
                "窗口可能处于最小化状态，或系统拒绝了尺寸调整。请恢复窗口后重试。",
                parent=root,
            )

    def apply_window_title() -> None:
        """Persist the title and refresh a running child capture immediately."""
        title = title_var.get().strip()
        if not title:
            messagebox.showwarning(
                "窗口标题不能为空",
                "请输入串流窗口标题后再应用。也可以保留默认的 Chiaki | Stream。",
                parent=root,
            )
            return
        save_background_choice()
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            send_automation_hotkey(0x73)  # F4
            set_status(f"窗口标题已应用：{title}；正在重新捕获串流窗口")
        else:
            set_status(f"窗口标题已应用：{title}；下次启动或点击 F4 时生效")

    def capture_stream_window_title(show_feedback: bool = True) -> bool:
        """Capture and persist the actual visible Chiaki stream caption."""
        found = _find_stream_window_with_title(
            title_var.get(), require_stream_marker=True
        )
        if not found:
            if show_feedback:
                set_status("未找到明确的 Chiaki 串流窗口；请连接串流后重试")
            return False
        _, detected_title = found
        title_var.set(detected_title)
        save_background_choice()
        if show_feedback:
            set_status(f"已捕获并应用串流窗口标题：{detected_title}")
        return True

    def apply_refocus_mode() -> None:
        """Persist the selected targeting strategy for the next run."""
        mode = refocus_mode_label_to_code.get(
            refocus_mode_var.get(), REFOCUS_MODE_DEFAULT
        )
        if mode not in REFOCUS_MODE_LABELS:
            mode = REFOCUS_MODE_DEFAULT
            refocus_mode_var.set(REFOCUS_MODE_LABELS[mode])
        settings = load_gui_settings()
        settings["refocus_mode"] = mode
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = settings_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, settings_path)
        except OSError as exc:
            messagebox.showerror("索敌方案未保存", str(exc), parent=root)
            return
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            set_status(
                f"索敌方案已应用为：{REFOCUS_MODE_LABELS[mode]}；请停止并重新启动自动重战后生效"
            )
        else:
            set_status(f"索敌方案已应用：{REFOCUS_MODE_LABELS[mode]}")

    def save_recovery_configuration() -> None:
        """Persist the user-selected reconnect target without starting a run."""
        nickname = reconnect_nickname_var.get().strip()
        host = reconnect_host_var.get().strip()
        try:
            minutes = float(freeze_minutes_var.get().strip())
        except ValueError:
            minutes = 0.0
        if auto_recover.get() and (
            not nickname
            or not host
            or not math.isfinite(minutes)
            or minutes * 60.0 < MIN_FREEZE_TIMEOUT_SECONDS
        ):
            messagebox.showerror(
                "卡死恢复设置无效",
                "启用自动重连时，请填写主机昵称、主机地址/IP，"
                "并将静止判定设为至少 0.1 分钟。",
                parent=root,
            )
            return
        save_background_choice()
        set_status(
            f"重连设置已保存：{nickname} @ {host}"
            if nickname and host
            else "重连设置已保存；自动恢复当前未配置完整目标"
        )

    def _choose_discovered_value(title: str, values: list[str]) -> str | None:
        """Choose one registry value when Chiaki has more than one target."""
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        options = "\n".join(f"{index}. {value}" for index, value in enumerate(values, 1))
        selected = simpledialog.askstring(
            title,
            f"Chiaki 找到多个目标，请输入序号：\n\n{options}",
            parent=root,
        )
        if not selected:
            return None
        try:
            index = int(selected.strip()) - 1
        except ValueError:
            return None
        return values[index] if 0 <= index < len(values) else None

    def discover_reconnect_target() -> None:
        """Read the saved Chiaki nickname/IP and put them into recovery fields."""
        nicknames, addresses = read_chiaki_reconnect_targets()
        if not nicknames and not addresses:
            messagebox.showwarning(
                "未找到主机信息",
                "没有从当前用户的 Chiaki 配置中读到已注册主机或手动主机地址。\n\n"
                "请先在 Chiaki 中添加/注册 PS5，再点击此按钮；也可以直接手动填写下面两项。",
                parent=root,
            )
            set_status("未找到 Chiaki 主机信息；可手动填写主机昵称和地址/IP")
            return

        nickname = _choose_discovered_value("选择 Chiaki 主机昵称", nicknames)
        host = _choose_discovered_value("选择 Chiaki 主机地址", addresses)
        if not nickname and reconnect_nickname_var.get().strip():
            nickname = reconnect_nickname_var.get().strip()
        if not host and reconnect_host_var.get().strip():
            host = reconnect_host_var.get().strip()
        if not nickname or not host:
            messagebox.showwarning(
                "主机信息不完整",
                "已读取到部分 Chiaki 配置，但缺少主机昵称或地址/IP。\n"
                "请补充下面对应输入框后点击“保存重连设置”。",
                parent=root,
            )
            set_status("主机信息读取不完整，请补充昵称和地址/IP")
            return

        old = (reconnect_nickname_var.get().strip(), reconnect_host_var.get().strip())
        if old != (nickname, host) and any(old):
            if not messagebox.askyesno(
                "应用发现的主机信息",
                f"将使用 Chiaki 读取到的目标：\n{nickname} @ {host}\n\n覆盖当前重连目标吗？",
                parent=root,
            ):
                return
        reconnect_nickname_var.set(nickname)
        reconnect_host_var.set(host)
        save_background_choice()
        set_status(f"已从 Chiaki 获取主机信息：{nickname} @ {host}")

    def write_schedule_file(schedule: tuple[int, float, str]) -> None:
        """Persist the live automatic-stop settings for the child process."""
        max_battles, max_runtime, stop_at = schedule
        payload = {
            "max_battles": max_battles,
            "max_runtime_minutes": max_runtime,
            "stop_at": stop_at,
        }
        try:
            schedule_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = schedule_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, schedule_path)
        except OSError as exc:
            raise OSError(f"无法保存自动结束设置：{exc}") from exc

    def read_schedule() -> tuple[int, float, str] | None:
        """Validate the three optional automatic-stop controls."""
        try:
            max_battles = int(max_battles_var.get().strip() or "0")
            max_runtime = float(max_runtime_var.get().strip() or "0")
            stop_at = stop_at_var.get().strip()
            if max_battles < 0 or not math.isfinite(max_runtime) or max_runtime < 0:
                raise ValueError
            if stop_at:
                datetime.strptime(stop_at, "%H:%M")
            return max_battles, max_runtime, stop_at
        except ValueError:
            messagebox.showerror(
                "定时设置无效",
                "完成场数和运行时长必须是非负数字；自动关闭时间请填写 HH:MM，例如 23:30。",
                parent=root,
            )
            return None

    def apply_schedule() -> None:
        schedule = read_schedule()
        if schedule is None:
            return
        try:
            write_schedule_file(schedule)
            save_background_choice()
        except OSError as exc:
            messagebox.showerror("自动结束设置未保存", str(exc), parent=root)
            return
        max_battles, max_runtime, stop_at = schedule
        enabled = []
        if max_battles:
            enabled.append(f"{max_battles} 场")
        if max_runtime:
            enabled.append(f"{max_runtime:g} 分钟")
        if stop_at:
            enabled.append(f"本机时间 {stop_at}")
        description = "、".join(enabled) if enabled else "未启用任何自动结束条件"
        set_status(f"自动结束设置已应用：{description}；运行中修改立即生效")

    def app_root() -> Path:
        return Path(
            sys.executable if getattr(sys, "frozen", False) else __file__
        ).resolve().parent

    def sync_input_configuration(show_dialog: bool = True) -> bool:
        """Synchronize foreground Chiaki keys and verify background ViGEm."""
        mapping, notes = read_chiaki_keymap()
        nicknames, addresses = read_chiaki_reconnect_targets()
        if not reconnect_nickname_var.get().strip() and len(nicknames) == 1:
            reconnect_nickname_var.set(nicknames[0])
        if not reconnect_host_var.get().strip() and len(addresses) == 1:
            reconnect_host_var.set(addresses[0])
        unsupported = [item for item in notes if "=" in item]
        reverse_mapping: dict[str, list[str]] = {}
        for action, key_name in mapping.items():
            reverse_mapping.setdefault(key_name, []).append(action)
        conflicts = {
            key_name: actions
            for key_name, actions in reverse_mapping.items()
            if len(actions) > 1
        }
        foreground_ready = not unsupported and not conflicts
        foreground_issues: list[str] = []
        if unsupported:
            foreground_issues.append(
                "不支持的组合键：" + "；".join(unsupported)
            )
        if conflicts:
            foreground_issues.append(
                "重复键位："
                + "；".join(
                    f"{key_name}={','.join(actions)}"
                    for key_name, actions in conflicts.items()
                )
            )

        # Foreground automation sends Chiaki's physical keyboard bindings, so
        # an incomplete or ambiguous map is unsafe. Background automation sends
        # fixed DS4 semantics through ViGEm and must remain independent of it.
        if not foreground_ready and not background.get():
            set_status("前台输入同步失败：请修正 Chiaki 必需键位")
            if show_dialog:
                messagebox.showwarning(
                    "前台同步未完成",
                    "Chiaki 前台键位存在以下问题：\n"
                    + "\n".join(f"- {item}" for item in foreground_issues)
                    + "\n\n请将必需动作设为互不重复的单个普通按键后重新同步。\n"
                    "也可以勾选后台运行；后台使用 ViGEm 虚拟 DS4，不依赖这些键位。",
                    parent=root,
                )
            return False

        background_missing = background_dependency_report()
        profile = {
            "version": 1,
            "synced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "foreground_keys": mapping,
            "foreground": {
                "available": foreground_ready,
                "issues": foreground_issues,
            },
            "background": {
                "input": "ViGEm virtual DS4",
                "invert_movement": bool(invert_movement.get()),
                "available": not background_missing,
            },
        }
        try:
            input_profile_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = input_profile_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, input_profile_path)
            save_background_choice()
        except OSError as exc:
            set_status(f"输入配置同步失败：{exc}")
            if show_dialog:
                messagebox.showerror("同步失败", str(exc), parent=root)
            return False

        source = "、".join(item for item in notes if "=" not in item)
        if foreground_ready:
            foreground_lines = (
                f"前台：已就绪；Cross={mapping['cross']}，"
                f"Square={mapping['square']}，Pyramid={mapping['pyramid']}，"
                f"L2={mapping['l2']}，"
                f"R1={mapping['r1']}，十字键上={mapping['dpad_up']}\n"
                f"左摇杆={mapping['left_up']}/{mapping['left_down']}/"
                f"{mapping['left_left']}/{mapping['left_right']}，"
                f"右摇杆左右={mapping['right_left']}/{mapping['right_right']}"
            )
        else:
            foreground_lines = (
                "前台：未就绪（"
                + "；".join(foreground_issues)
                + "）\n后台模式仍可独立使用。"
            )
        if background_missing:
            background_line = "后台：未就绪（" + "、".join(background_missing) + "）"
        else:
            background_line = (
                "后台：ViGEm 虚拟 DS4 已就绪；键位使用固定手柄语义，"
                f"移动轴反向={'是' if invert_movement.get() else '否'}\n"
                + background_input_details()
            )
        if foreground_ready and not background_missing:
            set_status("输入配置已同步：前台 Chiaki 与后台 ViGEm DS4 均已就绪")
        elif background.get() and not background_missing:
            set_status("后台输入已就绪；Chiaki 前台键位有问题，但不影响后台运行")
        elif foreground_ready:
            set_status("前台 Chiaki 键位已同步；后台环境尚未就绪")
        else:
            set_status("输入配置已保存；前台键位和后台环境均需要处理")
        if show_dialog:
            dialog = messagebox.showinfo if foreground_ready else messagebox.showwarning
            reconnect_line = (
                f"重连目标：{reconnect_nickname_var.get().strip()} @ "
                f"{reconnect_host_var.get().strip()}"
                if reconnect_nickname_var.get().strip()
                and reconnect_host_var.get().strip()
                else "重连目标：未完整配置；请在主界面确认主机昵称和地址"
            )
            dialog(
                "输入配置同步结果",
                f"来源：{source}\n\n{foreground_lines}\n\n{background_line}\n\n"
                f"{reconnect_line}",
                parent=root,
            )
        return True

    def open_logs() -> None:
        log_dir = Path(get_runtime_log_dir())
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(log_dir))
        except OSError as exc:
            messagebox.showerror("无法打开日志目录", str(exc))

    def background_dependency_report() -> list[str]:
        """Return actionable missing requirements for reliable background mode."""
        missing: list[str] = []
        if WindowsCapture is None:
            missing.append("后台窗口捕获组件 windows-capture")
        if vg is None:
            missing.append("虚拟手柄组件 vgamepad")
        else:
            try:
                # Importing vgamepad alone is not enough. A real DS4 probe
                # confirms that the signed ViGEmBus driver is installed.
                probe = vg.VDS4Gamepad()
                probe.reset()
                probe.update()
                del probe
            except Exception:
                missing.append("ViGEmBus 虚拟手柄驱动")
        return missing

    def background_input_details() -> str:
        """Describe the actual virtual DS4 report and the selected axis sign."""
        if vg is None:
            return "输入链路：vgamepad 未加载，将无法创建虚拟 DS4"
        try:
            probe = vg.VDS4Gamepad()
            probe.reset()
            probe.update()
            report = probe.report
            center = int(report.bThumbLY) & 0xFF
            pressed = 255 if invert_movement.get() else 1
            return (
                "输入链路：ViGEm DS4\n"
                f"bThumbLY 实测中立值：{center}\n"
                f"W 按下发送值：{pressed}\n"
                f"W 释放发送值：{center}\n"
                f"反向移动选项：{'开启' if invert_movement.get() else '关闭'}\n"
                "Return=Cross，\\=Square，L=L2，3=R1"
            )
        except Exception as exc:
            return f"输入链路：创建 ViGEm DS4 失败\n{exc}"

    def check_background_environment(show_dialog: bool = True) -> bool:
        missing = background_dependency_report()
        input_details = background_input_details()
        if not missing:
            set_status("后台环境检查通过：虚拟 DS4 与窗口捕获均可用")
            if show_dialog:
                messagebox.showinfo(
                    "后台环境已就绪",
                    "后台输入和窗口捕获均可用。\n"
                    "Chiaki 可以被其他窗口覆盖；误最小化后恢复窗口即可自动重新绑定。\n\n"
                    + input_details,
                )
            return True
        details = "\n".join(f"- {item}" for item in missing)
        set_status("后台环境缺少必要组件，请安装 ViGEmBus 后重新检查")
        if show_dialog:
            messagebox.showwarning(
                "后台环境未完成",
                "缺少以下组件：\n"
                f"{details}\n\n"
                "后台模式只需要 Windows Capture 和 ViGEmBus；\n"
                "HidHide 是可选的冲突隔离工具，不影响本项检查。\n"
                "安装 ViGEmBus 后请回到这里点击“检查后台环境”。\n\n"
                + input_details,
            )
        return False

    def install_virtual_gamepad_driver() -> None:
        """Launch an optional bundled, signed ViGEmBus installer with UAC."""
        installer = (
            app_root()
            / "Dependencies"
            / "ViGEmBus_1.22.0_x64_x86_arm64.exe"
        )
        if installer.is_file():
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(installer), None, str(installer.parent), 1
            )
            if result <= 32:
                messagebox.showerror("安装未启动", "Windows 未能启动虚拟手柄驱动安装器。")
            else:
                set_status("已启动 ViGEmBus 安装器；完成后请点击“检查后台环境”")
            return

        webbrowser.open("https://github.com/nefarius/ViGEmBus/releases/latest")
        messagebox.showinfo(
            "需要安装 ViGEmBus",
            "完整包未附带驱动安装器，已打开 ViGEmBus 官方发布页。\n"
            "下载并完成安装后，回到工具点击“检查后台环境”。",
        )

    def install_hidhide() -> None:
        """Launch the optional bundled HidHide installer with UAC.

        HidHide is deliberately independent from the background-mode check.
        The application never changes HidHide's device hiding list or allowlist.
        """
        installer = app_root() / "Dependencies" / "HidHide_1.4.202_x64.exe"
        if installer.is_file():
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(installer), None, str(installer.parent), 1
            )
            if result <= 32:
                messagebox.showerror("安装未启动", "Windows 未能启动 HidHide 安装器。")
            else:
                set_status(
                    "已启动 HidHide 安装器；安装后请按需打开 HidHide Configuration Client 配置实体手柄"
                )
            return

        webbrowser.open("https://github.com/nefarius/HidHide/releases/latest")
        messagebox.showinfo(
            "需要安装 HidHide",
            "完整包未附带 HidHide 安装器，已打开官方发布页。\n"
            "HidHide 只在实体手柄与虚拟 DS4 冲突时需要；安装后请手动配置隐藏设备。",
        )

    def append_console_log(text: str) -> None:
        # ConsoleFormatter includes ANSI color escapes; Tk's Text widget
        # should receive plain readable text instead.
        clean = text.replace("\x1b[0m", "")
        for code in ("\x1b[31m", "\x1b[32m", "\x1b[36m", "\x1b[91m", "\x1b[93m", "\x1b[2m"):
            clean = clean.replace(code, "")
        if not clean:
            return
        if (
            active_run_kind["value"] == "ability"
            and not ability_manual_notice_shown["value"]
            and (
                "已停在覆盖确认页" in clean
                or "已进入待选暂停" in clean
                or "自动覆盖未开启，已停止等待人工确认" in clean
            )
        ):
            ability_manual_notice_shown["value"] = True
            ability_run_status.set("能力提升重抽：等待手动确认覆盖")
            set_ability_primary_state()
            set_status("能力提升发现符合条件的词条，已停在覆盖确认页，请手动选择‘是’或‘否’")
            messagebox.showinfo(
                "等待手动确认覆盖",
                "已发现符合条件的新能力提升词条。\n"
                "当前已停在‘能力值覆盖确认’页面，自动输入已暂停。\n"
                "请手动选择‘是’保留新词条，或选择‘否’取消覆盖。\n"
                "处理完成后如需结束，请点击能力提升重抽按钮或按 F2。",
                parent=root,
            )
        if "界面语言已识别：日文" in clean:
            set_status("自动重战运行中；界面语言：日文（自动识别）")
        elif "界面语言已识别：简体中文" in clean:
            set_status("自动重战运行中；界面语言：简体中文（自动识别）")
        if "已暂停" in clean:
            set_automation_state("自动战斗：已暂停", "paused")
        elif "已继续" in clean:
            set_automation_state("自动战斗：运行中", "running")
        elif "开始串流恢复" in clean or "串流恢复流程" in clean:
            set_automation_state("自动战斗：串流恢复中", "recovering")
        elif "一键重连成功后的自动重战交接" in clean:
            set_status("一键重连已完成；本工具自动重战已明确启动")
            set_automation_state("自动战斗：运行中", "running")
        elif "自动重战状态机已启动，初始阶段:" in clean:
            phase = clean.rsplit("初始阶段:", 1)[-1].strip()
            set_status(f"自动重战状态机运行中；当前入口阶段：{phase}")
            set_automation_state("自动战斗：运行中", "running")
        console.configure(state="normal")
        console.insert("end", clean)
        # Keep the embedded viewer responsive during long unattended runs.
        if int(console.index("end-1c").split(".")[0]) > 1200:
            console.delete("1.0", "201.0")
        if log_follow["value"]:
            console.see("end")
        console.configure(state="disabled")

    def poll_console_log() -> None:
        console_log = Path(get_runtime_log_dir()) / "automation-console.log"
        try:
            if console_log.is_file():
                size = console_log.stat().st_size
                if size < log_cursor["value"]:
                    log_cursor["value"] = 0
                    log_pending["value"] = b""
                with console_log.open("rb") as handle:
                    handle.seek(log_cursor["value"])
                    raw = log_pending["value"] + handle.read()
                    text, log_pending["value"] = decode_console_bytes(raw)
                    append_console_log(text)
                    log_cursor["value"] = handle.tell()
        except OSError:
            pass
        root.after(250, poll_console_log)

    def start_chiaki() -> None:
        if chiaki_process["value"] is not None and chiaki_process["value"].poll() is None:
            set_status("Chiaki 已经在运行；连接串流后可点击“捕获当前标题”")
            return True
        if _find_window(title_var.get()):
            set_status("已检测到现有 Chiaki 串流窗口，直接复用")
            return True
        chiaki_path = Path(path_var.get()).expanduser()
        if not chiaki_path.is_absolute():
            base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
            chiaki_path = base / chiaki_path
        if not chiaki_path.is_file():
            messagebox.showerror("找不到 Chiaki", f"未找到文件：\n{chiaki_path}")
            return False
        try:
            chiaki_process["value"] = subprocess.Popen(
                [str(chiaki_path)], cwd=str(chiaki_path.parent)
            )
            set_status("Chiaki 已启动；连接串流后可点击“捕获当前标题”")
            return True
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))
            return False

    def browse_chiaki() -> None:
        selected = filedialog.askopenfilename(
            parent=root,
            title="选择 Chiaki 程序",
            filetypes=(("Chiaki 程序", "chiaki.exe"), ("EXE 程序", "*.exe")),
        )
        if selected:
            path_var.set(selected)
            save_background_choice()
            set_status("已保存 Chiaki 程序路径")

    def detect_chiaki_path() -> None:
        candidates = find_chiaki_executables(app_root())
        if not candidates:
            set_status("未自动找到 Chiaki，请点击“浏览”选择 chiaki.exe")
            messagebox.showinfo(
                "未自动找到 Chiaki",
                "Chiaki 的按键和主机配置已能从注册表读取，但程序路径不一定会注册到 Windows。\n\n"
                "请点击“浏览”选择现有的 chiaki.exe，路径只保存在本机。",
                parent=root,
            )
            return
        path_var.set(str(candidates[0]))
        save_background_choice()
        set_status(f"已自动找到 Chiaki：{candidates[0]}")

    def start_automation(reconnect_once: bool = False) -> None:
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            set_status("自动重战已经在运行")
            return
        run_in_background = bool(background.get())
        schedule = read_schedule()
        if schedule is None:
            return
        max_battles, max_runtime, stop_at = schedule
        reconnect_enabled = bool(auto_recover.get()) or reconnect_once
        reconnect_nickname = reconnect_nickname_var.get().strip()
        reconnect_host = reconnect_host_var.get().strip()
        chiaki_path = Path(path_var.get().strip()).expanduser()
        if not chiaki_path.is_absolute():
            chiaki_path = app_root() / chiaki_path
        if reconnect_enabled:
            try:
                freeze_minutes = float(freeze_minutes_var.get().strip())
            except ValueError:
                freeze_minutes = 0.0
            if auto_recover.get() and (
                freeze_minutes * 60.0 < MIN_FREEZE_TIMEOUT_SECONDS
                or not math.isfinite(freeze_minutes)
            ):
                messagebox.showerror(
                    "卡死恢复设置无效",
                    "静止判定至少填写 0.1 分钟。正式挂机建议使用 10 分钟。",
                    parent=root,
                )
                return
            if auto_recover.get() and freeze_minutes < 2.0:
                confirmed = messagebox.askyesno(
                    "确认使用测试阈值",
                    f"当前静止判定为 {freeze_minutes:g} 分钟，适合测试，但正常游戏短暂静止也可能触发重连。\n\n"
                    "正式挂机建议使用 10 分钟。确定按当前测试阈值启动吗？",
                    parent=root,
                )
                if not confirmed:
                    return
            if not chiaki_path.is_file() or not reconnect_nickname or not reconnect_host:
                messagebox.showerror(
                    "卡死恢复设置不完整",
                    "请确认 Chiaki 程序路径、主机昵称和主机地址/IP。\n"
                    "多台主机时请手动填写目标，工具不会自动猜测。",
                    parent=root,
                )
                return
        else:
            freeze_minutes = 0.0
        try:
            write_schedule_file(schedule)
            save_background_choice()
        except OSError as exc:
            messagebox.showerror("自动结束设置未保存", str(exc), parent=root)
            return
        if run_in_background and not check_background_environment(show_dialog=True):
            return
        if (
            not reconnect_once
            and (
                chiaki_process["value"] is None
                or chiaki_process["value"].poll() is not None
            )
        ):
            if not start_chiaki():
                return
        # Always pass the option name. The previous foreground branch built
        # [exe, "Chiaki | Stream"], so argparse treated the title as an
        # unexpected positional argument and exited with code 2.
        command = _self_command()
        command.extend(("--launcher-pid", str(os.getpid())))
        if args.debug:
            command.append("--debug")
        if run_in_background:
            command.append("--background")
        command.extend(("--window-title", title_var.get()))
        command.extend(
            (
                "--ui-language",
                selected_game_language_code(),
            )
        )
        command.extend(("--recognition-profile", selected_recognition_profile_code()))
        command.extend(
            (
                "--refocus-mode",
                refocus_mode_label_to_code.get(
                    refocus_mode_var.get(), REFOCUS_MODE_DEFAULT
                ),
            )
        )
        command.extend(("--chiaki-exe", str(chiaki_path)))
        if reconnect_enabled:
            command.extend(
                (
                    "--reconnect-nickname",
                    reconnect_nickname,
                    "--reconnect-host",
                    reconnect_host,
                )
            )
        if auto_recover.get():
            command.extend(
                (
                    "--auto-recover",
                    "--freeze-timeout-seconds",
                    str(freeze_minutes * 60.0),
                )
            )
        command.extend(("--stats-file", str(stats_path)))
        command.append("--reconnect-once" if reconnect_once else "--auto-start")
        # Always refresh before a run so changes made in Chiaki since the last
        # manual sync are picked up automatically.
        if not sync_input_configuration(show_dialog=False):
            return
        command.extend(("--input-profile", str(input_profile_path)))
        if max_battles:
            command.extend(("--max-battles", str(max_battles)))
        if max_runtime:
            command.extend(("--max-runtime-minutes", str(max_runtime)))
        if stop_at:
            command.extend(("--stop-at", stop_at))
        if invert_movement.get():
            command.append("--invert-movement")
        command.extend(("--schedule-file", str(schedule_path)))
        try:
            log_dir = Path(get_runtime_log_dir())
            log_dir.mkdir(parents=True, exist_ok=True)
            console_log = log_dir / "automation-console.log"
            if automation_output["value"] is not None:
                automation_output["value"].close()
            log_cursor["value"] = 0
            console.configure(state="normal")
            console.delete("1.0", "end")
            console.configure(state="disabled")
            # The child normally writes its console log here; unexpected
            # import/startup tracebacks are captured in the same live view.
            # Keep the child pipe explicitly UTF-8 on Windows. The BOM makes
            # the persisted console log readable outside this GUI as well.
            automation_output["value"] = console_log.open(
                "w", encoding="utf-8-sig", buffering=1
            )
            child_env = os.environ.copy()
            child_env["PYTHONUTF8"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUNBUFFERED"] = "1"
            log_mode = "后台 ViGEm DS4" if run_in_background else "前台键盘"
            launcher_line = f"[启动器] 本次运行模式：{log_mode}；正在启动自动化子进程...\n"
            automation_output["value"].write(launcher_line)
            automation_output["value"].flush()
            # Display launcher progress immediately and start polling after
            # these bytes so the same line is not appended twice.
            log_cursor["value"] = console_log.stat().st_size
            append_console_log(launcher_line)
            automation_process["value"] = subprocess.Popen(
                command,
                stdout=automation_output["value"],
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=child_env,
            )
            active_run_kind["value"] = "rebattle"
            active_background_mode["value"] = run_in_background
            background_check.configure(state="disabled")
            background_label.set(
                "后台运行（当前已启用并锁定；先停止自动重战，再取消勾选即可前台运行）"
                if run_in_background
                else "后台运行（当前未启用；运行中锁定，停止后可勾选）"
            )
            if reconnect_once:
                set_status("一键重连并开始挂机：正在恢复串流和游戏状态")
                set_automation_state("自动战斗：串流恢复中", "recovering")
            elif run_in_background:
                set_status("自动重战已启动：后台模式中，Chiaki 可被覆盖；最小化后恢复即可")
                set_automation_state("自动战斗：正在初始化", "running")
            else:
                set_status("自动重战已启动：前台模式中，请保持 Chiaki 为当前活动窗口")
                set_automation_state("自动战斗：正在初始化", "running")
        except OSError as exc:
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            messagebox.showerror("启动失败", str(exc))

    def start_ability_reroll() -> None:
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            set_status("当前已有自动化功能运行，请先停止后再启动能力提升重抽")
            return
        if not apply_ability_configuration():
            return
        run_in_background = bool(background.get())
        if run_in_background and not check_background_environment(show_dialog=True):
            return
        if chiaki_process["value"] is None or chiaki_process["value"].poll() is not None:
            if not start_chiaki():
                return
        command = _self_command()
        command.extend(
            (
                "--launcher-pid",
                str(os.getpid()),
                "--ability-reroll",
                "--window-title",
                title_var.get(),
                "--ui-language",
                selected_game_language_code(),
                "--recognition-profile",
                selected_recognition_profile_code(),
                "--ability-config-file",
                str(ability_config_path),
                "--ability-stats-file",
                str(ability_journal_path),
            )
        )
        if run_in_background:
            command.append("--background")
        elif sync_input_configuration(show_dialog=False):
            command.extend(("--input-profile", str(input_profile_path)))
        else:
            return
        try:
            log_dir = Path(get_runtime_log_dir())
            log_dir.mkdir(parents=True, exist_ok=True)
            console_log = log_dir / "automation-console.log"
            if automation_output["value"] is not None:
                automation_output["value"].close()
            log_cursor["value"] = 0
            console.configure(state="normal")
            console.delete("1.0", "end")
            console.configure(state="disabled")
            automation_output["value"] = console_log.open("w", encoding="utf-8-sig", buffering=1)
            child_env = os.environ.copy()
            child_env["PYTHONUTF8"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"
            child_env["PYTHONUNBUFFERED"] = "1"
            launcher_line = "[启动器] 正在启动独立能力提升重抽；词条记录会写入能力提升词条记录 JSON。\n"
            automation_output["value"].write(launcher_line)
            automation_output["value"].flush()
            log_cursor["value"] = console_log.stat().st_size
            append_console_log(launcher_line)
            automation_process["value"] = subprocess.Popen(
                command,
                stdout=automation_output["value"],
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=child_env,
            )
            active_run_kind["value"] = "ability"
            ability_manual_notice_shown["value"] = False
            active_background_mode["value"] = run_in_background
            background_check.configure(state="disabled")
            ability_run_status.set("能力提升重抽：运行中（F2 停止）")
            set_status("能力提升重抽已启动；结果会自动记录到词条统计文件")
            set_ability_primary_state()
        except OSError as exc:
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            messagebox.showerror("能力提升重抽启动失败", str(exc), parent=root)

    def open_ability_journal() -> None:
        existing = ability_journal_window["value"]
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return

        window = tk.Toplevel(root)
        ability_journal_window["value"] = window
        window.title("能力提升 · 词条记录")
        window.geometry("1280x820")
        window.minsize(1000, 640)
        window.configure(bg=palette["surface"])
        window.transient(root)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(3, weight=1)

        header = tk.Frame(window)
        header.grid(row=0, column=0, padx=16, pady=(14, 4), sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="能力提升词条记录", font=("Segoe UI Semibold", 15), anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        updated_text = tk.StringVar(value="尚未读取记录")
        tk.Label(header, textvariable=updated_text, fg="#5f7080", anchor="e").grid(
            row=0, column=1, sticky="e"
        )
        tk.Label(
            header,
            text="默认显示最新轮次；点击一行后，下方会显示旧词条、新词条和逐项变化。",
            fg="#5f7080",
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, pady=(3, 0), sticky="w")

        summary_text = tk.StringVar(value="")
        tk.Label(
            window,
            textvariable=summary_text,
            bg=palette["panel"],
            fg=palette["ink"],
            anchor="w",
            padx=10,
            pady=7,
        ).grid(row=1, column=0, padx=16, pady=(2, 5), sticky="ew")

        toolbar = tk.Frame(window)
        toolbar.grid(row=2, column=0, padx=16, pady=(0, 5), sticky="ew")
        toolbar.columnconfigure(1, weight=1)
        tk.Label(toolbar, text="筛选").grid(row=0, column=0, padx=(0, 6), sticky="w")
        search_var = tk.StringVar()
        search_entry = tk.Entry(toolbar, textvariable=search_var, width=30)
        search_entry.grid(row=0, column=1, padx=(0, 8), sticky="ew")
        search_entry.insert(0, "属性名、OCR 或判断说明")
        search_entry.configure(fg="#7b8792")

        def clear_search_placeholder(_event=None) -> None:
            if search_entry.get() == "属性名、OCR 或判断说明":
                search_entry.delete(0, "end")
                search_entry.configure(fg=palette["ink"])

        def restore_search_placeholder(_event=None) -> None:
            if not search_entry.get().strip():
                search_entry.insert(0, "属性名、OCR 或判断说明")
                search_entry.configure(fg="#7b8792")

        search_entry.bind("<FocusIn>", clear_search_placeholder)
        search_entry.bind("<FocusOut>", restore_search_placeholder)
        tk.Label(toolbar, text="结果").grid(row=2, column=0, padx=(0, 6), pady=(5, 0), sticky="w")
        decision_filter = tk.StringVar(value="全部结果")
        decision_combo = ttk.Combobox(
            toolbar,
            textvariable=decision_filter,
            values=("全部结果", "继续重抽", "等待手动确认覆盖", "自动选择‘是’并覆盖"),
            state="readonly",
            width=12,
        )
        decision_combo.grid(row=2, column=1, padx=(0, 8), pady=(5, 0), sticky="w")
        notebook = ttk.Notebook(window)
        notebook.grid(row=3, column=0, padx=16, pady=(0, 8), sticky="nsew")
        table_frames: dict[str, tk.Frame] = {}
        tables: dict[str, ttk.Treeview] = {}
        row_records: dict[tuple[str, str], object] = {}
        tab_titles = {"attributes": "属性汇总", "rounds": "每轮历史", "unknown": "未识别 OCR"}

        def create_table(key: str, columns: tuple[str, ...], headings: tuple[str, ...], widths: tuple[int, ...]) -> None:
            tab = tk.Frame(notebook, bg=palette["surface"])
            tab.rowconfigure(0, weight=1)
            tab.columnconfigure(0, weight=1)
            notebook.add(tab, text=tab_titles[key])
            tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
            y_scroll = ttk.Scrollbar(tab, orient="vertical", command=tree.yview)
            x_scroll = ttk.Scrollbar(tab, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
            for column, heading, width in zip(columns, headings, widths):
                tree.heading(column, text=heading)
                tree.column(column, width=width, minwidth=55, stretch=column in {"old", "new", "reason", "raw"})
            tree.grid(row=0, column=0, sticky="nsew")
            y_scroll.grid(row=0, column=1, sticky="ns")
            x_scroll.grid(row=1, column=0, sticky="ew")
            table_frames[key] = tab
            tables[key] = tree

        create_table(
            "rounds",
            ("round", "time", "decision", "total", "old", "new", "reason"),
            ("轮次", "时间", "处理结果", "新总星数", "旧词条", "新词条", "判断结果"),
            (60, 175, 95, 85, 250, 250, 390),
        )
        create_table(
            "attributes",
            ("attribute", "count", "stars", "raw"),
            ("属性", "出现次数", "星数分布", "识别到的原文"),
            (240, 100, 220, 560),
        )
        create_table(
            "unknown",
            ("raw", "count", "last_seen"),
            ("未归类 OCR 原文", "出现次数", "最近出现"),
            (650, 120, 220),
        )
        notebook.select(table_frames["rounds"])

        detail_frame = tk.LabelFrame(window, text="当前选中轮次")
        detail_frame.grid(row=4, column=0, padx=16, pady=(0, 10), sticky="ew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        detail_title = tk.StringVar(value="请选择一轮查看详细对照")
        tk.Label(detail_frame, textvariable=detail_title, font=("Segoe UI Semibold", 10), anchor="w").grid(
            row=0, column=0, padx=8, pady=(7, 4), sticky="ew"
        )
        detail_table = ttk.Treeview(
            detail_frame,
            columns=("attribute", "old", "new", "change"),
            show="headings",
            height=5,
        )
        for column, heading, width in (
            ("attribute", "属性", 260),
            ("old", "旧词条", 210),
            ("new", "新词条", 210),
            ("change", "变化", 260),
        ):
            detail_table.heading(column, text=heading)
            detail_table.column(column, width=width, minwidth=90, stretch=column in {"attribute", "change"})
        detail_table.grid(row=1, column=0, padx=8, pady=(0, 5), sticky="ew")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=detail_table.yview)
        detail_table.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.grid(row=1, column=1, padx=(0, 8), pady=(0, 5), sticky="ns")
        detail_text = tk.Text(detail_frame, height=5, wrap="word", state="disabled")
        detail_text.grid(row=2, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="ew")

        journal_data: dict[str, object] = {}

        def set_detail_text(text: str) -> None:
            detail_text.configure(state="normal")
            detail_text.delete("1.0", "end")
            detail_text.insert("1.0", text)
            detail_text.configure(state="disabled")

        def numeric_text(value: object) -> str:
            if value is None or value == "":
                return "未识别"
            try:
                number = float(value)
            except (TypeError, ValueError):
                return str(value)
            return str(int(number)) if number.is_integer() else f"{number:g}"

        def roll_name(roll: object) -> str:
            if not isinstance(roll, dict):
                return "未识别"
            return str(roll.get("attribute") or roll.get("raw_text") or "未识别")

        def display_roll_name(roll: object) -> str:
            return ability_roll_display_name(roll)

        def roll_value(roll: object) -> str:
            if not isinstance(roll, dict):
                return "未识别"
            stars = roll.get("stars")
            value = roll.get("value")
            if stars is None and value is None:
                return "未识别"
            parts = []
            if stars is not None:
                parts.append(f"{numeric_text(stars)}星")
            if value is not None:
                parts.append(numeric_text(value))
            source_labels = {
                "frame": "图像星带",
                "inline_ocr": "OCR行内星号",
                "star_ocr": "OCR独立星号行",
                "value_rule": "数值规则",
                "history_value": "历史记录",
            }
            source = source_labels.get(str(roll.get("stars_source", "")))
            if source:
                parts.append(source)
            return " / ".join(parts)

        def format_names(rolls: object) -> str:
            if not isinstance(rolls, list):
                return "未识别"
            return "、".join(display_roll_name(roll) for roll in rolls) or "未识别"

        def total_text(rolls: object) -> str:
            if not isinstance(rolls, list) or len(rolls) != 4:
                return "未识别"
            stars = [roll.get("stars") for roll in rolls if isinstance(roll, dict)]
            if len(stars) != 4 or any(value is None for value in stars):
                return "未识别"
            return str(sum(int(value) for value in stars))

        def display_decision(value: object) -> str:
            return {
                "reroll": "继续重抽",
                "stop": "等待手动确认覆盖",
                "accept": "自动选择‘是’并覆盖",
            }.get(str(value), str(value) or "未知")

        def selected_key() -> str | None:
            current = notebook.select()
            return next((key for key, frame in table_frames.items() if str(frame) == current), None)

        def clear_detail() -> None:
            detail_title.set("请选择一轮查看详细对照")
            for item in detail_table.get_children():
                detail_table.delete(item)
            set_detail_text("提示：每轮历史中点击一行，可查看旧词条、新词条、星数/数值变化和完整判断说明。")

        def show_selected_detail(_event=None) -> None:
            key = selected_key()
            if key is None:
                return
            tree = tables[key]
            selection = tree.selection()
            if not selection:
                clear_detail()
                return
            record = row_records.get((key, selection[0]))
            for item in detail_table.get_children():
                detail_table.delete(item)
            if key == "rounds" and isinstance(record, dict):
                old_rolls = record.get("old", [])
                new_rolls = record.get("new", [])
                old_map = {roll_name(roll): roll for roll in old_rolls} if isinstance(old_rolls, list) else {}
                new_map = {roll_name(roll): roll for roll in new_rolls} if isinstance(new_rolls, list) else {}
                names = list(new_map)
                names.extend(name for name in old_map if name not in new_map)
                for name in names:
                    old = old_map.get(name)
                    new = new_map.get(name)
                    change = "新增属性" if old is None else "移除属性" if new is None else "同名属性，详见判断说明"
                    if old is not None and new is not None:
                        old_stars, new_stars = old.get("stars"), new.get("stars")
                        if old_stars is not None and new_stars is not None:
                            if new_stars > old_stars:
                                change = f"星数 +{int(new_stars) - int(old_stars)}"
                            elif new_stars < old_stars:
                                change = f"星数 {int(new_stars) - int(old_stars)}"
                            elif old.get("value") is not None and new.get("value") is not None:
                                change = "星数相同，数值提高" if new["value"] > old["value"] else "星数相同，数值未提高"
                            else:
                                change = "星数相同"
                    detail_name = display_roll_name(new if new is not None else old)
                    detail_table.insert(
                        "", "end", values=(detail_name, roll_value(old), roll_value(new), change)
                    )
                decision = display_decision(record.get("decision"))
                detail_title.set(
                    f"第 {record.get('round', '?')} 轮 · {record.get('time', '')} · {decision} · 新总星数 {total_text(new_rolls)}"
                )
                unknown = record.get("unknown_ocr", [])
                raw_ocr = record.get("raw_ocr", [])
                detail_lines = [f"判断说明：{record.get('reason', '无')}" ]
                action_labels = {
                    "cross_sent": "已发送 Cross",
                    "moon_sent": "已发送 Moon",
                    "wait_manual": "停在覆盖确认页，等待手动选择",
                    "highlight_unknown": "未确认‘是’高亮，未发送按键",
                }
                action = action_labels.get(str(record.get("action", "")))
                if action:
                    detail_lines.append(f"实际动作：{action}")
                if isinstance(unknown, list) and unknown:
                    detail_lines.append("本轮未归类 OCR：" + "、".join(str(item) for item in unknown))
                if isinstance(raw_ocr, list) and raw_ocr:
                    detail_lines.append("本轮 OCR 快照：" + " | ".join(str(item) for item in raw_ocr))
                validations = []
                for side_name, rolls in (("旧词条", old_rolls), ("新词条", new_rolls)):
                    if not isinstance(rolls, list):
                        continue
                    for roll in rolls:
                        if not isinstance(roll, dict) or not roll.get("star_validation"):
                            continue
                        validations.append(
                            f"{side_name} {roll_name(roll)}：{roll['star_validation']}"
                        )
                if validations:
                    detail_lines.append("星数交叉验证：" + " | ".join(validations))
                set_detail_text("\n".join(detail_lines))
            elif key == "attributes" and isinstance(record, dict):
                name = str(record.get("name", ""))
                detail_title.set(f"属性：{name} · 共出现 {record.get('count', 0)} 次")
                star_counts = record.get("star_counts", {})
                raw_texts = record.get("raw_texts", [])
                set_detail_text(
                    "星数分布：" + ("、".join(f"{key}星 × {value}" for key, value in star_counts.items()) if isinstance(star_counts, dict) else "未记录")
                    + "\n识别到的原文："
                    + (" | ".join(str(item) for item in raw_texts) if isinstance(raw_texts, list) else "未记录")
                )
            elif key == "unknown" and isinstance(record, dict):
                detail_title.set(f"未归类 OCR · 出现 {record.get('count', 0)} 次")
                set_detail_text(
                    f"原文：{record.get('raw', '')}\n最近出现：{record.get('last_seen', '未记录')}\n"
                    "说明：这里记录的是未能归入已知属性的 OCR 文本；历史旧版本可能包含标题、星号或按钮文字。"
                )

        for tree in tables.values():
            tree.bind("<<TreeviewSelect>>", show_selected_detail)
        notebook.bind("<<NotebookTabChanged>>", show_selected_detail)

        def refresh_journal() -> None:
            nonlocal journal_data
            try:
                loaded = json.loads(ability_journal_path.read_text(encoding="utf-8"))
                journal_data = loaded if isinstance(loaded, dict) else {}
            except (OSError, UnicodeError, json.JSONDecodeError):
                journal_data = {}
            row_records.clear()
            for tree in tables.values():
                for item in tree.get_children():
                    tree.delete(item)
            query = search_var.get().strip().lower()
            if query == "属性名、ocr 或判断说明":
                query = ""
            selected_decision = decision_filter.get()
            decision_map = {
                "继续重抽": "reroll",
                "等待手动确认覆盖": "stop",
                "自动选择‘是’并覆盖": "accept",
            }
            raw_rounds = journal_data.get("rounds", [])
            rounds = raw_rounds if isinstance(raw_rounds, list) else []
            for original_index in range(len(rounds) - 1, -1, -1):
                item = rounds[original_index]
                if not isinstance(item, dict):
                    continue
                blob = json.dumps(item, ensure_ascii=False).lower()
                decision = str(item.get("decision", ""))
                if query and query not in blob:
                    continue
                if selected_decision != "全部结果" and decision != decision_map.get(selected_decision):
                    continue
                old_rolls, new_rolls = item.get("old", []), item.get("new", [])
                iid = tables["rounds"].insert(
                    "", "end",
                    values=(
                        original_index + 1,
                        str(item.get("time", "")).replace("T", " ")[:19],
                        display_decision(decision),
                        total_text(new_rolls),
                        format_names(old_rolls),
                        format_names(new_rolls),
                        str(item.get("reason", "")),
                    ),
                    tags=(decision,),
                )
                row_records[("rounds", iid)] = item

            attributes = journal_data.get("attributes", {})
            attribute_values = sorted(attributes.items(), key=lambda item: (-int(item[1].get("count", 0)), str(item[0]))) if isinstance(attributes, dict) else []
            for name, item in attribute_values:
                item = item if isinstance(item, dict) else {}
                if query and query not in json.dumps({name: item}, ensure_ascii=False).lower():
                    continue
                star_counts = item.get("star_counts", {})
                star_text = "、".join(f"{stars}星 × {count}" for stars, count in sorted(star_counts.items(), key=lambda value: int(value[0]))) if isinstance(star_counts, dict) else "未记录"
                raw_texts = item.get("raw_texts", [])
                iid = tables["attributes"].insert("", "end", values=(name, item.get("count", 0), star_text or "未记录", " | ".join(str(text) for text in raw_texts) if isinstance(raw_texts, list) else "未记录"))
                row_records[("attributes", iid)] = {**item, "name": name}

            unknown = journal_data.get("unknown_ocr", {})
            unknown_values = sorted(unknown.items(), key=lambda item: (-int(item[1].get("count", 0)), str(item[0]))) if isinstance(unknown, dict) else []
            for raw_text, item in unknown_values:
                item = item if isinstance(item, dict) else {}
                if query and query not in str(raw_text).lower():
                    continue
                iid = tables["unknown"].insert("", "end", values=(raw_text, item.get("count", 0), item.get("last_seen", "未记录")))
                row_records[("unknown", iid)] = {**item, "raw": raw_text}

            decisions = [str(item.get("decision", "")) for item in rounds if isinstance(item, dict)]
            summary_text.set(
                f"共 {len(rounds)} 轮　继续重抽 {decisions.count('reroll')} 次　等待手动确认 {decisions.count('stop')} 次　自动覆盖 {decisions.count('accept')} 次　"
                f"已记录属性 {len(attribute_values)} 项　未归类 OCR {len(unknown_values)} 项"
            )
            updated_text.set(f"最后更新：{str(journal_data.get('updated_at') or '暂无').replace('T', ' ')}")
            current_tree = tables.get(selected_key() or "rounds")
            if current_tree is not None and current_tree.get_children():
                current_tree.selection_set(current_tree.get_children()[0])
                current_tree.focus(current_tree.get_children()[0])
                show_selected_detail()
            else:
                clear_detail()

        def clear_ability_journal() -> None:
            process = automation_process["value"]
            if (
                active_run_kind["value"] == "ability"
                and process is not None
                and process.poll() is None
            ):
                messagebox.showwarning(
                    "无法清除词条记录",
                    "能力提升重抽正在运行，请先停止后再清除历史数据。",
                    parent=window,
                )
                return
            rounds = journal_data.get("rounds", [])
            attributes = journal_data.get("attributes", {})
            unknown_ocr = journal_data.get("unknown_ocr", {})
            round_count = len(rounds) if isinstance(rounds, list) else 0
            attribute_count = len(attributes) if isinstance(attributes, dict) else 0
            unknown_count = len(unknown_ocr) if isinstance(unknown_ocr, dict) else 0
            if round_count == 0 and attribute_count == 0 and unknown_count == 0:
                messagebox.showinfo("清除词条记录", "当前没有可清除的历史数据。", parent=window)
                return
            confirmed = messagebox.askyesno(
                "确认清除词条历史",
                (
                    "将清除能力提升词条记录中的全部历史数据：\n"
                    f"每轮历史 {round_count} 轮、属性汇总 {attribute_count} 项、"
                    f"未识别 OCR {unknown_count} 项。\n\n"
                    "此操作不会清除自动重战日志或能力提升配置，且清除后不能从本工具恢复。\n"
                    "确定继续吗？"
                ),
                parent=window,
            )
            if not confirmed:
                return
            try:
                removed = AbilityJournal(ability_journal_path).clear()
            except OSError as exc:
                messagebox.showerror(
                    "清除词条记录失败",
                    f"无法写入记录文件：{exc}",
                    parent=window,
                )
                return
            refresh_journal()
            clear_detail()
            set_status(
                f"已清除能力提升词条记录：{removed[0]} 轮、{removed[1]} 项属性、{removed[2]} 项未识别 OCR"
            )
            messagebox.showinfo(
                "清除完成",
                f"已清除每轮历史 {removed[0]} 轮、属性汇总 {removed[1]} 项、未识别 OCR {removed[2]} 项。",
                parent=window,
            )

        refresh_button = tk.Button(toolbar, text="应用筛选", command=refresh_journal, width=12)
        refresh_button.grid(row=0, column=2, padx=(0, 6), sticky="e")
        tk.Button(toolbar, text="刷新记录", command=refresh_journal, width=10).grid(row=0, column=3, padx=(0, 6), sticky="e")
        tk.Button(toolbar, text="清空筛选", command=lambda: (search_var.set(""), decision_filter.set("全部结果"), refresh_journal()), width=10).grid(row=0, column=4, sticky="e")
        tk.Button(
            toolbar,
            text="清除历史数据",
            command=clear_ability_journal,
            width=14,
        ).grid(row=0, column=5, padx=(6, 0), sticky="e")
        search_entry.bind("<Return>", lambda _event: refresh_journal())
        decision_combo.bind("<<ComboboxSelected>>", lambda _event: refresh_journal())
        refresh_journal()
        refresh_token = {"value": None}

        def schedule_refresh() -> None:
            if not window.winfo_exists():
                return
            refresh_journal()
            refresh_token["value"] = window.after(3000, schedule_refresh)

        refresh_token["value"] = window.after(3000, schedule_refresh)

        def on_close() -> None:
            if refresh_token["value"] is not None:
                try:
                    window.after_cancel(refresh_token["value"])
                except tk.TclError:
                    pass
            ability_journal_window["value"] = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", on_close)
        apply_theme(window, palette["surface"])

    def open_ability_config_page() -> None:
        existing = ability_config_window["value"]
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return
        window = tk.Toplevel(root)
        ability_config_window["value"] = window
        window.title("能力提升重抽 · 独立配置")
        window.geometry("820x920")
        window.minsize(760, 850)
        window.configure(bg=palette["surface"])
        window.transient(root)
        window.columnconfigure(1, weight=1)
        tk.Label(
            window,
            text="能力提升重抽",
            font=("Segoe UI Semibold", 13),
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, padx=16, pady=(14, 2), sticky="w")
        tk.Label(
            window,
            text="独立于自动重战；每轮结果会写入能力提升词条记录 JSON，属性顺序不影响匹配。",
            fg="#555",
            anchor="w",
            justify="left",
            wraplength=700,
        ).grid(row=1, column=0, columnspan=4, padx=16, pady=(0, 12), sticky="ew")
        total_frame = tk.LabelFrame(window, text="整体覆盖条件（任一满足即可）")
        total_frame.grid(row=2, column=0, columnspan=4, padx=16, pady=4, sticky="ew")
        total_frame.columnconfigure(4, weight=1)
        tk.Checkbutton(total_frame, text="启用总星数", variable=ability_total_enabled).grid(
            row=0, column=0, padx=8, pady=8, sticky="w"
        )
        tk.Label(total_frame, text="最低总星数").grid(row=0, column=1, padx=(12, 4), sticky="e")
        tk.Entry(total_frame, textvariable=ability_total_min, width=8).grid(row=0, column=2, padx=(0, 8), sticky="w")
        tk.Label(total_frame, text="与下面其它覆盖条件按“或”判断", fg="#666").grid(
            row=0, column=3, padx=8, sticky="w"
        )
        tk.Label(
            total_frame,
            text="下方可配置多种可接受组合；总星数或任一组合满足后，按‘达到条件后的处理’执行。",
            fg="#666",
        ).grid(row=1, column=0, columnspan=4, padx=8, pady=(0, 8), sticky="w")
        groups_notebook = ttk.Notebook(window)
        groups_notebook.grid(row=3, column=0, columnspan=4, padx=16, pady=6, sticky="ew")
        for group_index, group in enumerate(ability_group_states, start=1):
            group_tab = tk.Frame(groups_notebook, bg=palette["surface"])
            group_tab.columnconfigure(1, weight=1)
            groups_notebook.add(group_tab, text=f"组合 {group_index}")
            tk.Checkbutton(
                group_tab,
                text="启用此组合",
                variable=group["enabled"],
                anchor="w",
            ).grid(row=0, column=0, padx=8, pady=(8, 4), sticky="w")
            tk.Label(group_tab, text="名称").grid(row=0, column=1, padx=(12, 4), pady=(8, 4), sticky="e")
            tk.Entry(group_tab, textvariable=group["name"], width=18).grid(
                row=0, column=2, padx=8, pady=(8, 4), sticky="w"
            )
            tk.Checkbutton(
                group_tab,
                text="启用指定属性逐项达标",
                variable=group["thresholds_enabled"],
                anchor="w",
            ).grid(row=1, column=0, columnspan=2, padx=8, pady=4, sticky="w")
            tk.Label(group_tab, text="选择").grid(row=2, column=0, padx=8, pady=2)
            tk.Label(group_tab, text="属性").grid(row=2, column=1, padx=8, pady=2, sticky="w")
            tk.Label(group_tab, text="最低星数").grid(row=2, column=2, padx=8, pady=2, sticky="w")
            rows = group["rows"]
            for row_index, row in enumerate(rows):
                tk.Checkbutton(group_tab, variable=row["enabled"]).grid(
                    row=row_index + 3, column=0, padx=8, pady=2
                )
                ttk.Combobox(
                    group_tab,
                    textvariable=row["name"],
                    values=ability_attribute_names,
                    state="readonly",
                    width=23,
                ).grid(row=row_index + 3, column=1, padx=8, pady=2, sticky="w")
                tk.Entry(group_tab, textvariable=row["min"], width=8).grid(
                    row=row_index + 3, column=2, padx=8, pady=2, sticky="w"
                )
            sum_row = 7
            tk.Checkbutton(
                group_tab,
                text="启用本组合指定属性星数之和",
                variable=group["sum_enabled"],
            ).grid(row=sum_row, column=0, columnspan=2, padx=8, pady=(6, 8), sticky="w")
            tk.Label(group_tab, text="最低星数之和").grid(
                row=sum_row, column=2, padx=(8, 4), pady=(6, 8), sticky="e"
            )
            tk.Entry(group_tab, textvariable=group["sum_min"], width=8).grid(
                row=sum_row, column=3, padx=(0, 8), pady=(6, 8), sticky="w"
            )
            tk.Label(
                group_tab,
                text="本组合的逐项条件和星数之和任一满足即可；组合之间也是任一满足。",
                fg="#666",
                anchor="w",
            ).grid(row=sum_row + 1, column=0, columnspan=4, padx=8, pady=(0, 8), sticky="w")
        stop_frame = tk.LabelFrame(window, text="停止方式（单选）")
        stop_frame.grid(row=4, column=0, columnspan=4, padx=16, pady=6, sticky="ew")
        stop_frame.columnconfigure(1, weight=1)
        tk.Radiobutton(
            stop_frame,
            text="按上方启用的整体条件判断；达标后的处理方式见下方‘达到条件后的处理’",
            variable=ability_stop_mode,
            value=ABILITY_STOP_MODE_ATTRIBUTES,
            anchor="w",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, padx=8, pady=4, sticky="w")
        tk.Radiobutton(
            stop_frame,
            text="已使用 MSP 达到",
            variable=ability_stop_mode,
            value=ABILITY_STOP_MODE_SPENT_MSP,
            anchor="w",
        ).grid(row=1, column=0, padx=8, pady=4, sticky="w")
        tk.Spinbox(
            stop_frame,
            from_=1,
            to=999999999,
            increment=1,
            textvariable=ability_msp_spent_limit,
            width=12,
        ).grid(row=1, column=1, padx=4, pady=4, sticky="w")
        tk.Label(stop_frame, text="点后停止当前流程").grid(row=1, column=2, padx=4, pady=4, sticky="w")
        tk.Radiobutton(
            stop_frame,
            text="剩余 MSP 小于等于",
            variable=ability_stop_mode,
            value=ABILITY_STOP_MODE_REMAINING_MSP,
            anchor="w",
        ).grid(row=2, column=0, padx=8, pady=4, sticky="w")
        tk.Spinbox(
            stop_frame,
            from_=0,
            to=999999999,
            increment=1,
            textvariable=ability_msp_remaining_limit,
            width=12,
        ).grid(row=2, column=1, padx=4, pady=4, sticky="w")
        tk.Label(stop_frame, text="点后停止当前流程").grid(row=2, column=2, padx=4, pady=4, sticky="w")
        mode_frame = tk.LabelFrame(window, text="达到条件后的处理")
        mode_frame.grid(row=5, column=0, columnspan=4, padx=16, pady=6, sticky="ew")
        tk.Checkbutton(
            mode_frame,
            text="总星数或任意属性组合满足后：自动选择‘是’并覆盖；未勾选则停在覆盖确认页等待手动选择",
            variable=ability_auto_overwrite,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, padx=8, pady=4, sticky="w")
        tk.Checkbutton(
            mode_frame,
            text="四项新词条均优于旧词条时：也自动选择‘是’并覆盖（属性顺序可不同）",
            variable=ability_compare_enabled,
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, padx=8, pady=4, sticky="w")
        tk.Checkbutton(
            mode_frame,
            text="自动覆盖完成后停止任务（取消勾选则等待下一张候选页并继续重抽）",
            variable=ability_stop_after_completion,
            anchor="w",
        ).grid(row=2, column=0, columnspan=2, padx=8, pady=4, sticky="w")
        advanced_frame = tk.LabelFrame(window, text="高级时序（秒，按 0.1 秒保存）")
        advanced_frame.grid(row=6, column=0, columnspan=4, padx=16, pady=6, sticky="ew")
        advanced_frame.columnconfigure(1, weight=1)
        advanced_frame.columnconfigure(3, weight=1)
        for index, (key, label, minimum, maximum, _fallback) in enumerate(ability_timing_specs):
            row = index // 2
            column = (index % 2) * 2
            tk.Label(advanced_frame, text=label).grid(
                row=row, column=column, padx=(8, 4), pady=4, sticky="e"
            )
            tk.Spinbox(
                advanced_frame,
                from_=minimum,
                to=maximum,
                increment=0.1,
                textvariable=ability_timing_vars[key],
                width=8,
                format="%.1f",
            ).grid(row=row, column=column + 1, padx=(0, 12), pady=4, sticky="w")
        tk.Label(
            advanced_frame,
            text="数值越低速度越快，但页面未完成切换时可能重复按键。",
            fg="#666",
            anchor="w",
        ).grid(row=3, column=0, columnspan=4, padx=8, pady=(2, 6), sticky="w")
        action_frame = tk.Frame(window)
        action_frame.grid(row=7, column=0, columnspan=4, padx=16, pady=(8, 4), sticky="ew")
        tk.Button(action_frame, text="保存配置", command=apply_ability_configuration, width=14).pack(side="left")
        ability_primary_button = tk.Button(
            action_frame,
            textvariable=ability_primary_text,
            command=toggle_ability_reroll,
            width=23,
            height=2,
            justify="center",
        )
        ability_primary_button.pack(side="left", padx=8)
        ability_primary_widget["value"] = ability_primary_button
        tk.Button(action_frame, text="打开词条记录", command=open_ability_journal, width=14).pack(side="right")
        tk.Label(window, textvariable=ability_run_status, anchor="w", fg="#444").grid(
            row=8, column=0, columnspan=4, padx=16, pady=(4, 8), sticky="ew"
        )

        def on_close() -> None:
            ability_config_window["value"] = None
            ability_primary_widget["value"] = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", on_close)
        apply_theme(window, palette["surface"])
        set_ability_primary_state()

    def open_settings_page() -> None:
        existing = settings_window["value"]
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return
        window = tk.Toplevel(root)
        settings_window["value"] = window
        window.title("GBFR 自动重战 · 设置")
        window.geometry("940x760")
        window.minsize(820, 650)
        window.configure(bg=palette["surface"])
        window.columnconfigure(0, weight=1)
        tk.Label(
            window,
            text="自动重战设置",
            font=("Segoe UI Semibold", 13),
            anchor="w",
        ).grid(row=0, column=0, padx=16, pady=(14, 2), sticky="w")
        tk.Label(
            window,
            text="设置保存到本机；运行控制和状态保留在主界面。",
            fg="#555",
            anchor="w",
        ).grid(row=1, column=0, padx=16, pady=(0, 10), sticky="w")

        stream_frame = tk.LabelFrame(window, text="Chiaki 串流")
        stream_frame.grid(row=2, column=0, padx=16, pady=5, sticky="ew")
        stream_frame.columnconfigure(1, weight=1)
        tk.Label(stream_frame, text="Chiaki 程序").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        tk.Entry(stream_frame, textvariable=path_var).grid(row=0, column=1, padx=8, pady=6, sticky="ew")
        tk.Button(stream_frame, text="浏览", command=browse_chiaki, width=8).grid(row=0, column=2, padx=4, pady=6)
        tk.Button(stream_frame, text="自动查找", command=detect_chiaki_path, width=10).grid(row=0, column=3, padx=4, pady=6)
        tk.Label(stream_frame, text="窗口标题").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        tk.Entry(stream_frame, textvariable=title_var).grid(row=1, column=1, padx=8, pady=6, sticky="ew")
        tk.Button(stream_frame, text="应用标题", command=apply_window_title, width=10).grid(row=1, column=2, padx=4, pady=6)
        tk.Button(stream_frame, text="捕获当前标题", command=capture_stream_window_title, width=12).grid(row=1, column=3, padx=4, pady=6)
        tk.Button(stream_frame, text="一键同步输入配置", command=sync_input_configuration, width=18).grid(row=1, column=4, padx=8, pady=6)

        runtime_frame = tk.LabelFrame(window, text="输入与兼容性设置")
        runtime_frame.grid(row=3, column=0, padx=16, pady=5, sticky="ew")
        tk.Checkbutton(
            runtime_frame,
            text="反向移动方向（仅后台客户机方向相反时）",
            variable=invert_movement,
            command=lambda: (save_background_choice(), sync_input_configuration(show_dialog=False)),
            anchor="w",
        ).grid(row=0, column=0, padx=8, pady=6, sticky="w")
        tk.Button(runtime_frame, text="安装 ViGEmBus", command=install_virtual_gamepad_driver, width=16).grid(row=0, column=1, padx=8, pady=6, sticky="w")
        tk.Button(runtime_frame, text="安装 HidHide", command=install_hidhide, width=16).grid(row=0, column=2, padx=8, pady=6, sticky="w")

        recovery_page_frame = tk.LabelFrame(window, text="串流卡死恢复与重连")
        recovery_page_frame.grid(row=4, column=0, padx=16, pady=5, sticky="ew")
        recovery_page_frame.columnconfigure(1, weight=1)
        recovery_page_frame.columnconfigure(3, weight=1)
        tk.Checkbutton(recovery_page_frame, text="画面持续静止后自动重连", variable=auto_recover, command=save_background_choice).grid(row=0, column=0, columnspan=2, padx=8, pady=6, sticky="w")
        tk.Label(recovery_page_frame, text="静止判定（分钟）").grid(row=0, column=2, padx=8, pady=6, sticky="e")
        tk.Entry(recovery_page_frame, textvariable=freeze_minutes_var, width=8).grid(row=0, column=3, padx=8, pady=6, sticky="w")
        tk.Button(recovery_page_frame, text="从 Chiaki 获取主机信息", command=discover_reconnect_target, width=20).grid(row=0, column=4, padx=8, pady=6)
        tk.Label(recovery_page_frame, text="主机昵称").grid(row=1, column=0, padx=8, pady=4, sticky="w")
        tk.Entry(recovery_page_frame, textvariable=reconnect_nickname_var).grid(row=1, column=1, padx=8, pady=4, sticky="ew")
        tk.Label(recovery_page_frame, text="主机地址 / IP").grid(row=1, column=2, padx=8, pady=4, sticky="e")
        tk.Entry(recovery_page_frame, textvariable=reconnect_host_var).grid(row=1, column=3, padx=8, pady=4, sticky="ew")
        tk.Button(recovery_page_frame, text="保存重连设置", command=save_recovery_configuration, width=14).grid(row=1, column=4, padx=8, pady=4)
        tk.Button(recovery_page_frame, text="重新捕获串流窗口（F4）", command=recapture_stream_window, width=20).grid(row=2, column=1, padx=8, pady=6, sticky="w")

        schedule_page_frame = tk.LabelFrame(window, text="自动结束")
        schedule_page_frame.grid(row=5, column=0, padx=16, pady=5, sticky="ew")
        tk.Label(schedule_page_frame, text="完成场数后关闭").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        tk.Entry(schedule_page_frame, textvariable=max_battles_var, width=8).grid(row=0, column=1, padx=8, pady=6, sticky="w")
        tk.Label(schedule_page_frame, text="运行时长（分钟）").grid(row=0, column=2, padx=8, pady=6, sticky="w")
        tk.Entry(schedule_page_frame, textvariable=max_runtime_var, width=8).grid(row=0, column=3, padx=8, pady=6, sticky="w")
        tk.Label(schedule_page_frame, text="关闭时间（HH:MM）").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        tk.Entry(schedule_page_frame, textvariable=stop_at_var, width=8).grid(row=1, column=1, padx=8, pady=6, sticky="w")
        tk.Button(schedule_page_frame, text="应用设置", command=apply_schedule, width=12).grid(row=1, column=2, padx=8, pady=6, sticky="w")

        def on_close() -> None:
            settings_window["value"] = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", on_close)
        apply_theme(window, palette["surface"])

    def one_click_reconnect() -> None:
        process = automation_process["value"]
        if not reconnect_nickname_var.get().strip() or not reconnect_host_var.get().strip():
            messagebox.showwarning(
                "重连目标未配置",
                "请先填写并保存主机昵称和主机地址/IP。",
                parent=root,
            )
            return
        confirmed = messagebox.askyesno(
            "确认一键重连并开始挂机",
            "这会关闭当前自动化绑定的 Chiaki 串流，并使用已保存的主机昵称和地址重新连接。\n\n"
            "重连后会自动判断战斗、结算或主城、尝试继续任务，然后进入完整自动挂机流程。\n"
            "挂机统计、自动结束、F2 停止、F3 暂停/继续和 Ctrl+Shift+F5 关闭 Chiaki 都会正常生效。\n\n"
            "确定继续吗？",
            parent=root,
        )
        if not confirmed:
            return
        if process is not None and process.poll() is None:
            set_status("正在停止旧自动化并释放输入")
            try:
                keybd_event = ctypes.windll.user32.keybd_event
                send_automation_hotkey_combo((0x11, 0x10), 0x74)
                sleep(0.5)
                process.terminate()
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
            except OSError:
                pass
            automation_process["value"] = None
            active_run_kind["value"] = None
            active_background_mode["value"] = None
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            background_check.configure(state="normal")
        start_automation(reconnect_once=True)

    def stop_automation() -> None:
        process = automation_process["value"]
        if process is None or process.poll() is not None:
            run_kind = active_run_kind["value"]
            active_run_kind["value"] = None
            active_background_mode["value"] = None
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            background_check.configure(state="normal")
            background_label.set(
                "后台运行（勾选后启用；运行中锁定，停止后可取消）"
            )
            if run_kind == "ability":
                ability_run_status.set("能力提升重抽：未启动")
                set_status("能力提升重抽当前没有运行")
                ability_manual_notice_shown["value"] = False
                set_automation_state("自动战斗：未启动", "idle")
            else:
                set_status("自动重战当前没有运行")
                set_automation_state("自动战斗：未启动", "idle")
            set_ability_primary_state()
            return
        # Give the child a chance to run its own F2 cleanup first. This is
        # important for the independent ability worker: it clears the offer /
        # success / result phase and releases any held stick input before the
        # next normal rebattle worker may be launched.
        try:
            keybd_event = ctypes.windll.user32.keybd_event
            keybd_event(0x71, 0, 0, 0)  # F2 down
            keybd_event(0x71, 0, 0x0002, 0)  # F2 up
        except (AttributeError, OSError):
            pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=4.0)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait(timeout=3.0)
        automation_process["value"] = None
        run_kind = active_run_kind["value"]
        active_run_kind["value"] = None
        active_background_mode["value"] = None
        if automation_output["value"] is not None:
            automation_output["value"].close()
            automation_output["value"] = None
        background_check.configure(state="normal")
        background_label.set(
            "后台运行（勾选后启用；运行中锁定，停止后可取消）"
        )
        if run_kind == "ability":
            ability_run_status.set("能力提升重抽：已停止")
            set_status("能力提升重抽已停止")
            set_automation_state("自动战斗：未启动", "idle")
            ability_manual_notice_shown["value"] = False
        else:
            set_status("自动重战已停止；现在可以取消“后台运行”勾选，改为前台运行")
            set_automation_state("自动战斗：已停止", "stopped")
        set_ability_primary_state()

    def toggle_automation() -> None:
        """Use one primary action for the normal start/stop workflow."""
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            stop_automation()
        else:
            start_automation()

    def send_automation_hotkey(vk: int) -> None:
        """Send a global automation command to the elevated child."""
        process = automation_process["value"]
        if process is None or process.poll() is not None:
            set_status("自动重战尚未运行")
            return
        try:
            keybd_event = ctypes.windll.user32.keybd_event
            keybd_event(vk, 0, 0, 0)
            keybd_event(vk, 0, 0x0002, 0)
        except Exception as exc:
            set_status(f"发送快捷键失败：{exc}")

    def send_automation_hotkey_combo(modifier_vks: tuple[int, ...], key_vk: int) -> None:
        """Send a modifier combination to the elevated automation child."""
        process = automation_process["value"]
        if process is None or process.poll() is not None:
            set_status("自动重战尚未运行")
            return
        try:
            keybd_event = ctypes.windll.user32.keybd_event
            for modifier_vk in modifier_vks:
                keybd_event(modifier_vk, 0, 0, 0)
            keybd_event(key_vk, 0, 0, 0)
            keybd_event(key_vk, 0, 0x0002, 0)
            for modifier_vk in reversed(modifier_vks):
                keybd_event(modifier_vk, 0, 0x0002, 0)
        except Exception as exc:
            set_status(f"发送组合快捷键失败：{exc}")

    def toggle_pause() -> None:
        send_automation_hotkey(0x72)  # F3

    def stop_and_close_chiaki() -> None:
        send_automation_hotkey_combo((0x11, 0x10), 0x74)  # Ctrl+Shift+F5
        set_status("已请求停止自动重战并关闭 Chiaki")

    def recapture_stream_window() -> None:
        process = automation_process["value"]
        if process is None or process.poll() is not None:
            set_status("请先启动自动重战，再执行重新捕获")
            return
        send_automation_hotkey(0x73)  # F4
        set_status("已请求重新捕获 Chiaki 串流窗口，请查看运行日志确认结果")

    def show_latest_log() -> None:
        log_follow["value"] = True
        log_follow_text.set("暂停自动滚动")
        console.see("end")

    def toggle_log_follow() -> None:
        log_follow["value"] = not log_follow["value"]
        if log_follow["value"]:
            log_follow_text.set("暂停自动滚动")
            console.see("end")
        else:
            log_follow_text.set("恢复自动滚动")

    stats_table_signature = {"value": None}

    def poll_stats() -> None:
        try:
            if stats_path.is_file():
                data = json.loads(stats_path.read_text(encoding="utf-8"))
                completed = int(data.get("completed_battles", 0))
                current = data.get("current_battle_seconds")
                total_runtime = data.get("total_runtime_seconds") or 0
                average = data.get("average_battle_seconds")
                last = data.get("last_battle_seconds")
                shortest = data.get("shortest_battle_seconds")
                longest = data.get("longest_battle_seconds")
                stats_summary.set(
                    f"已完成 {completed} 场  |  总运行 {_format_duration(total_runtime)}  |  "
                    f"平均 {_format_duration(average)}  |  上一场 {_format_duration(last)}"
                )
                stats_detail.set(
                    f"状态：{data.get('status', '未知')}  |  当前场耗时：{_format_duration(current)}  |  "
                    f"最快 {_format_duration(shortest)}  |  最慢 {_format_duration(longest)}"
                )
                process = automation_process["value"]
                if process is not None and process.poll() is None:
                    runtime_status = str(data.get("status", ""))
                    state_labels = {
                        "战斗中": ("自动战斗：战斗中", "running"),
                        "结算中": ("自动战斗：结算推进中", "running"),
                        "等待战斗": ("自动战斗：运行中，等待战斗", "running"),
                        "已暂停": ("自动战斗：已暂停", "paused"),
                        "串流恢复中": ("自动战斗：串流恢复中", "recovering"),
                        "等待启动": ("自动战斗：等待启动", "idle"),
                    }
                    label = state_labels.get(runtime_status)
                    if label is not None:
                        set_automation_state(*label)
                battles = data.get("battles", [])
                table_rows = tuple(
                    (
                        item.get("number", ""),
                        item.get("duration_seconds"),
                        item.get("ended_at", ""),
                    )
                    for item in battles[-30:]
                )
                if table_rows != stats_table_signature["value"]:
                    battle_table.delete(*battle_table.get_children())
                    for item in battles[-30:]:
                        battle_table.insert(
                            "",
                            "end",
                            values=(
                                item.get("number", ""),
                                _format_duration(item.get("duration_seconds")),
                                item.get("ended_at", ""),
                            ),
                        )
                    stats_table_signature["value"] = table_rows
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
        root.after(1000, poll_stats)

    def account_id() -> None:
        webbrowser.open(LOGIN_URL)
        redirect = simpledialog.askstring(
            "获取 PSN AccountID",
            "登录后复制地址栏中的完整 redirect URL：",
            parent=root,
        )
        if not redirect:
            return

        def worker() -> None:
            try:
                value = account_id_from_redirect(redirect)
            except Exception as exc:
                root.after(0, lambda: messagebox.showerror("获取失败", str(exc)))
                return

            def show_result() -> None:
                root.clipboard_clear()
                root.clipboard_append(value)
                root.update()
                messagebox.showinfo(
                    "AccountID 获取成功",
                    f"AccountID：\n{value}\n\n已复制到剪贴板。",
                )

            root.after(0, show_result)

        threading.Thread(target=worker, daemon=True).start()
        set_status("正在请求 Sony Account Info，请稍候")

    def poll_processes() -> None:
        process = automation_process["value"]
        if process is not None and process.poll() is not None:
            exit_code = process.returncode
            run_kind = active_run_kind["value"]
            automation_process["value"] = None
            active_run_kind["value"] = None
            was_background = active_background_mode["value"] is True
            active_background_mode["value"] = None
            background_check.configure(state="normal")
            background_label.set(
                "后台运行（勾选后启用；运行中锁定，停止后可取消）"
            )
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            if run_kind == "ability":
                if ability_manual_notice_shown["value"]:
                    ability_run_status.set("能力提升重抽：等待手动确认覆盖")
                    set_status("能力提升已停在覆盖确认页，请手动选择‘是’或‘否’")
                else:
                    ability_run_status.set(
                        "能力提升重抽：已结束" if exit_code == 0 else f"能力提升重抽：异常退出 ({exit_code})"
                    )
                    set_status("能力提升重抽已结束；详细词条见能力提升词条记录 JSON")
                set_automation_state("自动战斗：未启动", "idle")
                set_ability_primary_state()
                root.after(500, poll_processes)
                return
            stop_reason = ""
            try:
                stats_data = json.loads(stats_path.read_text(encoding="utf-8"))
                stop_reason = str(stats_data.get("stop_reason", ""))
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
            if stop_reason:
                set_status(f"自动重战已按计划结束：{stop_reason}")
                set_automation_state("自动战斗：已按计划结束", "stopped")
            elif exit_code == 0:
                set_status("自动重战已结束；详细信息见下方运行日志")
                set_automation_state("自动战斗：已停止", "stopped")
            else:
                set_status(
                    f"自动重战异常退出（退出码 {exit_code}）；详细信息见下方运行日志"
                )
                set_automation_state("自动战斗：异常退出", "stopped")
            if was_background:
                if stop_reason:
                    set_status(
                        f"自动重战已按计划结束：{stop_reason}；如需前台运行，请取消“后台运行”后重新启动"
                    )
                else:
                    set_status("自动重战已结束；如需前台运行，请取消“后台运行”后重新启动")
        root.after(500, poll_processes)

    tk.Label(
        root,
        text="Chiaki 程序",
        image=header_icon,
        compound="left",
        font=("Segoe UI Semibold", 10),
        padx=2,
    ).grid(row=0, column=0, padx=12, pady=(10, 6), sticky="w")
    path_frame = tk.Frame(root)
    path_frame.grid(row=0, column=1, padx=8, pady=(14, 6), sticky="ew")
    path_frame.columnconfigure(0, weight=1)
    tk.Entry(path_frame, textvariable=path_var).grid(row=0, column=0, sticky="ew")
    tk.Button(path_frame, text="浏览", command=browse_chiaki, width=7).grid(row=0, column=1, padx=(6, 0))
    tk.Button(path_frame, text="自动查找", command=detect_chiaki_path, width=9).grid(row=0, column=2, padx=(6, 0))
    start_chiaki_button = tk.Button(root, text="启动 Chiaki", command=start_chiaki, width=12)
    start_chiaki_button.grid(row=0, column=2, padx=12, pady=(14, 6))
    tk.Label(root, text="串流窗口标题").grid(row=1, column=0, padx=12, pady=6, sticky="w")
    title_frame = tk.Frame(root)
    title_frame.grid(row=1, column=1, padx=8, pady=6, sticky="ew")
    title_frame.columnconfigure(0, weight=1)
    tk.Entry(title_frame, textvariable=title_var).grid(row=0, column=0, sticky="ew")
    tk.Button(title_frame, text="应用标题", command=apply_window_title, width=10).grid(
        row=0, column=1, padx=(6, 0), sticky="e"
    )
    tk.Button(
        title_frame,
        text="捕获当前标题",
        command=capture_stream_window_title,
        width=12,
    ).grid(row=0, column=2, padx=(6, 0), sticky="e")
    tk.Button(root, text="一键同步输入配置", command=sync_input_configuration, width=18).grid(row=1, column=2, padx=12, pady=6)
    background_check = tk.Checkbutton(
        root,
        textvariable=background_label,
        variable=background,
        command=save_background_choice,
    )
    background_check.configure(anchor="w", justify="left", wraplength=760)
    language_frame = tk.Frame(root)
    language_frame.grid(row=2, column=0, columnspan=3, padx=12, pady=(4, 2), sticky="ew")
    language_frame.columnconfigure(1, weight=1)
    tk.Label(language_frame, text="游戏界面语言").grid(row=0, column=0, padx=(0, 8), sticky="w")
    language_combo = ttk.Combobox(
        language_frame,
        textvariable=ui_language_var,
        values=tuple(GAME_LANGUAGE_LABELS[saved_app_language].values()),
        state="readonly",
        width=14,
    )
    language_combo_holder["value"] = language_combo
    language_combo.grid(row=0, column=1, sticky="w")
    tk.Button(language_frame, text="应用语言", command=apply_ui_language, width=12).grid(
        row=0, column=2, padx=(8, 0), sticky="w"
    )
    tk.Label(
        language_frame,
        text="自动识别会在本次运行首次确认到战斗/结算/主城文字后锁定语言",
        fg="#666",
        anchor="w",
    ).grid(row=0, column=3, padx=(14, 0), sticky="ew")
    tk.Label(language_frame, text="识别画面档位").grid(
        row=1, column=0, padx=(0, 8), pady=(3, 2), sticky="w"
    )
    recognition_profile_combo = ttk.Combobox(
        language_frame,
        textvariable=recognition_profile_var,
        values=tuple(recognition_profile_labels.values()),
        state="readonly",
        width=25,
    )
    recognition_profile_combo.set(
        recognition_profile_labels.get(recognition_profile_var.get(), recognition_profile_labels["auto"])
    )
    recognition_profile_combo.grid(row=1, column=1, pady=(3, 2), sticky="w")
    recognition_profile_combo.bind(
        "<<ComboboxSelected>>",
        lambda _event: save_background_choice(),
    )
    tk.Label(
        language_frame,
        text="按实际像素档位归一化；拖动 Chiaki 后自动重新取样，前后台规则一致",
        fg="#666",
        anchor="w",
    ).grid(row=1, column=3, padx=(14, 0), pady=(3, 2), sticky="ew")
    tk.Label(language_frame, text="恢复 Chiaki 画面").grid(
        row=2, column=0, padx=(0, 8), pady=(4, 2), sticky="w"
    )
    resolution_buttons = tk.Frame(language_frame)
    resolution_buttons.grid(row=2, column=1, columnspan=2, pady=(4, 2), sticky="w")
    for profile, label in (
        ("chiaki_360p", "恢复 360p"),
        ("chiaki_540p", "恢复 540p"),
        ("chiaki_720p", "恢复 720p"),
        ("chiaki_1080p", "恢复 1080p"),
    ):
        tk.Button(
            resolution_buttons,
            text=label,
            command=lambda selected=profile: restore_chiaki_resolution(selected),
            width=12,
        ).pack(side="left", padx=(0, 5))
    tk.Label(
        language_frame,
        text="仅调整 Chiaki 窗口客户区，不改变 PS5 编码流；调整后识别档位会同步",
        fg="#666",
        anchor="w",
    ).grid(row=2, column=3, padx=(14, 0), pady=(4, 2), sticky="ew")

    refocus_home_frame = tk.LabelFrame(root, text="战斗索敌方案")
    refocus_home_frame.grid(row=3, column=0, columnspan=3, padx=12, pady=(2, 6), sticky="ew")
    refocus_home_frame.columnconfigure(1, weight=1)
    tk.Label(refocus_home_frame, text="索敌方案").grid(row=0, column=0, padx=8, pady=6, sticky="w")
    ttk.Combobox(
        refocus_home_frame,
        textvariable=refocus_mode_var,
        values=tuple(REFOCUS_MODE_LABELS.values()),
        state="readonly",
        width=18,
    ).grid(row=0, column=1, padx=8, pady=6, sticky="w")
    tk.Button(
        refocus_home_frame,
        text="应用索敌方案",
        command=apply_refocus_mode,
        width=14,
    ).grid(row=0, column=2, padx=8, pady=6, sticky="w")
    tk.Label(
        refocus_home_frame,
        text="方案编号会写入运行日志，便于对比不同方案的战斗速度",
        fg="#555",
        anchor="w",
    ).grid(row=1, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="w")

    background_check.grid(row=4, column=0, columnspan=3, padx=12, pady=6, sticky="ew")
    background_environment_button = tk.Button(root, text="检查后台环境", command=check_background_environment, width=16)
    background_environment_button.grid(row=5, column=0, padx=12, pady=(2, 6))
    tk.Button(root, text="安装 ViGEmBus", command=install_virtual_gamepad_driver, width=16).grid(row=5, column=1, padx=8, pady=(2, 6), sticky="w")
    tk.Button(root, text="安装 HidHide", command=install_hidhide, width=16).grid(row=5, column=2, padx=12, pady=(2, 6), sticky="w")
    primary_automation_button = tk.Button(
        root,
        textvariable=automation_primary_text,
        command=toggle_automation,
        width=23,
        height=2,
        justify="center",
    )
    primary_automation_button.grid(row=6, column=0, padx=12, pady=8)
    automation_primary_widget["value"] = primary_automation_button
    pause_automation_button = tk.Button(root, text="暂停/继续（F3）", command=toggle_pause, width=16)
    pause_automation_button.grid(row=6, column=1, padx=8, pady=8, sticky="w")
    account_id_button = tk.Button(root, text="获取 PSN AccountID", command=account_id, width=20)
    account_id_button.grid(row=7, column=0, padx=12, pady=(0, 6), sticky="w")
    utility_frame = tk.Frame(root)
    utility_frame.grid(row=7, column=1, padx=8, pady=(0, 6), sticky="w")
    stop_and_close_button = tk.Button(
        utility_frame,
        text="停止并关闭 Chiaki",
        command=stop_and_close_chiaki,
        width=18,
    )
    stop_and_close_button.pack(side="left")
    tk.Button(utility_frame, text="打开日志目录", command=open_logs, width=14).pack(side="left")
    tk.Button(utility_frame, text="设置…", command=open_settings_page, width=9).pack(side="left", padx=(6, 0))
    tk.Button(utility_frame, text="能力提升重抽…", command=open_ability_config_page, width=16).pack(side="left", padx=(6, 0))
    tk.Button(utility_frame, text="查看词条记录", command=open_ability_journal, width=14).pack(side="left", padx=(6, 0))
    invert_check = tk.Checkbutton(
        root,
        text="反向移动方向（仅后台客户机方向相反时）",
        variable=invert_movement,
        command=lambda: (save_background_choice(), sync_input_configuration(show_dialog=False)),
        anchor="w",
        justify="left",
        wraplength=300,
    )
    invert_check.grid(row=7, column=2, padx=8, pady=(0, 6), sticky="ew")

    recovery_frame = tk.LabelFrame(root, text="串流卡死恢复（配置会保存在本机）")
    recovery_frame.grid(row=8, column=0, columnspan=3, padx=12, pady=(2, 6), sticky="ew")
    recovery_frame.columnconfigure(1, weight=1)
    recovery_frame.columnconfigure(3, weight=1)
    tk.Checkbutton(
        recovery_frame,
        text="画面持续静止后自动重连",
        variable=auto_recover,
        command=save_background_choice,
    ).grid(row=0, column=0, columnspan=2, padx=8, pady=6, sticky="ew")
    tk.Button(
        recovery_frame,
        text="从 Chiaki 获取主机信息",
        command=discover_reconnect_target,
        width=20,
    ).grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")
    tk.Label(recovery_frame, text="静止判定（分钟，可填 0.2 测试）").grid(row=0, column=3, padx=(8, 4), pady=6, sticky="e")
    tk.Entry(recovery_frame, textvariable=freeze_minutes_var, width=8).grid(row=0, column=4, padx=(0, 8), pady=6, sticky="w")
    recovery_frame.columnconfigure(4, weight=0)
    tk.Label(recovery_frame, text="主机昵称").grid(row=1, column=0, padx=(8, 4), pady=4, sticky="w")
    tk.Entry(recovery_frame, textvariable=reconnect_nickname_var).grid(row=1, column=1, padx=(0, 12), pady=4, sticky="ew")
    tk.Label(recovery_frame, text="主机地址 / IP").grid(row=1, column=2, padx=(8, 4), pady=4, sticky="e")
    tk.Entry(recovery_frame, textvariable=reconnect_host_var).grid(row=1, column=3, padx=(0, 8), pady=4, sticky="ew")
    tk.Button(
        recovery_frame,
        text="保存重连设置",
        command=save_recovery_configuration,
        width=14,
    ).grid(row=1, column=4, padx=(0, 8), pady=4, sticky="w")
    tk.Label(
        recovery_frame,
        text="“从 Chiaki 获取主机信息”会读取当前用户的 Chiaki 注册表；发现多个目标时会让你选择。"
        "确认后请点击“保存重连设置”，重连只使用这里保存的目标。",
        fg="#666",
        anchor="w",
        justify="left",
        wraplength=760,
    ).grid(row=2, column=0, columnspan=5, padx=8, pady=(2, 6), sticky="ew")
    tk.Button(
        recovery_frame,
        text="一键重连并挂机",
        command=one_click_reconnect,
        width=14,
    ).grid(row=3, column=0, padx=8, pady=(0, 6), sticky="w")
    tk.Button(
        recovery_frame,
        text="重新捕获串流窗口（F4）",
        command=recapture_stream_window,
        width=20,
    ).grid(row=3, column=1, padx=(0, 12), pady=(0, 6), sticky="w")
    tk.Label(
        recovery_frame,
        text="会关闭当前绑定串流，恢复成功后自动进入完整挂机流程；捕获失败时可点 F4 重试。",
        fg="#8a4b08",
        anchor="w",
    ).grid(row=4, column=0, columnspan=5, padx=8, pady=(0, 6), sticky="ew")

    schedule_frame = tk.LabelFrame(root, text="自动结束设置（点击“应用设置”后生效，运行中修改也会生效）")
    schedule_frame.grid(row=9, column=0, columnspan=3, padx=12, pady=(2, 6), sticky="ew")
    schedule_frame.columnconfigure(1, weight=1)
    schedule_frame.columnconfigure(3, weight=1)
    schedule_frame.columnconfigure(5, weight=1)
    tk.Label(schedule_frame, text="完成场数后关闭").grid(row=0, column=0, padx=(8, 4), pady=6, sticky="w")
    tk.Entry(schedule_frame, textvariable=max_battles_var, width=8).grid(row=0, column=1, padx=(0, 16), pady=6, sticky="w")
    tk.Label(schedule_frame, text="运行时长（分钟）").grid(row=0, column=2, padx=(8, 4), pady=6, sticky="w")
    tk.Entry(schedule_frame, textvariable=max_runtime_var, width=8).grid(row=0, column=3, padx=(0, 16), pady=6, sticky="w")
    tk.Label(schedule_frame, text="关闭时间（HH:MM）").grid(row=1, column=0, padx=(8, 4), pady=(0, 6), sticky="w")
    tk.Entry(schedule_frame, textvariable=stop_at_var, width=8).grid(row=1, column=1, padx=(0, 16), pady=(0, 6), sticky="w")
    tk.Button(schedule_frame, text="应用设置", command=apply_schedule, width=12).grid(row=1, column=2, padx=(8, 8), pady=(0, 6), sticky="w")
    tk.Label(
        schedule_frame,
        text="时间格式 HH:MM，按本机时钟；已过该时间则按次日。留空或 0 表示不启用，任一条件满足即关闭 Chiaki。",
        fg="#666",
        anchor="w",
        justify="left",
        wraplength=760,
    ).grid(row=2, column=0, columnspan=6, padx=8, pady=(0, 6), sticky="ew")

    stats_frame = tk.LabelFrame(root, text="本轮挂机统计")
    stats_frame.grid(row=10, column=0, columnspan=3, padx=12, pady=(0, 6), sticky="nsew")
    stats_frame.columnconfigure(0, weight=1)
    stats_frame.rowconfigure(3, weight=1)
    state_banner = tk.Label(
        stats_frame,
        textvariable=automation_state,
        anchor="w",
        padx=10,
        pady=7,
        font=("Segoe UI Semibold", 11),
    )
    state_banner.grid(row=0, column=0, padx=8, pady=(7, 5), sticky="ew")
    automation_state_widget["value"] = state_banner
    tk.Label(stats_frame, textvariable=stats_summary, anchor="w").grid(row=1, column=0, padx=8, pady=(0, 0), sticky="ew")
    tk.Label(stats_frame, textvariable=stats_detail, anchor="w", fg="#555").grid(row=2, column=0, padx=8, pady=(0, 4), sticky="ew")
    battle_table = ttk.Treeview(stats_frame, columns=("number", "duration", "ended"), show="headings", height=5)
    battle_table.heading("number", text="场次")
    battle_table.heading("duration", text="本场耗时")
    battle_table.heading("ended", text="结算时间")
    battle_table.column("number", width=60, anchor="center", stretch=False)
    battle_table.column("duration", width=100, anchor="center", stretch=False)
    battle_table.column("ended", width=250, anchor="center", stretch=True)
    battle_table.grid(row=3, column=0, padx=8, pady=(0, 6), sticky="nsew")

    status_widget = tk.Label(
        root,
        textvariable=status,
        anchor="w",
        fg="#444",
        height=1,
        wraplength=0,
    )
    status_widget.grid(row=10, column=0, columnspan=3, padx=12, pady=(2, 6), sticky="ew")
    tk.Label(
        root,
        text="运行日志（暂停自动滚动后可拖动右侧滚动条查看历史）",
        anchor="w",
    ).grid(row=11, column=0, padx=12, pady=(4, 2), sticky="w")
    log_action_frame = tk.Frame(root)
    log_action_frame.grid(row=11, column=1, columnspan=2, padx=12, pady=(4, 2), sticky="e")
    tk.Button(log_action_frame, text="查看最新日志", command=show_latest_log, width=14).grid(row=0, column=0, padx=(0, 6))
    tk.Button(log_action_frame, textvariable=log_follow_text, command=toggle_log_follow, width=16).grid(row=0, column=1)
    console = scrolledtext.ScrolledText(
        root,
        height=15,
        bg="#111111",
        fg="#9fe49f",
        insertbackground="#ffffff",
        font=("Consolas", 9),
        wrap="word",
        state="disabled",
    )
    console.grid(row=12, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="nsew")
    root.rowconfigure(9, weight=1)
    root.rowconfigure(12, weight=2)
    tk.Label(root, text="提示：点击“启动自动重战”即开始，无需按 F1；F2 仅停止自动化并保留 Chiaki，Ctrl+Shift+F5 停止并关闭 Chiaki，F3 暂停/继续，F4 重新捕获串流窗口。", anchor="w", justify="left").grid(row=13, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="ew")

    # Keep low-frequency configuration in separate pages, while retaining the
    # common launch, language, environment, reconnect, and AccountID actions
    # on the main panel.
    for old_row in (0, 1, 2, 4, 5, 7, 8):
        for widget in root.grid_slaves(row=old_row):
            widget.grid_remove()
    tk.Label(
        root,
        text="GBFR 自动重战",
        font=("Segoe UI Semibold", 13),
        anchor="w",
    ).grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 2), sticky="w")
    display_language_frame = tk.Frame(root)
    display_language_frame.grid(row=0, column=2, padx=12, pady=(8, 2), sticky="e")
    tk.Label(display_language_frame, text="工具界面语言").pack(side="left", padx=(0, 5))
    display_language_combo = ttk.Combobox(
        display_language_frame,
        textvariable=app_language_var,
        values=tuple(APP_LANGUAGE_LABELS.values()),
        state="readonly",
        width=10,
    )
    display_language_combo.pack(side="left")
    tk.Button(
        display_language_frame,
        text="应用界面",
        command=apply_display_language,
        width=8,
    ).pack(side="left", padx=(5, 0))
    path_frame.grid_configure(row=1, column=1, padx=8, pady=(4, 5), sticky="ew")
    path_frame.grid()
    start_chiaki_button.grid_configure(row=1, column=2, padx=12, pady=(4, 5), sticky="e")
    start_chiaki_button.grid()
    language_frame.grid_configure(row=2, column=0, columnspan=3, padx=12, pady=(2, 4), sticky="ew")
    language_frame.grid()
    refocus_home_frame.grid_configure(row=3, column=0, columnspan=3, padx=12, pady=(2, 4), sticky="ew")
    refocus_home_frame.grid()
    background_check.grid_configure(row=4, column=0, columnspan=3, padx=12, pady=(2, 4), sticky="ew")
    background_check.grid()
    background_environment_button.grid_configure(row=5, column=0, padx=12, pady=5, sticky="w")
    background_environment_button.grid()
    account_id_button.grid_configure(row=5, column=1, padx=8, pady=5, sticky="w")
    account_id_button.grid()
    primary_automation_button.grid_configure(row=6, column=0, padx=12, pady=6, sticky="w")
    primary_automation_button.grid()
    pause_automation_button.grid_configure(row=6, column=1, padx=8, pady=6, sticky="w")
    pause_automation_button.grid()
    reconnect_main_button = tk.Button(
        root,
        text="一键重连并挂机",
        command=one_click_reconnect,
        width=20,
        height=2,
        justify="center",
    )
    reconnect_main_button.grid(row=6, column=2, padx=12, pady=6, sticky="e")
    utility_frame.grid_configure(row=7, column=0, columnspan=3, padx=12, pady=(0, 5), sticky="w")
    utility_frame.grid()
    stats_frame.grid_configure(row=8, column=0, columnspan=3, sticky="ew")
    stats_frame.grid()
    schedule_frame.grid_configure(row=9, column=0, columnspan=3, sticky="ew")
    schedule_frame.grid()
    status_widget.grid_configure(row=10, column=0, columnspan=3, sticky="ew")
    status_widget.grid()
    # Rows 9-13 already contain the schedule, status, log, console, and hint
    # widgets in their final positions. Keep them distinct from the stats row.
    root.rowconfigure(8, weight=0)
    root.rowconfigure(9, weight=0)
    root.rowconfigure(10, weight=0)
    root.rowconfigure(11, weight=0)
    root.rowconfigure(12, weight=2)

    primary_commands = {
        "启动自动重战",
        "一键重连并挂机",
        "应用语言",
        "应用设置",
    }
    gold_commands = {"安装 ViGEmBus", "安装 HidHide"}

    def apply_theme(widget, container_bg: str) -> None:
        for child in widget.winfo_children():
            child_bg = container_bg
            # ttk.Entry and ttk.Combobox inherit from tk.Entry at the Python
            # level, but their Tcl widgets reject classic options such as
            # ``-bg``. Their colors are already configured through ttk.Style.
            if isinstance(child, ttk.Widget):
                pass
            elif isinstance(child, tk.LabelFrame):
                child_bg = palette["panel"]
                child.configure(
                    bg=child_bg,
                    fg=palette["ink"],
                    font=("Segoe UI Semibold", 9),
                    bd=1,
                    relief="solid",
                )
            elif isinstance(child, tk.Frame):
                child.configure(bg=container_bg)
            elif isinstance(child, tk.Label):
                current_fg = str(child.cget("fg")).lower()
                foreground = (
                    palette["muted"]
                    if current_fg in {"#555", "#666", "#444", "#8a4b08"}
                    else palette["ink"]
                )
                child.configure(bg=container_bg, fg=foreground)
            elif isinstance(child, (tk.Checkbutton, tk.Radiobutton)):
                child.configure(
                    bg=container_bg,
                    fg=palette["ink"],
                    activebackground=container_bg,
                    activeforeground=palette["blue"],
                    selectcolor="#ffffff",
                    highlightthickness=0,
                )
            elif isinstance(child, tk.Button):
                text = str(child.cget("text"))
                if text in primary_commands:
                    bg, fg, active = (
                        palette["blue"],
                        "#ffffff",
                        palette["blue_hover"],
                    )
                elif text in gold_commands:
                    bg, fg, active = (
                        palette["gold_soft"],
                        palette["ink"],
                        palette["gold"],
                    )
                elif text == "停止自动重战":
                    bg, fg, active = "#f7e8e4", palette["danger"], "#edd2ca"
                else:
                    bg, fg, active = "#ffffff", palette["ink"], palette["ice"]
                child.configure(
                    bg=bg,
                    fg=fg,
                    activebackground=active,
                    activeforeground=fg,
                    relief="flat",
                    bd=0,
                    cursor="hand2",
                    highlightthickness=1,
                    highlightbackground="#a9c5d8",
                    highlightcolor=palette["blue"],
                )
            elif isinstance(child, tk.Entry):
                child.configure(
                    bg="#ffffff",
                    fg=palette["ink"],
                    insertbackground=palette["ink"],
                    relief="solid",
                    bd=1,
                    highlightthickness=1,
                    highlightbackground="#b8cddc",
                    highlightcolor=palette["blue"],
                )
            apply_theme(child, child_bg)

    apply_theme(root, palette["surface"])
    if saved_app_language != "zh":
        apply_display_language()
    set_automation_state(
        automation_state.get(), str(automation_state_current["tone"])
    )

    def close() -> None:
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            # Reuse the same F2/timeout cleanup as the visible Stop action so
            # a window close cannot leave a held key, stick, or child process.
            stop_automation()
        elif automation_output["value"] is not None:
            automation_output["value"].close()
            automation_output["value"] = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(500, poll_processes)
    root.after(250, poll_console_log)
    root.after(1000, poll_stats)
    root.mainloop()
    return 0


# ============================================================
#  入口
# ============================================================
if __name__ == "__main__":
    args = parse_args()
    DEBUG_MODE = bool(args.debug or os.environ.get("GBFR_DEBUG", "").strip() == "1")
    if args.diagnostics:
        print(f"windows-capture: {'可用' if WindowsCapture is not None else '缺失'}")
        print(f"vgamepad: {'可用' if vg is not None else '缺失'}")
        missing_runtime = missing_runtime_dependencies()
        missing_ocr = missing_ocr_runtime_dependencies()
        if missing_ocr:
            print("OCR 依赖缺失: " + ", ".join(missing_ocr))
        else:
            print("OCR 依赖: 可用")
        if missing_runtime:
            print("运行时依赖缺失: " + ", ".join(missing_runtime))
        else:
            print("运行时依赖: 可用")
        raise SystemExit(0 if not missing_runtime else 1)
    if args.ability_reroll:
        if not _ensure_gui_admin():
            raise SystemExit(0)
        raise SystemExit(run_ability_reroll(args))
    # A portable package is commonly launched by double-clicking the EXE.
    # Treat that no-argument path as the unified panel instead of dropping
    # users into the legacy controller loop that waits for an existing stream.
    if args.gui or len(sys.argv) == 1:
        if not _ensure_gui_admin():
            raise SystemExit(0)
        raise SystemExit(run_unified_gui(args))
    if args.account_id:
        raise SystemExit(run_account_id_prompt())
    if args.reconnect_once and not _ensure_gui_admin():
        raise SystemExit(0)
    RELINK_DICT = {
        "跳跃": [0.72, 0.865, 0.79, 0.898],
        # The timer shifts between Chinese/Japanese layouts. This broad crop
        # uses text detection and is evaluated only after the cheap battle HUD
        # markers have already matched.
        "战斗右半屏": [0.50, 0.0, 1.0, 1.0],
        # The retry label is a narrow line within the lower-left blue bar.
        # Keep the crop tight: on a real client-area capture its y=0.885..0.922
        # range aligns with the text; a broad crop makes recognition-only OCR
        # return an empty string because it includes the decoration bar.
        "再次": [0.075, 0.885, 0.245, 0.922],
        "撤销": [0.075, 0.885, 0.245, 0.922],
        # The old narrow crop works on a large client but loses the Japanese
        # result title on small/compressed windows. Center-page OCR uses this
        # wider crop and its own enhanced passes; coordinate-bearing controls
        # continue to use their dedicated narrow regions.
        "结算": [0.20, 0.15, 0.80, 0.62],
        "挑战": [0.4489, 0.3231, 0.5578, 0.3787],
        # Keep the historical narrow prompt crop.  It covers both Chinese
        # ``继续`` and Japanese ``次へ`` while excluding the countdown, so
        # detector OCR returns the prompt as its own item.
        "继续": [0.87, 0.92, 0.92, 0.98],
    }

    log = Log("GBFR", "i").logger
    if DEBUG_MODE:
        debug_config = {
            "ui_language": args.ui_language,
            "background": args.background,
            "window_title": args.window_title,
            "refocus_mode": args.refocus_mode,
            "l2_key": args.l2_key,
            "input_profile": args.input_profile or "",
            "auto_recover": args.auto_recover,
            "reconnect_once": args.reconnect_once,
            "freeze_timeout_seconds": args.freeze_timeout_seconds,
        }
        DEBUG_DIAGNOSTIC_PATH = (
            Path(get_runtime_log_dir())
            / f"debug-recovery-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
        )
        try:
            DEBUG_DIAGNOSTIC_PATH.write_text(
                json.dumps(
                    {
                        "type": "config",
                        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                        "config": debug_config,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            log.info("DEBUG 诊断文件：%s", DEBUG_DIAGNOSTIC_PATH)
        except OSError as exc:
            DEBUG_DIAGNOSTIC_PATH = None
            log.warning("DEBUG 诊断文件创建失败，仍保留普通日志：%s", exc)
        log.warning(
            "DEBUG 模式已启用；仅记录恢复诊断，不改变自动按键逻辑：%s",
            json.dumps(debug_config, ensure_ascii=False),
        )
    stats_path = Path(args.stats_file) if args.stats_file else Path(get_runtime_log_dir()) / "session-stats.json"
    SCHEDULE_FILE = Path(args.schedule_file) if args.schedule_file else None
    try:
        SESSION_STATS = BattleSessionStats(
            stats_path,
            max_battles=args.max_battles,
            max_runtime_minutes=args.max_runtime_minutes,
            stop_at=args.stop_at,
        )
    except ValueError:
        raise SystemExit("--stop-at 必须使用 HH:MM 格式，例如 23:30")
    reconnect_configured = bool(
        args.reconnect_nickname.strip() and args.reconnect_host.strip()
    )
    if args.auto_recover or args.reconnect_once or reconnect_configured:
        base_dir = Path(
            sys.executable if getattr(sys, "frozen", False) else __file__
        ).resolve().parent
        if args.chiaki_exe:
            executable = Path(args.chiaki_exe).expanduser()
            if not executable.is_absolute():
                executable = base_dir / executable
        else:
            executable = next(
                (
                    candidate
                    for candidate in (
                        base_dir / "Chiaki" / "chiaki.exe",
                        base_dir / "chiaki.exe",
                    )
                    if candidate.is_file()
                ),
                base_dir / "Chiaki" / "chiaki.exe",
            )
        invalid_timeout = args.auto_recover and (
            not math.isfinite(args.freeze_timeout_seconds)
            or args.freeze_timeout_seconds < MIN_FREEZE_TIMEOUT_SECONDS
        )
        if (
            not executable.is_file()
            or not args.reconnect_nickname.strip()
            or not args.reconnect_host.strip()
            or invalid_timeout
        ):
            raise SystemExit(
                "卡死恢复配置无效：请指定有效的 Chiaki 路径、主机昵称、"
                "主机地址；启用自动卡死监控时静止时间必须至少 5 秒"
            )
        RECOVERY_CONFIG = {
            "chiaki_exe": str(executable.resolve()),
            "nickname": args.reconnect_nickname.strip(),
            "host": args.reconnect_host.strip(),
            "freeze_seconds": (
                float(args.freeze_timeout_seconds) if args.auto_recover else 0.0
            ),
        }
        if args.auto_recover:
            log.info(
                "卡死恢复已启用 | 静止判定=%.1f 分钟 | 重连目标=%s @ %s",
                args.freeze_timeout_seconds / 60.0,
                args.reconnect_nickname.strip(),
                args.reconnect_host.strip(),
            )
        else:
            log.info(
                "串流窗口丢失守护已启用 | 重连目标=%s @ %s",
                args.reconnect_nickname.strip(),
                args.reconnect_host.strip(),
            )
    else:
        RECOVERY_CONFIG = {"freeze_seconds": 0.0}
    _start_chiaki(args)

    synchronized_mapping: dict[str, str] | None = None
    if args.input_profile:
        try:
            profile_data = json.loads(Path(args.input_profile).read_text(encoding="utf-8"))
            candidate = profile_data.get("foreground_keys", {})
            if isinstance(candidate, dict) and all(
                field in candidate for field in AUTOMATION_KEY_FIELDS
            ):
                validated_mapping = {
                    field: str(candidate[field]) for field in AUTOMATION_KEY_FIELDS
                }
                invalid_keys = {
                    field: key_name
                    for field, key_name in validated_mapping.items()
                    if key_name.lower() not in Controller.KEY_MAP
                }
                duplicate_keys = len(set(validated_mapping.values())) != len(
                    validated_mapping
                )
                if invalid_keys:
                    log.warning(
                        "输入同步配置含有无法发送的键位，沿用内置兼容键位：%s",
                        invalid_keys,
                    )
                elif duplicate_keys:
                    log.warning("输入同步配置含有重复键位，沿用内置兼容键位")
                else:
                    synchronized_mapping = {
                        field: key_name.lower()
                        for field, key_name in validated_mapping.items()
                    }
            elif not args.background:
                log.warning("输入同步配置不完整，前台将沿用内置兼容键位")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            log.warning("输入同步配置读取失败，沿用内置兼容键位", exc_info=True)

    if not args.background and synchronized_mapping is not None:
        apply_foreground_keymap(synchronized_mapping)
        log.info("前台输入已应用现有 Chiaki 键位：%s", synchronized_mapping)
    elif args.background:
        # Background presses a virtual DS4 directly. Keep the canonical keys
        # used by Controller._set_virtual_key as semantic action identifiers.
        log.info("后台输入使用 ViGEm DS4 固定手柄语义，不依赖 Chiaki 键盘映射")

    # 1. 创建 Controller
    relink = Controller(
        args.window_title,
        "GBFR Chiaki 自动重战",
        RELINK_DICT,
        background=args.background,
        invert_movement=args.invert_movement,
        allow_missing_window=args.reconnect_once,
        ui_language=args.ui_language,
        recognition_profile=args.recognition_profile,
    )
    if not args.background and synchronized_mapping is not None:
        relink.set_automation_release_keys(synchronized_mapping.values())
    # Keep the selected L2 mapping available to both battle-loop variants.
    if not args.background and synchronized_mapping is None:
        L2_KEY = args.l2_key
    REFOCUS_SECONDS = max(5.0, args.refocus_seconds)
    REFOCUS_MODE = args.refocus_mode
    # The unified control panel always starts this child with --auto-start.
    # Do not register F1 here: clicking the GUI start button is sufficient and
    # users should not be able to re-trigger startup while it is already live.
    if not args.auto_start:
        relink.set_battle_start_key("f1")
    relink.set_battle_stop_key("f2")
    relink.set_close_chiaki_key("f5")
    relink.set_battle_pause_key("f3")
    relink.set_window_recapture_key("f4")
    watch_launcher_process(relink, args.launcher_pid)
    threading.Thread(target=_stats_watchdog, args=(relink,), daemon=True).start()
    if args.reconnect_once:
        # Recovery needs input enabled before the actual automation state
        # machine starts; it is explicitly activated again after phase handoff.
        relink.activate_automation("一键重连恢复流程")
        outcome = recover_frozen_chiaki(relink)
        with AUTOMATION_INPUT_LOCK:
            relink.release_automation_inputs()
        if not outcome:
            relink.running = False
            relink.stop()
            log.error("一键重连流程未完成，未启动挂机")
            raise SystemExit(2)
        INITIAL_AUTOMATION_PHASE = outcome
        relink.activate_automation("一键重连成功后的自动重战交接")
        log.info(
            "一键重连恢复完成：%s；已显式启动本工具自动重战状态机",
            outcome,
        )
    elif args.auto_start:
        relink.activate_automation("控制面板启动")
        # A control-panel start can happen from town.  Let the state machine
        # classify the live screen before entering the battle/result polling
        # loop; reconnect-once already hands off its verified phase above.
        INITIAL_AUTOMATION_PHASE = "startup_probe"
        log.info(">> 已按控制面板命令启动，无需按 F1")

    # 2. 直接启动战斗循环（控制台模式）
    if args.background:
        relink.show_toast("GBFR 自动重战", "后台窗口模式已开启；最小化后恢复即可自动重绑")
    if args.silent:
        relink.show_toast("GBFR 自动重战", "静默模式已开启")
        relink.start(relink_battle_silent)
    else:
        relink.start(relink_battle)

    if relink.shutdown_reason in {"schedule_limit", "close_chiaki_hotkey"}:
        close_chiaki_for_title(args.window_title)
