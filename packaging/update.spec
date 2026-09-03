# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(SPECPATH))

a = Analysis(
    [os.path.join(ROOT, "src", "update.py")],
    pathex=[ROOT, os.path.join(ROOT, "src")],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "packaging", "assets", "icons", "app_icon_v7.png"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # CLI only uses the standard library plus the small runtime helpers.
    # Keep optional scientific/GUI packages from the build machine out.
    excludes=["matplotlib", "numpy", "pygame", "pandas", "scipy", "PIL"],
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
    name="update",
    icon=os.path.join(ROOT, "packaging", "assets", "icons", "app_icon_v7.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

