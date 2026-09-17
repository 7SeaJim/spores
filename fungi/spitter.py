"""喷孢菌、孢子弹、菌斑。"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, fields
from functools import lru_cache

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QTransform
from PyQt6.QtWidgets import QWidget

from .config import INK, PAPER, PATCH_MAX, PX
from .sprites import add_halo, art_pixmap, HALO, load_pxl, paint_rows, withered
from .creature_view import DUST
from .mat import _hash, _vnoise2, MAT_DUST, MAT_HALO, MAT_INK, MAT_PAPER, MAT_SALT, MAXH


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
        sp, m = self.colony.spitter, self.colony.spitter_field().mat
        edge = m.edge_of(sp["i"])[0]
        (ux, uy), rot = EDGE_POSE[edge]
        age = now - sp["born"]
        reveal = int(22 * age / 2.5) if age < 2.5 else 99
        shake = (0, 0)
        starving = self.colony.starving() or sp.get("salted", 0) > now    # 饿扁或被腌着：蔫着不喷
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
        c, m, sp = self.colony, self.colony.spitter_field().mat, self.colony.spitter
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
    salted: float = 0.0                                   # 撒了盐：到这个时间之前不长

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
        salted = self.patch.salted > time.time()
        step = (int(self.patch.r * 4), salted)
        if step == self.shown_r:
            return
        self.shown_r = step
        self.image.fill(Qt.GlobalColor.transparent)
        for u in range(-PATCH_HALF, PATCH_HALF + 1):
            for v in range(-PATCH_HALF, PATCH_HALF + 1):
                argb = patch_cell(self.patch, u, v)
                if salted and argb in (MAT_INK, MAT_DUST) and _hash(u * 131 + v, 4099) % 5 == 0:
                    argb = MAT_SALT
                if argb:
                    self.image.setPixel(u + PATCH_HALF, v + PATCH_HALF, argb)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawImage(self.rect(), self.image)
        p.end()
