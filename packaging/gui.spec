# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(SPECPATH))

# The entry point imports customtkinter and tkinter directly. Broadly collecting
# every submodule also pulled optional image/scientific packages from the build host.
hiddenimports = []

a = Analysis(
    [os.path.join(ROOT, "src", "gui_redesign.py")],
    pathex=[ROOT, os.path.join(ROOT, "src")],
    binaries=[],
    datas=[
        # 把 update.exe 一起打进包，_resource_path()/update.exe 即可找到
        (os.path.join(ROOT, "dist", "update.exe"), "."),
        # 应用图标（窗口用 PNG，exe 用 ICO）
        (os.path.join(ROOT, "packaging", "assets", "icons", "app_icon_v7.png"), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name="CF优选测速工具",
    icon=os.path.join(ROOT, "packaging", "assets", "icons", "app_icon_v7.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # 窗口程序
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

