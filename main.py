# ============================================================
# GBFR Auto ReBattle — 主入口
# ============================================================

import threading
from collections.abc import Callable
from time import sleep, time
from pathlib import Path
import ctypes
import os
import subprocess
import sys
import tkinter as tk
import tkinter.scrolledtext as scrolledtext
from tkinter import messagebox, simpledialog
import webbrowser
from module.controller import Controller, WindowsCapture, vg
from module.log import Log, get_runtime_log_dir
from module.psn_account import LOGIN_URL, account_id_from_redirect, run_account_id_prompt
import argparse
import numpy as np


# Chiaki's stream window accepts keyboard mappings configured in Settings.
# These defaults match the stock Chiaki mapping except for W, which must be
# assigned to "Left Stick Up" by the user.
CHIAKI_WINDOW_TITLE = "Chiaki | Stream"
CROSS_KEY = "enter"
LEFT_STICK_UP_KEY = "w"
R1_KEY = "3"
SQUARE_KEY = "\\"
TOUCHPAD_KEY = "t"
L2_KEY = "l"
REFOCUS_SECONDS = 10.0
SHOW_SKILL_LOGS = False

# Normalized centers of the top and right trigger skills in a 16:9 Chiaki frame.
SKILL_TRIGGER_CENTERS = (
    (0.8172, 0.8083),  # upper skill
    (0.8224, 0.8231),  # right skill
)
SKILL_PATCH_HALF_SIZE = (0.0031, 0.0056)
SKILL_TRIGGER_MIN_BRIGHTNESS = 180.0
SKILL_TRIGGER_DIM_GRACE_SECONDS = 5.0
SKILL_MONITOR_IDLE_POLL_SECONDS = 2.0
SKILL_MONITOR_ACTIVE_POLL_SECONDS = 1.0
AUTOMATION_INPUT_LOCK = threading.Lock()


def skill_trigger_slots_bright(relink: Controller) -> tuple[bool, list[float]]:
    """Return whether the upper or right trigger skill is bright."""
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
    for center_x, center_y in centers:
        local_x = center_x - crop_left
        local_y = center_y - crop_top
        patch = pixels[
            local_y - half_h : local_y + half_h + 1,
            local_x - half_w : local_x + half_w + 1,
        ]
        values.append(float(patch.max(axis=2).mean()))

    trigger_bright = any(
        value >= SKILL_TRIGGER_MIN_BRIGHTNESS
        for value in values
    )
    return trigger_bright, values


def focus_watchdog(relink: Controller, battle_is_active: Callable[[], bool]) -> None:
    """Re-lock when the upper/right skills stay bright for the configured duration."""
    bright_since: float | None = None
    dim_since: float | None = None

    while relink.running:
        if not battle_is_active():
            bright_since = None
            dim_since = None
            sleep(0.5)
            continue

        try:
            trigger_bright, values = skill_trigger_slots_bright(relink)
            now = time()

            if trigger_bright:
                dim_since = None
                if bright_since is None:
                    bright_since = now
                    if SHOW_SKILL_LOGS:
                        log.debug(
                            "上方或右侧技能高亮，开始 %.0f 秒失焦计时: %s",
                            REFOCUS_SECONDS,
                            [round(v, 1) for v in values],
                        )
            elif bright_since is not None:
                if dim_since is None:
                    dim_since = now
                elif now - dim_since >= SKILL_TRIGGER_DIM_GRACE_SECONDS:
                    if SHOW_SKILL_LOGS:
                        log.debug(
                            "上方/右侧技能连续变暗 %.0f 秒，重置失焦计时: %s",
                            SKILL_TRIGGER_DIM_GRACE_SECONDS,
                            [round(v, 1) for v in values],
                        )
                    bright_since = None
                    dim_since = None

            if bright_since is not None and now - bright_since >= REFOCUS_SECONDS:
                log.warning(
                    "上方或右侧技能持续高亮 %.0f 秒，补按 L2 重新锁定",
                    REFOCUS_SECONDS,
                )
                with AUTOMATION_INPUT_LOCK:
                    relink.press(L2_KEY)
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
    return relink.recognize_line(relink.screenshot_text(region_key))


def press_verified_result_continue(relink: Controller) -> bool:
    """Press Cross only while the result-screen ``继续`` prompt is stable."""
    if "继续" not in read_region_text(relink, "继续"):
        return False

    # Confirm on a second captured frame.  This additional OCR runs only after
    # a positive match and prevents a single corrupted stream frame from
    # leaking Cross into a transition or battle.
    sleep(0.25)
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

    def enter_battle() -> None:
        nonlocal battle_active, phase
        battle_active = True
        phase = "battle_active"
        log.info("阶段切换: battle_wait/result -> battle_active")
        with AUTOMATION_INPUT_LOCK:
            relink.press(L2_KEY)

    def battle_loop() -> None:
        """后台线程：战斗中持续 W+Touchpad 推进动作"""
        while True:
            if not battle_active or not relink.running:
                sleep(0.1)
                continue
            try:
                with AUTOMATION_INPUT_LOCK:
                    relink.press(LEFT_STICK_UP_KEY, movement="press")
                    relink.press(TOUCHPAD_KEY, interval=0.5, times=3)
                    relink.press(LEFT_STICK_UP_KEY, movement="release")
            except Exception:
                pass

    battle_thread = threading.Thread(target=battle_loop, daemon=True)
    battle_thread.start()
    focus_watchdog_thread = threading.Thread(
        target=focus_watchdog,
        args=(relink, lambda: battle_active),
        daemon=True,
    )
    focus_watchdog_thread.start()

    while relink.running:
        if phase == "battle_wait":
            if "跳跃" in read_region_text(relink, "跳跃"):
                enter_battle()
                continue
            # Allow starting/restarting the tool while a result screen is open.
            if "RES" in read_region_text(relink, "RES"):
                phase = "result"
                log.info("识别到 BATTLE RESULTS，恢复到结算阶段")
                continue
            sleep(1.0)
            continue

        if phase == "battle_active":
            # V6's stable battle-end marker.  One OCR per second is sufficient
            # for a screen that remains visible until user input.
            if "RES" in read_region_text(relink, "RES"):
                battle_active = False
                phase = "result"
                log.info("阶段切换: battle_active -> result")
                log.info("--- 第 %d 场战斗结算 ---", battle_number)
                battle_number += 1
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
        if phase == "battle_wait":
            if "跳跃" in read_region_text(relink, "跳跃"):
                battle_active = True
                phase = "battle_active"
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
                continue
            sleep(1.0)
            continue

        if "跳跃" in read_region_text(relink, "跳跃"):
            battle_active = True
            phase = "battle_active"
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
        help="上方或右侧技能持续高亮多少秒后补按 L2，默认 10",
    )
    parser.add_argument(
        "--show-skill-logs",
        action="store_true",
        help="显示技能亮度监控的调试明细（默认隐藏）",
    )
    parser.add_argument(
        "--invert-movement",
        action="store_true",
        help="后台虚拟手柄反向移动轴；仅客户机方向相反时启用",
    )
    return parser.parse_args()


def _find_window(title: str) -> bool:
    """Return whether a visible window containing title already exists."""
    import win32gui

    found = []

    def callback(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd):
            caption = win32gui.GetWindowText(hwnd)
            if title.lower() in caption.lower():
                found.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return bool(found)


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
    root.geometry("720x610")
    root.minsize(620, 500)
    root.columnconfigure(1, weight=1)

    chiaki_process = {"value": None}
    automation_process = {"value": None}
    automation_output = {"value": None}
    # Keep a byte offset rather than a TextIO cookie. The automation child may
    # write a partial multi-byte character between two UI polling intervals.
    log_cursor = {"value": 0}
    log_pending = {"value": b""}
    status = tk.StringVar(value="就绪：默认沿用原脚本的前台输入模式")
    # Preserve the original script's input behavior by default.  Background
    # capture/direct window messages remain available as an explicit option.
    background = tk.BooleanVar(value=False)
    show_skill_logs = tk.BooleanVar(value=False)
    invert_movement = tk.BooleanVar(value=False)
    title_var = tk.StringVar(value=args.window_title)
    path_var = tk.StringVar(value=args.chiaki_exe or "Chiaki\\chiaki.exe")

    def set_status(text: str) -> None:
        status.set(text)

    def app_root() -> Path:
        return Path(
            sys.executable if getattr(sys, "frozen", False) else __file__
        ).resolve().parent

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
                "Return=Cross，\\=Square，L=L2，3=R1，T=Touchpad"
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
        set_status("后台环境缺少组件，请先点击“安装虚拟手柄驱动”或重新安装完整包")
        if show_dialog:
            messagebox.showwarning(
                "后台环境未完成",
                "缺少以下组件：\n"
                f"{details}\n\n"
                "安装驱动后请回到这里点击“检查后台环境”。\n\n"
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
                set_status("已启动虚拟手柄驱动安装器；完成后请点击“检查后台环境”")
            return

        webbrowser.open("https://github.com/nefarius/ViGEmBus/releases/latest")
        messagebox.showinfo(
            "需要安装虚拟手柄驱动",
            "完整包未附带驱动安装器，已打开 ViGEmBus 官方发布页。\n"
            "下载并完成安装后，回到工具点击“检查后台环境”。",
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

    def decode_console_bytes(data: bytes) -> tuple[str, bytes]:
        """Decode the live child log without corrupting split CJK characters.

        New launches always use UTF-8. GB18030 is retained as a fallback so a
        log written by an older Windows build can still be viewed correctly.
        """
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        if not data:
            return "", b""

        for encoding in ("utf-8", "gb18030"):
            try:
                return data.decode(encoding), b""
            except UnicodeDecodeError as exc:
                # An incomplete character at the end is expected while the
                # subprocess is still writing; carry it into the next poll.
                if exc.end == len(data):
                    try:
                        return data[:exc.start].decode(encoding), data[exc.start:]
                    except UnicodeDecodeError:
                        pass

        # Do not let a malformed external message stop the GUI log viewer.
        return data.decode("utf-8", errors="replace"), b""

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
            set_status("Chiaki 已经在运行")
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
        if background.get() and not check_background_environment(show_dialog=True):
            return
        if chiaki_process["value"] is None or chiaki_process["value"].poll() is not None:
            if not start_chiaki():
                return
        # Always pass the option name. The previous foreground branch built
        # [exe, "Chiaki | Stream"], so argparse treated the title as an
        # unexpected positional argument and exited with code 2.
        command = _self_command()
        if background.get():
            command.append("--background")
        command.extend(("--window-title", title_var.get()))
        if show_skill_logs.get():
            command.append("--show-skill-logs")
        if invert_movement.get():
            command.append("--invert-movement")
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
            automation_process["value"] = subprocess.Popen(
                command,
                stdout=automation_output["value"],
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=child_env,
            )
            if background.get():
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
        set_status("自动重战已停止")

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
            if automation_output["value"] is not None:
                automation_output["value"].close()
                automation_output["value"] = None
            set_status(
                f"自动重战异常退出（退出码 {exit_code}）；详细信息见下方运行日志"
            )
        root.after(500, poll_processes)

    tk.Label(root, text="Chiaki 程序").grid(row=0, column=0, padx=12, pady=(14, 6), sticky="w")
    tk.Entry(root, textvariable=path_var).grid(row=0, column=1, padx=8, pady=(14, 6), sticky="ew")
    tk.Button(root, text="启动 Chiaki", command=start_chiaki, width=12).grid(row=0, column=2, padx=12, pady=(14, 6))
    tk.Label(root, text="串流窗口标题").grid(row=1, column=0, padx=12, pady=6, sticky="w")
    tk.Entry(root, textvariable=title_var).grid(row=1, column=1, columnspan=2, padx=8, pady=6, sticky="ew")
    tk.Checkbutton(root, text="后台运行（允许其他窗口覆盖 Chiaki，不要最小化）", variable=background).grid(row=2, column=0, columnspan=3, padx=12, pady=6, sticky="w")
    tk.Button(root, text="检查后台环境", command=check_background_environment, width=16).grid(row=3, column=0, padx=12, pady=(2, 6))
    tk.Button(root, text="安装虚拟手柄驱动", command=install_virtual_gamepad_driver, width=18).grid(row=3, column=1, padx=8, pady=(2, 6), sticky="w")
    tk.Checkbutton(root, text="显示技能监控明细", variable=show_skill_logs).grid(row=3, column=2, padx=8, pady=(2, 6), sticky="w")
    tk.Button(root, text="启动自动重战", command=start_automation, width=16).grid(row=4, column=0, padx=12, pady=8)
    tk.Button(root, text="停止自动重战", command=stop_automation, width=16).grid(row=4, column=1, padx=8, pady=8, sticky="w")
    tk.Button(root, text="获取 PSN AccountID", command=account_id, width=20).grid(row=4, column=2, padx=12, pady=8)
    tk.Button(root, text="打开日志目录", command=open_logs, width=16).grid(row=5, column=0, padx=12, pady=(0, 6), sticky="w")
    tk.Checkbutton(root, text="反向移动方向（仅后台客户机方向相反时）", variable=invert_movement).grid(row=5, column=1, columnspan=2, padx=8, pady=(0, 6), sticky="w")
    tk.Label(root, textvariable=status, anchor="w", fg="#444").grid(row=6, column=0, columnspan=3, padx=12, pady=(2, 6), sticky="ew")
    tk.Label(root, text="运行日志", anchor="w").grid(row=7, column=0, padx=12, pady=(4, 2), sticky="w")
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
    console.grid(row=8, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="nsew")
    root.rowconfigure(8, weight=1)
    tk.Label(root, text="提示：首次使用先在 Chiaki 中完成主机注册；后台模式首次使用请先完成环境检查。", anchor="w", justify="left").grid(row=9, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="ew")

    def close() -> None:
        process = automation_process["value"]
        if process is not None and process.poll() is None:
            process.terminate()
        if automation_output["value"] is not None:
            automation_output["value"].close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(500, poll_processes)
    root.after(250, poll_console_log)
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
    SHOW_SKILL_LOGS = args.show_skill_logs
    relink.set_battle_start_key("f1")
    relink.set_battle_stop_key("f2")

    # 2. 直接启动战斗循环（控制台模式）
    if args.background:
        relink.show_toast("GBFR 自动重战", "后台窗口模式已开启（请勿最小化 Chiaki）")
    if args.silent:
        relink.show_toast("GBFR 自动重战", "静默模式已开启")
        relink.start(relink_battle_silent)
    else:
        relink.start(relink_battle)
