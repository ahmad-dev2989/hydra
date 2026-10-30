# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve()

datas = [
    (str(PROJECT_ROOT / "index.html"), "."),
    (str(PROJECT_ROOT / "assets"), "assets"),
    (str(PROJECT_ROOT / "plugins"), "plugins"),
    (str(PROJECT_ROOT / "jarvis"), "jarvis"),
    (str(PROJECT_ROOT / "data"), "data"),
    (str(PROJECT_ROOT / "contacts.json"), "."),
    (str(PROJECT_ROOT / "connected_plugins.json"), "."),
    (str(PROJECT_ROOT / ".env"), "."),
    (str(PROJECT_ROOT / "user_preferences.json"), "."),
    (str(PROJECT_ROOT / "jarvis.toml"), ".")
]

# Collect any additional data from edge_tts or pygame
datas += collect_data_files("edge_tts")

hiddenimports = [
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtWidgets",
    "PyQt6.QtGui",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "edge_tts",
    "pygame",
    "pyttsx3",
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "sqlite3",
    "PIL",
    "PIL.Image",
    "certifi",
    "media_controller",
    "window_ops",
    "verified_executor",
    "disambiguation_manager",
    "visual_verifier",
    "goal_decomposer",
    "jarvis",
    "jarvis.core.bus",
    "jarvis.brain.factory",
    "jarvis.brain.manager"
]
hiddenimports += collect_submodules("plugins")
hiddenimports += collect_submodules("jarvis")

a = Analysis(
    ["main.py"],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "PersonalJarvis-main")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Hydra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Windowed GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "hydra_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Hydra",
)
