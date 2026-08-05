"""Minimal OpenCV compatibility shim used by windows-capture.

``windows-capture`` imports ``cv2`` only for its optional Frame.save_as_image
helper. The automation never calls that helper, so shipping the full OpenCV
wheel would add about 150 MB for no runtime benefit. Keep a small ``imwrite``
implementation for compatibility and use Pillow for the rare debug save path.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def __collect_extra_submodules(*_args: object, **_kwargs: object) -> list[str]:
    """Satisfy Nuitka's optional OpenCV module discovery hook."""
    return []


def imwrite(filename: str | Path, image: np.ndarray) -> bool:
    """Write a BGR/BGRA/RGB image and match OpenCV's boolean result contract."""
    try:
        pixels = np.asarray(image)
        if pixels.ndim == 2:
            output = Image.fromarray(pixels)
        elif pixels.ndim == 3 and pixels.shape[2] == 3:
            output = Image.fromarray(pixels[:, :, ::-1], mode="RGB")
        elif pixels.ndim == 3 and pixels.shape[2] == 4:
            output = Image.fromarray(pixels[:, :, [2, 1, 0, 3]], mode="RGBA")
        else:
            return False
        output.save(str(filename))
        return True
    except Exception:
        return False
