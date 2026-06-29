# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WhisperQuiet — the self-contained, distributable .app.

Why PyInstaller and not py2app: this app's heaviest dependency, mlx, is a PEP-420
namespace package (no __init__.py) shipping a compiled extension (mlx.core.so), a
sibling native lib (mlx/lib/libmlx.dylib) and a Metal shader archive
(mlx/lib/mlx.metallib). py2app's legacy imp.find_module shim can't even locate a
namespace package on Python 3.12+, and it does not do the Mach-O dependency
analysis / rpath rewriting these native libs need. PyInstaller does both.

Build via scripts/build_app.sh (provisions a clean dictation-only build venv):
    bash scripts/build_app.sh        # -> dist/WhisperQuiet.app

Apple Silicon only (mlx). The ~1.6 GB whisper model is NOT bundled — it downloads
to ~/.cache on first run.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

# build_app.sh runs PyInstaller from the repo root, so cwd is the repo root.
REPO = os.path.abspath(os.getcwd())
sys.path.insert(0, REPO)

datas = []
binaries = []
hiddenimports = []

# mlx + mlx_whisper have no PyInstaller hooks: pull their modules, native libs
# (libmlx.dylib), and data (mlx.metallib, mel_filters.npz, *.tiktoken) wholesale.
# numba / llvmlite / scipy / numpy / sounddevice / tiktoken / huggingface_hub all
# have hooks in pyinstaller-hooks-contrib, so import tracing + hooks cover them.
# Note on two benign build warnings from this stack:
#   - "Library not found: @rpath/libomp.dylib" (numba's omppool.so)
#   - "Hidden import scipy.special._cdflib not found"
# Both are off the default decode path. mlx_whisper.transcribe imports numba +
# scipy.signal at module load, but the numba @jit DTW kernels (which need libomp)
# and scipy.special only run when word_timestamps=True — which whisperquiet never
# enables. The smoke-test warm-up decode confirms the default path runs without
# them. (If word timestamps are ever turned on, libomp must be collected.)
for pkg in ("mlx", "mlx_whisper"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# sounddevice loads its bundled PortAudio LAZILY, inside an `except OSError`
# branch that static analysis can't see: when find_library('portaudio') returns
# None (it does on a stock Mac), it runs `import _sounddevice_data` and dlopens
# _sounddevice_data/portaudio-binaries/libportaudio.dylib. So the data package
# (module + dylib) must be force-collected, along with the cffi backend used to
# dlopen it — none of which import-tracing from launch.py would reach.
sd_d, sd_b, sd_h = collect_all("_sounddevice_data")
datas += sd_d
binaries += sd_b
hiddenimports += sd_h

# Live dictation modules + the lazily-loaded mic/cffi machinery, listed so the
# build can't silently drop them (each is a real runtime import the static graph
# either can't see or a refactor could make conditional).
hiddenimports += [
    "sounddevice",
    "_sounddevice_data",
    "cffi",
    "_cffi_backend",
    "whisperquiet.incremental",
    "whisperquiet.transcribe",
    "whisperquiet.vad",
]

a = Analysis(
    # Absolute: PyInstaller resolves a relative script path against the SPEC
    # file's dir (packaging/), which would double to packaging/packaging/.
    [os.path.join(REPO, "packaging", "launch.py")],
    pathex=[REPO],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Camera stack + dev/optional extras are deliberately not in the bundle. The
    # whisperquiet.vision/control modules ride along but their mediapipe/cv2
    # imports are deferred and guarded, so excluding these is safe.
    excludes=[
        "mediapipe",
        "cv2",
        "torch",
        "torchvision",
        "tensorflow",
        "matplotlib",
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
        "parakeet_mlx",
        "mlx_lm",
        "noisereduce",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WhisperQuiet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # menu-bar / agent app — no terminal window
    argv_emulation=False,  # MUST stay off: its Carbon event loop fights our CGEventTap
    target_arch="arm64",
    codesign_identity=None,  # signing handled by scripts/build_app.sh
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WhisperQuiet",
)

app = BUNDLE(
    coll,
    name="WhisperQuiet.app",
    icon=os.path.join(REPO, "packaging", "whisperquiet.icns"),
    bundle_identifier="com.yacine.whisperquiet",
    info_plist={
        "CFBundleName": "WhisperQuiet",
        "CFBundleDisplayName": "WhisperQuiet",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSUIElement": True,  # no Dock icon / app-switcher entry
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.5",  # mlx needs a recent Metal
        "NSMicrophoneUsageDescription":
            "WhisperQuiet transcribes your speech on-device. No audio ever leaves your Mac.",
    },
)
