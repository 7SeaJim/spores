"""像素精灵：读 art/*.pxl 画稿，缺失时程序生成；拼呼吸、眨眼、枯萎等帧。"""
from __future__ import annotations

import json
from functools import lru_cache

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

from .config import ART_DIR, IDLE_RIG, INK, PAPER, PX, STAGES


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
