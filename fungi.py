#!/usr/bin/env python3
"""
FUNGI.EXE — 桌面真菌宠物（最小闭环 demo）

    桌面上的 3×3 黑色 spores → 拖入 .txt / .md / 文件夹 → 长大 → 变成蘑菇 → 放出新的 spores

运行:
    python3 fungi.py                 正常模式
    python3 fungi.py --fast          调试：成长加速（自然生长 ×30，喂食营养 ×3）
    python3 fungi.py --data-dir DIR  使用单独的存档目录

操作:
    拖文件 / 文件夹到它身上 = 喂食（只读取文件大小和数量，文件本身不会被改动）
    左键拖动 = 搬家    单击 = 戳一下    右键 = 状态和菜单
"""
from __future__ import annotations

import argparse
import fcntl
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

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import (QAction, QColor, QFont, QFontMetrics, QGuiApplication, QIcon,
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

EDIBLE_EXT = {".txt", ".md"}
MAX_ITEMS_PER_DROP = 5

NAMES = ["puff", "kino", "shii", "enoki", "morel", "nameko", "maitake", "chanty",
         "porcini", "reishi", "shimeji", "inky", "bolete", "truffle", "oyster"]

INK = QColor(17, 17, 17)
PAPER = QColor(250, 249, 244)

# ───────────────────────────── 像素精灵 ─────────────────────────────
# 字符: "#" 黑, "o" 白, "h" 描边(白), "." 透明

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
                        out[y][x] = "h"
    return ["".join(r) for r in out]


def spore_size(nutrition: float) -> int:
    """spores 阶段内部：3×3 → 4×4 → 5×5，方块慢慢变大"""
    return min(5, 3 + int(3 * nutrition / STAGES[1][1]))


@lru_cache(maxsize=128)
def art_pixmap(stage: int, size: int = 3, blink: bool = False, mouth: bool = False,
               invert: bool = False) -> QPixmap:
    art = add_halo(spore_art(size) if stage == 0 else mushroom_art(stage, blink, mouth))
    ink, paper = (PAPER, INK) if invert else (INK, PAPER)
    halo = QColor(paper)
    halo.setAlpha(235)
    colors = {"#": ink, "o": paper, "h": halo}
    img = QImage(len(art[0]) * PX, len(art) * PX, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            if ch in colors:
                p.fillRect(x * PX, y * PX, PX, PX, colors[ch])
    p.end()
    return QPixmap.fromImage(img)


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


def count_edible(root: Path, max_depth: int = 3, budget: int = 3000) -> int:
    n, stack = 0, [(root, 0)]
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
                                stack.append((Path(e.path), depth + 1))
                        elif os.path.splitext(e.name)[1].lower() in EDIBLE_EXT:
                            n += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return n


def digest(path: Path) -> tuple[Food | None, str]:
    """把一个路径变成食物。只看 stat 和目录结构，不读取、不修改文件内容。"""
    try:
        st = path.stat()
    except OSError:
        return None, "够不着…"
    key = hashlib.sha1(f"{os.path.realpath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    if path.is_dir():
        n = count_edible(path)
        if n == 0:
            return Food(path.name + "/", 3, key, "空空的"), ""
        return Food(path.name + "/", min(30, 6 + 2 * n), key), ""
    if path.suffix.lower() in EDIBLE_EXT:
        if st.st_size == 0:
            return Food(path.name, 2, key, "空的…"), ""
        return Food(path.name, min(20, 4 + int(3 * math.log2(st.st_size / 256 + 1))), key), ""
    return None, f"不吃 {path.suffix or path.name}"


def fmt_age(sec: float) -> str:
    sec = int(max(0, sec))
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def ui_font(px: int = 12, bold: bool = True) -> QFont:
    f = QFont()
    f.setFamilies(["DejaVu Sans Mono", "Noto Sans Mono CJK SC", "Noto Sans CJK SC", "monospace"])
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
         font-family:'DejaVu Sans Mono','Noto Sans CJK SC',monospace; font-size:12px; font-weight:bold; }}
QMenu::item {{ padding:4px 22px 4px 12px; }}
QMenu::item:selected {{ background:{INK.name()}; color:{PAPER.name()}; }}
QMenu::item:disabled {{ color:{INK.name()}; }}
QMenu::separator {{ height:2px; background:{INK.name()}; margin:4px 6px; }}
QMenu::indicator {{ width:10px; height:10px; border:2px solid {INK.name()}; margin-left:4px; }}
QMenu::indicator:checked {{ background:{INK.name()}; }}
"""

ANIM_DUR = {"eat": 0.8, "shake": 0.5, "poke": 0.4, "grow": 1.2, "land": 0.3, "hop": 0.35}


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
        self.masked: bool | None = None
        self.shown_stage = creature.stage
        self.blink_at = time.time() + random.uniform(2, 6)
        self.hop_at = time.time() + random.uniform(10, 30)
        self._key = None
        self.sprite_rect = QRect()

        self.refit()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    # ── 几何 ──
    def base_pixmap(self) -> QPixmap:
        return art_pixmap(self.c.stage, spore_size(self.c.nutrition))

    def refit(self):
        pm = self.base_pixmap()
        w, h = max(MIN_WIN_W, pm.width() + 40), pm.height() + TEXT_BAND + BOTTOM_PAD
        self.resize(w, h)
        self.sprite_rect = QRect((w - pm.width()) // 2, h - BOTTOM_PAD - pm.height(), pm.width(), pm.height())
        self.shown_stage = self.c.stage
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
        if not USE_INPUT_MASK:
            return
        want = not (self.hover or self.floaters or self.particles or self.anim or self.flight)
        if want == self.masked:
            return
        self.masked = want
        if want:
            self.setMask(QRegion(self.sprite_rect.adjusted(-14, -14, 14, BOTTOM_PAD)))
        else:
            self.clearMask()

    # ── 动画 ──
    def play(self, name: str):
        self.anim = (name, time.time())
        self.update_mask()

    def say(self, text: str, delay: float = 0.0, big: bool = False):
        t0 = time.time() + delay
        if self.floaters:
            t0 = max(t0, self.floaters[-1]["t0"] + 0.45)
        self.floaters.append({"text": text, "t0": t0, "dur": 1.6, "big": big})
        self.update_mask()

    def crumbs(self, n: int = 7):
        now = time.time()
        r = self.sprite_rect
        for _ in range(n):
            self.particles.append({"x": r.center().x(), "y": r.y() + PX * 2, "vx": random.uniform(-70, 70),
                                   "vy": random.uniform(-150, -60), "t0": now})

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
        if name in ("poke", "hop", "land"):
            k = {"poke": 3, "hop": 1, "land": 1}[name]
            return 0, -round(math.sin(math.pi * min(1.0, t / ANIM_DUR[name])) * k) * PX
        return 0, 0

    def current_pixmap(self, now: float) -> QPixmap:
        c = self.c
        anim = self.anim[0] if self.anim else None
        t = now - self.anim[1] if self.anim else 0
        invert = anim == "grow" and int(t / 0.15) % 2 == 0
        if c.stage == 0:
            return art_pixmap(0, spore_size(c.nutrition), invert=invert)
        mouth = self.drag_over or (anim == "eat" and int(t * 8) % 2 == 0)
        blink = self.blink_at <= now < self.blink_at + 0.15
        return art_pixmap(c.stage, 3, blink, mouth, invert)

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
        self.particles = [q for q in self.particles if now - q["t0"] < 0.6]
        if now > self.blink_at + 0.15:
            self.blink_at = now + random.uniform(2.5, 7)
        if now > self.hop_at:
            if not self.anim and not self.press:
                self.play("hop")
            self.hop_at = now + random.uniform(15, 40)
        if self.c.stage != self.shown_stage:
            self.refit()
        self.update_mask()

        busy = self.floaters or self.particles or self.flight
        key = (self.current_pixmap(now).cacheKey(), self.body_offset(now), self.hover, self.drag_over,
               int(now * 20) if busy else 0, int(now) if self.hover else 0)
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
            x = q["x"] + q["vx"] * t
            y = q["y"] + q["vy"] * t + 380 * t * t
            p.fillRect(int(x) - 3, int(y) - 3, 6, 6, PAPER)
            p.fillRect(int(x) - 2, int(y) - 2, 4, 4, INK)

        top = r.y() - 6
        active = [f for f in self.floaters if now >= f["t0"]][-1:]
        for f in active:
            k = (now - f["t0"]) / f["dur"]
            alpha = 255 if k < 0.6 else int(255 * (1 - (k - 0.6) / 0.4))
            draw_label(p, f["text"], self.width() / 2, top - k * 16, ui_font(15 if f["big"] else 13), alpha)

        if self.hover and not active and not self.drag_over:
            c = self.c
            draw_label(p, f"{c.name} · {c.stage_name}", self.width() / 2, top - 16, ui_font(12))
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
        self.hover = True
        self.update_mask()
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def contextMenuEvent(self, e):
        self.colony.show_menu(self, e.globalPos())


# ───────────────────────────── 菌落（存档、生长、繁殖） ─────────────────────────────

class Colony:
    def __init__(self, data_dir: Path, fast: bool = False):
        self.data_dir = data_dir
        self.save_path = data_dir / "save.json"
        self.passive_mult = 30 if fast else 1
        self.gain_mult = 3 if fast else 1
        self.widgets: dict[str, CreatureWidget] = {}
        self.creatures: list[Creature] = []
        self.name_i = 0
        self.on_top = True
        self.last_tick = time.time()

        offline = self.load()
        for c in self.creatures:
            self.spawn_widget(c)
        for c in list(self.creatures):
            self.after_growth(self.widgets[c.id], c.stage, quiet=True)
            if offline >= 1:
                self.widgets[c.id].say(f"+{int(offline)} 睡觉时长的", delay=0.6)
        self.save()

        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)
        self.autosave = QTimer()
        self.autosave.timeout.connect(self.save)
        self.autosave.start(30_000)
        self.tray = self.make_tray()

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
        if data:
            self.name_i = data.get("name_i", 0)
            self.on_top = data.get("on_top", True)
            self.creatures = [Creature.from_dict(d) for d in data.get("creatures", [])]
            elapsed = time.time() - data.get("last_seen", time.time())
            offline = min(OFFLINE_CAP, max(0.0, elapsed) * self.passive_mult / PASSIVE_SECONDS)
            for c in self.creatures:
                c.nutrition += offline
                if QGuiApplication.screenAt(QPoint(c.x, c.y - 8)) is None:
                    c.x, c.y = self.default_spot()
        if not self.creatures:
            self.creatures = [self.new_creature(*self.default_spot())]
        return offline

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "last_seen": time.time(), "name_i": self.name_i, "on_top": self.on_top,
                "creatures": [asdict(c) for c in self.creatures]}
        tmp = self.save_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.save_path)

    # ── 生长 ──
    def spawn_widget(self, c: Creature) -> CreatureWidget:
        w = CreatureWidget(self, c, self.on_top)
        self.widgets[c.id] = w
        w.show()
        return w

    def tick(self):
        now = time.time()
        dt = min(5.0, now - self.last_tick)
        self.last_tick = now
        for c in list(self.creatures):
            before = c.stage
            c.nutrition += dt * self.passive_mult / PASSIVE_SECONDS
            if c.stage != before or c.spores_due() > 0:
                self.after_growth(self.widgets[c.id], before)

    def feed(self, widget: CreatureWidget, paths: list[Path]):
        c = widget.c
        before = c.stage
        eaten, rejected = [], []
        for path in paths[:MAX_ITEMS_PER_DROP]:
            food, why = digest(path)
            if food is None:
                rejected.append(why)
                continue
            value, note = food.value, food.note
            if food.key in c.eaten:
                value, note = max(1, value // 4), note or "嚼过了"
            else:
                c.eaten = (c.eaten + [food.key])[-500:]
            value *= self.gain_mult
            c.nutrition += value
            c.feeds += 1
            c.log = (c.log + [[int(time.time()), food.label, value]])[-50:]
            eaten.append((value, note))
        if len(paths) > MAX_ITEMS_PER_DROP:
            rejected.append("吃不下了")

        if eaten:
            widget.play("eat")
            widget.crumbs()
            for i, (value, note) in enumerate(eaten):
                widget.say(f"+{value} {note}".strip(), delay=i * 0.35)
            if rejected:
                widget.say(rejected[0], delay=len(eaten) * 0.35)
        elif rejected:
            widget.play("shake")
            widget.say(rejected[0])
        self.after_growth(widget, before)
        self.save()

    def after_growth(self, widget: CreatureWidget, before: int, quiet: bool = False):
        c = widget.c
        if c.stage != before:
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
        m.addSeparator()
        top = m.addAction("总在最前")
        top.setCheckable(True)
        top.setChecked(self.on_top)
        top.toggled.connect(self.set_on_top)
        m.addAction("叫大家回来", self.gather)
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
    ap.add_argument("--data-dir", type=Path,
                    default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "fungi")
    args = ap.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = open(args.data_dir / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
