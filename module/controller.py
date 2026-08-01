import ctypes
import os
import threading
from ctypes import wintypes
from time import monotonic, sleep, time
import tkinter as tk
import sys
import win32api
import win32con
import win32gui
import win32ui
from PIL import Image, ImageGrab
from module.log import Log, setup_project_log
import numpy as np

try:
    import vgamepad as vg
except ImportError:
    vg = None

try:
    from windows_capture import WindowsCapture
except ImportError:
    WindowsCapture = None

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_union(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_union)]


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass

setup_project_log()
_log = Log("controller", "i").logger  # 新增：全局复用


class Controller:
    def __init__(
        self,
        target,
        project_name="Project",
        region_dict=None,
        background=False,
        invert_movement=False,
    ) -> None:
        self.run_as_admin()

        self.target_window = target
        self._target_hwnd: int | None = None
        self.window_rect = None
        self.text2region = region_dict
        self.project_name = project_name
        self.background_mode = bool(background)
        # Some ViGEm/driver/game combinations report the DS4 Y axis with the
        # opposite sign. Keep this explicit so a client machine can correct
        # direction without changing its Chiaki keyboard mapping.
        self.invert_movement = bool(invert_movement)

        self._running: bool = False
        self._paused: bool = False
        # Monotonic toggle counter.  Consumers can detect a pause/resume edge
        # even when both hotkey events happen between two polling iterations.
        self._pause_generation: int = 0
        self._shutdown_requested: bool = False
        self._hotkeys: dict[int, callable] = {}
        self._hwnd_warned: bool = False
        self._capture_lock = threading.Lock()
        self._latest_capture = None
        self._capture_ready = threading.Event()
        self._capture_control = None
        self._capture = None
        self._capture_hwnd: int | None = None
        self._capture_generation = 0
        self._capture_rebind_lock = threading.Lock()
        self._capture_warned = False
        self._capture_last_copy = 0.0
        self._virtual_gamepad = None
        self._left_stick_x = 0.0
        self._left_stick_y = 0.0
        self._right_stick_x = 0.0
        self._right_stick_y = 0.0
        self._movement_runtime_logged = False
        self._camera_runtime_logged = False

        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        _log.info("=" * 40)
        _log.info("  %s 启动", self.project_name)

        _log.info("  Admin: %s", "是" if is_admin else "否")
        _log.info("=" * 40)
        if not is_admin:
            _log.warning("尝试以管理员模式启动失败，需要手动以管理员模式运行")
            _log.info("按 回车 键退出程序")
            input("")
            sys.exit(1)
        else:
            # 等待目标窗口出现
            _log.info("等待目标窗口 '%s' ...", target)
            while True:
                hwnd = self._find_window(target)
                if hwnd is not None:
                    self._target_hwnd = hwnd
                    _log.info("已找到窗口: '%s'", target)
                    break
                sleep(1)

            self._hotkey_thread: threading.Thread | None = None
            logical_cpus = os.cpu_count() or 1
            ocr_threads = 1 if logical_cpus <= 8 else 2
            _log.info("正在加载 OCR 识别引擎，请稍候...")
            # ONNX Runtime is the heaviest child-process import. Loading it
            # here lets the live logger report progress before that work.
            from module.rapidocr_onnxruntime import RapidOCR

            # Fixed-position markers use recognition-only OCR. Detection and
            # orientation models stay unloaded unless a legacy full-OCR call
            # actually requests them.
            self.ocrmodel = RapidOCR(
                use_det=False,
                use_cls=False,
                use_rec=True,
                intra_op_num_threads=ocr_threads,
                inter_op_num_threads=1,
            )
            _log.info(
                "OCR 配置 | 仅预加载文字识别模型 | 逻辑处理器=%d | "
                "计算线程=%d | 调度线程=1 | 空闲自旋=关闭",
                logical_cpus,
                ocr_threads,
            )
            self._ocr_lock = threading.Lock()
            self._stop_event = threading.Event()
            self._rect_thread: threading.Thread | None = None
            self._start_rect_watchdog()
            if self.background_mode:
                self._init_virtual_gamepad()
                self._start_background_capture()
            self._init_toast()
            _log.info(
                "初始化完成 | 目标窗口: '%s' | 捕获: %s | Toast: %s",
                target,
                "后台窗口捕获" if self.background_mode else "桌面可见区域",
                "可用" if self._tk_root is not None else "不可用",
            )
            if self.background_mode:
                self._log_input_configuration()
            else:
                _log.info(
                    "输入配置 | 模式=前台键盘 | W=Chiaki Left Stick Up | "
                    "Return=Cross | \\=Square | L=L2 | 3=R1"
                )
            _log.debug("区域配置: %s 个", len(region_dict) if region_dict else 0)

    def _init_virtual_gamepad(self) -> None:
        """Create a DS4 device for reliable background Chiaki input.

        Chiaki's Qt stream window can ignore posted WM_KEY messages while it
        is unfocused.  ViGEm exposes a real virtual DualShock 4 controller,
        which Chiaki receives independently of keyboard focus.
        """
        if vg is None:
            _log.warning("后台输入未找到 vgamepad，回退到窗口键盘消息")
            return
        try:
            pad = vg.VDS4Gamepad()
            pad.reset()
            pad.update()
            self._virtual_gamepad = pad
            _log.info("后台输入已启用 ViGEm 虚拟 DualShock 4 手柄")
            self._log_input_configuration()
        except Exception:
            _log.warning("无法创建 ViGEm 虚拟手柄，回退到窗口键盘消息", exc_info=True)

    def _log_input_configuration(self) -> None:
        """Log the concrete input path and DS4 axis values for troubleshooting."""
        if self._virtual_gamepad is None:
            _log.info(
                "输入配置 | 模式=后台窗口消息回退 | "
                "W/S/A/D=Chiaki Left Stick Up/Down/Left/Right | "
                "Q/E=Chiaki Right Stick Left/Right | "
                "Return=Cross | \\=Square | L=L2 | 3=R1"
            )
            return

        report = self._virtual_gamepad.report
        center = int(report.bThumbLY) & 0xFF
        up_pressed = 255 if self.invert_movement else 1
        down_pressed = 1 if self.invert_movement else 255
        _log.info(
            "输入配置 | 模式=ViGEm DS4 | W/S/A/D=左摇杆上/下/左/右 | "
            "Q/E=右摇杆左/右 | "
            "bThumbLY: 中立=%d, W按下=%d, S按下=%d, 释放=%d | 反向=%s | "
            "Return=Cross, \\=Square, L=L2, 3=R1",
            center,
            up_pressed,
            down_pressed,
            center,
            "是" if self.invert_movement else "否",
        )

    def run_as_admin(self) -> None:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas",
                sys.argv[0],
                " ".join(sys.argv[1:]),
                None, 1,
            )
            sys.exit(0)

    def _start_background_capture(self, hwnd: int | None = None) -> None:
        """Capture the current Chiaki HWND and rebind after window recreation."""
        if WindowsCapture is None:
            raise RuntimeError(
                "后台模式缺少 windows-capture 依赖，请重新安装完整版本或执行 "
                "py -3.10 -m pip install windows-capture"
            )

        if hwnd is None:
            hwnd = self._target_hwnd
        if hwnd is None:
            raise RuntimeError("无法启动后台捕获：尚未找到 Chiaki 串流窗口")

        with self._capture_rebind_lock:
            if self._capture_hwnd == hwnd and self._capture_control is not None:
                return

            previous_control = self._capture_control
            self._capture_generation += 1
            generation = self._capture_generation
            self._capture = None
            self._capture_control = None
            self._capture_hwnd = None
            with self._capture_lock:
                self._latest_capture = None
                self._capture_last_copy = 0.0
            self._capture_ready.clear()

        # Windows Capture can invoke on_closed synchronously from stop().
        # Never hold the rebind lock while stopping the previous session or
        # that callback could wait on the same lock and freeze automation.
        if previous_control is not None:
            try:
                previous_control.stop()
            except Exception:
                _log.debug("停止旧 Chiaki 窗口捕获失败", exc_info=True)

        def on_frame_arrived(frame, _control) -> None:
            try:
                if generation != self._capture_generation:
                    return
                # OCR runs at most once per second in normal battle phases.
                # Two frames per second still exceeds every normal OCR polling
                # rate while halving repeated 1080p/4K buffer copies that can contend
                # with Chiaki's decoder and renderer.
                now = monotonic()
                if now - self._capture_last_copy < 0.5:
                    return
                # The callback buffer may be reused after the callback returns.
                pixels = np.array(frame.frame_buffer, copy=True)
                with self._capture_lock:
                    self._latest_capture = pixels
                    self._capture_last_copy = now
                self._capture_ready.set()
            except Exception:
                _log.debug("后台窗口帧复制失败", exc_info=True)

        def on_closed() -> None:
            if generation == self._capture_generation:
                _log.warning("后台窗口捕获已关闭，等待 Chiaki 窗口恢复或重建")
                with self._capture_rebind_lock:
                    if generation == self._capture_generation:
                        self._capture = None
                        self._capture_control = None
                        self._capture_hwnd = None
                with self._capture_lock:
                    self._latest_capture = None
                self._capture_ready.clear()

        capture = WindowsCapture(
            cursor_capture=False,
            window_hwnd=hwnd,
            minimum_update_interval=500,
        )
        capture.event(on_frame_arrived)
        capture.event(on_closed)
        try:
            control = capture.start_free_threaded()
        except Exception as exc:
            raise RuntimeError(
                "无法启动 Chiaki 后台窗口捕获；请确认 Chiaki 使用窗口/无边框模式"
            ) from exc

        with self._capture_rebind_lock:
            if generation != self._capture_generation:
                try:
                    control.stop()
                except Exception:
                    pass
                return
            self._capture = capture
            self._capture_control = control
            self._capture_hwnd = hwnd
        _log.info("后台窗口捕获已绑定 hwnd=%s", hwnd)
        if not self._capture_ready.wait(timeout=5):
            _log.warning("后台窗口捕获尚未收到首帧，Chiaki 可能被最小化或暂停渲染")


    def screenshot_text(self, text):
        if self.window_rect is None:
            try:
                rect = self.get_window_rect(silent=True)
            except TypeError:
                if not self.background_mode:
                    self.focus_window()
                rect = self.get_window_rect(silent=True)
            if rect is None:
                raise RuntimeError("Chiaki 串流窗口暂时不可用")
            left, top, width, height = rect
        else:
            left, top, width, height = self.window_rect
        if self.text2region is None or text not in self.text2region.keys():
            img = self.screenshot(region=(left, top, width, height))
        else:
            x1 = int(left + width * self.text2region[text][0])
            y1 = int(top + height * self.text2region[text][1])
            width1 = int(
                width * (self.text2region[text][2] - self.text2region[text][0])
            )
            height1 = int(
                height * (self.text2region[text][3] - self.text2region[text][1])
            )
            region = (x1, y1, width1, height1)
            img = self.screenshot(region=region)

        return img

    def screenshot(
        self, region: tuple[int, int, int, int] | None = None
    ) -> Image.Image:
        """Capture the Chiaki stream window from the desktop or a HWND frame."""
        if self.window_rect is None:
            self.get_window_rect()
        if self.window_rect is None:
            raise RuntimeError("Chiaki stream window is not available")

        hwnd = self._get_hwnd()
        if hwnd is not None and win32gui.IsIconic(hwnd):
            if self.background_mode:
                if not self._capture_warned:
                    _log.warning(
                        "Chiaki 窗口已最小化；请恢复窗口（可以被其他窗口覆盖，但不要最小化）"
                    )
                    self._capture_warned = True
                left, top, width, height = self.window_rect
                return Image.new("RGB", (max(1, width), max(1, height)), "black")
            _log.warning("Chiaki stream window is minimized; restore it before starting")
            self.focus_window()

        left, top, width, height = self.window_rect
        if region is None:
            region = (left, top, width, height)
        r_left, r_top, r_w, r_h = region

        if self.background_mode:
            with self._capture_lock:
                # The callback assigns a new immutable NumPy array; retaining
                # its reference is safe. Only the requested crop is copied.
                pixels = self._latest_capture
            if pixels is None:
                raise RuntimeError("后台窗口捕获尚未提供画面")
            if pixels.ndim != 3 or pixels.shape[2] < 3:
                raise RuntimeError("后台窗口捕获返回了无效画面")

            frame_h, frame_w = pixels.shape[:2]
            scale_x = frame_w / max(1, width)
            scale_y = frame_h / max(1, height)
            x0 = max(0, min(frame_w, int((r_left - left) * scale_x)))
            y0 = max(0, min(frame_h, int((r_top - top) * scale_y)))
            x1 = max(x0 + 1, min(frame_w, int((r_left - left + r_w) * scale_x)))
            y1 = max(y0 + 1, min(frame_h, int((r_top - top + r_h) * scale_y)))
            # Windows Graphics Capture returns BGRA; RapidOCR receives RGB.
            crop = pixels[y0:y1, x0:x1, :3][:, :, ::-1].copy()
            return Image.fromarray(crop, mode="RGB")

        return ImageGrab.grab(
            bbox=(r_left, r_top, r_left + r_w, r_top + r_h),
            all_screens=True,
        ).convert("RGB")

    def get_window_rect(self, silent: bool = False):
        hwnd = self._get_hwnd()
        if hwnd is None:
            if not silent:
                _log.warning("未找到窗口: '%s'", self.target_window)
            return None
        if win32gui.IsIconic(hwnd):
            if not silent:
                _log.debug("窗口最小化，沿用上次有效矩形: %s", self.window_rect)
            return self.window_rect

        c_left, c_top, c_right, c_bottom = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (c_left, c_top))
        right, bottom = win32gui.ClientToScreen(hwnd, (c_right, c_bottom))
        width = right - left
        height = bottom - top
        self.window_rect = (left, top, width, height)
        return (left, top, width, height)

    def _rect_watchdog(self, interval: float = 0.5) -> None:
        """后台线程：周期性刷新窗口客户区矩形，窗口移动/缩放时保持最新"""
        while not self._stop_event.is_set():
            try:
                self.get_window_rect(silent=True)
            except Exception:
                _log.debug("窗口矩形刷新异常（已忽略）", exc_info=True)
            self._stop_event.wait(interval)

    def _start_rect_watchdog(self, interval: float = 0.5) -> None:
        """启动窗口矩形监听线程（幂等，重复调用不会起多个线程）"""
        if self._rect_thread is not None and self._rect_thread.is_alive():
            return
        self._rect_thread = threading.Thread(
            target=self._rect_watchdog, args=(interval,), daemon=True
        )
        self._rect_thread.start()
        _log.debug("窗口矩形监听线程已启动 (间隔 %.1fs)", interval)

    def stop(self) -> None:
        """停止监听线程（daemon 线程也会随主进程退出，这里用于显式优雅停止）"""
        self._stop_event.set()
        if self._virtual_gamepad is not None:
            try:
                self._virtual_gamepad.reset()
                self._virtual_gamepad.update()
                self._left_stick_x = 0.0
                self._left_stick_y = 0.0
                self._right_stick_x = 0.0
                self._right_stick_y = 0.0
            except Exception:
                _log.debug("复位虚拟手柄失败", exc_info=True)
        if self._capture_control is not None:
            self._capture_generation += 1
            try:
                self._capture_control.stop()
            except Exception:
                _log.debug("停止后台窗口捕获失败", exc_info=True)

    def focus_window(self) -> bool:
        hwnd = self._get_hwnd()
        if hwnd is None:
            _log.warning("聚焦失败，未找到窗口: '%s'", self.target_window)
            return False

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY = -1

        foreground = user32.GetForegroundWindow()
        if foreground and foreground != hwnd:
            fg_thread = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(foreground, ctypes.byref(fg_thread))
            my_thread = kernel32.GetCurrentThreadId()
            if fg_thread.value:
                user32.AttachThreadInput(my_thread, fg_thread.value, True)
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(my_thread, fg_thread.value, False)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                user32.SetForegroundWindow(hwnd)

        return user32.GetForegroundWindow() == hwnd

    def _get_hwnd(self) -> int | None:
        """返回目标窗口句柄并缓存；窗口句柄失效时自动重新查找

        游戏窗口存活期间 hwnd 稳定，无需每次 EnumWindows 全量枚举。
        仅用 IsWindow 做一次轻量校验（微秒级），窗口被销毁/重建时才重查。
        """
        hwnd = self._target_hwnd
        if hwnd is not None and win32gui.IsWindow(hwnd):
            if self.background_mode and self._capture_hwnd != hwnd:
                try:
                    self._start_background_capture(hwnd)
                except Exception:
                    _log.warning("恢复 Chiaki 后台窗口捕获失败，将继续重试", exc_info=True)
            return hwnd
        previous_hwnd = hwnd
        self._target_hwnd = None
        self.window_rect = None
        hwnd = self._find_window(self.target_window)
        if hwnd is not None:
            self._target_hwnd = hwnd
            self._hwnd_warned = False
            if previous_hwnd is not None and previous_hwnd != hwnd:
                _log.info(
                    "检测到 Chiaki 串流窗口已重建: hwnd %s -> %s",
                    previous_hwnd,
                    hwnd,
                )
            if self.background_mode and self._capture_hwnd != hwnd:
                try:
                    self._start_background_capture(hwnd)
                except Exception:
                    _log.warning("重新绑定新 Chiaki 窗口捕获失败", exc_info=True)
            return hwnd
        else:
            if not self._hwnd_warned:
                _log.warning("没有找到窗口: %s ", self.target_window)
                self._hwnd_warned = True
            return None

    def _find_window(self, title: str) -> int | None:
        """按标题（不区分大小写模糊匹配）查找可见窗口句柄"""
        result: list[int] = []

        def callback(hwnd: int, _: object) -> bool:
            if win32gui.IsWindowVisible(hwnd):
                text: str = win32gui.GetWindowText(hwnd)
                if title.lower() in text.lower():
                    result.append(hwnd)
            return True

        win32gui.EnumWindows(callback, None)
        return result[0] if result else None

    # ============================================================
    #  热键系统（内部实现，外部通过 set_battle_start/stop_key 使用）
    # ============================================================
    @property
    def running(self) -> bool:
        """战斗循环是否运行中"""
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        self._running = value

    @property
    def paused(self) -> bool:
        """Whether automation is temporarily paused without ending the run."""
        return self._paused

    @property
    def pause_generation(self) -> int:
        """Return the number of pause/resume toggles since controller start."""
        return self._pause_generation

    @property
    def shutdown_requested(self) -> bool:
        """Whether an automatic limit requested a clean process shutdown."""
        return self._shutdown_requested

    def release_automation_inputs(self) -> None:
        """Neutralize every controller axis/button the automation may hold."""
        pad = self._virtual_gamepad
        if pad is not None:
            try:
                pad.reset()
                pad.update()
                self._left_stick_x = 0.0
                self._left_stick_y = 0.0
                self._right_stick_x = 0.0
                self._right_stick_y = 0.0
                return
            except Exception:
                _log.warning("虚拟手柄归零失败", exc_info=True)

        hwnd = self._get_hwnd()
        for key in ("w", "s", "a", "d", "q", "e", "l"):
            vk = self.KEY_MAP[key]
            try:
                if self.background_mode and hwnd is not None:
                    self._post_key(hwnd, vk, keyup=True)
                elif not self.background_mode:
                    self._send_key(vk, keyup=True)
            except Exception:
                _log.debug("释放自动化按键 %s 失败", key, exc_info=True)

    def request_shutdown(self) -> None:
        """Stop automation and allow ``start`` to return to the process entry."""
        self._shutdown_requested = True
        self._paused = False
        self._running = False
        self.release_automation_inputs()

    def set_battle_start_key(self, key: str) -> None:
        """设置战斗开始快捷键（自动注册热键）

        :param key: 按键名 (如 'f1', 'f2')
        """

        def _on_start() -> None:
            self._paused = False
            self._running = True
            _log.info(">> %s 已启动", self.project_name)
            self.show_toast(self.project_name, "已启动")

        _log.info("按 %s 启动", key)
        self._register_hotkey(key, _on_start)

    def set_battle_stop_key(self, key: str) -> None:
        """设置战斗停止快捷键（自动注册热键）

        :param key: 按键名 (如 'f1', 'f2')
        """

        def _on_stop() -> None:
            self._running = False
            self._paused = False
            self.release_automation_inputs()
            _log.info("<< %s 已停止 按启动键重新开始", self.project_name)
            self.show_toast(self.project_name, "已停止，按启动键重新开始")

        _log.info("按 %s 停止", key)
        self._register_hotkey(key, _on_stop)

    def set_battle_pause_key(self, key: str) -> None:
        """Register a pause/resume hotkey which preserves the battle phase."""

        def _on_toggle_pause() -> None:
            if not self._running:
                _log.info("%s 暂停键已忽略：自动重战尚未启动", key.upper())
                return
            self._paused = not self._paused
            self._pause_generation += 1
            if self._paused:
                self.release_automation_inputs()
                _log.info("|| %s 已暂停；所有自动化按键已释放", self.project_name)
                self.show_toast(self.project_name, "已暂停，按 F3 继续")
            else:
                _log.info(">> %s 已继续", self.project_name)
                self.show_toast(self.project_name, "已继续")

        _log.info("按 %s 暂停/继续", key)
        self._register_hotkey(key, _on_toggle_pause)

    # ============================================================
    #  Win11 风格 Toast 通知（tkinter 圆角阴影 + 滑入滑出动画）
    # ============================================================
    def _init_toast(self) -> None:
        """初始化 tkinter 通知系统：在独立 daemon 线程中创建 root 并运行事件循环"""
        self._tk_root: tk.Tk | None = None
        self._tk_ready = threading.Event()

        def _tk_thread() -> None:
            try:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                self._tk_root = root
            except Exception as e:
                msg = f"[WARN] Toast 通知不可用 (tkinter 初始化失败): {e}"
                print(msg)
                try:
                    _log.warning("Toast 通知不可用 (tkinter 初始化失败): %s", e)
                except Exception:
                    pass
                self._tk_root = None
            finally:
                self._tk_ready.set()
            if self._tk_root is not None:
                self._tk_root.mainloop()

        t = threading.Thread(target=_tk_thread, daemon=True)
        t.start()
        self._tk_ready.wait(timeout=3)

    def show_toast(self, title: str, content: str) -> None:
        """Win11 风格通知：圆角、阴影、滑入滑出动画，总时长 5 秒，非阻塞"""
        if self._tk_root is None:
            return
        # 调度到 tk 主循环线程执行
        self._tk_root.after(0, self._create_toast, title, content)

    def _create_toast(self, title: str, content: str) -> None:
        """（在 tk 线程内运行）创建并播放一条 toast 通知"""

        root = self._tk_root

        # —— 样式常量 ——
        MARGIN = 16
        WIN_W = 340
        WIN_H = 90
        RADIUS = 10
        BG = "#1e1e1e"
        BORDER = "#404040"
        TITLE_FG = "#ffffff"
        TEXT_FG = "#b0b0b0"
        SHADOW_ALPHA = 0.25
        SHADOW_DX = 5
        SHADOW_DY = 5

        ENTER_MS = 600
        STAY_MS = 3800  # 5000 - 600 - 600
        EXIT_MS = 600
        ENTER_STEPS = 40  # 15ms/step → ~67fps
        EXIT_STEPS = 30  # 20ms/step → ~50fps

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()

        final_x = screen_w - WIN_W - MARGIN
        final_y = screen_h - WIN_H - MARGIN - 40  # 任务栏上方

        # —— 辅助：裁剪文字防溢出 ——
        def _clip(text: str, max_chars: int) -> str:
            return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

        # —— 辅助：绘制圆角矩形（polygon + smooth） ——
        def _round_rect(c: tk.Canvas, **kw) -> int:
            x, y, w, h, r = 0, 0, WIN_W, WIN_H, RADIUS
            pts = [
                x + r,
                y,
                x + w - r,
                y,
                x + w,
                y,
                x + w,
                y + r,
                x + w,
                y + h - r,
                x + w,
                y + h,
                x + w - r,
                y + h,
                x + r,
                y + h,
                x,
                y + h,
                x,
                y + h - r,
                x,
                y + r,
                x,
                y,
            ]
            return c.create_polygon(pts, smooth=True, **kw)

        # ===== 阴影窗口 =====
        shadow = tk.Toplevel(root)
        shadow.overrideredirect(True)
        shadow.attributes("-topmost", True)
        shadow.attributes("-alpha", 0.0)
        shadow.configure(bg="black")
        shadow.geometry(f"{WIN_W}x{WIN_H}+{screen_w}+{final_y}")
        sh_canvas = tk.Canvas(
            shadow, width=WIN_W, height=WIN_H, bg="black", highlightthickness=0
        )
        sh_canvas.pack(fill="both", expand=True)
        _round_rect(sh_canvas, fill="black", outline="")

        # ===== 主通知窗口 =====
        toast = tk.Toplevel(root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.attributes("-alpha", 0.0)
        toast.configure(bg=BG)
        toast.geometry(f"{WIN_W}x{WIN_H}+{screen_w}+{final_y}")

        canvas = tk.Canvas(
            toast, width=WIN_W, height=WIN_H, bg=BG, highlightthickness=0
        )
        canvas.pack(fill="both", expand=True)

        # 圆角背景 + 边框
        _round_rect(canvas, fill=BG, outline=BORDER, width=1)

        # 标题与内容
        title_text = _clip(title, 30)
        body_text = _clip(content, 45)
        canvas.create_text(
            20,
            22,
            text=title_text,
            anchor="w",
            fill=TITLE_FG,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        canvas.create_text(
            20,
            50,
            text=body_text,
            anchor="w",
            fill=TEXT_FG,
            font=("Microsoft YaHei UI", 10),
        )

        # ===== 进入动画（smoothstep ease-out） =====
        def anim_in(step: int = 0) -> None:
            if step > ENTER_STEPS:
                toast.attributes("-alpha", 1.0)
                shadow.attributes("-alpha", SHADOW_ALPHA)
                root.after(STAY_MS, anim_out)
                return
            t = step / ENTER_STEPS
            # smoothstep: 曲线两端导数为 0，无顿挫感
            eased = t * t * (3 - 2 * t)
            cx = screen_w - int((WIN_W + MARGIN) * eased)
            toast.geometry(f"+{cx}+{final_y}")
            shadow.geometry(f"+{cx + SHADOW_DX}+{final_y + SHADOW_DY}")
            toast.attributes("-alpha", eased)
            shadow.attributes("-alpha", SHADOW_ALPHA * eased)
            root.after(ENTER_MS // ENTER_STEPS, anim_in, step + 1)

        # ===== 退出动画（smoothstep ease-in） =====
        def anim_out(step: int = 0) -> None:
            if step > EXIT_STEPS:
                toast.destroy()
                shadow.destroy()
                return
            t = step / EXIT_STEPS
            eased = t * t * (3 - 2 * t)
            cx = final_x + int((MARGIN + WIN_W) * eased)
            toast.geometry(f"+{cx}+{final_y}")
            shadow.geometry(f"+{cx + SHADOW_DX}+{final_y + SHADOW_DY}")
            toast.attributes("-alpha", 1 - eased)
            shadow.attributes("-alpha", SHADOW_ALPHA * (1 - eased))
            root.after(EXIT_MS // EXIT_STEPS, anim_out, step + 1)

        anim_in()

    def _register_hotkey(self, key: str, callback: callable) -> None:
        """内部注册热键回调，首次调用自动启动监听线程

        :param key:      按键名 (如 'f1', 'f2')，需在 KEY_MAP 中存在
        :param callback: 触发时调用的无参回调函数
        """
        vk = self.KEY_MAP.get(key.lower())
        if vk is None:
            raise ValueError(f"未知的按键名: '{key}'")
        self._hotkeys[vk] = callback
        _log.debug("注册热键: '%s' (0x%02X)", key, vk)
        self._start_hotkey()

    def _start_hotkey(self) -> None:
        """启动后台热键监听线程（幂等）"""
        if self._hotkey_thread is not None:
            return
        self._hotkey_thread = threading.Thread(target=self._hotkey_loop, daemon=True)
        self._hotkey_thread.start()
        _log.debug("热键监听已启动")

    def _hotkey_loop(self) -> None:
        """后台线程：轮询 GetAsyncKeyState 检测热键按下"""
        prev: dict[int, bool] = {}
        while True:
            for vk, cb in list(self._hotkeys.items()):
                pressed = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
                if pressed and not prev.get(vk, False):
                    _log.debug("热键触发: 0x%02X", vk)
                    cb()
                prev[vk] = pressed
            sleep(0.05)

    def ocr(self, pic: Image, confidence=0.6):
        # RapidOCR can be called by multiple phase checks. Serialize inference
        # so concurrent frame checks cannot corrupt or stall the ONNX session.
        with self._ocr_lock:
            result = self.ocrmodel(pic, use_det=True, use_cls=False)

        if result is None or result[0] is None or len(result[0]) == 0:
            _log.debug("OCR: 未识别到文本")
            return None

        ocr_result_list = []
        for item in result[0]:
            if item[2] > confidence:
                d = {
                    "text": item[1],
                    "location": (
                        int(item[0][0][0]) - 1,
                        int(item[0][0][1]) - 1,
                        int(item[0][2][0]) + 1,
                        int(item[0][2][1]) + 1,
                    ),
                }
                ocr_result_list.append(d)

        texts = [t["text"] for t in ocr_result_list]
        _log.debug("OCR: %d 个文本 → %s", len(ocr_result_list), texts)
        return ocr_result_list

    def recognize_line(self, pic: Image, confidence: float = 0.65) -> str:
        """Recognize one fixed-position text line without running detection.

        GBFR's battle markers live in stable normalized regions.  Sending the
        already-cropped image directly to the recognition model avoids the
        expensive text detector while retaining OCR tolerance for resolution
        and stream-compression changes.
        """
        with self._ocr_lock:
            result = self.ocrmodel(pic, use_det=False, use_cls=False)

        if result is None or result[0] is None or len(result[0]) == 0:
            return ""
        item = result[0][0]
        if len(item) < 2 or float(item[1]) < confidence:
            return ""
        text = str(item[0]).strip()
        _log.debug("单行识别: '%s' (%.3f)", text, float(item[1]))
        return text

    # ----------------------------------------------------------
    #  操作协议（_do_press 接受的格式）
    #  ----------------------------------------------------------
    #  "key"                        → 按键，无间隔(sleep 0)
    #  "click"                      → 点击(200,200)，无间隔
    #  ("key", delay)               → 按键，后休眠 delay 秒
    #  ("click", x, y)              → 点击(x,y)，无间隔
    #  ("click", x, y, delay)       → 点击(x,y)，后休眠 delay 秒
    #  ("click", delay)             → 点击(200,200)，后休眠 delay 秒
    # ----------------------------------------------------------
    def wait(
        self, text, timeout=60, fail_press=None, success_press=None, poll: float = 0.3
    ):
        """等待文字出现在画面中
        :param poll: 轮询间隔(秒)，控制截图+OCR频率，避免CPU占满导致游戏卡顿
        """
        _log.debug("等待文字: '%s' (超时: %ds)", text, timeout)

        deadline = time() + timeout

        while True:
            self.get_window_rect(silent=True)
            if not self._running:
                _log.debug("等待中断 (热键停止): '%s'", text)
                return False

            pic = self.screenshot_text(text)
            result = self.ocr(pic)

            if isinstance(result, list) and result:
                if self._find_ocr_match(result, text):
                    elapsed = time() + timeout - deadline
                    _log.debug("已找到: '%s' (%.1fs)", text, elapsed)

                    self._do_press(success_press)
                    return True

            self._do_press(fail_press)
            sleep(poll)

            if time() >= deadline:
                break

        _log.debug("超时未找到: '%s' (%.0fs)", text, timeout)
        return False

    def _do_press(self, press, default_interval=0):
        """执行操作序列"""
        if not press:
            return None

        _log.debug("执行操作: %s", press)
        for item in press:
            delay = default_interval

            if isinstance(item, str):
                if item == "click":
                    self.click()
                else:
                    self.press(item)

            elif isinstance(item, tuple) and item:
                act, *args = item
                if act == "click":
                    delay = self._do_click_action(args, delay)
                else:
                    self.press(act)
                    if args:
                        delay = args[0]

            else:
                _log.warning("无效的操作项（已跳过）: %r", item)
                continue

            sleep(delay)
   
        
    def _do_click_action(self, args: list, fallback_delay: float) -> float:
        """解析 (click, ...) 元组的点击逻辑，返回延迟秒数"""
        n = len(args)
        if n == 0:
            self.click()
            return fallback_delay
        if n == 1:
            self.click()
            return args[0]  # (click, delay)
        if n == 2:
            self.click(args[0], args[1])
            return fallback_delay  # (click, x, y)
        # n >= 3
        self.click(args[0], args[1])
        return args[2]  # (click, x, y, delay)

    @staticmethod
    def _find_ocr_match(ocr_list: list[dict], text: str) -> bool:
        """在 OCR 结果列表中查找包含指定文字的条目"""
        ocr_string = ""
        for item in ocr_list:
            ocr_string += item["text"]

        if text in ocr_string:
            return True
        return False

    MOUSE_MAP = {
        "left": [win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP],
        "right": [win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP],
        "middle": [win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP],
        "x": [win32con.MOUSEEVENTF_XDOWN, win32con.MOUSEEVENTF_XUP],
    }

    def click(self, x=200, y=200, key="left", times=1, interval=0):
        _log.debug("点击%s: (%d, %d) x%d", key, x, y, times)
        if self.background_mode:
            hwnd = self._get_hwnd()
            if hwnd is None:
                return
            button_messages = {
                "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP),
                "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP),
                "middle": (win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP),
            }
            down, up = button_messages.get(key, button_messages["left"])
            lparam = (int(y) << 16) | (int(x) & 0xFFFF)
            for _ in range(times):
                ctypes.windll.user32.PostMessageW(hwnd, down, 0, lparam)
                sleep(0.1)
                ctypes.windll.user32.PostMessageW(hwnd, up, 0, lparam)
                sleep(interval)
            return

        self.focus_window()
        for _ in range(times):

            xx, yy = x + self.window_rect[0], y + self.window_rect[1]
            ctypes.windll.user32.SetCursorPos(xx, yy)
            win32api.mouse_event(self.MOUSE_MAP[key][0], xx, yy, 0, 0)
            sleep(0.1)
            win32api.mouse_event(self.MOUSE_MAP[key][1], xx, yy, 0, 0)
            sleep(interval)

    # 按键名字符串 → 虚拟键码映射表
    KEY_MAP: dict[str, int] = {
        # --- 字母键 ---
        "a": 0x41,
        "b": 0x42,
        "c": 0x43,
        "d": 0x44,
        "e": 0x45,
        "f": 0x46,
        "g": 0x47,
        "h": 0x48,
        "i": 0x49,
        "j": 0x4A,
        "k": 0x4B,
        "l": 0x4C,
        "m": 0x4D,
        "n": 0x4E,
        "o": 0x4F,
        "p": 0x50,
        "q": 0x51,
        "r": 0x52,
        "s": 0x53,
        "t": 0x54,
        "u": 0x55,
        "v": 0x56,
        "w": 0x57,
        "x": 0x58,
        "y": 0x59,
        "z": 0x5A,
        # --- 主键盘数字 (也用作别名 '0'-'9') ---
        "0": 0x30,
        "1": 0x31,
        "2": 0x32,
        "3": 0x33,
        "4": 0x34,
        "5": 0x35,
        "6": 0x36,
        "7": 0x37,
        "8": 0x38,
        "9": 0x39,
        # --- 控制键 ---
        "enter": 0x0D,
        "return": 0x0D,
        "esc": 0x1B,
        "escape": 0x1B,
        "space": 0x20,
        "spacebar": 0x20,
        "backspace": 0x08,
        "bs": 0x08,
        "tab": 0x09,
        "shift": 0x10,
        "lshift": 0xA0,
        "rshift": 0xA1,
        "ctrl": 0x11,
        "lctrl": 0xA2,
        "rctrl": 0xA3,
        "alt": 0x12,
        "lalt": 0xA4,
        "ralt": 0xA5,
        "delete": 0x2E,
        "del": 0x2E,
        "insert": 0x2D,
        "ins": 0x2D,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "pagedown": 0x22,
        "capslock": 0x14,
        "numlock": 0x90,
        "scrolllock": 0x91,
        "printscreen": 0x2C,
        "pause": 0x13,
        # --- 方向键 ---
        "left": 0x25,
        "right": 0x27,
        "up": 0x26,
        "down": 0x28,
        # --- F功能键 ---
        "f1": 0x70,
        "f2": 0x71,
        "f3": 0x72,
        "f4": 0x73,
        "f5": 0x74,
        "f6": 0x75,
        "f7": 0x76,
        "f8": 0x77,
        "f9": 0x78,
        "f10": 0x79,
        "f11": 0x7A,
        "f12": 0x7B,
        # --- 小键盘 ---
        "num0": 0x60,
        "num1": 0x61,
        "num2": 0x62,
        "num3": 0x63,
        "num4": 0x64,
        "num5": 0x65,
        "num6": 0x66,
        "num7": 0x67,
        "num8": 0x68,
        "num9": 0x69,
        "num*": 0x6A,
        "num+": 0x6B,
        "num-": 0x6D,
        "num.": 0x6E,
        "num/": 0x6F,
        # --- 符号键 ---
        ";": 0xBA,
        "=": 0xBB,
        ",": 0xBC,
        "-": 0xBD,
        ".": 0xBE,
        "/": 0xBF,
        "`": 0xC0,
        "[": 0xDB,
        "\\": 0xDC,
        "]": 0xDD,
        "'": 0xDE,
    }

    # ---- SendInput 缓存（避免重复 ctypes 属性查找） ----
    _send_input = ctypes.windll.user32.SendInput
    _map_vk_to_scan = ctypes.windll.user32.MapVirtualKeyW
    _extended_vks = {
        0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
        0x2D, 0x2E, 0x90, 0x91, 0xA2, 0xA3, 0xA4, 0xA5,
    }

    @classmethod
    def _post_key(cls, hwnd: int, vk: int, keyup: bool = False) -> None:
        """Post a key directly to Chiaki without changing the foreground window."""
        scan = cls._map_vk_to_scan(vk, 0) & 0xFF
        lparam = 1 | (scan << 16)
        if vk in cls._extended_vks:
            lparam |= 0x01000000
        if keyup:
            lparam |= 0xC0000000
            message = win32con.WM_KEYUP
        else:
            message = win32con.WM_KEYDOWN
        if not ctypes.windll.user32.PostMessageW(hwnd, message, vk, lparam):
            _log.error("后台按键消息发送失败 hwnd=%s vk=0x%02X", hwnd, vk)

    @classmethod
    def _send_key(cls, vk: int, keyup: bool = False) -> None:
        scan = cls._map_vk_to_scan(vk, 0)
        flags = 0
        if scan & 0x100:  # 扩展键前缀 0xE0
            flags |= KEYEVENTF_EXTENDEDKEY
            scan &= 0xFF
        if keyup:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki.wVk = vk
        inp.u.ki.wScan = scan  # 填扫描码但不设 SCANCODE 标志，与 pynput 一致
        inp.u.ki.dwFlags = flags
        ret = cls._send_input(1, ctypes.byref(inp), ctypes.sizeof(inp))
        if ret == 0:
            _log.error("SendInput 被拦截 lastError=%d (UIPI/权限)", ctypes.GetLastError())

    def _set_virtual_key(self, key: str, pressed: bool) -> bool:
        """Map the original Chiaki keyboard bindings to a virtual DS4.

        Returning False keeps compatibility for an unknown custom key by
        letting the caller use the legacy message path instead.
        """
        pad = self._virtual_gamepad
        if pad is None or vg is None:
            return False

        normalized = key.lower()
        button_map = {
            "enter": vg.DS4_BUTTONS.DS4_BUTTON_CROSS,
            "return": vg.DS4_BUTTONS.DS4_BUTTON_CROSS,
            "backspace": vg.DS4_BUTTONS.DS4_BUTTON_CIRCLE,
            "bs": vg.DS4_BUTTONS.DS4_BUTTON_CIRCLE,
            "3": vg.DS4_BUTTONS.DS4_BUTTON_SHOULDER_RIGHT,
            "\\": vg.DS4_BUTTONS.DS4_BUTTON_SQUARE,
        }
        if normalized in button_map:
            if pressed:
                pad.press_button(button_map[normalized])
            else:
                pad.release_button(button_map[normalized])
        elif normalized == "l":
            pad.left_trigger_float(1.0 if pressed else 0.0)
        elif normalized in {"w", "s", "a", "d"}:
            # Preserve the other axis so combinations such as S+A form a
            # diagonal search arc instead of one key overwriting the other.
            if normalized in {"w", "s"}:
                y_value = -1.0 if normalized == "w" else 1.0
                if self.invert_movement:
                    y_value = -y_value
                self._left_stick_y = y_value if pressed else 0.0
            else:
                x_value = -1.0 if normalized == "a" else 1.0
                self._left_stick_x = x_value if pressed else 0.0
            pad.left_joystick_float(
                self._left_stick_x,
                self._left_stick_y,
            )
        elif normalized in {"q", "e"}:
            x_value = -1.0 if normalized == "q" else 1.0
            self._right_stick_x = x_value if pressed else 0.0
            pad.right_joystick_float(
                self._right_stick_x,
                self._right_stick_y,
            )
        elif normalized == "up":
            pad.directional_pad(
                vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTH
                if pressed
                else vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NONE
            )
        elif normalized == "t":
            touchpad = vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_TOUCHPAD
            if pressed:
                pad.press_special_button(touchpad)
            else:
                pad.release_special_button(touchpad)
        else:
            return False

        pad.update()
        if normalized in {"w", "s", "a", "d"} and pressed and not self._movement_runtime_logged:
            report = pad.report
            _log.info(
                "运行时 DS4 实测 | %s 按下后 bThumbLX=%d, bThumbLY=%d",
                normalized.upper(),
                int(report.bThumbLX) & 0xFF,
                int(report.bThumbLY) & 0xFF,
            )
            self._movement_runtime_logged = True
        if normalized in {"q", "e"} and pressed and not self._camera_runtime_logged:
            report = pad.report
            _log.info(
                "运行时 DS4 实测 | %s 按下后 bThumbRX=%d, bThumbRY=%d",
                normalized.upper(),
                int(report.bThumbRX) & 0xFF,
                int(report.bThumbRY) & 0xFF,
            )
            self._camera_runtime_logged = True
        return True

    def press(
        self,
        key: str,
        times: int = 1,
        interval: float = 0,
        movement: str = "press_and_release",
    ) -> None:
        """使用裸 ctypes SendInput 模拟键盘按键

        :param key:      按键名（同 KEY_MAP 中的键名）
        :param times:    连续按键次数
        :param interval: 每次按键之间的间隔（秒）
        :param movement: "press"仅按下 / "release"仅弹起 / "press_and_release"按下后弹起
        """
        # A release must always be allowed so pausing or a phase transition can
        # neutralize a key that was pressed just before the state changed.
        if self._paused and movement != "release":
            return

        vk = self.KEY_MAP.get(key.lower())
        if vk is None:
            raise ValueError(f"未知的按键名: '{key}'，请检查 KEY_MAP")

        _log.debug("按键: '%s' (0x%02X) x%d", key, vk, times)
        hwnd = self._get_hwnd()
        if self.background_mode and hwnd is None:
            return
        if not self.background_mode:
            self.focus_window()
        for _ in range(times):
            if movement != "release":
                if self.background_mode:
                    if not self._set_virtual_key(key, pressed=True):
                        self._post_key(hwnd, vk)
                else:
                    self._send_key(vk)
            sleep(0.2)
            if movement != "press":
                if self.background_mode:
                    if not self._set_virtual_key(key, pressed=False):
                        self._post_key(hwnd, vk, keyup=True)
                else:
                    self._send_key(vk, keyup=True)
            sleep(interval)

    def start(self, func):
        while not self.shutdown_requested:
            if not self.running or self.paused:
                # 等待 F1 启动，或等待 F3 继续。
                sleep(0.1)
            else:
                func(self)
