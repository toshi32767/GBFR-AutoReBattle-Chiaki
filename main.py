# ============================================================
# GBFR Auto ReBattle — 主入口
# ============================================================

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from time import sleep, time
from pathlib import Path
import ctypes
import json
import os
import subprocess
import sys
import tkinter as tk
import tkinter.scrolledtext as scrolledtext
from tkinter import messagebox, simpledialog, ttk
import webbrowser
from module.controller import Controller, WindowsCapture, vg
from module.log import Log, get_runtime_log_dir
from module.psn_account import LOGIN_URL, account_id_from_redirect, run_account_id_prompt
import argparse
import math
import numpy as np


# Chiaki's stream window accepts keyboard mappings configured in Settings.
# These defaults match the stock Chiaki mapping except for W, which must be
# assigned to "Left Stick Up" by the user.
CHIAKI_WINDOW_TITLE = "Chiaki | Stream"
CROSS_KEY = "enter"
LEFT_STICK_UP_KEY = "w"
LEFT_STICK_DOWN_KEY = "s"
LEFT_STICK_LEFT_KEY = "a"
LEFT_STICK_RIGHT_KEY = "d"
RIGHT_STICK_LEFT_KEY = "q"
RIGHT_STICK_RIGHT_KEY = "e"
R1_KEY = "3"
SQUARE_KEY = "\\"
L2_KEY = "l"
REFOCUS_SECONDS = 15.0
REFOCUS_SEARCH_SECONDS = 1.0
REFOCUS_STABILIZE_SECONDS = 1.5
REFOCUS_CONFIRM_SAMPLES = 2
REFOCUS_CONFIRM_INTERVAL_SECONDS = 0.5

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
AUTOMATION_INPUT_LOCK = threading.Lock()
_CAPTURE_UNAVAILABLE_WARNED = False
SESSION_STATS = None
SCHEDULE_FILE: Path | None = None


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
            if SCHEDULE_FILE is not None:
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
                relink.request_shutdown()
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


def focus_watchdog(relink: Controller, battle_is_active: Callable[[], bool]) -> None:
    """Recover target lock when trigger skills stay bright for long enough."""
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


def read_region_text(relink: Controller, region_key: str) -> str:
    """Recognize a fixed-position single-line marker using the lightweight path."""
    global _CAPTURE_UNAVAILABLE_WARNED
    try:
        text = relink.recognize_line(relink.screenshot_text(region_key))
    except (OSError, RuntimeError):
        if not _CAPTURE_UNAVAILABLE_WARNED:
            log.warning("Chiaki 画面暂时不可用，保留当前阶段并等待窗口恢复或重建")
            _CAPTURE_UNAVAILABLE_WARNED = True
        return ""

    if _CAPTURE_UNAVAILABLE_WARNED:
        log.info("Chiaki 画面已恢复，继续当前自动化阶段")
        _CAPTURE_UNAVAILABLE_WARNED = False
    return text


def press_verified_result_continue(relink: Controller) -> bool:
    """Press Cross only while the result-screen ``继续`` prompt is stable."""
    if relink.paused:
        return False
    if "继续" not in read_region_text(relink, "继续"):
        return False

    # Confirm on a second captured frame.  This additional OCR runs only after
    # a positive match and prevents a single corrupted stream frame from
    # leaking Cross into a transition or battle.
    # Background capture runs at 2 FPS. Waiting slightly over 0.5 seconds
    # guarantees that this verifies a newly captured frame rather than reading
    # the same cached frame twice.
    sleep(0.55)
    if relink.paused:
        return False
    if "继续" not in read_region_text(relink, "继续"):
        log.debug("结算‘继续’仅单帧出现，不发送 Cross")
        return False

    with AUTOMATION_INPUT_LOCK:
        if not relink.running:
            return False
        relink.press(CROSS_KEY)
    log.info("识别到右下角‘继续’，发送一次 Cross")
    sleep(0.75)
    return True


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

    yes_score = blue_score(0.625)
    no_score = blue_score(0.677)
    delta = yes_score - no_score
    if delta >= 6.0:
        return "yes"
    if delta <= -6.0:
        return "no"
    return None


# ============================================================
#  Relink 战斗逻辑
# ============================================================
def relink_battle(relink: Controller) -> None:
    """单次战斗 → 结算 → 再次挑战 的完整循环"""
    battle_active = False
    phase = "battle_wait"
    battle_number = 1
    # Resume is a synchronization boundary: the OCR/state loop must get one
    # chance to classify a result screen before the movement worker can send
    # another forward pulse.  A dict keeps this shared value writable from the
    # worker thread without introducing another lock around every poll.
    resume_guard = {"until": 0.0}
    main_pause_generation = relink.pause_generation

    def enter_battle() -> None:
        nonlocal battle_active, phase
        with AUTOMATION_INPUT_LOCK:
            # A previous result/stop transition must never leak a held axis
            # into the next battle entry.
            relink.release_automation_inputs()
        battle_active = True
        phase = "battle_active"
        _stats_start_battle()
        log.info("阶段切换: battle_wait/result -> battle_active")
        with AUTOMATION_INPUT_LOCK:
            relink.press(L2_KEY)

    def transition_to_result() -> None:
        """Move to result handling exactly once and finish the battle stats."""
        nonlocal battle_active, phase, battle_number
        if phase == "battle_active":
            battle_active = False
            phase = "result"
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
        elif phase != "result":
            phase = "result"
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

    while relink.running:
        if relink.paused:
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
                if "RES" in read_region_text(relink, "RES"):
                    transition_to_result()
                    continue
                if "继续" in read_region_text(relink, "继续"):
                    transition_to_result()
                    continue
            # For the normal result phase, fall through immediately so the
            # verified two-frame ``继续`` check can run without waiting a tick.
            if phase == "result":
                if press_verified_result_continue(relink):
                    continue
        if phase == "battle_wait":
            if "跳跃" in read_region_text(relink, "跳跃"):
                enter_battle()
                continue
            # Allow starting/restarting the tool while a result screen is open.
            if "RES" in read_region_text(relink, "RES"):
                phase = "result"
                with AUTOMATION_INPUT_LOCK:
                    relink.release_automation_inputs()
                log.info("识别到 BATTLE RESULTS，恢复到结算阶段")
                continue
            sleep(1.0)
            continue

        if phase == "battle_active":
            # V6's stable battle-end marker.  One OCR per second is sufficient
            # for a screen that remains visible until user input.
            if "RES" in read_region_text(relink, "RES"):
                transition_to_result()
                continue
            sleep(1.0)
            continue

        # Auto-repeat can transition directly into the next battle.  Once the
        # jump marker appears, result input is disabled before any other action.
        if "跳跃" in read_region_text(relink, "跳跃"):
            enter_battle()
            continue

        center_text = read_region_text(relink, "结算")
        if "挑战" in center_text:
            with AUTOMATION_INPUT_LOCK:
                relink.press(LEFT_STICK_UP_KEY)
                relink.press(CROSS_KEY)
            log.info("识别到挑战确认界面，选择并确认")
            sleep(1.0)
            continue
        if "结算" in center_text:
            if "确认" in center_text:
                selection = settlement_confirmation_selection(relink)
                if selection == "no":
                    with AUTOMATION_INPUT_LOCK:
                        relink.press("up")
                    log.info("结算确认当前选中‘否’，发送一次上以选择‘是’")
                elif selection == "yes":
                    with AUTOMATION_INPUT_LOCK:
                        relink.press(CROSS_KEY)
                    log.info("结算确认已选中‘是’，发送一次 Cross")
                else:
                    log.debug("结算确认弹窗高亮不明确，暂不发送确认键")
            else:
                with AUTOMATION_INPUT_LOCK:
                    relink.press(CROSS_KEY)
                log.info("识别到结算确认界面，发送一次 Cross")
            sleep(1.0)
            continue

        # Enable repeat before advancing the result page.  The video shows
        # both controls at once: left-bottom '再次挑战' and right-bottom
        # '继续'.  Toggling repeat first prevents the next page from returning
        # to town before the automatic rematch is armed.
        left_text = read_region_text(relink, "再次")
        if "再次" in left_text and "撤销" not in left_text:
            with AUTOMATION_INPUT_LOCK:
                relink.press(SQUARE_KEY, interval=0.3)
            log.info("识别到‘再次挑战’，发送一次 Square 方块键开启自动再次挑战")
            sleep(1.0)
            continue

        # Result phase: Cross is permitted only after two consecutive OCR
        # matches of the bottom-right '继续' prompt.
        if press_verified_result_continue(relink):
            continue

        sleep(1.0)

    battle_active = False
    with AUTOMATION_INPUT_LOCK:
        relink.release_automation_inputs()


def relink_battle_silent(relink: Controller):
    battle_active = False
    phase = "battle_wait"
    focus_watchdog_thread = threading.Thread(
        target=focus_watchdog,
        args=(relink, lambda: battle_active),
        daemon=True,
    )
    focus_watchdog_thread.start()

    while relink.running:
        if relink.paused:
            sleep(0.2)
            continue
        if phase == "battle_wait":
            if "跳跃" in read_region_text(relink, "跳跃"):
                battle_active = True
                phase = "battle_active"
                _stats_start_battle()
                with AUTOMATION_INPUT_LOCK:
                    relink.press(L2_KEY)
                continue
            if "RES" in read_region_text(relink, "RES"):
                phase = "result"
                continue
            sleep(1.0)
            continue

        if phase == "battle_active":
            if "RES" in read_region_text(relink, "RES"):
                battle_active = False
                phase = "result"
                _stats_finish_battle()
                continue
            sleep(1.0)
            continue

        if "跳跃" in read_region_text(relink, "跳跃"):
            battle_active = True
            phase = "battle_active"
            _stats_start_battle()
            with AUTOMATION_INPUT_LOCK:
                relink.press(L2_KEY)
            continue

        center_text = read_region_text(relink, "结算")
        if "挑战" in center_text:
            with AUTOMATION_INPUT_LOCK:
                relink.press(LEFT_STICK_UP_KEY)
                relink.press(CROSS_KEY)
            sleep(1.0)
            continue
        if "结算" in center_text:
            if "确认" in center_text:
                selection = settlement_confirmation_selection(relink)
                if selection == "no":
                    with AUTOMATION_INPUT_LOCK:
                        relink.press("up")
                elif selection == "yes":
                    with AUTOMATION_INPUT_LOCK:
                        relink.press(CROSS_KEY)
            else:
                with AUTOMATION_INPUT_LOCK:
                    relink.press(CROSS_KEY)
            sleep(1.0)
            continue

        left_text = read_region_text(relink, "再次")
        if "再次" in left_text and "撤销" not in left_text:
            with AUTOMATION_INPUT_LOCK:
                relink.press(SQUARE_KEY, interval=0.3)
            sleep(1.0)
            continue

        if press_verified_result_continue(relink):
            continue
        sleep(1.0)

    battle_active = False


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
    parser.add_argument("--window-title", default=CHIAKI_WINDOW_TITLE, help="Chiaki 串流窗口标题")
    parser.add_argument("--l2-key", default=L2_KEY, help="Chiaki 中 L2 对应的键盘按键，默认 L")
    parser.add_argument(
        "--refocus-seconds",
        type=float,
        default=REFOCUS_SECONDS,
        help="上方或右侧技能持续高亮多少秒后恢复索敌，默认 15",
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
    return parser.parse_args()


def _find_window_handle(title: str) -> int | None:
    """Return a visible window handle containing ``title``."""
    import win32gui

    found = []

    def callback(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd):
            caption = win32gui.GetWindowText(hwnd)
            if title.lower() in caption.lower():
                found.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return found[0] if found else None


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


def _ensure_gui_admin() -> bool:
    """Relaunch the control panel once with elevation so children inherit it."""
    if ctypes.windll.shell32.IsUserAnAdmin():
        return True
    if getattr(sys, "frozen", False):
        executable = sys.executable
        parameters = subprocess.list2cmdline(sys.argv[1:])
    else:
        executable = sys.executable
        parameters = subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        parameters,
        str(Path(executable).resolve().parent),
        1,
    )
    if result <= 32:
        ctypes.windll.user32.MessageBoxW(
            None,
            "无法获取管理员权限，自动重战无法启动。",
            "Chiaki + GBFR 自动重战",
            0x10,
        )
    return False


def run_unified_gui(args) -> int:
    """Run a small controller panel while keeping Chiaki's native UI intact."""
    root = tk.Tk()
    root.title("Chiaki + GBFR 自动重战")
    root.geometry("820x820")
    root.minsize(700, 650)
    root.columnconfigure(1, weight=1)

    chiaki_process = {"value": None}
    automation_process = {"value": None}
    automation_output = {"value": None}
    active_background_mode = {"value": None}
    # Keep a byte offset rather than a TextIO cookie. The automation child may
    # write a partial multi-byte character between two UI polling intervals.
    log_cursor = {"value": 0}
    log_pending = {"value": b""}
    settings_path = Path(get_runtime_log_dir()).parent / "settings.json"
    stats_path = Path(get_runtime_log_dir()) / "session-stats.json"
    schedule_path = Path(get_runtime_log_dir()) / "schedule.json"

    def load_gui_settings() -> dict[str, object]:
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    saved_settings = load_gui_settings()
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
    invert_movement = tk.BooleanVar(value=False)
    title_var = tk.StringVar(value=args.window_title)
    path_var = tk.StringVar(value=args.chiaki_exe or "Chiaki\\chiaki.exe")
    max_battles_var = tk.StringVar(value=str(saved_settings.get("max_battles", "")))
    max_runtime_var = tk.StringVar(value=str(saved_settings.get("max_runtime_minutes", "")))
    stop_at_var = tk.StringVar(value=str(saved_settings.get("stop_at", "")))
    stats_summary = tk.StringVar(value="等待自动重战启动")
    stats_detail = tk.StringVar(value="当前场耗时：--:--")

    def set_status(text: str) -> None:
        status.set(text)

    def save_background_choice() -> None:
        """Persist only the selected mode; environment checks never alter it."""
        settings = load_gui_settings()
        settings["background_mode"] = bool(background.get())
        settings["max_battles"] = max_battles_var.get().strip()
        settings["max_runtime_minutes"] = max_runtime_var.get().strip()
        settings["stop_at"] = stop_at_var.get().strip()
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

    def show_chiaki_mapping_hint() -> None:
        messagebox.showinfo(
            "Chiaki 按键配置提示",
            "请在 Chiaki 的 Settings / Keyboard Mapping 中修改以下按键：\n\n"
            "W → Left Stick Up        S → Left Stick Down\n"
            "A → Left Stick Left      D → Left Stick Right\n"
            "Q → Right Stick Left     E → Right Stick Right\n"
            "Return → Cross           \\ → Square\n"
            "L → L2                   3 → R1\n\n"
            "以上按键需要按此设置；其他按键保持默认即可。",
            parent=root,
        )

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
                    "Chiaki 可以被其他窗口覆盖，但不要最小化。\n\n"
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
        console.configure(state="normal")
        console.insert("end", clean)
        # Keep the embedded viewer responsive during long unattended runs.
        if int(console.index("end-1c").split(".")[0]) > 1200:
            console.delete("1.0", "201.0")
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
        root.after(500, poll_console_log)

    def start_chiaki() -> None:
        if chiaki_process["value"] is not None and chiaki_process["value"].poll() is None:
            set_status("Chiaki 已经在运行")
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
            show_chiaki_mapping_hint()
            chiaki_process["value"] = subprocess.Popen(
                [str(chiaki_path)], cwd=str(chiaki_path.parent)
            )
            set_status("Chiaki 已启动，请在 Chiaki 窗口中连接 PS5")
            return True
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))
            return False

    def start_automation() -> None:
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            set_status("自动重战已经在运行")
            return
        run_in_background = bool(background.get())
        schedule = read_schedule()
        if schedule is None:
            return
        max_battles, max_runtime, stop_at = schedule
        try:
            write_schedule_file(schedule)
            save_background_choice()
        except OSError as exc:
            messagebox.showerror("自动结束设置未保存", str(exc), parent=root)
            return
        if run_in_background and not check_background_environment(show_dialog=True):
            return
        if chiaki_process["value"] is None or chiaki_process["value"].poll() is not None:
            if not start_chiaki():
                return
        # Always pass the option name. The previous foreground branch built
        # [exe, "Chiaki | Stream"], so argparse treated the title as an
        # unexpected positional argument and exited with code 2.
        command = _self_command()
        if run_in_background:
            command.append("--background")
        command.extend(("--window-title", title_var.get()))
        command.extend(("--stats-file", str(stats_path), "--auto-start"))
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
            automation_output["value"] = console_log.open("w", encoding="utf-8-sig")
            child_env = os.environ.copy()
            child_env["PYTHONUTF8"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"
            log_mode = "后台 ViGEm DS4" if run_in_background else "前台键盘"
            automation_output["value"].write(
                f"[启动器] 本次运行模式：{log_mode}\n"
            )
            automation_output["value"].flush()
            automation_process["value"] = subprocess.Popen(
                command,
                stdout=automation_output["value"],
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=child_env,
            )
            active_background_mode["value"] = run_in_background
            background_check.configure(state="disabled")
            background_label.set(
                "后台运行（当前已启用并锁定；先停止自动重战，再取消勾选即可前台运行）"
                if run_in_background
                else "后台运行（当前未启用；运行中锁定，停止后可勾选）"
            )
            if run_in_background:
                set_status("自动重战已启动：后台模式中，Chiaki 可以被覆盖但不要最小化")
            else:
                set_status("自动重战已启动：前台模式中，请保持 Chiaki 为当前活动窗口")
        except OSError as exc:
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            messagebox.showerror("启动失败", str(exc))

    def stop_automation() -> None:
        process = automation_process["value"]
        if process is None or process.poll() is not None:
            set_status("自动重战当前没有运行")
            return
        process.terminate()
        automation_process["value"] = None
        active_background_mode["value"] = None
        background_check.configure(state="normal")
        background_label.set(
            "后台运行（勾选后启用；运行中锁定，停止后可取消）"
        )
        set_status("自动重战已停止；现在可以取消“后台运行”勾选，改为前台运行")

    def send_automation_hotkey(vk: int) -> None:
        """Send a global F3/F2 command to the elevated automation child."""
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

    def toggle_pause() -> None:
        send_automation_hotkey(0x72)  # F3

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
            automation_process["value"] = None
            was_background = active_background_mode["value"] is True
            active_background_mode["value"] = None
            background_check.configure(state="normal")
            background_label.set(
                "后台运行（勾选后启用；运行中锁定，停止后可取消）"
            )
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            stop_reason = ""
            try:
                stats_data = json.loads(stats_path.read_text(encoding="utf-8"))
                stop_reason = str(stats_data.get("stop_reason", ""))
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
            if stop_reason:
                set_status(f"自动重战已按计划结束：{stop_reason}")
            elif exit_code == 0:
                set_status("自动重战已结束；详细信息见下方运行日志")
            else:
                set_status(
                    f"自动重战异常退出（退出码 {exit_code}）；详细信息见下方运行日志"
                )
            if was_background:
                if stop_reason:
                    set_status(
                        f"自动重战已按计划结束：{stop_reason}；如需前台运行，请取消“后台运行”后重新启动"
                    )
                else:
                    set_status("自动重战已结束；如需前台运行，请取消“后台运行”后重新启动")
        root.after(500, poll_processes)

    tk.Label(root, text="Chiaki 程序").grid(row=0, column=0, padx=12, pady=(14, 6), sticky="w")
    tk.Entry(root, textvariable=path_var).grid(row=0, column=1, padx=8, pady=(14, 6), sticky="ew")
    tk.Button(root, text="启动 Chiaki", command=start_chiaki, width=12).grid(row=0, column=2, padx=12, pady=(14, 6))
    tk.Label(root, text="串流窗口标题").grid(row=1, column=0, padx=12, pady=6, sticky="w")
    tk.Entry(root, textvariable=title_var).grid(row=1, column=1, padx=8, pady=6, sticky="ew")
    tk.Button(root, text="按键配置提示", command=show_chiaki_mapping_hint, width=14).grid(row=1, column=2, padx=12, pady=6)
    background_check = tk.Checkbutton(
        root,
        textvariable=background_label,
        variable=background,
        command=save_background_choice,
    )
    background_check.grid(row=2, column=0, columnspan=3, padx=12, pady=6, sticky="w")
    tk.Button(root, text="检查后台环境", command=check_background_environment, width=16).grid(row=3, column=0, padx=12, pady=(2, 6))
    tk.Button(root, text="安装 ViGEmBus", command=install_virtual_gamepad_driver, width=16).grid(row=3, column=1, padx=8, pady=(2, 6), sticky="w")
    tk.Button(root, text="安装 HidHide", command=install_hidhide, width=16).grid(row=3, column=2, padx=12, pady=(2, 6), sticky="w")
    tk.Button(root, text="启动自动重战", command=start_automation, width=16).grid(row=4, column=0, padx=12, pady=8)
    tk.Button(root, text="暂停/继续（F3）", command=toggle_pause, width=16).grid(row=4, column=1, padx=8, pady=8, sticky="w")
    tk.Button(root, text="停止自动重战", command=stop_automation, width=16).grid(row=4, column=2, padx=12, pady=8)
    tk.Button(root, text="获取 PSN AccountID", command=account_id, width=20).grid(row=5, column=0, padx=12, pady=(0, 6), sticky="w")
    tk.Button(root, text="打开日志目录", command=open_logs, width=16).grid(row=5, column=1, padx=8, pady=(0, 6), sticky="w")
    tk.Checkbutton(root, text="反向移动方向（仅后台客户机方向相反时）", variable=invert_movement).grid(row=5, column=2, padx=8, pady=(0, 6), sticky="w")

    schedule_frame = tk.LabelFrame(root, text="自动结束设置（点击“应用设置”后生效，运行中修改也会生效）")
    schedule_frame.grid(row=6, column=0, columnspan=3, padx=12, pady=(2, 6), sticky="ew")
    schedule_frame.columnconfigure(1, weight=1)
    schedule_frame.columnconfigure(3, weight=1)
    schedule_frame.columnconfigure(5, weight=1)
    tk.Label(schedule_frame, text="完成场数后关闭").grid(row=0, column=0, padx=(8, 4), pady=6)
    tk.Entry(schedule_frame, textvariable=max_battles_var, width=8).grid(row=0, column=1, padx=(0, 12), pady=6, sticky="w")
    tk.Label(schedule_frame, text="运行时长（分钟）").grid(row=0, column=2, padx=(8, 4), pady=6)
    tk.Entry(schedule_frame, textvariable=max_runtime_var, width=8).grid(row=0, column=3, padx=(0, 12), pady=6, sticky="w")
    tk.Label(schedule_frame, text="自动关闭时间").grid(row=0, column=4, padx=(8, 4), pady=6)
    tk.Entry(schedule_frame, textvariable=stop_at_var, width=8).grid(row=0, column=5, padx=(0, 8), pady=6, sticky="w")
    tk.Button(schedule_frame, text="应用设置", command=apply_schedule, width=12).grid(row=0, column=6, padx=(4, 8), pady=6)
    tk.Label(
        schedule_frame,
        text="时间格式 HH:MM，按本机时钟；已过该时间则按次日。留空或 0 表示不启用，任一条件满足即关闭 Chiaki。",
        fg="#666",
        anchor="w",
        justify="left",
        wraplength=760,
    ).grid(row=1, column=0, columnspan=7, padx=8, pady=(0, 6), sticky="ew")

    stats_frame = tk.LabelFrame(root, text="本轮挂机统计")
    stats_frame.grid(row=7, column=0, columnspan=3, padx=12, pady=(0, 6), sticky="nsew")
    stats_frame.columnconfigure(0, weight=1)
    stats_frame.rowconfigure(2, weight=1)
    tk.Label(stats_frame, textvariable=stats_summary, anchor="w").grid(row=0, column=0, padx=8, pady=(5, 0), sticky="ew")
    tk.Label(stats_frame, textvariable=stats_detail, anchor="w", fg="#555").grid(row=1, column=0, padx=8, pady=(0, 4), sticky="ew")
    battle_table = ttk.Treeview(stats_frame, columns=("number", "duration", "ended"), show="headings", height=5)
    battle_table.heading("number", text="场次")
    battle_table.heading("duration", text="本场耗时")
    battle_table.heading("ended", text="结算时间")
    battle_table.column("number", width=60, anchor="center", stretch=False)
    battle_table.column("duration", width=100, anchor="center", stretch=False)
    battle_table.column("ended", width=190, anchor="center", stretch=False)
    battle_table.grid(row=2, column=0, padx=8, pady=(0, 6), sticky="nsew")

    tk.Label(root, textvariable=status, anchor="w", fg="#444").grid(row=8, column=0, columnspan=3, padx=12, pady=(2, 6), sticky="ew")
    tk.Label(root, text="运行日志", anchor="w").grid(row=9, column=0, padx=12, pady=(4, 2), sticky="w")
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
    console.grid(row=10, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="nsew")
    root.rowconfigure(7, weight=0)
    root.rowconfigure(10, weight=1)
    tk.Label(root, text="提示：F1 启动，F2 停止，F3 暂停/继续；后台模式请勿最小化 Chiaki。", anchor="w", justify="left").grid(row=11, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="ew")

    def close() -> None:
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            process.terminate()
        if automation_output["value"] is not None:
            automation_output["value"].close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(500, poll_processes)
    root.after(500, poll_console_log)
    root.after(1000, poll_stats)
    root.mainloop()
    return 0


# ============================================================
#  入口
# ============================================================
if __name__ == "__main__":
    args = parse_args()
    if args.diagnostics:
        print(f"windows-capture: {'可用' if WindowsCapture is not None else '缺失'}")
        print(f"vgamepad: {'可用' if vg is not None else '缺失'}")
        raise SystemExit(0 if WindowsCapture is not None else 1)
    if args.gui:
        if not _ensure_gui_admin():
            raise SystemExit(0)
        raise SystemExit(run_unified_gui(args))
    if args.account_id:
        raise SystemExit(run_account_id_prompt())
    RELINK_DICT = {
        "跳跃": [0.733, 0.8681, 0.7595, 0.8938],
        "再次": [0.1121, 0.8916, 0.1742, 0.9145],
        "撤销": [0.1121, 0.8916, 0.1742, 0.9145],
        "结算": [0.4489, 0.3231, 0.5578, 0.3787],
        "挑战": [0.4489, 0.3231, 0.5578, 0.3787],
        # The result continuation prompt is a small, stable bottom-right
        # label.  Keeping this crop narrow avoids OCR of the full rewards UI.
        "继续": [0.87, 0.92, 0.92, 0.98],
        "RES": [0.0543, 0.0377, 0.435, 0.1055],
        
    }

    log = Log("GBFR", "i").logger
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
    _start_chiaki(args)

    # 1. 创建 Controller
    relink = Controller(
        args.window_title,
        "GBFR Chiaki 自动重战",
        RELINK_DICT,
        background=args.background,
        invert_movement=args.invert_movement,
    )
    # Keep the selected L2 mapping available to both battle-loop variants.
    L2_KEY = args.l2_key
    REFOCUS_SECONDS = max(5.0, args.refocus_seconds)
    relink.set_battle_start_key("f1")
    relink.set_battle_stop_key("f2")
    relink.set_battle_pause_key("f3")
    threading.Thread(target=_stats_watchdog, args=(relink,), daemon=True).start()
    if args.auto_start:
        relink.running = True
        log.info(">> 已按命令行选项自动启动，无需按 F1")

    # 2. 直接启动战斗循环（控制台模式）
    if args.background:
        relink.show_toast("GBFR 自动重战", "后台窗口模式已开启（请勿最小化 Chiaki）")
    if args.silent:
        relink.show_toast("GBFR 自动重战", "静默模式已开启")
        relink.start(relink_battle_silent)
    else:
        relink.start(relink_battle)

    if relink.shutdown_requested:
        close_chiaki_for_title(args.window_title)
