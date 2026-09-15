#!/usr/bin/env python3
"""
FUNGI.EXE — 桌面真菌宠物（最小闭环 demo）

    桌面上的 3×3 黑色 spores → 拖入 .txt / .md / 文件夹 → 长大 → 变成蘑菇 → 放出新的 spores

运行:
    python3 fungi.py                 正常模式
    python3 fungi.py --fast          调试：成长加速（自然生长 ×30，喂食营养 ×3）
    python3 fungi.py --data-dir DIR  使用单独的存档目录

操作:
    拖文件 / 文件夹到它身上 = 喂食：能吃的 .txt / .md 会被真的吃掉（Linux 等直接删除，Windows 移到回收站），
                                  吃掉的完整路径记在存档目录的 eaten.log；右键可关掉「吞噬文件」
    左键拖动 = 搬家    单击 = 戳一下    右键 = 状态和菜单
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import random
import signal
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from functools import lru_cache
from pathlib import Path

try:
    import fcntl
except ImportError:                      # Windows
    fcntl = None

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QTransform, QFont, QFontMetrics, QGuiApplication, QIcon,
                         QImage, QPainter, QPainterPath, QPen, QPixmap, QRegion)
from PyQt6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon, QWidget

# ───────────────────────────── 可调参数 ─────────────────────────────

PX = 4                     # 1 个像素格 = 多少屏幕像素
TEXT_BAND = 38             # 精灵上方给文字/动画留的高度
BOTTOM_PAD = 6
MIN_WIN_W = 170
USE_INPUT_MASK = True      # 空闲时把窗口形状收缩到精灵附近，不挡桌面点击

STAGES = [                 # (名字, 进入该阶段需要的累计营养)
    ("spores", 0),
    ("sprout", 20),
    ("baby", 50),
    ("young", 100),
    ("adult", 170),
]
ADULT = len(STAGES) - 1
FIRST_BURST = 2            # 刚成年时放出的 spores 数
SPORE_EVERY = 60           # 成年后每再吃这么多营养，放出 1 个 spores
MAX_COLONY = 12

PASSIVE_SECONDS = 120      # 每隔多少秒自然 +1 营养
OFFLINE_CAP = 24           # 关闭程序期间最多补多少营养

# 待机动效
IDLE_RIG = {               # 阶段: (菌盖行数, 呼吸时抽掉的菌柄行) —— 对应 art/<阶段>.pxl 的行号
    1: (7, 8),
    2: (10, 14),
    3: (14, 20),
    4: (19, 27),
}
BREATH_PERIOD = 3.2        # 呼吸周期（秒），睡着时 ×1.6
IDLE_GAP = (6, 16)         # 两个待机小动作之间隔几秒
SLEEP_AFTER = 600          # 多少秒没人理就睡着；深夜 0–7 点缩短到 1/5
HUNGRY_IDLE = {            # 饿的时候：不蹦跶，嘟囔
    "hungry": [("grumble", 2), ("blink2", 2), ("tilt", 1)],
    "starving": [("grumble", 3), ("blink2", 1)],
}
IDLE_WEIGHTS = {           # 阶段: [(动作, 权重)]
    0: [("wiggle", 3), ("hop", 1)],
    1: [("tilt", 2), ("hop", 2), ("blink2", 2)],
    2: [("tilt", 3), ("hop", 2), ("blink2", 2)],
    3: [("tilt", 3), ("hop", 1), ("blink2", 2)],
    4: [("tilt", 3), ("hop", 1), ("blink2", 2), ("puff", 3)],
}

# 菌毯（沿桌面可用区域的四条边生长）
MAT_MAX = 8                # 最厚多少格（每格 PX 像素，8 格 = 32px）
MAT_STRIP = MAT_MAX + 7    # 边缘窗口厚度（格）：菌毯 + 菌丝 + 小蘑菇，共 60px
MAT_TICK = 5.0             # 每隔多少秒长一次
MAT_VIGOR = (0.3, 0.6, 1.0, 1.5, 2.5)   # 各阶段的活力
MAT_SEED = 0.03            # 每次、每单位活力，在离它最近的边缘落孢子的概率
MAT_RATE = 0.35            # 每次、每单位活力的生长步数
MAT_FEED = 0.5             # 喂食时每 1 营养让最近那段菌毯多长几步
MAT_OFFLINE_CAP = 20000    # 关闭期间最多补长多少步
MAT_SPROUT_DEPTH = 6       # 菌毯厚到多少格才会冒小蘑菇

# 喷孢菌：菌毯占到一定范围后长出来，不会动，定期往随机处喷孢子
SPITTER_AT = 0.25          # 屏幕边缘一圈被菌毯占到多少长度时长出来
SPITTER_EVERY = (300, 600) # 喷射间隔（秒），5–10 分钟
SHOT_OUTCOMES = (("vanish", 0.75), ("mat", 0.20), ("spore", 0.05))   # 落地：消失 / 形成菌毯 / 变成独立小孢子
PATCH_MAX = (3, 7)         # 桌面中间的菌斑最多长到多大半径（格），每块随机
PATCH_GROW = 0.04          # 菌斑每次（MAT_TICK）长多少格半径
MAX_PATCHES = 30
OUTCOME_NAMES = {"vanish": "消失", "mat": "菌毯", "spore": "孢子"}

# 饥饿：不喂食的话会饿，饿扁了会缩回去，最后变成休眠孢子（喂一次就醒）
SATIETY_MAX = 100
HUNGER_HOURS = 16          # 从吃饱到饿扁要多少小时
HUNGRY_AT = 30             # 饱腹低于这个就是「饿」：生长减半，会嘟囔
SATIETY_PER_FOOD = 4       # 每 1 营养加多少饱腹
STARVE_LOSS = 6            # 饿扁（饱腹 0）后每小时掉多少营养
OFFLINE_STARVE_CAP = 36    # 关闭程序期间最多饿掉多少营养
MAT_RECEDE = 0.3           # 全体饿扁时，边缘菌毯每次（MAT_TICK）退缩几步
MOOD_NAMES = {"full": "", "hungry": "饿", "starving": "饿扁了", "dormant": "休眠"}

DEVOUR = True              # 默认吞噬文件（右键菜单可关）
PROJECT_DIR = Path(__file__).resolve().parent

EDIBLE_EXT = {".txt", ".md"}
MAX_ITEMS_PER_DROP = 5

NAMES = ["puff", "kino", "shii", "enoki", "morel", "nameko", "maitake", "chanty",
         "porcini", "reishi", "shimeji", "inky", "bolete", "truffle", "oyster"]

ART_DIR = Path(__file__).resolve().parent / "art"   # pixel4ai 画稿（.pxl），缺失时回退到程序生成

INK = QColor(17, 17, 17)
PAPER = QColor(250, 249, 244)

# ───────────────────────────── 像素精灵 ─────────────────────────────
# 优先读 art/<阶段>.pxl、<阶段>_blink.pxl、<阶段>_eat.pxl；没有画稿时用下面的程序生成
# 程序生成的字符: "#" 黑, "o" 白, "." 透明
HALO = "\x01"

MUSHROOM_SHAPES = {        # 阶段: (菌盖宽, 菌盖高, 菌柄宽, 菌柄高)
    1: (11, 5, 7, 4),
    2: (15, 7, 7, 6),
    3: (21, 10, 9, 8),
    4: (27, 13, 11, 10),
}
CAP_ROUNDNESS = 2.6
SPOTS = {                  # 阶段: [(横向 -1..1, 纵向 0..1, 大小)]
    1: [(-0.5, 0.6, 1), (0.35, 0.3, 1)],
    2: [(-0.5, 0.6, 1), (0.1, 0.3, 2), (0.55, 0.65, 1)],
    3: [(-0.55, 0.62, 2), (0.05, 0.28, 3), (0.55, 0.55, 2), (-0.15, 0.8, 1)],
    4: [(-0.6, 0.62, 3), (0.0, 0.25, 4), (0.58, 0.55, 3), (-0.22, 0.82, 2), (0.3, 0.85, 1)],
}


def spot_cells(size: int) -> list[tuple[int, int]]:
    if size == 1:
        return [(0, 0)]
    if size == 2:
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    if size == 3:
        return [(0, -1), (-1, 0), (0, 0), (1, 0), (0, 1)]
    return [(x, y) for x in range(-1, 3) for y in range(-1, 3) if (x, y) not in {(-1, -1), (2, -1), (-1, 2), (2, 2)}]


def spore_art(n: int) -> list[str]:
    return ["#" * n] * n


def mushroom_art(stage: int, blink: bool = False, mouth: bool = False) -> list[str]:
    w, ch, sw, sh = MUSHROOM_SHAPES[stage]
    height = ch + sh
    g = [["."] * w for _ in range(height)]
    cx = w // 2

    # 菌盖：超椭圆圆顶
    for y in range(ch):
        t = (ch - y - 0.5) / ch
        hw = (w / 2) * (1 - t ** CAP_ROUNDNESS) ** (1 / CAP_ROUNDNESS)
        for x in range(w):
            if abs(x - cx) < hw:
                g[y][x] = "#"

    # 菌盖上的白点
    for u, v, size in SPOTS[stage]:
        sy = round(v * (ch - 1))
        row = [x for x in range(w) if g[sy][x] == "#"]
        if not row:
            continue
        half = (row[-1] - row[0]) / 2
        sx = round(cx + u * (half - 1))
        cells = [(sx + dx, sy + dy) for dx, dy in spot_cells(size)]
        ok = all(0 < x < w - 1 and 0 < y < ch - 1 and
                 all(g[y + dy][x + dx] in "#o" for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                 for x, y in cells)
        if ok:
            for x, y in cells:
                g[y][x] = "o"

    # 菌柄：底部略微外扩，外轮廓黑、内部白
    stem = set()
    for i in range(sh):
        half = sw // 2
        if sw >= 7 and i == sh - 1:
            half += 1
        if sw >= 9 and i >= sh - 2:
            half += 1
        for x in range(cx - half, cx + half + 1):
            stem.add((x, ch + i))
    for x, y in stem:
        edge = y == height - 1 or any((x + dx, y + dy) not in stem and not (y + dy < ch and g[y + dy][x + dx] == "#")
                                      for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        g[y][x] = "#" if edge else "o"

    # 脸
    eye_dx = 1 if sw <= 7 else 2
    eye_h = 2 if sh >= 8 else 1
    ey = ch + max(1, round(sh * 0.25))
    for dx in (-eye_dx, eye_dx):
        for k in range(eye_h):
            if blink and k < eye_h - 1 or (blink and eye_h == 1):
                continue
            g[ey + k][cx + dx] = "#"
    if mouth:
        my = ey + eye_h + (1 if sh >= 6 else 0)
        mw = 0 if sw <= 5 else 1
        mh = 2 if sh >= 8 else 1
        for dx in range(-mw, mw + 1):
            for k in range(mh):
                if my + k < height - 1:
                    g[my + k][cx + dx] = "#"
    return ["".join(r) for r in g]


@lru_cache(maxsize=64)
def load_pxl(name: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]] | None:
    """读 art/<name>.pxl（JSON: size / palette / rows）。没有或损坏时返回 None。"""
    try:
        data = json.loads((ART_DIR / f"{name}.pxl").read_text("utf-8"))
        rows = tuple(data["rows"])
        palette = tuple((ch, hexc) for ch, hexc in data["palette"].items() if hexc)
        if not rows or any(len(r) != len(rows[0]) for r in rows):
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return rows, palette


def add_halo(art: list[str]) -> list[str]:
    h, w = len(art), len(art[0])
    out = [["."] * (w + 2) for _ in range(h + 2)]
    for y in range(h):
        for x in range(w):
            out[y + 1][x + 1] = art[y][x]
    for y in range(h + 2):
        for x in range(w + 2):
            if out[y][x] != ".":
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy - 1, x + dx - 1
                    if 0 <= yy < h and 0 <= xx < w and art[yy][xx] != ".":
                        out[y][x] = HALO
    return ["".join(r) for r in out]


def pose(art: list[str], cap_rows: int, breath_row: int, breath: bool, tilt: int) -> list[str]:
    """待机姿势。左右各留 1 列给歪头；呼吸时抽掉一行菌柄、顶部补一行，脚底不动、菌盖和脸下沉 1 格。"""
    rows = ["." + r + "." for r in art]
    if tilt:
        for i in range(min(cap_rows, len(rows))):
            rows[i] = "." + rows[i][:-1] if tilt > 0 else rows[i][1:] + "."
    if breath and 0 <= breath_row < len(rows):
        del rows[breath_row]
        rows.insert(0, "." * len(rows[0]))
    return rows


def spore_size(nutrition: float) -> int:
    """spores 阶段内部：3×3 → 4×4 → 5×5，方块慢慢变大"""
    return min(5, 3 + int(3 * nutrition / STAGES[1][1]))


def withered(colors: dict[str, QColor]) -> dict[str, QColor]:
    """饿扁了：白的发灰、黑的褪成灰褐"""
    out = {}
    for ch, c in colors.items():
        light = c.lightness()
        out[ch] = (QColor(196, 191, 178) if light > 200 else QColor(150, 146, 136) if light > 150
                   else QColor(78, 74, 67) if light < 60 else QColor(110, 106, 98))
    return out


def paint_rows(rows: list[str], colors: dict[str, QColor]) -> QImage:
    img = QImage(len(rows[0]) * PX, len(rows) * PX, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in colors:
                p.fillRect(x * PX, y * PX, PX, PX, colors[ch])
    p.end()
    return img


@lru_cache(maxsize=512)
def art_pixmap(stage: int, size: int = 3, blink: bool = False, mouth: bool = False,
               invert: bool = False, breath: bool = False, tilt: int = 0, wither: bool = False,
               mood: str = "full") -> QPixmap:
    art, colors = None, {"#": INK, "o": PAPER}
    if stage == 0 and mood == "dormant":                 # 休眠孢子：专门的孢囊帧（画稿缺失时退回灰方块）
        drawn = load_pxl("dormant")
        if drawn:
            art, colors, wither = list(drawn[0]), {ch: QColor(hexc) for ch, hexc in drawn[1]}, False
    if stage > 0:
        name = STAGES[stage][0]
        frame = ("_eat" if mouth else "_starving" if mood == "starving" else "_blink" if blink
                 else "_hungry" if mood == "hungry" else "")
        drawn = load_pxl(name + frame) or load_pxl(name)
        if drawn:
            art, colors = list(drawn[0]), {ch: QColor(hexc) for ch, hexc in drawn[1]}
            rig = IDLE_RIG[stage]
    if art is None:
        art = spore_art(size) if stage == 0 else mushroom_art(stage, blink, mouth)
        if stage > 0:
            rig = (MUSHROOM_SHAPES[stage][1], len(art) - 2)
    if stage > 0:
        art = pose(art, rig[0], rig[1], breath, tilt)
    art = add_halo(art)
    if wither:
        colors = withered(colors)
    if invert:
        colors = {ch: QColor(255 - c.red(), 255 - c.green(), 255 - c.blue()) for ch, c in colors.items()}
    halo = QColor(INK if invert else PAPER)
    halo.setAlpha(235)
    colors[HALO] = halo
    return QPixmap.fromImage(paint_rows(art, colors))


# ───────────────────────────── 数据 ─────────────────────────────

@dataclass
class Creature:
    id: str
    name: str
    gen: int = 1
    born: float = field(default_factory=time.time)
    nutrition: float = 0.0
    feeds: int = 0
    released: int = 0                                   # 已放出的 spores 数
    satiety: float = 60.0                               # 饱腹 0–100
    x: int = 0                                          # 脚底中心的屏幕坐标
    y: int = 0
    eaten: list[str] = field(default_factory=list)      # 吃过的食物指纹（不保存路径）
    log: list[list] = field(default_factory=list)       # [时间戳, 名字, 营养]

    @classmethod
    def from_dict(cls, d: dict) -> "Creature":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def stage(self) -> int:
        return max(i for i, (_, need) in enumerate(STAGES) if self.nutrition >= need)

    @property
    def stage_name(self) -> str:
        return STAGES[self.stage][0]

    @property
    def mood(self) -> str:
        if self.satiety > HUNGRY_AT:
            return "full"
        if self.satiety > 0:
            return "hungry"
        return "dormant" if self.nutrition <= 0 else "starving"

    @property
    def growth_factor(self) -> float:
        return {"full": 1.0, "hungry": 0.5}.get(self.mood, 0.0)

    def next_goal(self) -> int:
        if self.stage < ADULT:
            return STAGES[self.stage + 1][1]
        return STAGES[ADULT][1] + SPORE_EVERY * (int((self.nutrition - STAGES[ADULT][1]) // SPORE_EVERY) + 1)

    def stage_floor(self) -> int:
        if self.stage < ADULT:
            return STAGES[self.stage][1]
        return self.next_goal() - SPORE_EVERY

    def spores_due(self) -> int:
        if self.stage < ADULT:
            return 0
        return FIRST_BURST + int((self.nutrition - STAGES[ADULT][1]) // SPORE_EVERY) - self.released


@dataclass
class Food:
    label: str
    value: int
    key: str
    note: str = ""
    targets: list[Path] = field(default_factory=list)   # 吞噬时要吃掉的文件
    dirs: list[Path] = field(default_factory=list)      # 吃完后尝试清掉的空目录（深的在前）


def find_edible(root: Path, max_depth: int = 3, budget: int = 3000) -> tuple[list[Path], list[Path]]:
    """文件夹里能吃的 txt/md，以及走过的子目录。跳过隐藏项，不跟随目录链接。"""
    files, dirs, stack = [], [], [(root, 0)]
    while stack and budget > 0:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    budget -= 1
                    if budget <= 0:
                        break
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                dirs.append(Path(e.path))
                                stack.append((Path(e.path), depth + 1))
                        elif os.path.splitext(e.name)[1].lower() in EDIBLE_EXT:
                            files.append(Path(e.path))
                    except OSError:
                        pass
        except OSError:
            pass
    return files, sorted(dirs, key=lambda p: len(p.parts), reverse=True) + [root]


def count_edible(root: Path, max_depth: int = 3, budget: int = 3000) -> int:
    return len(find_edible(root, max_depth, budget)[0])


def digest(path: Path) -> tuple[Food | None, str]:
    """把一个路径变成食物。只看 stat 和目录结构，不读文件内容；真正吃掉（删除）由 Colony.devour 做。"""
    try:
        st = path.stat()
    except OSError:
        return None, "够不着…"
    key = hashlib.sha1(f"{os.path.realpath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    if path.is_dir():
        files, dirs = find_edible(path)
        if not files:
            return Food(path.name + "/", 3, key, "空空的", [], dirs), ""
        return Food(path.name + "/", min(30, 6 + 2 * len(files)), key, "", files, dirs), ""
    if path.suffix.lower() in EDIBLE_EXT:
        if st.st_size == 0:
            return Food(path.name, 2, key, "空的…", [path]), ""
        return Food(path.name, min(20, 4 + int(3 * math.log2(st.st_size / 256 + 1))), key, "", [path]), ""
    return None, f"不吃 {path.suffix or path.name}"


def devour_file(path: Path) -> None:
    """吃掉一个文件：Windows 移到回收站，其他系统直接删除。失败抛 OSError。"""
    if sys.platform != "win32":
        path.unlink()
        return
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None
    if send2trash:
        try:
            send2trash(str(path))
        except Exception as err:          # send2trash 的异常不一定是 OSError
            raise OSError(str(err)) from err
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path.resolve()) + "\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) or op.fAnyOperationsAborted:
        raise OSError(f"没能移到回收站：{path}")


def foreground_fullscreen() -> bool:
    """Windows：前台是不是别的程序的全屏窗口（看视频、打游戏）。其他系统返回 False。"""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):   # 桌面、任务栏不算
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    rect, info = wintypes.RECT(), MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)) or \
            not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


def fmt_age(sec: float) -> str:
    sec = int(max(0, sec))
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def ui_font(px: int = 12, bold: bool = True) -> QFont:
    f = QFont()
    f.setFamilies(["DejaVu Sans Mono", "Consolas", "Noto Sans Mono CJK SC", "Noto Sans CJK SC", "Microsoft YaHei UI",
                   "Microsoft YaHei", "monospace"])
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def draw_label(p: QPainter, text: str, cx: float, baseline: float, font: QFont, alpha: int = 255):
    path = QPainterPath()
    path.addText(cx - QFontMetrics(font).horizontalAdvance(text) / 2, baseline, font, text)
    paper, ink = QColor(PAPER), QColor(INK)
    paper.setAlpha(alpha)
    ink.setAlpha(alpha)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.strokePath(path, QPen(paper, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.fillPath(path, ink)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)


MENU_QSS = f"""
QMenu {{ background:{PAPER.name()}; color:{INK.name()}; border:2px solid {INK.name()}; padding:4px;
         font-family:'DejaVu Sans Mono','Consolas','Noto Sans CJK SC','Microsoft YaHei UI',monospace; font-size:12px; font-weight:bold; }}
QMenu::item {{ padding:4px 22px 4px 12px; }}
QMenu::item:selected {{ background:{INK.name()}; color:{PAPER.name()}; }}
QMenu::item:disabled {{ color:{INK.name()}; }}
QMenu::separator {{ height:2px; background:{INK.name()}; margin:4px 6px; }}
QMenu::indicator {{ width:10px; height:10px; border:2px solid {INK.name()}; margin-left:4px; }}
QMenu::indicator:checked {{ background:{INK.name()}; }}
"""

ANIM_DUR = {"eat": 0.8, "shake": 0.5, "poke": 0.4, "grow": 1.2, "land": 0.3, "hop": 0.35,
            "tilt": 1.5, "wiggle": 0.5}
LOUD_ANIMS = {"eat", "shake", "poke", "grow"}    # 会跳出精灵附近的动画，需要整窗绘制
TILT_STEPS = (-1, -1, 0, 1, 1, 0)
DUST = QColor(85, 82, 76)


# ───────────────────────────── 单只菌的窗口 ─────────────────────────────

class CreatureWidget(QWidget):
    def __init__(self, colony: "Colony", creature: Creature, on_top: bool = True):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.colony, self.c = colony, creature
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        self.setWindowTitle(f"fungi · {creature.name}")

        self.anim: tuple[str, float] | None = None
        self.floaters: list[dict] = []       # 飘字
        self.particles: list[dict] = []      # 碎屑
        self.flight: dict | None = None      # 孢子飞行
        self.hover = self.drag_over = False
        self.press: tuple[QPoint, int, int] | None = None
        self.moved = False
        self.masked: str | None = None
        self.shown_stage = creature.stage
        now = time.time()
        self.blinks = [now + random.uniform(2, 6)]
        self.idle_at = now + random.uniform(*IDLE_GAP)
        self.phase = random.uniform(0, BREATH_PERIOD)   # 错开每只菌的呼吸
        self.last_touch = now
        self.asleep = False
        self.z_at = 0.0
        self.z_n = 0
        self.shown_mood = creature.mood
        self._key = None
        self.sprite_rect = QRect()

        self.refit()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    # ── 几何 ──
    def base_pixmap(self) -> QPixmap:
        return art_pixmap(self.c.stage, spore_size(self.c.nutrition), mood="dormant" if self.c.mood == "dormant" else "full")

    def refit(self):
        pm = self.base_pixmap()
        w, h = max(MIN_WIN_W, pm.width() + 40), pm.height() + TEXT_BAND + BOTTOM_PAD
        self.resize(w, h)
        self.sprite_rect = QRect((w - pm.width()) // 2, h - BOTTOM_PAD - pm.height(), pm.width(), pm.height())
        self.shown_stage = self.c.stage
        self.shown_dormant = self.c.mood == "dormant"
        self.masked = None
        if not self.flight:
            self.place()
        self.update_mask()

    def place(self, x: float | None = None, y: float | None = None):
        # 精灵底部 = 脚底锚点；描边占 1 格。飞行中传入显示位置，存档里的 c.x/c.y 始终是落点
        x = self.c.x if x is None else x
        y = self.c.y if y is None else y
        self.move(int(x - self.width() / 2), int(y - (self.height() - BOTTOM_PAD) + PX))

    def update_mask(self):
        """tight: 只有精灵附近；band: 再加精灵正上方一条（z、孢子尘）；full: 整窗（飘字、跳跃、悬停）"""
        if not USE_INPUT_MASK:
            return
        loud = (self.hover or self.flight or (self.anim and self.anim[0] in LOUD_ANIMS)
                or any(not f["ambient"] for f in self.floaters)
                or any(q["kind"] == "crumb" for q in self.particles))
        mode = "full" if loud else "band" if (self.asleep or self.floaters or self.particles) else "tight"
        if mode == self.masked:
            return
        self.masked = mode
        if mode == "full":
            self.clearMask()
            return
        r = self.sprite_rect
        region = QRegion(r.adjusted(-14, -14, 14, BOTTOM_PAD))
        if mode == "band":
            region = region.united(QRegion(QRect(r.x() - 14, 0, r.width() + 28, r.y())))
        self.setMask(region)

    # ── 动画 ──
    def play(self, name: str):
        self.anim = (name, time.time())
        self.update_mask()

    def say(self, text: str, delay: float = 0.0, big: bool = False, ambient: bool = False):
        t0 = time.time() + delay
        queue = [f for f in self.floaters if not f["ambient"]]
        if queue and not ambient:
            t0 = max(t0, queue[-1]["t0"] + 0.45)
        self.floaters.append({"text": text, "t0": t0, "dur": 2.2 if ambient else 1.6, "big": big, "ambient": ambient})
        self.update_mask()

    def crumbs(self, n: int = 7):
        now = time.time()
        r = self.sprite_rect
        for _ in range(n):
            self.particles.append({"kind": "crumb", "x": r.center().x(), "y": r.y() + PX * 2, "life": 0.6,
                                   "vx": random.uniform(-70, 70), "vy": random.uniform(-150, -60), "t0": now})

    def puff(self, n: int = 3):
        """成年菌从菌盖顶上飘出几粒孢子尘"""
        now = time.time()
        r = self.sprite_rect
        for i in range(n):
            self.particles.append({"kind": "mote", "x": r.x() + random.uniform(0.3, 0.7) * r.width(),
                                   "y": r.y() + PX * 3, "vx": random.uniform(-6, 6), "vy": random.uniform(-17, -11),
                                   "t0": now + i * 0.3, "life": 2.4, "wob": random.uniform(0, 6.3)})
        self.update_mask()

    # ── 待机 ──
    def touch(self):
        """有人理它：刷新计时，睡着的话醒过来"""
        self.last_touch = time.time()
        if self.asleep:
            self.asleep = False
            self.floaters = [f for f in self.floaters if not f["ambient"]]
            self.idle_at = self.last_touch + random.uniform(*IDLE_GAP)
            self.play("hop")
            self.say("!")

    def breathing_out(self, now: float) -> bool:
        period = BREATH_PERIOD * (1.6 if self.asleep else 1.0)
        return (now + self.phase) % period > period * 0.55

    def idle(self, now: float):
        if self.c.mood == "dormant":                    # 休眠孢子：一动不动，等人喂
            self.asleep = False
            return
        busy = self.anim or self.press or self.flight or self.drag_over or self.hover
        if not self.asleep and not busy and now - self.last_touch > self.colony.sleep_after():
            self.asleep = True
            self.z_at = now + 0.8
        if self.asleep:
            if now >= self.z_at:
                self.z_n += 1
                self.say("Z" if self.z_n % 3 == 0 else "z", ambient=True)
                self.z_at = now + 1.4
            return
        if busy or now < self.idle_at:
            return
        self.idle_at = now + random.uniform(*IDLE_GAP)
        acts = HUNGRY_IDLE.get(self.c.mood, IDLE_WEIGHTS[self.c.stage])
        self.do_idle(random.choices([a for a, _ in acts], [w for _, w in acts])[0])

    def do_idle(self, act: str):
        now = time.time()
        if act == "blink2":
            self.blinks = [now, now + 0.3]
        elif act == "puff":
            self.puff()
        elif act == "grumble":
            self.say(random.choice(["饿…", "咕…", "……", "想吃 .txt"]) if self.c.mood == "hungry"
                     else random.choice(["好饿……", "咕噜……", "……"]))
        else:
            self.play(act)

    def fly(self, start: tuple[int, int], end: tuple[int, int], delay: float = 0.0, dur: float = 0.75):
        self.flight = {"a": start, "b": end, "t0": time.time() + delay, "dur": dur}
        self.c.x, self.c.y = end
        self.place(*start)
        self.update_mask()

    def body_offset(self, now: float) -> tuple[int, int]:
        if not self.anim:
            return 0, 0
        name, t0 = self.anim
        t = now - t0
        if name == "eat":
            return 0, -PX if int(t * 8) % 2 else 0
        if name == "shake":
            return (PX if int(t * 16) % 2 else -PX), 0
        if name == "wiggle":
            return (PX if int(t * 8) % 2 == 0 else -PX) if t < ANIM_DUR["wiggle"] else 0, 0
        if name in ("poke", "hop", "land"):
            k = {"poke": 3, "hop": 1, "land": 1}[name]
            return 0, -round(math.sin(math.pi * min(1.0, t / ANIM_DUR[name])) * k) * PX
        return 0, 0

    def current_pixmap(self, now: float) -> QPixmap:
        c = self.c
        anim = self.anim[0] if self.anim else None
        t = now - self.anim[1] if self.anim else 0
        invert = anim == "grow" and int(t / 0.15) % 2 == 0
        wither = c.mood in ("starving", "dormant")
        if c.stage == 0:
            return art_pixmap(0, spore_size(c.nutrition), invert=invert, wither=wither, mood=c.mood)
        mouth = self.drag_over or (anim == "eat" and int(t * 8) % 2 == 0)
        blink = self.asleep or any(b <= now < b + 0.15 for b in self.blinks)
        calm = not (mouth or anim in LOUD_ANIMS or self.flight or self.press)
        breath = calm and self.breathing_out(now)
        tilt = TILT_STEPS[min(len(TILT_STEPS) - 1, int(t / ANIM_DUR["tilt"] * len(TILT_STEPS)))] if anim == "tilt" else 0
        return art_pixmap(c.stage, 3, blink, mouth, invert, breath, tilt, wither, c.mood)

    def tick(self):
        now = time.time()
        if self.flight and now >= self.flight["t0"]:
            f = self.flight
            k = min(1.0, (now - f["t0"]) / f["dur"])
            (ax, ay), (bx, by) = f["a"], f["b"]
            self.place(ax + (bx - ax) * k, ay + (by - ay) * k - math.sin(math.pi * k) * 90)
            if k >= 1.0:
                self.flight = None
                self.place()
                self.play("land")
                self.colony.save()
        if self.anim and now - self.anim[1] > ANIM_DUR[self.anim[0]]:
            self.anim = None
        self.floaters = [f for f in self.floaters if now - f["t0"] < f["dur"]]
        self.particles = [q for q in self.particles if now - q["t0"] < q["life"]]
        self.blinks = [b for b in self.blinks if now < b + 0.15]
        if not self.blinks:
            first = now + random.uniform(2.5, 7)
            self.blinks = [first, first + 0.3] if random.random() < 0.2 else [first]
        self.idle(now)
        if self.c.stage != self.shown_stage or (self.c.mood == "dormant") != self.shown_dormant:
            self.refit()
        mood = self.c.mood
        if mood != self.shown_mood:
            order = list(MOOD_NAMES)
            if order.index(mood) > order.index(self.shown_mood):
                self.say({"hungry": "饿了…", "starving": "好饿……", "dormant": "（休眠了）"}[mood])
            self.shown_mood = mood
        self.update_mask()

        loud = self.flight or any(not f["ambient"] for f in self.floaters) or any(q["kind"] == "crumb" for q in self.particles)
        fps = 20 if loud else 10 if (self.floaters or self.particles) else 0
        key = (self.current_pixmap(now).cacheKey(), self.body_offset(now), self.hover, self.drag_over,
               int(now * fps) if fps else 0, int(now) if self.hover else 0)
        if key != self._key:
            self._key = key
            self.update()

    # ── 绘制 ──
    def paintEvent(self, _):
        now = time.time()
        p = QPainter(self)
        r = self.sprite_rect
        dx, dy = self.body_offset(now)

        if self.drag_over:
            box = r.adjusted(-8, -8, 8, 4)
            p.setPen(QPen(PAPER, 4))
            p.drawRect(box)
            p.setPen(QPen(INK, 2, Qt.PenStyle.DashLine))
            p.drawRect(box)

        p.drawPixmap(r.x() + dx, r.y() + dy, self.current_pixmap(now))

        for q in self.particles:
            t = now - q["t0"]
            if t < 0:
                continue
            if q["kind"] == "crumb":
                x, y, paper, ink = q["x"] + q["vx"] * t, q["y"] + q["vy"] * t + 380 * t * t, PAPER, INK
            else:
                x = q["x"] + q["vx"] * t + math.sin(q["wob"] + t * 3) * 3
                y = q["y"] + q["vy"] * t
                alpha = int(255 * min(1.0, (1 - t / q["life"]) * 2.5))
                paper, ink = QColor(PAPER), QColor(DUST)
                paper.setAlpha(alpha)
                ink.setAlpha(alpha)
            p.fillRect(int(x) - 3, int(y) - 3, 6, 6, paper)
            p.fillRect(int(x) - 2, int(y) - 2, 4, 4, ink)

        for f in self.floaters:
            if f["ambient"] and now >= f["t0"]:
                k = (now - f["t0"]) / f["dur"]
                draw_label(p, f["text"], r.right() - 2 + k * 8, r.y() + 8 - k * 28,
                           ui_font(15 if f["text"] == "Z" else 12), int(255 * min(1.0, (1 - k) * 2)))

        top = r.y() - 6
        active = [f for f in self.floaters if not f["ambient"] and now >= f["t0"]][-1:]
        for f in active:
            k = (now - f["t0"]) / f["dur"]
            alpha = 255 if k < 0.6 else int(255 * (1 - (k - 0.6) / 0.4))
            draw_label(p, f["text"], self.width() / 2, top - k * 16, ui_font(15 if f["big"] else 13), alpha)

        if self.hover and not active and not self.drag_over:
            c = self.c
            mood = {"dormant": "休眠 · 喂点东西吧"}.get(c.mood, MOOD_NAMES[c.mood])
            draw_label(p, f"{c.name} · {c.stage_name}" + (f" · {mood}" if mood else ""), self.width() / 2, top - 16, ui_font(12))
            lo, hi = c.stage_floor(), c.next_goal()
            frac = max(0.0, min(1.0, (c.nutrition - lo) / (hi - lo)))
            bw, bh = 64, 8
            bx, by = int(self.width() / 2 - bw / 2), top - 10
            p.fillRect(bx - 2, by - 2, bw + 4, bh + 4, PAPER)
            p.fillRect(bx, by, bw, bh, INK)
            p.fillRect(bx + 2, by + 2, bw - 4, bh - 4, PAPER)
            p.fillRect(bx + 2, by + 2, int((bw - 4) * frac), bh - 4, INK)
        p.end()

    # ── 拖放喂食 ──
    @staticmethod
    def _local_paths(e) -> list[Path]:
        md = e.mimeData()
        if not md.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]

    @staticmethod
    def _safe_action(e):
        """只接受「复制/链接」，绝不接受「移动」——避免文件管理器把源文件删掉。"""
        acts = e.possibleActions()
        if acts & Qt.DropAction.CopyAction:
            return Qt.DropAction.CopyAction
        if acts & Qt.DropAction.LinkAction:
            return Qt.DropAction.LinkAction
        return None

    def dragEnterEvent(self, e):
        action = self._safe_action(e)
        if self._local_paths(e) and action is not None:
            self.touch()
            e.setDropAction(action)
            e.accept()
            self.drag_over = True
            self.update()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        action = self._safe_action(e)
        if action is not None:
            e.setDropAction(action)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.drag_over = False
        self.update()

    def dropEvent(self, e):
        self.drag_over = False
        action = self._safe_action(e)
        paths = self._local_paths(e)
        if action is None or not paths:
            e.ignore()
            return
        e.setDropAction(action)
        e.accept()
        self.colony.feed(self, paths)

    # ── 鼠标 ──
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.flight:
            self.touch()
            self.press = (e.globalPosition().toPoint(), self.c.x, self.c.y)
            self.moved = False

    def mouseMoveEvent(self, e):
        if not self.press:
            return
        start, ax, ay = self.press
        d = e.globalPosition().toPoint() - start
        if not self.moved and d.manhattanLength() < 5:
            return
        self.moved = True
        self.c.x, self.c.y = ax + d.x(), ay + d.y()
        self.place()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.press:
            return
        self.press = None
        if self.moved:
            self.colony.save()
        else:
            self.play("poke")
            self.say(random.choice(["?", "…", "!", "嗯？"]) if self.c.stage else "·")

    def enterEvent(self, e):
        self.touch()
        self.hover = True
        self.update_mask()
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def contextMenuEvent(self, e):
        self.colony.show_menu(self, e.globalPos())


# ───────────────────────────── 菌毯 ─────────────────────────────

MAXH = 0xFFFFFFFF


def _hash(a: int, b: int = 0) -> int:
    x = (a * 374761393 + b * 668265263 + 0x9E3779B9) & MAXH
    x = ((x ^ (x >> 13)) * 1274126177) & MAXH
    return x ^ (x >> 16)


def _vnoise(x: float, seed: int) -> float:
    """一维值噪声，0..1，平滑、不重复的起伏"""
    i = math.floor(x)
    f = x - i
    f = f * f * (3 - 2 * f)
    a, b = _hash(i, seed) / MAXH, _hash(i + 1, seed) / MAXH
    return a + (b - a) * f


def _vnoise2(x: float, y: float, seed: int) -> float:
    """二维值噪声，0..1，用来做成团的斑驳"""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)

    def h(a, b):
        return _hash(a * 7919 + b, seed) / MAXH
    top = h(ix, iy) + (h(ix + 1, iy) - h(ix, iy)) * fx
    bottom = h(ix, iy + 1) + (h(ix + 1, iy + 1) - h(ix, iy + 1)) * fx
    return top + (bottom - top) * fy


class Mycelium:
    """屏幕边缘一圈菌毯的厚度（格）。环形下标：上边左→右、右边上→下、下边右→左、左边下→上。"""

    def __init__(self, cols: int, rows: int, depth: bytes | None = None):
        self.cols, self.rows = max(1, cols), max(1, rows)
        self.n = 2 * (self.cols + self.rows)
        self.d = bytearray(depth) if depth is not None and len(depth) == self.n else bytearray(self.n)
        for i, v in enumerate(self.d):
            self.d[i] = min(v, MAT_MAX)
        self.active = [i for i, v in enumerate(self.d) if v]
        self.dirty: set[int] = set()
        self._eff: dict[int, float] = {}
        self._sprout: dict[int, tuple | None] = {}

    # ── 坐标 ──
    def edge_len(self, edge: str) -> int:
        return self.cols if edge in ("top", "bottom") else self.rows

    def edge_of(self, i: int) -> tuple[str, int]:
        c, r = self.cols, self.rows
        i %= self.n
        if i < c:
            return "top", i
        if i < c + r:
            return "right", i - c
        if i < 2 * c + r:
            return "bottom", i - c - r
        return "left", i - 2 * c - r

    def index_at(self, edge: str, pos: int) -> int:
        c, r = self.cols, self.rows
        return {"top": 0, "right": c, "bottom": c + r, "left": 2 * c + r}[edge] + pos

    def nearest(self, cx: float, cy: float) -> int:
        """离格坐标 (cx, cy) 最近的边缘格"""
        c, r = self.cols, self.rows
        x, y = int(min(max(cx, 0), c - 1)), int(min(max(cy, 0), r - 1))
        dist = {"top": y, "bottom": r - 1 - y, "left": x, "right": c - 1 - x}
        edge = min(dist, key=dist.get)
        return self.index_at(edge, {"top": x, "right": y, "bottom": c - 1 - x, "left": r - 1 - y}[edge])

    # ── 生长 ──
    def bump(self, i: int) -> bool:
        i %= self.n
        if self.d[i] >= MAT_MAX:
            return False
        if not self.d[i]:
            self.active.append(i)
        self.d[i] += 1
        self.dirty.update(j % self.n for j in range(i - 11, i + 12))
        self._eff.clear()
        self._sprout.clear()
        return True

    def seed(self, i: int) -> bool:
        """落下孢子：这一格还没有菌毯时长出第一格"""
        i %= self.n
        return not self.d[i] and self.bump(i)

    def grow(self, steps: int, near: int | None = None, rng=random):
        """随机挑已有菌毯的格：比邻格厚 2 格以上就往旁边蔓延，否则原地增厚（越厚越慢）"""
        d, n = self.d, self.n
        for _ in range(steps):
            if not self.active:
                return
            i = None
            if near is not None:
                for _ in range(6):
                    j = (near + int(rng.gauss(0, 25))) % n
                    if d[j]:
                        i = j
                        break
            if i is None:
                i = rng.choice(self.active)
            j = (i + rng.choice((-1, 1)) * rng.choice((1, 1, 1, 2))) % n
            if d[j] + 1 < d[i]:
                self.bump(j)
            elif rng.random() < 1 - d[i] / MAT_MAX:
                self.bump(i)

    def shrink(self, steps: int, rng=random):
        """菌毯退缩：优先从比邻格厚的地方（前沿、凸起）往回收"""
        d, n = self.d, self.n
        for _ in range(steps):
            if not self.active:
                return
            k = rng.randrange(len(self.active))
            i = self.active[k]
            if min(d[(i - 1) % n], d[(i + 1) % n]) >= d[i] and rng.random() > 0.2:
                continue
            d[i] -= 1
            if not d[i]:
                self.active[k] = self.active[-1]
                self.active.pop()
            self.dirty.update(j % n for j in range(i - 11, i + 12))
            self._eff.clear()
            self._sprout.clear()

    def coverage(self) -> float:
        return sum(self.d) / (self.n * MAT_MAX)

    def occupied(self) -> float:
        """边缘一圈里有菌毯的长度占比"""
        return len(self.active) / self.n

    # ── 绘制用 ──
    def eff(self, i: int) -> float:
        """画出来的厚度：平滑后叠加大小两层起伏，满厚的地方也有丘陵和洼地"""
        i %= self.n
        if i in self._eff:
            return self._eff[i]
        d, n = self.d, self.n
        a, b, c = d[(i - 1) % n], d[i], d[(i + 1) % n]
        v = 0.0
        if a or b or c:
            base = (a + 2 * b + c) / 4
            hills = 0.55 + 0.9 * _vnoise(i / 23, 11)
            bumps = (_vnoise(i / 6.5, 12) - 0.5) * 2.2
            v = base * hills + bumps * min(1.0, base / 3)
            if b:
                v = max(v, 0.8)
            v = max(0.0, min(MAT_MAX + 0.5, v))
        self._eff[i] = v
        return v

    def _sprout_candidate(self, i: int) -> bool:
        i %= self.n
        edge, pos = self.edge_of(i)
        if not 6 <= pos < self.edge_len(edge) - 6 or self.d[i] < MAT_SPROUT_DEPTH - _hash(i, 8) % 3:
            return False
        return _hash(i, 7) / MAXH < 0.25 * _vnoise(i / 41, 9) ** 2     # 有的地方一小片，有的地方光秃

    def sprout_at(self, i: int) -> tuple[int, bool, int] | None:
        """第 i 列的小蘑菇：(造型, 是否翻转, 底行离屏幕边几格)；没有则 None"""
        i %= self.n
        if i in self._sprout:
            return self._sprout[i]
        found = None
        if self._sprout_candidate(i) and not any(self._sprout_candidate(j) for j in range(i - 5, i)):
            variants = sprout_variants()
            v = _hash(i, 21) % len(variants)
            base = int(self.eff(i)) - 1 - _hash(i, 23) % 3
            if base + len(variants[v][0]) <= MAT_STRIP:
                found = (v, bool(_hash(i, 22) & 1), base)
        self._sprout[i] = found
        return found

    def tendril(self, j: int) -> list[tuple[int, int]]:
        """第 j 列伸出去的菌丝：[(列偏移, 离屏幕边几格)]，长短不一、会拐弯，疏密按区域变化"""
        j %= self.n
        D = self.eff(j)
        if D < 2.5:
            return []
        hairy = _vnoise(j / 17, 31)
        if _hash(j, 55) / MAXH >= 0.05 + 0.4 * hairy ** 2:
            return []
        cells, off, k0 = [], 0, math.ceil(D)
        for t in range(1 + _hash(j, 56) % (2 + int(3 * hairy))):
            r = _hash(j, 60 + t) % 10
            if t and r < 3:
                off = max(-2, off - 1)
            elif t and r > 6:
                off = min(2, off + 1)
            cells.append((off, k0 + t))
        return cells

    # ── 存档 ──
    def resized(self, cols: int, rows: int) -> "Mycelium":
        """屏幕分辨率变了：每条边按比例重新采样"""
        if (cols, rows) == (self.cols, self.rows):
            return self
        new = Mycelium(cols, rows)
        for i in range(new.n):
            edge, pos = new.edge_of(i)
            old = min(self.edge_len(edge) - 1, pos * self.edge_len(edge) // new.edge_len(edge))
            new.d[i] = self.d[self.index_at(edge, old)]
        new.active = [i for i, v in enumerate(new.d) if v]
        return new

    def to_json(self) -> dict:
        return {"cols": self.cols, "rows": self.rows, "depth": base64.b64encode(bytes(self.d)).decode()}

    @classmethod
    def from_json(cls, data) -> "Mycelium | None":
        try:
            m = cls(int(data["cols"]), int(data["rows"]), base64.b64decode(data["depth"]))
        except (TypeError, KeyError, ValueError):
            return None
        return m if len(m.d) == m.n else None


MAT_INK, MAT_DUST, MAT_PAPER, MAT_HALO = 0xFF111111, 0xFF55524C, 0xFFFAF9F4, 0xC8FAF9F4
SPROUT_NAMES = ("mat_sprout", "mat_sprout_b", "mat_sprout_c", "mat_sprout_d")
SPROUT_REACH = 5           # 小蘑菇（含描边）左右最多伸出几列


@lru_cache(maxsize=1)
def sprout_variants() -> tuple[tuple[tuple[str, ...], dict[str, int]], ...]:
    out = []
    for name in SPROUT_NAMES:
        drawn = load_pxl(name)
        if drawn:
            out.append((tuple(add_halo(list(drawn[0]))),
                        {ch: 0xFF000000 | int(h.lstrip("#"), 16) for ch, h in drawn[1]}))
    if not out:
        out.append((tuple(add_halo(["..###..", ".##o##.", "#######", "..#o#..", "..#o#.."])), {"#": MAT_INK, "o": MAT_PAPER}))
    return tuple(out)


def mat_cell(i: int, k: int, D: float, side: float, sprouts: list, hairs: dict[int, int]) -> int:
    """环形下标 i、离屏幕边 k 格处的颜色（ARGB，0 = 透明）"""
    variants = sprout_variants()
    for delta, (v, flip, base) in sprouts:
        art, pal = variants[v]
        w = len(art[0])
        r, c = len(art) - 1 - (k - base), delta + w // 2
        if flip:
            c = w - 1 - c
        if 0 <= r < len(art) and 0 <= c < w:
            ch = art[r][c]
            if ch == HALO:
                if k >= D:
                    return MAT_HALO
            elif ch != ".":
                return pal.get(ch, MAT_INK)
    s = D - k
    if s > 0:
        if s <= 0.5 + 1.7 * _vnoise(i / 9, 13):         # 表面一档深灰，宽窄沿边缘变化，偶有黑团顶出
            return MAT_INK if _vnoise2(i / 2.3, k / 2.3, 17) > 0.66 else MAT_DUST
        if _hash(i, k + 101) % 37 == 0:                   # 零星白色孢子
            return MAT_PAPER
        return MAT_DUST if _vnoise2(i / 3.1, k / 2.4, 41) > 0.7 else MAT_INK   # 成团斑驳
    if k in hairs:
        return hairs[k]
    return MAT_HALO if s > -1 or k < side else 0


class MatStrip(QWidget):
    """屏幕一条边上的菌毯窗口：透明、鼠标穿透、不抢焦点。image 每像素 = 1 格。"""

    def __init__(self, edge: str, rect: QRect, layer: str):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.NoDropShadowWindowHint)
        flags |= Qt.WindowType.WindowStaysOnTopHint if layer == "top" else Qt.WindowType.WindowStaysOnBottomHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle(f"fungi · mat {edge}")
        self.edge = edge
        self.setGeometry(rect)
        self.image = QImage(rect.width() // PX, rect.height() // PX, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)

    def render_column(self, m: Mycelium, i: int):
        edge, pos = m.edge_of(i)
        n, length, img = m.n, m.edge_len(edge), self.image
        i %= n
        D = m.eff(i)
        side = max(m.eff(i - 1), m.eff(i + 1))
        sprouts = []
        for delta in range(-SPROUT_REACH, SPROUT_REACH + 1):
            sp = m.sprout_at(i - delta)
            if sp:
                sprouts.append((delta, sp))
        hairs = {}
        for j in range(i - 2, i + 3):
            cells = m.tendril(j)
            for t, (off, k) in enumerate(cells):
                if (j + off) % n == i:
                    hairs[k] = MAT_INK if t == len(cells) - 1 else MAT_DUST
        empty = not (D or side or sprouts or hairs)
        for k in range(MAT_STRIP):
            argb = 0 if empty else mat_cell(i, k, D, side, sprouts, hairs)
            if edge == "top":
                img.setPixel(pos, k, argb)
            elif edge == "bottom":
                img.setPixel(length - 1 - pos, MAT_STRIP - 1 - k, argb)
            elif edge == "right":
                img.setPixel(MAT_STRIP - 1 - k, pos, argb)
            else:
                img.setPixel(k, length - 1 - pos, argb)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawImage(self.rect(), self.image)
        p.end()


# ───────────────────────────── 喷孢菌、孢子弹、菌斑 ─────────────────────────────

EDGE_POSE = {"bottom": ((0, -1), 0), "top": ((0, 1), 180), "left": ((1, 0), 90), "right": ((-1, 0), -90)}   # 朝屏幕中心的方向、旋转角


@lru_cache(maxsize=64)
def spitter_image(frame: str, rotation: int = 0, reveal: int = 99, wither: bool = False) -> QImage:
    """喷孢菌的一帧（idle / blink / charge / shoot），reveal = 从根部往上露出几行"""
    drawn = load_pxl("spitter" if frame == "idle" else f"spitter_{frame}") or load_pxl("spitter")
    if drawn:
        rows, colors = list(drawn[0]), {ch: QColor(h) for ch, h in drawn[1]}
    else:
        rows, colors = ["..###..", ".#ooo#.", "#ooooo#", "#o#o#o#", "#ooooo#", ".#####."], {"#": INK, "o": PAPER}
    if reveal < len(rows):
        rows = ["." * len(rows[0])] * (len(rows) - reveal) + rows[len(rows) - reveal:]
    if wither:
        colors = withered(colors)
    halo = QColor(PAPER)
    halo.setAlpha(235)
    colors[HALO] = halo
    img = paint_rows(add_halo(rows), colors)
    return img.transformed(QTransform().rotate(rotation)) if rotation else img


class SpitterWidget(QWidget):
    """扎根在边缘菌毯上的喷孢菌：不会动，定期喷孢子"""
    MARGIN = 8

    def __init__(self, colony: "Colony", layer: str):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint
        flags |= Qt.WindowType.WindowStaysOnTopHint if layer == "top" else Qt.WindowType.WindowStaysOnBottomHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("fungi · spitter")
        self.colony = colony
        self.blinks = [time.time() + random.uniform(2, 5)]
        self._key = None
        self.place()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    def pose(self, now: float) -> tuple[str, int, int, tuple[int, int], bool]:
        sp, m = self.colony.spitter, self.colony.mat
        edge = m.edge_of(sp["i"])[0]
        (ux, uy), rot = EDGE_POSE[edge]
        age = now - sp["born"]
        reveal = int(22 * age / 2.5) if age < 2.5 else 99
        shake = (0, 0)
        starving = self.colony.starving()
        if now - sp.get("shot_at", 0) < 0.35:
            frame = "shoot"
        elif sp["next_at"] - now < 0.8 and not starving:
            frame = "charge"
            j = 2 if int(now * 20) % 2 else -2
            shake = (abs(uy) * j, abs(ux) * j)          # 沿着边抖
        elif any(b <= now < b + 0.15 for b in self.blinks):
            frame = "blink"
        else:
            frame = "idle"
        return frame, rot, reveal, shake, starving

    def place(self):
        c, m, sp = self.colony, self.colony.mat, self.colony.spitter
        (ux, uy), rot = EDGE_POSE[m.edge_of(sp["i"])[0]]
        img = spitter_image("idle", rot)
        r = spitter_image("idle").height() / 2 - PX          # 根部中心到图中心的距离
        bx, by = c.spitter_base()
        ix, iy = img.width() / 2 - ux * r, img.height() / 2 - uy * r
        self.setGeometry(int(bx - ix) - self.MARGIN, int(by - iy) - self.MARGIN,
                         img.width() + 2 * self.MARGIN, img.height() + 2 * self.MARGIN)

    def tick(self):
        now = time.time()
        self.blinks = [b for b in self.blinks if now < b + 0.15] or [now + random.uniform(3, 8)]
        key = self.pose(now)
        if key != self._key:
            self._key = key
            self.update()

    def paintEvent(self, _):
        frame, rot, reveal, (dx, dy), starving = self.pose(time.time())
        p = QPainter(self)
        p.drawImage(self.MARGIN + dx, self.MARGIN + dy, spitter_image(frame, rot, reveal, starving))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.colony.poke_spitter()

    def enterEvent(self, e):
        self.setToolTip(self.colony.spitter_tip())

    def contextMenuEvent(self, e):
        self.colony.spitter_menu(e.globalPos())


class SporeShot(QWidget):
    """喷出去的孢子：沿抛物线飞到落点，落地后交给菌落处理"""
    SIZE = 64

    def __init__(self, colony: "Colony", start: tuple[float, float], end: tuple[float, float], outcome: str):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("fungi · spore")
        self.colony, self.start, self.end, self.outcome = colony, start, end, outcome
        dist = math.dist(start, end)
        self.dur = min(1.8, max(0.6, 0.45 + dist / 1300))
        self.lift = 50 + dist * 0.22
        self.t0 = time.time()
        self.landed_at: float | None = None
        self.done = False
        self.resize(self.SIZE, self.SIZE)
        self.move_to(start)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def point(self, k: float) -> tuple[float, float]:
        (ax, ay), (bx, by) = self.start, self.end
        cx, cy = (ax + bx) / 2, min(ay, by) - self.lift
        return ((1 - k) ** 2 * ax + 2 * (1 - k) * k * cx + k * k * bx,
                (1 - k) ** 2 * ay + 2 * (1 - k) * k * cy + k * k * by)

    def move_to(self, pt: tuple[float, float]):
        self.move(int(pt[0] - self.SIZE / 2), int(pt[1] - self.SIZE / 2))

    def finish(self):
        self.done = True
        self.timer.stop()
        self.close()

    def tick(self):
        now = time.time()
        if self.landed_at is None:
            k = (now - self.t0) / self.dur
            if k < 1:
                self.move_to(self.point(k))
            else:
                self.landed_at = now
                self.move_to(self.end)
                self.colony.land_spore(self.outcome, *self.end)
                if self.outcome != "vanish":
                    self.finish()
                    return
        elif now - self.landed_at > 0.5:
            self.finish()
            return
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        c = self.SIZE / 2
        if self.landed_at is None:
            pm = art_pixmap(0, 3)
            p.drawPixmap(int(c - pm.width() / 2), int(c - pm.height() / 2), pm)
        else:                                            # 消失：散成一小团灰
            k = min(1.0, (time.time() - self.landed_at) / 0.5)
            paper, dust = QColor(PAPER), QColor(DUST)
            paper.setAlpha(int(220 * (1 - k)))
            dust.setAlpha(int(255 * (1 - k)))
            for a in range(6):
                ang = a * math.pi / 3 + 0.4
                x, y = c + math.cos(ang) * (4 + 18 * k), c + math.sin(ang) * (4 + 18 * k)
                p.fillRect(int(x) - 3, int(y) - 3, 6, 6, paper)
                p.fillRect(int(x) - 2, int(y) - 2, 4, 4, dust)
        p.end()


@dataclass
class Patch:
    """桌面中间的一块菌斑（孢子落地形成）"""
    x: int
    y: int
    r: float = 1.0
    max: int = 5
    seed: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Patch":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


PATCH_HALF = PATCH_MAX[1] + 8          # 菌斑窗口半宽（格）：最大半径 + 再被打中的余量 + 起伏 + 描边


def patch_cell(p: Patch, u: int, v: int) -> int:
    """菌斑中心偏移 (u, v) 格处的颜色（ARGB）"""
    dist = math.hypot(u, v)
    if dist > p.r * 1.3 + 3:
        return 0
    x = (math.atan2(v, u) + math.pi) / (2 * math.pi) * 7    # 一圈 7 段起伏，首尾相接
    j = math.floor(x)
    f = x - j
    f = f * f * (3 - 2 * f)
    wa, wb = _hash(j % 7, p.seed) / MAXH, _hash((j + 1) % 7, p.seed) / MAXH
    R = max(0.6, p.r * (0.7 + 0.6 * (wa + (wb - wa) * f)))
    s, sx = R - dist, p.seed % 997
    if s > 0:
        if s <= 0.4 + 1.2 * _vnoise2(u / 3 + sx, v / 3, 13):
            return MAT_INK if _vnoise2(u / 2.3 + sx, v / 2.3, 17) > 0.66 else MAT_DUST
        if _hash(u * 131 + v, p.seed + 101) % 37 == 0:
            return MAT_PAPER
        return MAT_DUST if _vnoise2(u / 3.1 + sx, v / 2.4, 41) > 0.7 else MAT_INK
    if s > -1:
        return MAT_HALO
    if s > -2.5 and p.r >= 2 and _hash(u * 131 + v, p.seed + 55) % 9 == 0:
        return MAT_DUST
    return 0


class PatchView(QWidget):
    """一块菌斑的窗口：透明、鼠标穿透"""

    def __init__(self, patch: Patch, layer: str):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.NoDropShadowWindowHint)
        flags |= Qt.WindowType.WindowStaysOnTopHint if layer == "top" else Qt.WindowType.WindowStaysOnBottomHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("fungi · patch")
        self.patch = patch
        side = 2 * PATCH_HALF + 1
        self.image = QImage(side, side, QImage.Format.Format_ARGB32)
        self.setGeometry(int(patch.x - (PATCH_HALF + 0.5) * PX), int(patch.y - (PATCH_HALF + 0.5) * PX), side * PX, side * PX)
        self.shown_r = None
        self.render()

    def render(self):
        step = int(self.patch.r * 4)
        if step == self.shown_r:
            return
        self.shown_r = step
        self.image.fill(Qt.GlobalColor.transparent)
        for u in range(-PATCH_HALF, PATCH_HALF + 1):
            for v in range(-PATCH_HALF, PATCH_HALF + 1):
                argb = patch_cell(self.patch, u, v)
                if argb:
                    self.image.setPixel(u + PATCH_HALF, v + PATCH_HALF, argb)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawImage(self.rect(), self.image)
        p.end()


# ───────────────────────────── 菌落（存档、生长、繁殖） ─────────────────────────────

class Colony:
    def __init__(self, data_dir: Path, fast: bool = False):
        self.data_dir = data_dir
        self.save_path = data_dir / "save.json"
        self.fast = fast
        self.passive_mult = 30 if fast else 1
        self.gain_mult = 3 if fast else 1
        self.widgets: dict[str, CreatureWidget] = {}
        self.creatures: list[Creature] = []
        self.name_i = 0
        self.on_top = True
        self.last_tick = time.time()
        self.mat: Mycelium | None = None
        self.mat_layer = "top"
        self.mat_views: dict[str, MatStrip] = {}
        self.mat_acc = 0.0
        self.offline_elapsed = 0.0
        self.spitter: dict | None = None
        self.spitter_view: SpitterWidget | None = None
        self.patches: list[Patch] = []
        self.patch_views: dict[int, PatchView] = {}
        self.shots: list[SporeShot] = []
        self.devour = DEVOUR
        self.protected_dirs = [PROJECT_DIR, data_dir.resolve()]
        self.offline_delta: dict[str, float] = {}
        self.hidden_for_fullscreen = False
        self.views_stale = False
        self.watched_screen = None
        self.screen_debounce = QTimer()
        self.screen_debounce.setSingleShot(True)
        self.screen_debounce.timeout.connect(self.on_screen_changed)

        offline = self.load()
        self.grow_mat_offline(self.offline_elapsed)
        self.build_mat_views()
        for c in self.creatures:
            self.spawn_widget(c)
        for c in list(self.creatures):
            self.after_growth(self.widgets[c.id], c.stage, quiet=True)
            delta = self.offline_delta.get(c.id, 0.0)
            if delta >= 1:
                self.widgets[c.id].say(f"+{int(delta)} 睡觉时长的", delay=0.6)
            elif delta <= -1:
                self.widgets[c.id].say(f"{int(delta)} 饿瘦了", delay=0.6)
        self.save()

        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)
        self.autosave = QTimer()
        self.autosave.timeout.connect(self.save)
        self.autosave.start(30_000)
        self.spitter_timer = QTimer()
        self.spitter_timer.timeout.connect(self.spitter_tick)
        self.spitter_timer.start(100)
        self.fullscreen_timer = QTimer()
        self.fullscreen_timer.timeout.connect(self.check_fullscreen)
        if sys.platform == "win32":
            self.fullscreen_timer.start(1500)
        app = QGuiApplication.instance()
        if app:
            app.primaryScreenChanged.connect(self.watch_screen)
        self.watch_screen(QGuiApplication.primaryScreen(), initial=True)
        self.tray = self.make_tray()

    def sleep_after(self) -> float:
        base = 40 if self.fast else SLEEP_AFTER
        return base / 5 if time.localtime().tm_hour < 7 else base

    # ── 存档 ──
    def default_spot(self) -> tuple[int, int]:
        screen = QGuiApplication.primaryScreen()
        a = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        return a.right() - 260, a.bottom() - 40

    def new_creature(self, x: int, y: int, gen: int = 1) -> Creature:
        if not self.creatures and self.name_i == 0:
            name = "spores"
        else:
            base = NAMES[(self.name_i - 1) % len(NAMES)] if self.name_i else NAMES[0]
            loops = (self.name_i - 1) // len(NAMES) if self.name_i else 0
            name = base + (f"-{loops + 1}" if loops else "")
        self.name_i += 1
        return Creature(id=f"{int(time.time() * 1000):x}{random.randrange(4096):03x}", name=name, gen=gen, x=x, y=y)

    def load(self) -> float:
        data = None
        if self.save_path.exists():
            try:
                data = json.loads(self.save_path.read_text("utf-8"))
            except (OSError, ValueError) as err:
                broken = self.save_path.with_name(f"save.broken-{int(time.time())}.json")
                self.save_path.rename(broken)
                print(f"[fungi] 存档损坏，已备份到 {broken}: {err}", file=sys.stderr)
        offline = 0.0
        a = self.mat_area()
        self.mat = Mycelium(a.width() // PX, a.height() // PX)
        if data:
            self.name_i = data.get("name_i", 0)
            self.on_top = data.get("on_top", True)
            self.devour = data.get("devour", DEVOUR)
            self.mat_layer = data.get("mat_layer", "top")
            saved_mat = Mycelium.from_json(data.get("mat"))
            if saved_mat:
                self.mat = saved_mat.resized(self.mat.cols, self.mat.rows)
            self.patches = [Patch.from_dict(p) for p in data.get("patches", [])]
            sp = data.get("spitter")
            if sp and sp.get("edge") in EDGE_POSE:
                length = self.mat.edge_len(sp["edge"])
                pos = min(length - 1, max(0, int(sp.get("frac", 0.5) * length)))
                self.spitter = {"i": self.mat.index_at(sp["edge"], pos), "edge": sp["edge"], "frac": sp.get("frac", 0.5),
                                "born": sp.get("born", time.time()) - 99, "shots": sp.get("shots", 0),
                                "stats": {k: sp.get("stats", {}).get(k, 0) for k in OUTCOME_NAMES},
                                "next_at": time.time() + max(3.0, sp.get("next_in", 30) / self.passive_mult)}
            self.creatures = [Creature.from_dict(d) for d in data.get("creatures", [])]
            elapsed = time.time() - data.get("last_seen", time.time())
            self.offline_elapsed = max(0.0, elapsed)
            for c in self.creatures:
                self.offline_delta[c.id] = self.offline_metabolize(c, self.offline_elapsed * self.passive_mult)
                offline = max(offline, self.offline_delta[c.id])
                if QGuiApplication.screenAt(QPoint(c.x, c.y - 8)) is None:
                    c.x, c.y = self.default_spot()
        if not self.creatures:
            self.creatures = [self.new_creature(*self.default_spot())]
        return offline

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "last_seen": time.time(), "name_i": self.name_i, "on_top": self.on_top, "devour": self.devour,
                "mat_layer": self.mat_layer, "mat": self.mat.to_json(),
                "patches": [asdict(p) for p in self.patches],
                "spitter": ({k: self.spitter[k] for k in ("edge", "frac", "born", "shots", "stats")}
                            | {"next_in": max(0.0, self.spitter["next_at"] - time.time()) * self.passive_mult})
                if self.spitter else None,
                "creatures": [asdict(c) for c in self.creatures]}
        tmp = self.save_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.save_path)

    # ── 生长 ──
    def spawn_widget(self, c: Creature) -> CreatureWidget:
        w = CreatureWidget(self, c, self.on_top)
        self.widgets[c.id] = w
        if not self.hidden_for_fullscreen:
            w.show()
        return w

    def tick(self):
        now = time.time()
        dt = min(5.0, now - self.last_tick)
        self.last_tick = now
        for c in list(self.creatures):
            before = c.stage
            self.metabolize(c, dt * self.passive_mult)
            if c.stage != before or c.spores_due() > 0:
                self.after_growth(self.widgets[c.id], before)
        self.mat_acc += dt * self.passive_mult
        while self.mat_acc >= MAT_TICK:
            self.mat_acc -= MAT_TICK
            self.mat_step()
        if self.mat.dirty:
            self.render_mat()

    # ── 屏幕变化（换主屏、改分辨率/缩放、挪任务栏）与全屏 ──
    def watch_screen(self, screen, initial: bool = False):
        if self.watched_screen is not None:
            try:
                self.watched_screen.availableGeometryChanged.disconnect(self.schedule_screen_change)
            except (TypeError, RuntimeError):
                pass
        self.watched_screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self.schedule_screen_change)
        if not initial:
            self.schedule_screen_change()

    def schedule_screen_change(self, *_):
        self.screen_debounce.start(400)                  # 这类信号常常一次来好几个

    def on_screen_changed(self):
        """按新的桌面可用区域重新铺菌毯；跑到屏幕外的宠物拉回来"""
        a = self.mat_area()
        cols, rows = a.width() // PX, a.height() // PX
        if (cols, rows) != (self.mat.cols, self.mat.rows):
            self.mat = self.mat.resized(cols, rows)
            if self.spitter:
                sp = self.spitter
                length = self.mat.edge_len(sp["edge"])
                sp["i"] = self.mat.index_at(sp["edge"], min(length - 1, int(sp["frac"] * length)))
        for c in self.creatures:
            if QGuiApplication.screenAt(QPoint(c.x, c.y - 8)) is None:
                c.x, c.y = self.default_spot()
                self.widgets[c.id].place()
        if self.hidden_for_fullscreen:
            self.views_stale = True                      # 退出全屏再重铺
        else:
            self.build_mat_views()
        self.save()

    def check_fullscreen(self):
        full = foreground_fullscreen()
        if full != self.hidden_for_fullscreen:
            self.set_fullscreen_hidden(full)

    def set_fullscreen_hidden(self, hidden: bool):
        """前台全屏时把菌毯、菌斑、喷孢菌和宠物都藏起来，退出全屏再出来（期间照样长）"""
        self.hidden_for_fullscreen = hidden
        views = list(self.mat_views.values()) + list(self.patch_views.values()) + [self.spitter_view] + list(self.widgets.values())
        for v in views:
            if v:
                v.setVisible(not hidden)
        if not hidden:
            if self.views_stale:
                self.views_stale = False
                self.build_mat_views()
            for w in self.widgets.values():
                w.raise_()

    # ── 饥饿 ──
    def metabolize(self, c: Creature, seconds: float):
        """在线的 seconds 秒（已乘速度倍率）：吃饱正常长、饿了长一半、饿扁了掉营养"""
        hours = seconds / 3600
        if c.satiety > 0:
            c.nutrition += seconds / PASSIVE_SECONDS * c.growth_factor
            c.satiety = max(0.0, c.satiety - hours * SATIETY_MAX / HUNGER_HOURS)
        else:
            c.nutrition = max(0.0, c.nutrition - hours * STARVE_LOSS)

    def offline_metabolize(self, c: Creature, seconds: float) -> float:
        """关闭期间：先吃老本长（有上限），饿扁之后掉营养（有上限）。返回营养变化。"""
        hours, rate = seconds / 3600, SATIETY_MAX / HUNGER_HOURS
        fed_h = min(hours, c.satiety / rate)
        full_h = min(fed_h, max(0.0, (c.satiety - HUNGRY_AT) / rate))
        grow = min(OFFLINE_CAP, (full_h + (fed_h - full_h) * 0.5) * 3600 / PASSIVE_SECONDS)
        starve = min(OFFLINE_STARVE_CAP, (hours - fed_h) * STARVE_LOSS)
        before = c.nutrition
        c.satiety = max(0.0, c.satiety - hours * rate)
        c.nutrition = max(0.0, c.nutrition + grow - starve)
        return c.nutrition - before

    def starving(self) -> bool:
        """全体都饿扁 / 休眠了"""
        return bool(self.creatures) and all(c.growth_factor == 0 for c in self.creatures)

    # ── 菌毯 ──
    def mat_area(self) -> QRect:
        screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)

    def mat_index(self, c: Creature) -> int:
        a = self.mat_area()
        return self.mat.nearest((c.x - a.x()) / PX, (c.y - a.y()) / PX)

    def mat_step(self):
        vigor = 0.0
        for c in self.creatures:
            v = MAT_VIGOR[c.stage] * c.growth_factor
            vigor += v
            if random.random() < MAT_SEED * v and self.mat.seed(self.mat_index(c)):
                w = self.widgets.get(c.id)
                if w and c.stage and not w.asleep:
                    w.puff(2)
        budget = MAT_RATE * vigor if vigor else MAT_RECEDE
        steps = int(budget) + (random.random() < budget % 1)
        if vigor:
            self.mat.grow(steps)
            self.grow_patches(PATCH_GROW * (0.5 + min(vigor, 6) / 6))
        else:
            self.mat.shrink(steps)                        # 全体饿扁：菌毯慢慢退
        self.check_spitter()
        if self.spitter_view:
            self.spitter_view.place()

    def grow_mat_offline(self, elapsed: float):
        steps = elapsed * self.passive_mult / MAT_TICK
        if steps < 1:
            return
        for c in self.creatures:
            if random.random() < 1 - (1 - MAT_SEED * MAT_VIGOR[c.stage] * c.growth_factor) ** steps:
                self.mat.seed(self.mat_index(c))
        vigor = sum(MAT_VIGOR[c.stage] * c.growth_factor for c in self.creatures)
        if vigor:
            self.mat.grow(int(min(MAT_OFFLINE_CAP, steps * MAT_RATE * vigor)))
            self.grow_patches(steps * PATCH_GROW * (0.5 + min(vigor, 6) / 6))
        else:
            self.mat.shrink(int(min(MAT_OFFLINE_CAP, steps * MAT_RECEDE)))
        self.check_spitter(quiet=True)

    def build_mat_views(self):
        old = list(self.mat_views.values()) + list(self.patch_views.values()) + [self.spitter_view]
        for v in old:
            if v:
                v.close()
        self.mat_views, self.patch_views, self.spitter_view = {}, {}, None
        if self.mat_layer != "hidden":
            a, m, t = self.mat_area(), self.mat, MAT_STRIP * PX
            w, h = m.cols * PX, m.rows * PX
            rects = {"top": QRect(a.x(), a.y(), w, t), "bottom": QRect(a.x(), a.y() + h - t, w, t),
                     "left": QRect(a.x(), a.y(), t, h), "right": QRect(a.x() + w - t, a.y(), t, h)}
            self.mat_views = {edge: MatStrip(edge, rect, self.mat_layer) for edge, rect in rects.items()}
            self.render_mat(full=True)
            for p in self.patches:
                self.add_patch_view(p)
            if self.spitter:
                self.spitter_view = SpitterWidget(self, self.mat_layer)
            for v in list(self.mat_views.values()) + [self.spitter_view]:
                if v and not self.hidden_for_fullscreen:
                    v.show()
        for w in self.widgets.values():
            w.raise_()

    # ── 菌斑 ──
    def add_patch_view(self, p: Patch):
        if self.mat_layer == "hidden":
            return
        v = PatchView(p, self.mat_layer)
        self.patch_views[id(p)] = v
        if not self.hidden_for_fullscreen:
            v.show()
        for w in self.widgets.values():
            w.raise_()

    def grow_patches(self, amount: float):
        for p in self.patches:
            if p.r < p.max:
                p.r = min(p.max, p.r + amount)
                if id(p) in self.patch_views:
                    self.patch_views[id(p)].render()

    def mat_at(self, x: float, y: float):
        """孢子在 (x, y) 落地形成菌毯：贴边就加厚边缘菌毯，打中已有菌斑就让它长大，否则长出新菌斑"""
        a, m = self.mat_area(), self.mat
        cx, cy = (x - a.x()) / PX, (y - a.y()) / PX
        if min(cx, cy, m.cols - 1 - cx, m.rows - 1 - cy) < MAT_MAX + 4:
            i = m.nearest(cx, cy)
            m.seed(i)
            m.grow(12, near=i)
            self.render_mat()
            return
        hit = next((p for p in self.patches if math.hypot(p.x - x, p.y - y) < (p.r + 2) * PX), None)
        if hit is None and len(self.patches) >= MAX_PATCHES:
            hit = random.choice(self.patches)
        if hit:
            hit.max = min(PATCH_MAX[1] + 2, hit.max + 1)
            hit.r = min(hit.max, hit.r + 1)
            if id(hit) in self.patch_views:
                self.patch_views[id(hit)].render()
            return
        p = Patch(int(x), int(y), 1.0, random.randint(*PATCH_MAX), random.randrange(1 << 30))
        self.patches.append(p)
        self.add_patch_view(p)

    # ── 喷孢菌 ──
    def shot_gap(self) -> float:
        return random.uniform(*SPITTER_EVERY) / self.passive_mult

    def check_spitter(self, quiet: bool = False):
        m = self.mat
        if self.spitter or m.occupied() < SPITTER_AT:
            return
        cands = [i for i in m.active if m.d[i] >= MAT_SPROUT_DEPTH
                 and 12 <= m.edge_of(i)[1] < m.edge_len(m.edge_of(i)[0]) - 12]
        if not cands:
            return
        cands.sort(key=lambda i: m.eff(i) + random.random() * 2, reverse=True)
        self.plant_spitter(random.choice(cands[:20]), quiet)

    def plant_spitter(self, i: int, quiet: bool = False):
        m = self.mat
        edge, pos = m.edge_of(i)
        now = time.time()
        self.spitter = {"i": i % m.n, "edge": edge, "frac": (pos + 0.5) / m.edge_len(edge), "born": now - (99 if quiet else 0),
                        "shots": 0, "stats": {k: 0 for k in OUTCOME_NAMES}, "next_at": now + 3 + self.shot_gap()}
        if self.mat_layer != "hidden":
            if self.spitter_view:
                self.spitter_view.close()
            self.spitter_view = SpitterWidget(self, self.mat_layer)
            if not self.hidden_for_fullscreen:
                self.spitter_view.show()
            for w in self.widgets.values():
                w.raise_()
        self.save()

    def spitter_base(self) -> tuple[float, float]:
        """喷孢菌根部中心的屏幕坐标：埋进菌毯表面下 2 格"""
        a, m, i = self.mat_area(), self.mat, self.spitter["i"]
        edge, pos = m.edge_of(i)
        w, h = m.cols * PX, m.rows * PX
        u, v = (pos + 0.5) * PX, max(0.0, m.eff(i) - 2) * PX
        return {"top": (a.x() + u, a.y() + v), "bottom": (a.x() + w - u, a.y() + h - v),
                "right": (a.x() + w - v, a.y() + u), "left": (a.x() + v, a.y() + h - u)}[edge]

    def pick_outcome(self, rng=random) -> str:
        r, acc = rng.random(), 0.0
        for name, p in SHOT_OUTCOMES:
            acc += p
            if r < acc:
                return name
        return SHOT_OUTCOMES[-1][0]

    def shot_target(self, start: tuple[float, float]) -> tuple[float, float]:
        a = self.mat_area()
        pt = start
        for _ in range(12):
            pt = (random.uniform(a.left() + 40, a.right() - 40), random.uniform(a.top() + 40, a.bottom() - 40))
            if math.dist(pt, start) > 240:
                break
        return pt

    def spitter_tick(self):
        self.shots = [s for s in self.shots if not s.done]
        if self.spitter and time.time() >= self.spitter["next_at"]:
            if self.starving():                           # 全体饿扁：不喷，往后推
                self.spitter["next_at"] = time.time() + self.shot_gap()
            else:
                self.shoot()

    def shoot(self, outcome: str | None = None, target: tuple[float, float] | None = None) -> SporeShot:
        sp, now = self.spitter, time.time()
        (ux, uy), _ = EDGE_POSE[self.mat.edge_of(sp["i"])[0]]
        bx, by = self.spitter_base()
        reach = spitter_image("idle").height() - 2 * PX
        start = (bx + ux * reach, by + uy * reach)
        shot = SporeShot(self, start, target or self.shot_target(start), outcome or self.pick_outcome())
        if not self.hidden_for_fullscreen:
            shot.show()
        self.shots.append(shot)
        sp["shots"] += 1
        sp["shot_at"], sp["next_at"] = now, now + self.shot_gap()
        return shot

    def land_spore(self, outcome: str, x: float, y: float):
        if outcome == "spore" and len(self.creatures) >= MAX_COLONY:
            outcome = "mat"
        if self.spitter:
            self.spitter["stats"][outcome] += 1
        if outcome == "spore":
            c = self.new_creature(int(x), int(y))
            c.satiety = HUNGRY_AT                         # 野生孢子，一落地就有点饿
            self.creatures.append(c)
            w = self.spawn_widget(c)
            w.play("land")
            w.say("·")
        elif outcome == "mat":
            self.mat_at(x, y)
        self.save()

    def poke_spitter(self):
        sp, now = self.spitter, time.time()
        if sp and sp["next_at"] - now > 1.2 and now - sp.get("shot_at", 0) > 5:
            sp["next_at"] = now + 0.8                    # 戳一下就提前喷

    def spitter_tip(self) -> str:
        sp = self.spitter
        if not sp:
            return ""
        st = sp["stats"]
        return (f"喷孢菌\n下一次 ~{max(0, int(sp['next_at'] - time.time()))}s\n"
                f"喷了 {sp['shots']} 次：" + " · ".join(f"{OUTCOME_NAMES[k]} {st[k]}" for k in OUTCOME_NAMES))

    def spitter_menu(self, pos: QPoint):
        sp = self.spitter
        if not sp:
            return
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        for line in (f"NAME   喷孢菌", f"AGE    {fmt_age(time.time() - sp['born'])}", f"SHOTS  {sp['shots']}",
                     "  ".join(f"{OUTCOME_NAMES[k]} {sp['stats'][k]}" for k in OUTCOME_NAMES),
                     f"NEXT   ~{max(0, int(sp['next_at'] - time.time()))}s"):
            m.addAction(line).setEnabled(False)
        m.addSeparator()
        m.addAction("现在喷！", lambda: sp.__setitem__("next_at", time.time() + 0.8))
        self.mat_menu(m)
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        m.exec(pos)

    def render_mat(self, full: bool = False):
        m = self.mat
        if full:
            todo = {j % m.n for i in m.active for j in range(i - 12, i + 13)}
        else:
            todo = m.dirty
        m.dirty = set()
        if not self.mat_views:
            return
        touched = set()
        for i in todo:
            edge = m.edge_of(i)[0]
            self.mat_views[edge].render_column(m, i)
            touched.add(edge)
        for edge in touched:
            self.mat_views[edge].update()

    def set_mat_layer(self, layer: str):
        self.mat_layer = layer
        self.build_mat_views()
        self.save()

    def mat_menu(self, parent: QMenu) -> QMenu:
        sub = parent.addMenu("菌毯")
        sub.setStyleSheet(MENU_QSS)
        group = QActionGroup(sub)
        for key, label in (("top", "铺在窗口上面"), ("bottom", "只铺在桌面上"), ("hidden", "隐藏")):
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.mat_layer == key)
            act.triggered.connect(lambda _=False, k=key: self.set_mat_layer(k))
            group.addAction(act)

        def refresh():
            sub.setTitle(f"菌毯  {self.mat.coverage() * 100:.1f}%" + (f" · 菌斑 {len(self.patches)}" if self.patches else ""))
            for act, key in zip(group.actions(), ("top", "bottom", "hidden")):
                act.setChecked(self.mat_layer == key)
        refresh()
        parent.aboutToShow.connect(refresh)
        return sub

    def feed(self, widget: CreatureWidget, paths: list[Path]):
        c = widget.c
        before = c.stage
        widget.touch()
        was = c.mood
        eaten, rejected = [], []
        for path in paths[:MAX_ITEMS_PER_DROP]:
            food, why = digest(path)
            if food is None:
                rejected.append(why)
                continue
            if self.devour:
                if food.targets and all(self.is_protected(t) for t in food.targets):
                    rejected.append("这个不能吃")
                    continue
                done = self.devour_food(food)
                if food.targets and not done:
                    rejected.append("咬不动…")
                    continue
                if done < len(food.targets):
                    food.value = max(1, round(food.value * done / len(food.targets)))
            value, note = food.value, food.note
            if food.key in c.eaten:
                value, note = max(1, value // 4), note or "嚼过了"
            else:
                c.eaten = (c.eaten + [food.key])[-500:]
            value *= self.gain_mult
            c.nutrition += value
            c.satiety = min(SATIETY_MAX, c.satiety + value * SATIETY_PER_FOOD)
            c.feeds += 1
            c.log = (c.log + [[int(time.time()), food.label, value]])[-50:]
            eaten.append((value, note))
        if eaten and self.mat.active:
            self.mat.grow(int(sum(v for v, _ in eaten) * MAT_FEED), near=self.mat_index(c))
            self.render_mat()
        if len(paths) > MAX_ITEMS_PER_DROP:
            rejected.append("吃不下了")

        if eaten:
            widget.play("eat")
            widget.crumbs()
            for i, (value, note) in enumerate(eaten):
                widget.say(f"+{value} {note}".strip(), delay=i * 0.35)
            if rejected:
                widget.say(rejected[0], delay=len(eaten) * 0.35)
            if was in ("starving", "dormant"):
                widget.say("活过来了！", big=True)
        elif rejected:
            widget.play("shake")
            widget.say(rejected[0])
        self.after_growth(widget, before)
        self.save()

    def is_protected(self, path: Path) -> bool:
        """游戏自己的程序目录、存档目录里的东西不吃"""
        try:
            real = path.resolve()
        except OSError:
            return True
        return any(real == d or d in real.parents for d in self.protected_dirs)

    def devour_food(self, food: Food) -> int:
        """真的吃掉：删除（Windows 进回收站）能吃的文件，清掉吃空的目录，记进 eaten.log。返回吃掉几个文件。"""
        done = 0
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.data_dir / "eaten.log", "a", encoding="utf-8") as log:
            for f in food.targets:
                if self.is_protected(f):
                    continue
                try:
                    size = f.stat().st_size
                    devour_file(f)
                except OSError:
                    continue
                done += 1
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{size}\t{os.path.abspath(f)}\n")
        for d in food.dirs:
            if not self.is_protected(d):
                try:
                    os.rmdir(d)                            # 只删空目录
                except OSError:
                    pass
        return done

    def set_devour(self, on: bool):
        self.devour = on
        self.save()

    def after_growth(self, widget: CreatureWidget, before: int, quiet: bool = False):
        c = widget.c
        if c.stage < before:
            widget.refit()
            if not quiet:
                widget.say(f"缩回 {c.stage_name}…")
        elif c.stage > before:
            widget.refit()
            if not quiet:
                widget.play("grow")
                widget.say(f"→ {c.stage_name}!", delay=0.9, big=True)
        due = c.spores_due()
        if due <= 0:
            return
        released = 0
        for i in range(due):
            if len(self.creatures) >= MAX_COLONY:
                if not getattr(widget, "_full_said", False):
                    widget.say("菌落满了", delay=1.2)
                    widget._full_said = True
                break
            tx, ty = self.find_spot(c)
            child = self.new_creature(tx, ty, gen=c.gen + 1)
            self.creatures.append(child)
            c.released += 1
            released += 1
            start = (c.x, c.y - widget.sprite_rect.height() + PX * 2)
            child_w = self.spawn_widget(child)
            child_w.fly(start, (tx, ty), delay=1.3 + i * 0.4)
        if released:
            widget.say(f"噗—— ×{released}", delay=1.1, big=True)
            self.save()

    def find_spot(self, parent: Creature) -> tuple[int, int]:
        screen = QGuiApplication.screenAt(QPoint(parent.x, parent.y - 8)) or QGuiApplication.primaryScreen()
        a = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        best, best_d = (parent.x, parent.y), -1.0
        for _ in range(24):
            x = parent.x + random.choice((-1, 1)) * random.uniform(70, 230)
            y = parent.y + random.uniform(-70, 40)
            x = int(min(max(x, a.left() + 90), a.right() - 90))
            y = int(min(max(y, a.top() + 140), a.bottom() - 8))
            d = min(math.hypot(x - o.x, y - o.y) for o in self.creatures)
            if d > best_d:
                best, best_d = (x, y), d
            if d > 70:
                break
        return best

    # ── 菜单 ──
    def show_menu(self, widget: CreatureWidget, pos: QPoint):
        c = widget.c
        m = QMenu()
        m.setStyleSheet(MENU_QSS)

        def info(text):
            a = m.addAction(text)
            a.setEnabled(False)

        lo, hi = c.stage_floor(), c.next_goal()
        filled = round(10 * max(0.0, min(1.0, (c.nutrition - lo) / (hi - lo))))
        info(f"NAME   {c.name}")
        info(f"STAGE  {c.stage_name}")
        info(f"AGE    {fmt_age(time.time() - c.born)}")
        info(f"GEN    {c.gen}")
        info(f"FOOD   {'█' * filled}{'░' * (10 - filled)} {int(c.nutrition)}/{hi}")
        full = round(10 * c.satiety / SATIETY_MAX)
        info(f"FULL   {'█' * full}{'░' * (10 - full)} {int(c.satiety)}%  {MOOD_NAMES[c.mood]}")
        info(f"FEEDS  {c.feeds}   SPORES {c.released}")
        m.addSeparator()
        log_menu = m.addMenu("最近吃的")
        log_menu.setStyleSheet(MENU_QSS)
        for ts, label, value in reversed(c.log[-8:]):
            log_menu.addAction(f"[{time.strftime('%m-%d %H:%M', time.localtime(ts))}] {label}  +{value}").setEnabled(False)
        if not c.log:
            log_menu.addAction("（还没吃过东西）").setEnabled(False)
        m.addAction("喂文件…", lambda: self.feed_dialog(widget, folder=False))
        m.addAction("喂文件夹…", lambda: self.feed_dialog(widget, folder=True))
        devour = m.addAction("吞噬文件（吃掉后" + ("进回收站）" if sys.platform == "win32" else "删除）"))
        devour.setCheckable(True)
        devour.setChecked(self.devour)
        devour.toggled.connect(self.set_devour)
        m.addSeparator()
        top = m.addAction("总在最前")
        top.setCheckable(True)
        top.setChecked(self.on_top)
        top.toggled.connect(self.set_on_top)
        m.addAction("叫大家回来", self.gather)
        self.mat_menu(m)
        if len(self.creatures) > 1:
            m.addAction(f"放生 {c.name}…", lambda: self.release(widget))
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        m.exec(pos)

    def feed_dialog(self, widget: CreatureWidget, folder: bool):
        if folder:
            d = QFileDialog.getExistingDirectory(None, "喂一个文件夹", str(Path.home()))
            paths = [Path(d)] if d else []
        else:
            files, _ = QFileDialog.getOpenFileNames(None, "喂点文字", str(Path.home()), "文字 (*.txt *.md);;全部文件 (*)")
            paths = [Path(f) for f in files]
        if paths:
            self.feed(widget, paths)

    def set_on_top(self, on: bool):
        self.on_top = on
        for w in self.widgets.values():
            w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
            w.masked = None
            w.show()
            w.update_mask()
        self.save()

    def gather(self):
        screen = QGuiApplication.primaryScreen()
        a = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        for i, c in enumerate(self.creatures):
            row, col = divmod(i, 6)
            w = self.widgets[c.id]
            w.fly((c.x, c.y), (a.right() - 120 - col * 130, a.bottom() - 20 - row * 150), delay=i * 0.08, dur=0.6)

    def release(self, widget: CreatureWidget):
        c = widget.c
        ok = QMessageBox.question(None, "放生", f"让 {c.name}（{c.stage_name}）离开桌面？\n它的成长记录会被删除。")
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.creatures.remove(c)
        self.widgets.pop(c.id).close()
        self.save()

    def reset(self):
        ok = QMessageBox.question(None, "重新开始", "清空整个菌落，从一个 3×3 spores 重新开始？")
        if ok != QMessageBox.StandardButton.Yes:
            return
        for w in self.widgets.values():
            w.close()
        self.widgets.clear()
        self.creatures, self.name_i = [], 0
        self.mat = Mycelium(self.mat.cols, self.mat.rows)
        self.spitter, self.patches = None, []
        self.build_mat_views()
        c = self.new_creature(*self.default_spot())
        self.creatures.append(c)
        self.spawn_widget(c)
        self.save()

    def make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(QIcon(art_pixmap(ADULT)))
        tray.setToolTip("FUNGI.EXE")
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        m.addAction("叫大家回来", self.gather)
        self.mat_menu(m)
        m.addAction(f"存档位置：{self.save_path}").setEnabled(False)
        m.addAction("重新开始…", self.reset)
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        tray.setContextMenu(m)
        tray.show()
        tray._menu = m
        return tray


# ───────────────────────────── 入口 ─────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="FUNGI.EXE — 桌面真菌宠物 demo")
    ap.add_argument("--fast", action="store_true", help="调试：成长加速")
    default_dir = (Path(os.environ.get("APPDATA", Path.home())) if sys.platform == "win32"
                   else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))) / "fungi"
    ap.add_argument("--data-dir", type=Path, default=default_dir)
    args = ap.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = open(args.data_dir / "lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("[fungi] 已经在运行了（同一个存档目录只能开一个）", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("fungi")
    app.setQuitOnLastWindowClosed(False)
    colony = Colony(args.data_dir, fast=args.fast)
    app.aboutToQuit.connect(colony.save)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    pump = QTimer()                       # 让 Python 有机会处理 Ctrl+C / SIGTERM
    pump.timeout.connect(lambda: None)
    pump.start(300)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
