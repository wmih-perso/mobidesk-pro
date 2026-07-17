# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller pour MobiDeskPro.exe.

Génère un exécutable Windows unique embarquant l'interface (QSS, icônes).
Les données utilisateur (base SQLite) ne sont jamais embarquées : elles sont
créées à l'exécution dans le profil utilisateur (voir app/paths.py).
"""

datas = [
    ("app/ui/style.qss", "app/ui"),
    ("app/ui/resources", "app/ui/resources"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MobiDeskPro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="app/ui/resources/app_icon.ico",
    version="version_info.txt",
)
