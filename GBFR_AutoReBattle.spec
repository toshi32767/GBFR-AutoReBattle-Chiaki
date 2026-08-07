# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = ['skimage.measure', 'win32timezone']
hiddenimports += collect_submodules('module.rapidocr_onnxruntime')
vgamepad_datas = collect_data_files('vgamepad', includes=['win/vigem/**/*'])


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('module\\rapidocr_onnxruntime\\config.yaml', 'module\\rapidocr_onnxruntime'), ('module\\rapidocr_onnxruntime\\models', 'module\\rapidocr_onnxruntime\\models'), ('assets\\gbfr-crystal-icon.png', 'assets'), ('assets\\ability-qualified.wav', 'assets')] + vgamepad_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The local development Python has several very large AI/data packages.
    # GBFR only needs NumPy, ONNX Runtime, RapidOCR, Pillow, and the explicit
    # skimage.measure import above. Excluding the unrelated packages keeps the
    # portable package practical without changing runtime behavior.
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'transformers', 'tokenizers', 'sentencepiece',
        'pandas', 'scipy', 'sklearn', 'matplotlib',
        'numba', 'llvmlite', 'sympy',
        'pytest', 'pygments', 'nltk', 'jieba',
        'yt_dlp', 'websockets', 'Cryptodome', 'Crypto',
        'sqlalchemy', 'grpc', 'pydantic', 'rich',
    ],
    noarchive=False,
    optimize=0,
)
# RapidOCR imports the project's lightweight ``cv2.py`` compatibility shim,
# but the wheel's optional video-reader DLLs are not used by this application.
# Keep the shim and all required native libraries while dropping only these
# redundant OpenCV FFmpeg binaries from portable releases.
a.binaries = [
    entry
    for entry in a.binaries
    if not entry[0].lower().startswith("cv2\\opencv_videoio_ffmpeg")
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GBFR_AutoReBattle',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    uac_admin=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\gbfr-crystal-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='GBFR_AutoReBattle',
)
