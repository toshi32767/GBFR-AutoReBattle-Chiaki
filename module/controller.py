import ctypes
import os
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path
from time import monotonic, sleep, time
import tkinter as tk
import sys
import win32api
import win32con
import win32gui
import win32ui
import win32process
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


# These names are deliberately user-facing rather than detector-specific.  A
# Keep the recognition presets tied to Chiaki's four fixed 16:9 stream sizes.
# Monitor/laptop physical sizes do not change the captured pixel geometry and
# therefore must not select a different OCR policy.
RECOGNITION_PROFILES: dict[str, dict[str, object]] = {
    "auto": {"label": "自动适配（推荐）", "canvas": (1920, 1080)},
    "chiaki_360p": {"label": "Chiaki 360p（640×360）", "canvas": (640, 360)},
    "chiaki_540p": {"label": "Chiaki 540p（960×540）", "canvas": (960, 540)},
    "chiaki_720p": {"label": "Chiaki 720p（1280×720）", "canvas": (1280, 720)},
    "chiaki_1080p": {"label": "Chiaki 1080p", "canvas": (1920, 1080)},
}


def adjust_window_rect_ex(
    rect: tuple[int, int, int, int],
    style: int,
    has_menu: bool,
    ex_style: int,
) -> tuple[int, int, int, int]:
    """Call the Win32 API directly across pywin32 versions.

    ``AdjustWindowRectEx`` is exposed by some pywin32 builds through
    ``win32gui`` and absent from others.  The application only needs the
    native RECT transformation, so use user32 directly instead of depending
    on an optional wrapper attribute.
    """

    native_rect = wintypes.RECT(*[int(value) for value in rect])
    api = ctypes.windll.user32.AdjustWindowRectEx
    api.argtypes = [
        ctypes.POINTER(wintypes.RECT),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    api.restype = wintypes.BOOL
    if not api(ctypes.byref(native_rect), int(style), bool(has_menu), int(ex_style)):
        raise ctypes.WinError()
    return (
        native_rect.left,
        native_rect.top,
        native_rect.right,
        native_rect.bottom,
    )
RECOGNITION_RESIZE_SETTLE_SECONDS = 0.9
# OCR policy follows the selected recognition canvas. The profile is not only
# a display label: low-resolution captures receive stronger text enlargement,
# while 2K/4K captures stay bounded to avoid wasting OCR time on oversized
# crops. Individual OCR regions may still request their own enhancement pass.
RECOGNITION_OCR_POLICIES: dict[str, dict[str, object]] = {
    "auto": {"min_scale": 1.0, "max_crop_pixels": 1600000},
    "chiaki_360p": {"min_scale": 1.65, "max_crop_pixels": 900000},
    "chiaki_540p": {"min_scale": 1.35, "max_crop_pixels": 1000000},
    "chiaki_720p": {"min_scale": 1.15, "max_crop_pixels": 1200000},
    "chiaki_1080p": {"min_scale": 1.0, "max_crop_pixels": 1600000},
}


def detect_game_content_bounds(frame: Image.Image) -> tuple[int, int, int, int, bool]:
    """Return the visible game area, removing only confident black bars.

    A 16:10 client is not automatically cropped: some Chiaki forks stretch
    rather than letterbox.  We crop to the centred 16:9 candidate only when
    the discarded edge bands are genuinely black, avoiding a silent loss of
    game UI on unusual clients.
    """

    width, height = frame.size
    if width < 16 or height < 16:
        return 0, 0, width, height, False
    ratio = width / max(1, height)
    target = 16.0 / 9.0
    if abs(ratio - target) <= 0.015:
        return 0, 0, width, height, False
    if ratio > target:
        content_width = int(round(height * target))
        x0 = max(0, (width - content_width) // 2)
        candidate = (x0, 0, x0 + content_width, height)
        strips = (frame.crop((0, 0, x0, height)), frame.crop((x0 + content_width, 0, width, height)))
    else:
        content_height = int(round(width / target))
        y0 = max(0, (height - content_height) // 2)
        candidate = (0, y0, width, y0 + content_height)
        strips = (frame.crop((0, 0, width, y0)), frame.crop((0, y0 + content_height, width, height)))
    samples = []
    for strip in strips:
        pixels = np.asarray(strip.convert("RGB"), dtype=np.uint8)
        if pixels.size:
            samples.append(float((pixels.max(axis=2) <= 24).mean()))
    if samples and min(samples) >= 0.92:
        x0, y0, x1, y1 = candidate
        return x0, y0, x1 - x0, y1 - y0, True
    return 0, 0, width, height, False


def normalize_recognition_frame(
    frame: Image.Image, profile: str = "auto"
) -> tuple[Image.Image, dict[str, object]]:
    """Crop black bars and scale without changing the image aspect ratio."""

    chosen = profile if profile in RECOGNITION_PROFILES else "auto"
    x, y, width, height, letterboxed = detect_game_content_bounds(frame)
    content = frame.crop((x, y, x + width, y + height)).convert("RGB")
    if chosen == "auto":
        # The automatic mode must follow the actual Chiaki stream size.  A
        # 540p full-frame ability screen has several columns and is expensive
        # to detector-OCR; expanding it to 1080p before every pass turns a
        # four-pixel workload into sixteen pixels without revealing new text.
        # Narrow labels still receive their own OCR-only enlargement later.
        if height <= 450:
            canvas_width, canvas_height = (640, 360)
            auto_canvas = "chiaki_360p"
        elif height <= 630:
            canvas_width, canvas_height = (960, 540)
            auto_canvas = "chiaki_540p"
        elif height <= 900:
            canvas_width, canvas_height = (1280, 720)
            auto_canvas = "chiaki_720p"
        else:
            canvas_width, canvas_height = (1920, 1080)
            auto_canvas = "chiaki_1080p"
    else:
        canvas_width, canvas_height = RECOGNITION_PROFILES[chosen]["canvas"]
        auto_canvas = chosen
    # Fit to a fixed 16:9 canvas, preserving aspect ratio.  In automatic mode
    # this is the nearest supported Chiaki rung; 4K is still bounded at 1080p.
    scale = max(0.01, min(canvas_width / max(1, width), canvas_height / max(1, height)))
    target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    if content.size != target:
        content = content.resize(target, getattr(Image, "Resampling", Image).LANCZOS)
    return content, {
        "profile": chosen,
        "normalization_canvas": auto_canvas,
        "source_size": frame.size,
        "content_rect": (x, y, width, height),
        "normalized_size": content.size,
        "scale": round(scale, 5),
        "letterbox_detected": letterboxed,
    }


def crop_normalized_relative_region(
    frame: Image.Image, region: tuple[float, float, float, float]
) -> Image.Image:
    """Crop a normalized recognition frame from client-relative coordinates.

    ``frame`` has already had window chrome removed and has been normalized to
    the selected Chiaki recognition canvas.  Applying text regions here keeps
    foreground and background capture in the same coordinate system.  In
    particular, background HWND capture must not map a client frame through
    the larger title-bar-inclusive window rectangle a second time.
    """
    left, top, right, bottom = region
    x0 = int(round(frame.width * min(1.0, max(0.0, left))))
    y0 = int(round(frame.height * min(1.0, max(0.0, top))))
    x1 = int(round(frame.width * min(1.0, max(0.0, right))))
    y1 = int(round(frame.height * min(1.0, max(0.0, bottom))))
    return frame.crop(
        (
            min(x0, max(0, frame.width - 1)),
            min(y0, max(0, frame.height - 1)),
            max(x0 + 1, min(frame.width, x1)),
            max(y0 + 1, min(frame.height, y1)),
        )
    )


def crop_window_capture_to_client_area(
    frame: Image.Image,
    window_rect: tuple[int, int, int, int],
    client_rect: tuple[int, int, int, int],
) -> Image.Image:
    """Remove HWND non-client pixels from a Windows Graphics Capture frame."""
    outer_left, outer_top, outer_right, outer_bottom = window_rect
    client_left, client_top, client_right, client_bottom = client_rect
    outer_width = outer_right - outer_left
    outer_height = outer_bottom - outer_top
    if outer_width <= 0 or outer_height <= 0 or frame.width < 2 or frame.height < 2:
        return frame
    scale_x = frame.width / outer_width
    scale_y = frame.height / outer_height
    # A client-only capture has a different vertical scale when the window has
    # a title bar. Do not crop it a second time.
    if abs(scale_x - scale_y) > 0.03:
        return frame
    x0 = int(round((client_left - outer_left) * scale_x))
    y0 = int(round((client_top - outer_top) * scale_y))
    x1 = int(round((client_right - outer_left) * scale_x))
    y1 = int(round((client_bottom - outer_top) * scale_y))
    if x0 < 0 or y0 < 0 or x1 > frame.width or y1 > frame.height or x1 <= x0 or y1 <= y0:
        return frame
    return frame.crop((x0, y0, x1, y1))


def prepare_ocr_image(pic: Image.Image, profile: str = "auto") -> Image.Image:
    """Upscale small OCR crops without enlarging already-normalized frames.

    The recognition canvas normalizes the full stream geometry, but narrow
    labels can still be only a few dozen pixels tall when the Chiaki window is
    small. OCR benefits from more input pixels even though the underlying
    information is unchanged. This helper is used only by text OCR; pixel
    detectors and coordinate-bearing full-frame ability parsing keep their
    original images.
    """
    if not isinstance(pic, Image.Image):
        return pic
    width, height = pic.size
    # Full-frame OCR and large menu crops carry coordinates consumed by some
    # parsers. Keep them unchanged; narrow strips are the cases where text
    # height, rather than screen geometry, is the limiting factor.
    if min(width, height) >= 500:
        return pic
    largest = max(width, height)
    scale = 3 if largest < 500 else 2
    policy = RECOGNITION_OCR_POLICIES.get(profile, RECOGNITION_OCR_POLICIES["auto"])
    scale = max(scale, float(policy["min_scale"]))
    max_crop_pixels = int(policy["max_crop_pixels"])
    if width * height * scale * scale > max_crop_pixels:
        scale = min(scale, (max_crop_pixels / max(1, width * height)) ** 0.5)
    scale = max(1.0, scale)
    # PIL requires integral output dimensions. The pixel-budget branch above
    # intentionally produces a fractional scale for many rectangular OCR
    # crops (for example a 900x400 panel under a 1M-pixel budget). Truncate
    # after scaling so we also retain the promised maximum-pixel bound.
    target_width = max(1, int(width * scale))
    target_height = max(1, int(height * scale))
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    return pic.resize((target_width, target_height), resampling)


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


def stream_caption_matches_target(configured_title: str, observed_caption: str) -> bool:
    """Match a visible window against the title configured by the user.

    ``Chiaki | Stream`` is only the local default. Other clients may use any
    custom title, so no language or ``Stream`` keyword is inferred here.
    Substring matching preserves support for a runtime-added suffix such as a
    resolution or session name.
    """

    configured = str(configured_title or "").strip().casefold()
    observed = str(observed_caption or "").strip()
    return bool(configured) and configured in observed.casefold()


class Controller:
    def __init__(
        self,
        target,
        project_name="Project",
        region_dict=None,
        background=False,
        invert_movement=False,
        expected_process_id=None,
        allow_missing_window=False,
        ui_language="auto",
        recognition_profile="auto",
    ) -> None:
        self.run_as_admin()

        self.target_window = target
        self._target_hwnd: int | None = None
        self.window_rect = None
        self.text2region = region_dict
        self.recognition_profile = (
            recognition_profile if recognition_profile in RECOGNITION_PROFILES else "auto"
        )
        self.geometry_generation = 0
        self._geometry_changed_at = 0.0
        self._last_geometry_signature = None
        self.project_name = project_name
        self.background_mode = bool(background)
        # Some ViGEm/driver/game combinations report the DS4 Y axis with the
        # opposite sign. Keep this explicit so a client machine can correct
        # direction without changing its Chiaki keyboard mapping.
        self.invert_movement = bool(invert_movement)
        normalized_language = str(ui_language).strip().lower()
        if normalized_language not in {"auto", "zh", "ja"}:
            raise ValueError(f"unsupported UI language: {ui_language}")
        self.ui_language_mode = normalized_language
        self.detected_ui_language: str | None = (
            None if normalized_language == "auto" else normalized_language
        )
        self._ui_language_lock = threading.Lock()

        self._running: bool = False
        self._paused: bool = False
        # Monotonic toggle counter.  Consumers can detect a pause/resume edge
        # even when both hotkey events happen between two polling iterations.
        self._pause_generation: int = 0
        self._shutdown_requested: bool = False
        self._shutdown_reason: str | None = None
        self._hotkeys: dict[int, callable] = {}
        self._hotkey_combos: dict[tuple[int, tuple[int, ...]], callable] = {}
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
        self._capture_frame_serial = 0
        self._window_was_iconic = False
        self._expected_process_id: int | None = (
            int(expected_process_id) if expected_process_id else None
        )
        self._window_detection_logged: set[int] = set()
        self._virtual_gamepad = None
        self._left_stick_x = 0.0
        self._left_stick_y = 0.0
        self._right_stick_x = 0.0
        self._right_stick_y = 0.0
        self._movement_runtime_logged = False
        self._camera_runtime_logged = False
        # Foreground SendInput is global. Track keys that this controller has
        # actually pressed so a lost Chiaki window cannot cause a new global
        # key press, while a later stop can still release an already-held key.
        self._foreground_pressed_keys: set[str] = set()
        self._automation_release_keys = {
            "w", "s", "a", "d", "q", "e", "l", "c", "up", "3", "enter", "\\"
        }

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
                if allow_missing_window:
                    _log.info("尚无串流窗口，将由重连状态机启动并绑定")
                    break
                sleep(1)

            self._hotkey_thread: threading.Thread | None = None
            logical_cpus = os.cpu_count() or 1
            ocr_threads = 1 if logical_cpus <= 8 else 2
            _log.info("正在加载 OCR 识别引擎，请稍候...")
            # ONNX Runtime is the heaviest child-process import. Loading it
            # here lets the live logger report progress before that work.
            try:
                from module.rapidocr_onnxruntime import RapidOCR
            except ModuleNotFoundError as exc:
                missing = exc.name or "未知模块"
                raise RuntimeError(
                    f"OCR 运行依赖缺失：{missing}。请在项目 Python 环境执行 "
                    "python -m pip install -r requirements.txt；可先运行 main.py --diagnostics 检查。"
                ) from exc

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
            self.ocrmodels = {"zh": self.ocrmodel}
            if self.ui_language_mode in {"auto", "ja"}:
                japanese_model_path = (
                    Path(__file__).resolve().parent
                    / "rapidocr_onnxruntime"
                    / "models"
                    / "japan_PP-OCRv4_rec_infer.onnx"
                )
                if not japanese_model_path.is_file():
                    raise FileNotFoundError(
                        f"日文 OCR 模型缺失: {japanese_model_path}"
                    )
                self.ocrmodels["ja"] = RapidOCR(
                    use_det=False,
                    use_cls=False,
                    use_rec=True,
                    rec_model_path=str(japanese_model_path),
                    intra_op_num_threads=ocr_threads,
                    inter_op_num_threads=1,
                )
            _log.info(
                "OCR 配置 | 仅预加载文字识别模型 | 逻辑处理器=%d | "
                "计算线程=%d | 调度线程=1 | 空闲自旋=关闭 | 界面语言=%s",
                logical_cpus,
                ocr_threads,
                {"auto": "自动识别", "zh": "简体中文", "ja": "日文"}[
                    self.ui_language_mode
                ],
            )
            self._ocr_lock = threading.Lock()
            self._stop_event = threading.Event()
            self._rect_thread: threading.Thread | None = None
            self._start_rect_watchdog()
            if self.background_mode and self._target_hwnd is not None:
                self._init_virtual_gamepad()
                self._start_background_capture()
            elif self.background_mode:
                self._init_virtual_gamepad()
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
        if ctypes.windll.shell32.IsUserAnAdmin():
            return

        # Keep the legacy/controller entry point diagnosable too. The unified
        # GUI normally handles elevation first, but direct CLI launches can
        # still reach this method.
        executable_path = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()
        parameters = subprocess.list2cmdline(
            sys.argv[1:] if getattr(sys, "frozen", False) else [str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]
        )
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(executable_path),
            parameters,
            str(executable_path.parent),
            1,
        )
        if result <= 32:
            try:
                last_error = ctypes.get_last_error()
            except (AttributeError, OSError):
                last_error = 0
            _log.error(
                "管理员提权失败 | ShellExecuteW=%s | GetLastError=%s | exe=%s | 参数=%s",
                result,
                last_error,
                executable_path,
                parameters or "<无>",
            )
            raise RuntimeError(
                f"无法以管理员身份启动控制器（ShellExecuteW={result}, GetLastError={last_error}）"
            )
        sys.exit(0)

    def _start_background_capture(
        self, hwnd: int | None = None, *, force: bool = False
    ) -> None:
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
            if (
                not force
                and self._capture_hwnd == hwnd
                and self._capture_control is not None
            ):
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
            nonlocal first_frame_logged
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
                if not first_frame_logged:
                    first_frame_logged = True
                    sample = pixels[:, :, :3]
                    _log.info(
                        "后台捕获首帧 | hwnd=%s | shape=%s | mean=%.1f | std=%.1f",
                        hwnd,
                        tuple(pixels.shape),
                        float(np.mean(sample)),
                        float(np.std(sample)),
                    )
                with self._capture_lock:
                    self._latest_capture = pixels
                    self._capture_last_copy = now
                    self._capture_frame_serial += 1
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

        first_frame_logged = False
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
        process_id, executable = self.stream_window_process_info()
        _log.info(
            "后台窗口捕获已绑定 hwnd=%s | owner_pid=%s | owner_process=%s",
            hwnd,
            process_id,
            executable or "未知",
        )
        if not self._capture_ready.wait(timeout=5):
            _log.warning("后台窗口捕获尚未收到首帧，Chiaki 可能被最小化或暂停渲染")

    def capture_frame_state(self) -> tuple[int, float]:
        """Return the latest copied-frame serial and monotonic timestamp."""
        with self._capture_lock:
            return self._capture_frame_serial, self._capture_last_copy

    def wait_for_fresh_capture(
        self, previous_serial: int, timeout: float = 3.0
    ) -> bool:
        """Wait until Windows Graphics Capture supplies a newer stream frame."""
        if not self.background_mode:
            sleep(min(max(timeout, 0.0), 0.75))
            return True
        deadline = monotonic() + max(0.1, timeout)
        while monotonic() < deadline and not self._stop_event.is_set():
            serial, _ = self.capture_frame_state()
            if serial > previous_serial:
                return True
            sleep(min(0.05, max(0.0, deadline - monotonic())))
        return False


    def screenshot_text(self, text):
        if self.text2region is None or text not in self.text2region.keys():
            return self.screenshot()
        return crop_normalized_relative_region(
            self.screenshot(), self.text2region[text]
        )

    def recognition_geometry_state(self) -> dict[str, object]:
        """Return the current capture geometry for diagnostics and DEBUG logs."""
        rect = self.window_rect
        return {
            "actual_window_rect": rect,
            "actual_client_size": None if rect is None else tuple(rect[2:]),
            "selected_profile": self.recognition_profile,
            "geometry_generation": self.geometry_generation,
            "resize_settling": (
                bool(self._geometry_changed_at)
                and monotonic() - self._geometry_changed_at < RECOGNITION_RESIZE_SETTLE_SECONDS
            ),
        }

    def recognition_resize_settling(self) -> bool:
        """Avoid input while Chiaki is between two client-area sizes."""
        changed_at = getattr(self, "_geometry_changed_at", 0.0)
        return bool(changed_at) and monotonic() - changed_at < RECOGNITION_RESIZE_SETTLE_SECONDS

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
                        "Chiaki 窗口已最小化；已暂停画面判断，恢复窗口后会自动重新绑定"
                    )
                    self._capture_warned = True
                raise RuntimeError("Chiaki stream window is temporarily minimized")
            _log.warning("Chiaki stream window is minimized; restore it before starting")
            self.focus_window()

        if self.background_mode:
            with self._capture_lock:
                # The callback assigns a new immutable NumPy array; retaining
                # its reference is safe. Only the requested crop is copied.
                pixels = self._latest_capture
            if pixels is None:
                raise RuntimeError("后台窗口捕获尚未提供画面")
            if pixels.ndim != 3 or pixels.shape[2] < 3:
                raise RuntimeError("后台窗口捕获返回了无效画面")

            # Windows Graphics Capture returns BGRA; RapidOCR receives RGB.
            full_frame = Image.fromarray(pixels[:, :, :3][:, :, ::-1].copy(), mode="RGB")
            full_frame = self._background_capture_client_frame(full_frame)
        else:
            left, top, width, height = self.window_rect
            full_frame = ImageGrab.grab(
                bbox=(left, top, left + width, top + height),
                all_screens=True,
            ).convert("RGB")

        normalized, metadata = normalize_recognition_frame(
            full_frame, self.recognition_profile
        )
        self._last_recognition_metadata = metadata
        if region is None:
            return normalized
        left, top, width, height = self.window_rect
        r_left, r_top, r_w, r_h = region
        x0 = int(normalized.width * max(0.0, (r_left - left) / max(1, width)))
        y0 = int(normalized.height * max(0.0, (r_top - top) / max(1, height)))
        x1 = int(normalized.width * min(1.0, (r_left - left + r_w) / max(1, width)))
        y1 = int(normalized.height * min(1.0, (r_top - top + r_h) / max(1, height)))
        return normalized.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)))

    def _background_capture_client_frame(self, frame: Image.Image) -> Image.Image:
        """Align a HWND capture with the client coordinates used elsewhere."""
        hwnd = self._get_hwnd()
        if hwnd is None:
            return frame
        try:
            outer = win32gui.GetWindowRect(hwnd)
            client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
            client_right, client_bottom = win32gui.ClientToScreen(
                hwnd, win32gui.GetClientRect(hwnd)[2:]
            )
            cropped = crop_window_capture_to_client_area(
                frame,
                tuple(int(value) for value in outer),
                (client_left, client_top, client_right, client_bottom),
            )
            if cropped.size != frame.size:
                _log.debug("后台窗口捕获已裁切客户区 | 原始=%s | 客户区=%s", frame.size, cropped.size)
            return cropped
        except Exception:
            _log.debug("后台窗口捕获客户区裁切失败，保留原始帧", exc_info=True)
            return frame

    def get_window_rect(self, silent: bool = False):
        hwnd = self._get_hwnd()
        if hwnd is None:
            if not silent:
                _log.warning("未找到窗口: '%s'", self.target_window)
            return None
        if win32gui.IsIconic(hwnd):
            self._window_was_iconic = True
            if not silent:
                _log.debug("窗口最小化，沿用上次有效矩形: %s", self.window_rect)
            return self.window_rect

        if self._window_was_iconic:
            self._window_was_iconic = False
            self._capture_warned = False
            if self.background_mode:
                _log.info("Chiaki 窗口已恢复，正在重新绑定后台画面捕获")
                try:
                    self._start_background_capture(hwnd, force=True)
                except Exception:
                    _log.warning("Chiaki 窗口恢复后的捕获重绑失败，将继续重试", exc_info=True)

        c_left, c_top, c_right, c_bottom = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (c_left, c_top))
        right, bottom = win32gui.ClientToScreen(hwnd, (c_right, c_bottom))
        width = right - left
        height = bottom - top
        new_rect = (left, top, width, height)
        previous_rect = self.window_rect
        previous_size = None if previous_rect is None else tuple(previous_rect[2:])
        current_size = (width, height)
        # Moving Chiaki changes the screen-space origin but does not change
        # the normalized recognition canvas. Do not refresh the settling
        # fence for position-only changes.
        size_changed = previous_size is not None and previous_size != current_size
        first_geometry = previous_rect is None
        if new_rect != previous_rect:
            self.geometry_generation += 1
            if size_changed:
                self._geometry_changed_at = monotonic()
                _log.info(
                    "Chiaki 客户区尺寸变化 | %s -> %s | 识别暂停 %.1f 秒后恢复",
                    previous_size,
                    current_size,
                    RECOGNITION_RESIZE_SETTLE_SECONDS,
                )
            elif first_geometry:
                _log.debug("Chiaki 客户区已绑定 | 尺寸=%s | 位置=(%s,%s)", current_size, left, top)
            else:
                _log.debug("Chiaki 客户区位置变化 | %s -> (%s,%s) | 尺寸保持=%s", tuple(previous_rect[:2]), left, top, current_size)
        self.window_rect = new_rect
        return new_rect

    def resize_client_area(self, width: int, height: int) -> bool:
        """Resize the bound Chiaki client area while keeping its screen position."""
        if width <= 0 or height <= 0:
            raise ValueError("Chiaki 客户区尺寸必须为正数")
        hwnd = self._get_hwnd()
        if hwnd is None or win32gui.IsIconic(hwnd):
            return False
        window_rect = win32gui.GetWindowRect(hwnd)
        client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        adjusted = adjust_window_rect_ex(
            (0, 0, int(width), int(height)), style, False, ex_style
        )
        outer_width = adjusted[2] - adjusted[0]
        outer_height = adjusted[3] - adjusted[1]
        outer_left = client_left + adjusted[0]
        outer_top = client_top + adjusted[1]
        flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
        win32gui.SetWindowPos(
            hwnd, 0, outer_left, outer_top, outer_width, outer_height, flags
        )
        self.window_rect = None
        self.get_window_rect(silent=True)
        _log.info(
            "Chiaki 客户区已恢复为 %sx%s（窗口外框 %sx%s，原窗口 %s）",
            width, height, outer_width, outer_height, window_rect,
        )
        return self.window_rect is not None and self.window_rect[2:] == (width, height)

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
        self.release_automation_inputs()
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
            caption = win32gui.GetWindowText(hwnd).strip()
            if stream_caption_matches_target(self.target_window, caption):
                if (
                    self.background_mode
                    and self._capture_hwnd != hwnd
                    and not win32gui.IsIconic(hwnd)
                ):
                    try:
                        self._start_background_capture(hwnd)
                    except Exception:
                        _log.warning("恢复 Chiaki 后台窗口捕获失败，将继续重试", exc_info=True)
                return hwnd
            _log.warning(
                "缓存的 Chiaki 窗口标题已变化：hwnd=%s，标题='%s'，配置='%s'；重新搜索",
                hwnd,
                caption or "<无标题>",
                self.target_window,
            )
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
            if (
                self.background_mode
                and self._capture_hwnd != hwnd
                and not win32gui.IsIconic(hwnd)
            ):
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

    def set_expected_process_id(self, process_id: int | None) -> None:
        """Restrict window rebinding to a known Chiaki process after recovery."""
        self._expected_process_id = int(process_id) if process_id else None
        self._target_hwnd = None
        self.window_rect = None

    def target_process_id(self) -> int | None:
        """Return the process owning the currently bound stream window."""
        hwnd = self._target_hwnd
        if hwnd is None or not win32gui.IsWindow(hwnd):
            hwnd = self._get_hwnd()
        if hwnd is None:
            return None
        try:
            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            return int(process_id)
        except Exception:
            return None

    def stream_window_process_info(self) -> tuple[int | None, str]:
        """Return the bound stream window PID and executable basename."""
        hwnd = self._target_hwnd
        if hwnd is None or not win32gui.IsWindow(hwnd):
            return None, ""
        try:
            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            process_id = int(process_id)
        except Exception:
            return None, ""
        try:
            handle = win32api.OpenProcess(
                0x1000 | 0x0010,  # QUERY_LIMITED_INFORMATION | VM_READ
                False,
                process_id,
            )
            if not handle:
                return process_id, ""
            try:
                path = win32process.GetModuleFileNameEx(handle, 0)
            finally:
                win32api.CloseHandle(handle)
            return process_id, os.path.basename(path).lower()
        except Exception:
            return process_id, ""

    def stream_binding_is_valid(self) -> bool:
        """Confirm that the current capture target is still a Chiaki window."""
        hwnd = self._target_hwnd
        if hwnd is None or not win32gui.IsWindow(hwnd):
            return False
        caption = win32gui.GetWindowText(hwnd).strip()
        if not stream_caption_matches_target(self.target_window, caption):
            _log.warning(
                "当前捕获窗口标题不符合配置：hwnd=%s，标题='%s'，配置='%s'；拒绝继续识别",
                hwnd,
                caption or "<无标题>",
                self.target_window,
            )
            return False
        process_id, executable = self.stream_window_process_info()
        if process_id is None:
            return False
        if (
            self._expected_process_id is not None
            and process_id != self._expected_process_id
        ):
            _log.warning(
                "串流窗口进程不匹配：绑定 PID=%s，预期 PID=%s",
                process_id,
                self._expected_process_id,
            )
            return False
        if executable and not executable.startswith("chiaki"):
            _log.warning(
                "串流窗口不是 Chiaki 进程：PID=%s，进程=%s",
                process_id,
                executable,
            )
            return False
        if self._expected_process_id is None and not executable:
            _log.warning("无法确认串流窗口所属进程，拒绝继续使用当前捕获")
            return False
        return True

    @staticmethod
    def process_is_alive(process_id: int | None) -> bool:
        """Check one process without enumerating or affecting other Chiaki PIDs."""
        if not process_id:
            return False
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                int(process_id),
            )
            if not handle:
                return False
            try:
                return win32process.GetExitCodeProcess(handle) == win32con.STILL_ACTIVE
            finally:
                win32api.CloseHandle(handle)
        except Exception:
            return False

    def expected_process_has_window(self, title_fragment: str) -> bool:
        """Detect an error/dialog window owned by the current reconnect PID."""
        process_id = self._expected_process_id
        if not process_id:
            return False
        found = False

        def callback(hwnd: int, _: object) -> bool:
            nonlocal found
            try:
                _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
                if int(owner_pid) != process_id:
                    return True
                title = win32gui.GetWindowText(hwnd)
                if title_fragment.lower() in title.lower():
                    found = True
                    return False
            except Exception:
                return True
            return True

        try:
            win32gui.EnumWindows(callback, None)
        except Exception:
            pass
        return found

    def close_bound_stream_process(self, timeout: float = 8.0) -> int | None:
        """Close only the process owning this automation's current stream."""
        process_id = self.target_process_id() or self._expected_process_id
        if process_id is None:
            return None
        hwnd = self._target_hwnd
        if hwnd is not None and win32gui.IsWindow(hwnd):
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                _log.debug("请求关闭 Chiaki 串流窗口失败", exc_info=True)
        deadline = monotonic() + max(0.5, timeout)
        while monotonic() < deadline:
            try:
                handle = win32api.OpenProcess(
                    win32con.PROCESS_QUERY_INFORMATION,
                    False,
                    process_id,
                )
                if handle:
                    win32api.CloseHandle(handle)
                    sleep(0.2)
                    continue
            except Exception:
                pass
            break

        # A stuck decoder can ignore WM_CLOSE. Terminate only the PID that owns
        # the bound stream, never every process named chiaki.exe.
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_TERMINATE | win32con.PROCESS_QUERY_INFORMATION,
                False,
                process_id,
            )
            if handle:
                win32api.TerminateProcess(handle, 1)
                win32api.CloseHandle(handle)
                _log.warning("Chiaki 串流进程无响应，已结束绑定 PID=%d", process_id)
        except Exception:
            _log.debug("结束 Chiaki 绑定进程失败", exc_info=True)

        self._expected_process_id = None
        self._target_hwnd = None
        self.window_rect = None
        self._capture_hwnd = None
        with self._capture_lock:
            self._latest_capture = None
        return process_id

    def _find_window(self, title: str) -> int | None:
        """Find only the visible window whose caption matches the configuration.

        The Chiaki main window belongs to the same process and can be large
        enough to look like a stream surface. Process name, title keywords,
        and window size are therefore not allowed to replace the configured
        title. When a recovery PID is known, only windows from that PID are
        considered in addition to the title match.
        """
        if not str(title or "").strip():
            _log.warning("未配置 Chiaki 窗口标题，拒绝自动绑定")
            return None
        exact: list[int] = []
        candidates: list[tuple[int, int, int, str]] = []

        def process_name(process_id: int) -> str:
            try:
                handle = win32api.OpenProcess(
                    0x1000 | 0x0010,  # QUERY_LIMITED_INFORMATION | VM_READ
                    False,
                    process_id,
                )
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

        def callback(hwnd: int, _: object) -> bool:
            if win32gui.IsWindowVisible(hwnd):
                text: str = win32gui.GetWindowText(hwnd)
                if not text.strip():
                    return True
                try:
                    _, process_id = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    return True
                process_id = int(process_id)
                if (
                    self._expected_process_id is not None
                    and process_id != self._expected_process_id
                ):
                    return True
                lowered = text.lower()
                # A stale title saved by the GUI may be the unified control
                # panel itself. Never bind automation to that window; it is
                # not the Chiaki stream surface.
                if (
                    "gbfr 自动重战" in text
                    or "gbfr autorebattle" in lowered
                    or "控制台" in text
                ):
                    return True
                title_is_chiaki = "chiaki" in lowered
                title_is_stream = "stream" in lowered or "串流" in text
                process_is_chiaki = process_name(process_id).startswith("chiaki")
                # Never bind based on caption text alone. A browser tab can
                # contain "Stream"/"串流" and previously caused the control
                # panel to attach to Edge instead of launching Chiaki. The
                # recovery PID is trusted because this process launched it.
                trusted_process = (
                    process_is_chiaki
                    or self._expected_process_id is not None
                )
                if title and title.lower() in lowered and trusted_process:
                    exact.append(hwnd)
                    return True

                # The Chiaki launcher/main window is also a large window owned
                # by chiaki.exe. Never fall back to process name and window
                # size: the configured title is the only authoritative target.
                if not stream_caption_matches_target(self.target_window, text):
                    return True

                excluded_title = any(
                    marker in lowered
                    for marker in (
                        "settings",
                        "configuration",
                        "register",
                        "registration",
                        "设置",
                        "注册",
                    )
                )
                if excluded_title:
                    return True
                if not trusted_process:
                    return True
                score = 0
                if title_is_stream:
                    score += 60
                if title_is_chiaki:
                    score += 35
                if process_is_chiaki:
                    score += 45
                if self._expected_process_id is not None:
                    # A CLI-launched Chiaki stream process normally owns only
                    # one visible window, even when its title is customized.
                    score += 40
                try:
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                    area = max(0, right - left) * max(0, bottom - top)
                except Exception:
                    area = 0
                if process_is_chiaki and area >= 400_000:
                    # A custom build may remove both "Chiaki" and "Stream"
                    # from its title. Prefer its large stream surface over a
                    # small launcher/settings window in that case.
                    score += 25
                if score >= 60:
                    candidates.append((score, area, hwnd, text))
            return True

        win32gui.EnumWindows(callback, None)
        if exact:
            return exact[0]
        if not candidates:
            return None
        _, _, hwnd, detected_title = max(
            candidates, key=lambda item: (item[0], item[1])
        )
        if hwnd not in self._window_detection_logged:
            self._window_detection_logged.add(hwnd)
            _log.info(
                "已找到 Chiaki 串流窗口（标题与配置不同）：'%s' (hwnd=%s)",
                detected_title,
                hwnd,
            )
        return hwnd

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
        """Whether the automation process should leave its main loop."""
        return self._shutdown_requested

    @property
    def shutdown_reason(self) -> str | None:
        """Return why the automation process was asked to shut down."""
        return self._shutdown_reason

    def activate_automation(self, source: str = "启动命令") -> None:
        """Enter a clean running state through one shared, observable path."""
        self._paused = False
        self._running = True
        self.release_automation_inputs()
        _log.info(">> %s 已启动（来源：%s）", self.project_name, source)

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
                self._foreground_pressed_keys.clear()
                return
            except Exception:
                _log.warning("虚拟手柄归零失败", exc_info=True)

        try:
            hwnd = self._get_hwnd()
        except Exception:
            hwnd = None
            _log.debug("释放输入时目标窗口查询失败", exc_info=True)
        foreground_keys = set(self._foreground_pressed_keys)
        for key in (
            foreground_keys
            if not self.background_mode
            else self._automation_release_keys
        ):
            vk = self.KEY_MAP.get(key)
            if vk is None:
                continue
            try:
                if self.background_mode and hwnd is not None:
                    self._post_key(hwnd, vk, keyup=True)
                elif not self.background_mode and key in foreground_keys:
                    self._send_key(vk, keyup=True)
            except Exception:
                _log.debug("释放自动化按键 %s 失败", key, exc_info=True)
        self._foreground_pressed_keys.clear()

    def set_automation_release_keys(self, keys) -> None:
        """Set foreground keys that must be released on pause/stop/result."""
        valid = {str(key).lower() for key in keys if str(key).lower() in self.KEY_MAP}
        if valid:
            self._automation_release_keys = valid

    def request_shutdown(self, reason: str = "manual") -> None:
        """Stop automation and record the action that requested process exit."""
        self._shutdown_requested = True
        self._shutdown_reason = str(reason or "manual")
        self._paused = False
        self._running = False
        self.release_automation_inputs()

    def set_battle_start_key(self, key: str) -> None:
        """设置战斗开始快捷键（自动注册热键）

        :param key: 按键名 (如 'f1', 'f2')
        """

        def _on_start() -> None:
            self.activate_automation(f"快捷键 {key.upper()}")
            self.show_toast(self.project_name, "已启动")

        _log.info("按 %s 启动", key)
        self._register_hotkey(key, _on_start)

    def set_battle_stop_key(self, key: str) -> None:
        """设置战斗停止快捷键（自动注册热键）

        :param key: 按键名 (如 'f1', 'f2')
        """

        def _on_stop() -> None:
            # F2 is a local automation stop.  Chiaki remains open so the user
            # can inspect the current game state or start the run again.
            self.request_shutdown("manual_hotkey")
            _log.info("<< %s 已停止 按启动键重新开始", self.project_name)
            self.show_toast(self.project_name, "已停止，按启动键重新开始")

        _log.info("按 %s 停止", key)
        self._register_hotkey(key, _on_stop)

    def set_close_chiaki_key(
        self, key: str = "f5", modifiers: tuple[str, ...] = ("ctrl", "shift")
    ) -> None:
        """Register a distinct modifier-combination that also closes Chiaki."""

        def _on_close() -> None:
            self.request_shutdown("close_chiaki_hotkey")
            _log.info("<< %s 已停止并请求关闭 Chiaki", self.project_name)
            self.show_toast(self.project_name, "已停止并关闭 Chiaki（组合键）")

        self._register_hotkey_combo(key, modifiers, _on_close)
        combo_text = "+".join((*modifiers, key)).upper()
        _log.info("按 %s 停止并关闭 Chiaki", combo_text)

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

    def set_window_recapture_key(self, key: str = "f4") -> None:
        """Register a manual window recapture command for the control panel."""

        def _on_recapture() -> None:
            previous = self._target_hwnd
            if self._expected_process_id and not self.process_is_alive(
                self._expected_process_id
            ):
                self._expected_process_id = None
            self._target_hwnd = None
            self.window_rect = None
            hwnd = self._find_window(self.target_window)
            if hwnd is None:
                _log.warning(
                    "手动捕获失败：未找到 Chiaki 串流窗口；可检查窗口是否已打开"
                )
                return
            self._target_hwnd = hwnd
            self._hwnd_warned = False
            if self.background_mode and not win32gui.IsIconic(hwnd):
                try:
                    self._start_background_capture(hwnd, force=True)
                except Exception:
                    _log.warning("手动捕获后重绑后台画面失败", exc_info=True)
                    return
            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            _log.info(
                "手动捕获成功：Chiaki 串流窗口 hwnd=%s, PID=%s%s",
                hwnd,
                process_id,
                "（已替换旧窗口）" if previous and previous != hwnd else "",
            )

        _log.info("按 %s 重新捕获 Chiaki 串流窗口", key)
        self._register_hotkey(key, _on_recapture)

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

    def _register_hotkey_combo(
        self, key: str, modifiers: tuple[str, ...], callback: callable
    ) -> None:
        """Register a key that fires only while every modifier is held."""
        key_vk = self.KEY_MAP.get(key.lower())
        if key_vk is None:
            raise ValueError(f"未知的热键名: '{key}'")
        modifier_vks: list[int] = []
        for modifier in modifiers:
            modifier_vk = self.KEY_MAP.get(str(modifier).lower())
            if modifier_vk is None:
                raise ValueError(f"未知的组合键修饰键: '{modifier}'")
            if modifier_vk not in modifier_vks:
                modifier_vks.append(modifier_vk)
        if not modifier_vks:
            raise ValueError("组合键至少需要一个修饰键")
        combo = (key_vk, tuple(sorted(modifier_vks)))
        self._hotkey_combos[combo] = callback
        _log.debug(
            "注册组合热键: key=0x%02X modifiers=%s",
            key_vk,
            ",".join(f"0x{vk:02X}" for vk in combo[1]),
        )
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
        prev: dict[tuple[object, ...], bool] = {}
        while True:
            for vk, cb in list(self._hotkeys.items()):
                pressed = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
                event_key = ("key", vk)
                if pressed and not prev.get(event_key, False):
                    _log.debug("热键触发: 0x%02X", vk)
                    cb()
                prev[event_key] = pressed
            for (key_vk, modifier_vks), cb in list(self._hotkey_combos.items()):
                pressed = bool(
                    ctypes.windll.user32.GetAsyncKeyState(key_vk) & 0x8000
                ) and all(
                    bool(ctypes.windll.user32.GetAsyncKeyState(modifier_vk) & 0x8000)
                    for modifier_vk in modifier_vks
                )
                event_key = ("combo", key_vk, *modifier_vks)
                if pressed and not prev.get(event_key, False):
                    _log.debug(
                        "组合热键触发: key=0x%02X modifiers=%s",
                        key_vk,
                        ",".join(f"0x{vk:02X}" for vk in modifier_vks),
                    )
                    cb()
                prev[event_key] = pressed
            sleep(0.05)

    def ui_language_candidates(self) -> tuple[str, ...]:
        """Return the OCR languages that should be tried for the current run."""
        if self.detected_ui_language is not None:
            return (self.detected_ui_language,)
        return ("zh", "ja")

    def confirm_ui_language(self, language: str, evidence: str = "") -> None:
        """Lock automatic language detection after a language-specific marker."""
        if self.ui_language_mode != "auto" or language not in {"zh", "ja"}:
            return
        with self._ui_language_lock:
            if self.detected_ui_language is not None:
                return
            self.detected_ui_language = language
        label = "简体中文" if language == "zh" else "日文"
        suffix = f"（依据：{evidence}）" if evidence else ""
        _log.info("界面语言已识别：%s%s", label, suffix)

    def _ocr_model_for(self, language: str | None):
        selected = language or self.detected_ui_language or "zh"
        model = self.ocrmodels.get(selected)
        if model is None:
            raise RuntimeError(f"OCR language model is unavailable: {selected}")
        return model, selected

    def ocr(self, pic: Image, confidence=0.6, language: str | None = None):
        # RapidOCR can be called by multiple phase checks. Serialize inference
        # so concurrent frame checks cannot corrupt or stall the ONNX session.
        source_size = getattr(pic, "size", None)
        pic = prepare_ocr_image(pic, self.recognition_profile)
        source_width, source_height = source_size or pic.size
        prepared_width, prepared_height = pic.size
        coordinate_scale_x = source_width / max(1, prepared_width)
        coordinate_scale_y = source_height / max(1, prepared_height)
        model, selected_language = self._ocr_model_for(language)
        with self._ocr_lock:
            result = model(pic, use_det=True, use_cls=False)

        if result is None or result[0] is None or len(result[0]) == 0:
            _log.debug("OCR: 未识别到文本")
            return None

        ocr_result_list = []
        for item in result[0]:
            if item[2] > confidence:
                d = {
                    "text": item[1],
                    "location": (
                        max(0, int(item[0][0][0] * coordinate_scale_x) - 1),
                        max(0, int(item[0][0][1] * coordinate_scale_y) - 1),
                        min(source_width, int(item[0][2][0] * coordinate_scale_x) + 1),
                        min(source_height, int(item[0][2][1] * coordinate_scale_y) + 1),
                    ),
                }
                ocr_result_list.append(d)

        texts = [t["text"] for t in ocr_result_list]
        _log.debug(
            "OCR[%s]: %d 个文本 → %s",
            selected_language,
            len(ocr_result_list),
            texts,
        )
        return ocr_result_list

    def recognize_line(
        self,
        pic: Image,
        confidence: float = 0.65,
        language: str | None = None,
    ) -> str:
        """Recognize one fixed-position text line without running detection.

        GBFR's battle markers live in stable normalized regions.  Sending the
        already-cropped image directly to the recognition model avoids the
        expensive text detector while retaining OCR tolerance for resolution
        and stream-compression changes.
        """
        pic = prepare_ocr_image(pic, self.recognition_profile)
        model, selected_language = self._ocr_model_for(language)
        with self._ocr_lock:
            result = model(pic, use_det=False, use_cls=False)

        if result is None or result[0] is None or len(result[0]) == 0:
            return ""
        item = result[0][0]
        if len(item) < 2 or float(item[1]) < confidence:
            return ""
        text = str(item[0]).strip()
        _log.debug(
            "单行识别[%s]: '%s' (%.3f)",
            selected_language,
            text,
            float(item[1]),
        )
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
            # Background automation is controller-only. Suppressing this
            # legacy mouse protocol guarantees it never changes the desktop
            # cursor or injects a window click while the user works elsewhere.
            _log.warning("后台模式已忽略鼠标点击动作；自动重战仅使用虚拟手柄输入")
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
            "c": vg.DS4_BUTTONS.DS4_BUTTON_TRIANGLE,
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
    ) -> bool:
        """使用裸 ctypes SendInput 模拟键盘按键

        :param key:      按键名（同 KEY_MAP 中的键名）
        :param times:    连续按键次数
        :param interval: 每次按键之间的间隔（秒）
        :param movement: "press"仅按下 / "release"仅弹起 / "press_and_release"按下后弹起
        """
        # A release must always be allowed so pausing or a phase transition can
        # neutralize a key that was pressed just before the state changed.
        if self._paused and movement != "release":
            return False
        # Resizing Chiaki changes both the capture buffer and the desktop
        # coordinate mapping. The screenshot path adapts immediately, but a
        # short input fence prevents a pulse from landing on the transition
        # frame in either foreground or background mode.
        if movement != "release" and self.recognition_resize_settling():
            _log.debug("Chiaki 尺寸仍在变化，暂缓输入: '%s'", key)
            return False

        normalized = key.lower()
        vk = self.KEY_MAP.get(normalized)
        if vk is None:
            raise ValueError(f"未知的按键名: '{key}'，请检查 KEY_MAP")

        _log.debug("按键: '%s' (0x%02X) x%d", key, vk, times)
        try:
            hwnd = self._get_hwnd()
        except Exception:
            _log.warning("目标窗口查询异常，跳过按键 '%s'", key, exc_info=True)
            return False
        if self.background_mode and hwnd is None:
            _log.warning("后台按键目标窗口不可用，跳过按键 '%s'", key)
            return False
        if not self.background_mode:
            # Do not let SendInput leak into whichever application happens to
            # be foreground after Chiaki closes or its title changes. A release
            # of a key we already pressed is the only safe exception.
            if movement == "release" and normalized in self._foreground_pressed_keys:
                self._send_key(vk, keyup=True)
                self._foreground_pressed_keys.discard(normalized)
                return True
            if hwnd is None:
                _log.warning("前台按键目标窗口不可用，跳过按键 '%s'", key)
                return False
            if not self.focus_window():
                _log.warning("目标窗口未能获得焦点，跳过按键 '%s'", key)
                return False
        for _ in range(times):
            if not self.background_mode and movement != "release":
                # Revalidate between repeated pulses. Chiaki can be closed or
                # rebound while the first pulse is sleeping; never continue
                # with SendInput after that boundary.
                try:
                    if self._get_hwnd() is None or not self.focus_window():
                        _log.warning("重复按键期间目标窗口失效，停止按键 '%s'", key)
                        return False
                except Exception:
                    _log.warning("重复按键期间目标窗口查询失败，停止按键 '%s'", key, exc_info=True)
                    return False
            if movement != "release":
                if self.background_mode:
                    if not self._set_virtual_key(key, pressed=True):
                        self._post_key(hwnd, vk)
                else:
                    self._send_key(vk)
                    self._foreground_pressed_keys.add(normalized)
            sleep(0.2)
            if movement != "press":
                if self.background_mode:
                    if not self._set_virtual_key(key, pressed=False):
                        self._post_key(hwnd, vk, keyup=True)
                else:
                    self._send_key(vk, keyup=True)
                    self._foreground_pressed_keys.discard(normalized)
            sleep(interval)
        return True

    def start(self, func):
        while not self.shutdown_requested:
            if not self.running or self.paused:
                # 等待 F1 启动，或等待 F3 继续。
                sleep(0.1)
            else:
                func(self)
