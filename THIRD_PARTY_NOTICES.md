# 第三方组件许可 · Third-party notices

FUNGI.EXE 自己的代码和像素画稿（`main.py`、`fungi/`、`selftest.py`、`selftest_screens.py`、`art/`、`promo/`、`packaging/`）使用 MIT 许可，见 `LICENSE`。

打包好的 `FUNGI.exe` 里还包含下面这些组件，它们各自保留原来的许可。**因为 PyQt6 使用 GPLv3，分发打包好的 `FUNGI.exe` 时须遵守 GPLv3**：本项目完整源码公开在 https://github.com/7SeaJim/spores ，MIT 与 GPLv3 兼容。

| 组件 | 用途 | 许可 | 来源 |
| --- | --- | --- | --- |
| PyQt6 | Python 的 Qt 绑定（窗口、绘图） | GPL v3（或 Riverbank 商业许可） | https://www.riverbankcomputing.com/software/pyqt/ |
| Qt 6（随 PyQt6-Qt6 分发） | 图形界面框架 | LGPL v3（部分模块 GPL v3） | https://www.qt.io/licensing/open-source-lgpl-obligations |
| Send2Trash（仅 Windows） | 把吃掉的文件移到回收站 | BSD 3-Clause | https://github.com/arsenetar/send2trash |
| CPython | 运行时（PyInstaller 打包进 exe） | PSF License | https://docs.python.org/3/license.html |
| PyInstaller bootloader | 把程序打成单个 exe | GPL v2 + 例外条款（允许打包任何许可的程序） | https://pyinstaller.org/en/stable/license.html |

GPL v3 全文：https://www.gnu.org/licenses/gpl-3.0.txt ；LGPL v3 全文：https://www.gnu.org/licenses/lgpl-3.0.txt
