# PyInstaller spec for Retrozashi.exe (one-folder build).
# Build:  pyinstaller packaging/Retrozashi.spec --noconfirm --clean
# Output: dist/Retrozashi/Retrozashi.exe
#
# One-folder rather than one-file: starts in ~1 s instead of unpacking ~400 MB to %TEMP%
# on every launch, and antivirus is far less suspicious of it.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

hiddenimports = ["keyring.backends.Windows"]
datas = []
binaries = []


def optional(package, data=False, libs=False, submodules=False):
    """Bundle an optional extra only if it's installed in the build environment."""
    global hiddenimports, datas, binaries
    try:
        __import__(package)
    except Exception:
        print(f"[retrozashi.spec] {package} not installed: skipping")
        return
    if data:
        datas += collect_data_files(package)
    if libs:
        binaries += collect_dynamic_libs(package)
    if submodules:
        hiddenimports += collect_submodules(package)


# Voice: faster-whisper ships the Silero VAD model as package data; ctranslate2, onnxruntime
# and PyAV (imported by faster_whisper) carry native DLLs; sounddevice bundles PortAudio.
optional("faster_whisper", data=True)
optional("ctranslate2", libs=True, submodules=True)
optional("onnxruntime", libs=True)
optional("av", libs=True, submodules=True)
optional("_sounddevice_data", data=True)
# UI Automation: pywinauto imports its backends dynamically; comtypes generates COM wrappers.
optional("pywinauto", submodules=True)
optional("comtypes", submodules=True)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[os.path.join(SPECPATH, "..", "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        # Never imported; keep the bundle lean.
        "tkinter", "matplotlib", "IPython", "pytest", "torch",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Retrozashi",
    console=False,          # windowed app; logs go to %APPDATA%\Retrozashi\retrozashi.log
    uac_admin=False,        # never elevated: elevation would trip UIPI rules for SendInput
    upx=False,              # UPX-packed exes are a classic antivirus false positive
)
coll = COLLECT(exe, a.binaries, a.datas, name="Retrozashi", upx=False)
