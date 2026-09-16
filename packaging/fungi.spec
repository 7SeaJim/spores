# PyInstaller 打包配置：在 Windows 上运行  pyinstaller packaging/fungi.spec --noconfirm
# 产物：dist/FUNGI.exe（单文件、无命令行窗口，像素画稿打包在里面）
from pathlib import Path

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "fungi.py")],
    pathex=[str(root)],
    datas=[(str(root / "art" / "*.pxl"), "art")],
    hiddenimports=["send2trash"],
    excludes=["tkinter", "PIL", "numpy", "PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FUNGI",
    console=False,
    icon=str(root / "packaging" / "fungi.ico"),
    upx=False,
    debug=False,
    strip=False,
)
