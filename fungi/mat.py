"""菌毯：屏幕边缘一圈菌丝的数据（Mycelium）、每块屏幕一圈（MatField）、边缘窗口（MatStrip）。"""
from __future__ import annotations

import base64
import math
import random
from functools import lru_cache

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QWidget

from .config import MAT_MAX, MAT_SPROUT_DEPTH, MAT_STRIP, PX, SALT_BITE, SALT_FULL, SALT_RADIUS
from .sprites import add_halo, HALO, load_pxl


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
        self.salt = bytearray(self.n)                     # 每格还剩多少盐：有盐的格子长不动
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
        if self.d[i] >= MAT_MAX or self.salt[i]:
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
                if self.salt[j]:
                    self.bite(j, SALT_BITE)                # 往盐里挤：啃掉一点盐
                else:
                    self.bump(j)
            elif rng.random() < 1 - d[i] / MAT_MAX:
                if self.salt[i]:
                    self.bite(i, 1)
                else:
                    self.bump(i)

    # ── 盐 ──
    def bite(self, i: int, amount: int):
        i %= self.n
        before = self.salt[i]
        self.salt[i] = max(0, before - amount)
        if before and not self.salt[i]:
            self.dirty.update(j % self.n for j in range(i - 1, i + 2))

    def salt_at(self, i: int, radius: int = SALT_RADIUS) -> int:
        """以 i 为中心左右 radius 格撒满盐，返回新撒上（或补满）的格数"""
        added = 0
        for j in range(i - radius, i + radius + 1):
            k = j % self.n
            if self.salt[k] < SALT_FULL:
                added += 1
                self.salt[k] = SALT_FULL
                self.dirty.add(k)
        return added

    def salt_all(self) -> int:
        return self.salt_at(0, self.n)

    def salted(self) -> int:
        return sum(1 for v in self.salt if v)

    def decay_salt(self, amount: int):
        if amount <= 0:
            return
        for k, v in enumerate(self.salt):
            if v:
                self.salt[k] = max(0, v - amount)
                if not self.salt[k] or v // 64 != self.salt[k] // 64:
                    self.dirty.add(k)

    def clear_salt(self):
        self.dirty.update(k for k, v in enumerate(self.salt) if v)
        self.salt = bytearray(self.n)

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
            new.salt[i] = self.salt[self.index_at(edge, old)]
        new.active = [i for i, v in enumerate(new.d) if v]
        return new

    def to_json(self) -> dict:
        data = {"cols": self.cols, "rows": self.rows, "depth": base64.b64encode(bytes(self.d)).decode()}
        if any(self.salt):
            data["salt"] = base64.b64encode(bytes(self.salt)).decode()
        return data

    @classmethod
    def from_json(cls, data) -> "Mycelium | None":
        try:
            m = cls(int(data["cols"]), int(data["rows"]), base64.b64decode(data["depth"]))
            salt = base64.b64decode(data["salt"]) if data.get("salt") else b""
        except (TypeError, KeyError, ValueError, AttributeError):
            return None
        if len(salt) == m.n:
            m.salt = bytearray(salt)
        return m if len(m.d) == m.n else None


def screen_key(screen) -> str:
    """认屏幕：优先用系统给的名字（DISPLAY1、HDMI-1…），没有就用位置"""
    if screen is None:
        return "primary"
    g = screen.geometry()
    return screen.name() or f"{g.x()},{g.y()}"


class MatField:
    """一块屏幕上的菌毯：这块屏的可用区域 + 一圈菌丝 + 四条边的窗口"""

    def __init__(self, key: str, rect: QRect, mat: "Mycelium | None" = None):
        self.key, self.rect = key, QRect(rect)
        cols, rows = max(1, rect.width() // PX), max(1, rect.height() // PX)
        self.mat = mat.resized(cols, rows) if mat else Mycelium(cols, rows)
        self.views: dict[str, MatStrip] = {}

    def fit(self, rect: QRect) -> bool:
        if rect == self.rect:
            return False
        self.rect = QRect(rect)
        self.mat = self.mat.resized(max(1, rect.width() // PX), max(1, rect.height() // PX))
        return True

    def distance(self, x: float, y: float) -> float:
        r = self.rect
        return math.hypot(max(r.left() - x, 0, x - r.right()), max(r.top() - y, 0, y - r.bottom()))

    def index_near(self, x: float, y: float) -> int:
        return self.mat.nearest((x - self.rect.x()) / PX, (y - self.rect.y()) / PX)


MAT_INK, MAT_DUST, MAT_PAPER, MAT_HALO = 0xFF111111, 0xFF55524C, 0xFFFAF9F4, 0xC8FAF9F4
MAT_SALT = 0xFFE4E0D2
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
        salt = m.salt[i]
        top = math.ceil(D)
        for k in range(MAT_STRIP):
            argb = 0 if empty else mat_cell(i, k, D, side, sprouts, hairs)
            if salt:                                        # 盐粒：菌毯表面外一圈白点，菌毯里零星几粒，盐越少越稀
                h = _hash(i * 131 + k, 4099) % 1024
                if k == top and h < salt * 4:                   # 表面一层盐壳：白、灰相间，深浅背景都看得见
                    argb = MAT_SALT if (i + h) % 3 else MAT_DUST
                elif k == top + 1 and h < salt * 3 // 2:
                    argb = MAT_SALT if h % 2 else MAT_DUST
                elif k < top and argb and h < salt:
                    argb = MAT_SALT
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
