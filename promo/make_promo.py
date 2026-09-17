#!/usr/bin/env python3
"""小红书宣传图（1080×1440）+ 效果动图。画面全部用 fungi 包自己的渲染函数生成。

    QT_QPA_PLATFORM=offscreen python3 promo/make_promo.py            # 图片
    QT_QPA_PLATFORM=offscreen python3 promo/make_promo.py --gif      # 再加动图
"""
import math
import os
import random
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication([])
import fungi as F  # noqa: E402

OUT = ROOT / "out"
W, H = 1080, 1440
INK, PAPER, DUST = F.INK, F.PAPER, F.DUST
GREY = QColor(200, 195, 182)
WALL = QColor(44, 56, 64)                   # 桌面壁纸
PX = F.PX


# ───────────── 基础 ─────────────

def font(px: int, weight=QFont.Weight.Black) -> QFont:
    f = QFont()
    f.setFamilies(["Noto Sans CJK SC", "DejaVu Sans Mono"])
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def mono(px: int) -> QFont:
    f = QFont()
    f.setFamilies(["DejaVu Sans Mono", "Noto Sans CJK SC"])
    f.setPixelSize(px)
    f.setBold(True)
    return f


def text(p: QPainter, rect: QRect, s: str, f: QFont, color=INK, flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop):
    p.setFont(f)
    p.setPen(color)
    p.drawText(rect, int(flags | Qt.TextFlag.TextWordWrap), s)


def big(pm: QPixmap | QImage, k: int) -> QPixmap:
    pm = pm if isinstance(pm, QPixmap) else QPixmap.fromImage(pm)
    return pm.scaled(pm.width() * k, pm.height() * k, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)


def feet(p: QPainter, pm: QPixmap, x: float, y: float):
    """按脚底中心画精灵"""
    p.drawPixmap(int(x - pm.width() / 2), int(y - pm.height()), pm)


def page(title_bar: str = "FUNGI.EXE", index: str = "") -> tuple[QImage, QPainter]:
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(PAPER)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    m = 36
    p.fillRect(m, m, W - 2 * m, H - 2 * m, INK)                      # 像素窗口：黑框
    p.fillRect(m + 6, m + 76, W - 2 * m - 12, H - 2 * m - 82, PAPER)
    for cx, cy in ((m, m), (W - m - 6, m), (m, H - m - 6), (W - m - 6, H - m - 6)):
        p.fillRect(cx, cy, 6, 6, PAPER)                             # 切角
    text(p, QRect(m + 28, m + 18, 600, 50), title_bar, mono(30), PAPER)
    bx = W - m - 40
    for kind in ("x", "box", "min"):                                # 标题栏三个像素按钮
        p.setPen(QPen(PAPER, 4))
        p.drawRect(bx - 20, m + 22, 34, 34)
        if kind == "x":
            p.drawLine(bx - 11, m + 31, bx + 5, m + 47)
            p.drawLine(bx + 5, m + 31, bx - 11, m + 47)
        elif kind == "box":
            p.drawRect(bx - 12, m + 30, 18, 18)
        else:
            p.fillRect(bx - 12, m + 43, 18, 4, PAPER)
        bx -= 50
    if index:
        text(p, QRect(W - m - 330, H - m - 60, 280, 40), index, mono(24), DUST, Qt.AlignmentFlag.AlignRight)
    return img, p


def headline(p: QPainter, y: int, title: str, sub: str = "", size: int = 92) -> int:
    x = 96
    text(p, QRect(x, y, W - 2 * x, 400), title, font(size))
    fm = QFontMetrics(font(size))
    h = fm.boundingRect(QRect(0, 0, W - 2 * x, 1000), int(Qt.TextFlag.TextWordWrap), title).height()
    if sub:
        text(p, QRect(x, y + h + 18, W - 2 * x, 200), sub, font(38, QFont.Weight.Medium), DUST)
        h += 18 + QFontMetrics(font(38, QFont.Weight.Medium)).boundingRect(QRect(0, 0, W - 2 * x, 1000), int(Qt.TextFlag.TextWordWrap), sub).height()
    return y + h


def bubble(p: QPainter, s: str, cx: float, bottom: float, px: int = 30, max_w: int = 460, dark: bool = False, tail: bool = True) -> QRect:
    """游戏里的像素对话气泡，放大版"""
    f = font(px, QFont.Weight.Bold)
    fm = QFontMetrics(f)
    box = fm.boundingRect(QRect(0, 0, max_w, 2000), int(Qt.TextFlag.TextWrapAnywhere), s)
    pad, b = 20, 5
    w, h = box.width() + 2 * pad + 4, box.height() + 2 * pad
    x, y = int(cx - w / 2), int(bottom - h - (18 if tail else 0))
    fg, bg = (PAPER, INK) if dark else (INK, PAPER)
    p.fillRect(x + b, y, w - 2 * b, h, fg)
    p.fillRect(x, y + b, w, h - 2 * b, fg)
    p.fillRect(x + b, y + b, w - 2 * b, h - 2 * b, bg)
    if tail:
        c = int(cx)
        for i, half in enumerate((15, 10, 5)):
            p.fillRect(c - half, y + h - 3 + i * 6, 2 * half, 6, fg)
            if half > 5:
                p.fillRect(c - half + b, y + h - 3 + i * 6, 2 * half - 2 * b, 6, bg)
    text(p, QRect(x + pad, y + pad, box.width() + 4, box.height() + 4), s, f, fg,
         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return QRect(x, y, w, h)


def label(p: QPainter, s: str, cx: float, baseline: float, px: int = 30):
    F.draw_label(p, s, cx, baseline, font(px, QFont.Weight.Black))


# ───────────── 像素图标 ─────────────

DOC = ["kkkkkkkk....", "kwwwwwwkk...", "kwwwwwwkwk..", "kwwwwwwkkkk.", "kwkkkkwwwwk.", "kwwwwwwwwwk.",
       "kwkkkkkkwwk.", "kwwwwwwwwwk.", "kwkkkkkwwwk.", "kwwwwwwwwwk.", "kwkkkkkkwwk.", "kwwwwwwwwwk.", "kkkkkkkkkkk."]
FOLDER = ["kkkkk.........", "kgggkkkkkkkkk.", "kgggggggggggk.", "kkkkkkkkkkkkkk", "kwwwwwwwwwwwwk", "kwwwwwwwwwwwwk",
          "kwwwwwwwwwwwwk", "kwwwwwwwwwwwwk", "kwwwwwwwwwwwwk", "kkkkkkkkkkkkkk"]
CURSOR = ["k.........", "kk........", "kwk.......", "kwwk......", "kwwwk.....", "kwwwwk....", "kwwwwwk...", "kwwwwwwk..",
          "kwwwwwwwk.", "kwwwwkkkkk", "kwwkwk....", "kwk.kwk...", "kk..kwk...", "k....kwk..", ".....kk..."]


def icon(rows: list[str]) -> QImage:
    return F.paint_rows(F.add_halo(rows), {"k": INK, "w": PAPER, "g": GREY, F.HALO: QColor(250, 249, 244, 110)})


def pet(stage: int, **kw) -> QPixmap:
    return F.art_pixmap(stage, kw.pop("size", 3), kw.pop("blink", False), kw.pop("mouth", False), False,
                        kw.pop("breath", False), kw.pop("tilt", 0), kw.pop("wither", False), kw.pop("mood", "full"))


# ───────────── 桌面场景：半尺寸画像素，整体放大 2 倍；文字另在页面坐标上画 ─────────────

class Scene:
    Z = 2

    def __init__(self, w: int, h: int, seed: int = 7):
        self.w, self.h = w, h
        self.mat = F.Mycelium(w // PX, h // PX)
        t = F.MAT_STRIP * PX
        mw, mh = self.mat.cols * PX, self.mat.rows * PX
        self.rects = {"top": QRect(0, 0, mw, t), "bottom": QRect(0, mh - t, mw, t),
                      "left": QRect(0, 0, t, mh), "right": QRect(mw - t, 0, t, mh)}
        self.strips = {e: F.MatStrip(e, r, "top") for e, r in self.rects.items()}
        self.rng = random.Random(seed)
        self.patches: list[F.Patch] = []
        self.img = QImage(w, h, QImage.Format.Format_ARGB32)
        self.ox = self.oy = 0

    def grow(self, points, steps):
        for edge, frac in points:
            self.mat.seed(self.mat.index_at(edge, int(frac * self.mat.edge_len(edge))))
        self.mat.grow(steps, rng=self.rng)

    def render_mat(self, full=False):
        m = self.mat
        todo = {j % m.n for i in m.active for j in range(i - 12, i + 13)} if full else set(m.dirty)
        m.dirty = set()
        for i in todo:
            self.strips[m.edge_of(i)[0]].render_column(m, i)

    def begin(self, wall=WALL) -> QPainter:
        self.img.fill(wall)
        q = QPainter(self.img)
        for pt in self.patches:
            side = 2 * F.PATCH_HALF + 1
            pimg = QImage(side, side, QImage.Format.Format_ARGB32)
            pimg.fill(Qt.GlobalColor.transparent)
            for u in range(-F.PATCH_HALF, F.PATCH_HALF + 1):
                for v in range(-F.PATCH_HALF, F.PATCH_HALF + 1):
                    c = F.patch_cell(pt, u, v)
                    if c:
                        pimg.setPixel(u + F.PATCH_HALF, v + F.PATCH_HALF, c)
            q.drawImage(QRect(int(pt.x - (F.PATCH_HALF + 0.5) * PX), int(pt.y - (F.PATCH_HALF + 0.5) * PX), side * PX, side * PX), pimg)
        for e, r in self.rects.items():
            q.drawImage(r, self.strips[e].image)
        return q

    def blit(self, p: QPainter, ox: int, oy: int):
        self.ox, self.oy = ox, oy
        p.drawPixmap(ox, oy, big(self.img, self.Z))
        p.fillRect(ox - 6, oy - 6, self.w * self.Z + 12, 6, INK)       # 细黑框
        p.fillRect(ox - 6, oy + self.h * self.Z, self.w * self.Z + 12, 6, INK)
        p.fillRect(ox - 6, oy, 6, self.h * self.Z, INK)
        p.fillRect(ox + self.w * self.Z, oy, 6, self.h * self.Z, INK)

    def at(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + x * self.Z, self.oy + y * self.Z

    def spitter_base(self, frac: float) -> tuple[float, float]:
        m = self.mat
        pos = int(frac * m.cols)
        return self.w - (pos + 0.5) * PX, self.h - max(0.0, m.eff(m.index_at("bottom", pos)) - 2) * PX


def draw_spitter(q: QPainter, sc: Scene, frac: float, frame="idle"):
    bx, by = sc.spitter_base(frac)
    img = F.spitter_image(frame)
    q.drawImage(int(bx - img.width() / 2), int(by - img.height() + PX), img)
    return bx, by - img.height() + 3 * PX


def arc(q: QPainter, a, b, lift, color=QColor(250, 249, 244, 170), dots=12, size=3):
    cx, cy = (a[0] + b[0]) / 2, min(a[1], b[1]) - lift
    for i in range(1, dots):
        k = i / dots
        x = (1 - k) ** 2 * a[0] + 2 * (1 - k) * k * cx + k * k * b[0]
        y = (1 - k) ** 2 * a[1] + 2 * (1 - k) * k * cy + k * k * b[1]
        q.fillRect(int(x) - size // 2, int(y) - size // 2, size, size, color)


def icon_label(p: QPainter, sc: Scene, name: str, x: float, y: float):
    px, py = sc.at(x, y)
    f = font(22, QFont.Weight.Bold)
    r = QRect(int(px - 110), int(py), 220, 70)
    text(p, r.translated(2, 2), name, f, QColor(0, 0, 0, 170), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    text(p, r, name, f, PAPER, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)


def put_icon(q: QPainter, rows, x, y):
    img = icon(rows)
    q.drawImage(int(x - img.width() / 2), int(y), img)
    return y + img.height()


def measure(s: str, px: int, max_w: int) -> tuple[int, int]:
    box = QFontMetrics(font(px, QFont.Weight.Bold)).boundingRect(QRect(0, 0, max_w, 2000), int(Qt.TextFlag.TextWrapAnywhere), s)
    return box.width() + 44, box.height() + 40


# ───────────── 页面：像素窗口 + 一句标题 + 大场景 ─────────────

def framed(bar: str, index: str, title: str, size: int = 118):
    """上一版的窗口外观；标题下面整块给场景"""
    img, p = page(bar, index)
    bottom = headline(p, 128, title, "", size=size)
    top = bottom + 44
    return img, p, top


def stage_scene(top: int, zoom: int, seed: int) -> Scene:
    bottom = H - 36 - 70
    sc = Scene(900 // zoom, (bottom - top) // zoom, seed=seed)
    sc.Z = zoom
    return sc


def place(p: QPainter, sc: Scene, top: int):
    sc.blit(p, (W - sc.w * sc.Z) // 2, top)


def edges(sc: Scene, steps: int, per_edge: int = 3, seed: int = 0):
    rng = random.Random(seed)
    sc.grow([(e, rng.random()) for e in ("top", "right", "bottom", "left") for _ in range(per_edge)], steps)
    sc.render_mat(full=True)


def cover():
    img, p, top = framed("FUNGI.EXE", "", "我的桌面\n长蘑菇了", size=132)
    sc = stage_scene(top, 3, seed=31)
    edges(sc, 22000, 4, seed=2)
    rng = random.Random(8)
    sc.patches = [F.Patch(x, y, r, int(r) + 1, rng.randrange(1 << 30)) for x, y, r in
                  ((70, 90, 5), (215, 120, 7), (140, 40, 3.5), (255, 40, 3))]
    q = sc.begin()
    ground = sc.h - 12
    draw_spitter(q, sc, 0.08)
    feet(q, pet(3), 62, ground)
    feet(q, pet(4), 164, ground)
    feet(q, pet(2, blink=True), 215, 120 + 2)
    feet(q, pet(0, size=4), 70, 90)
    q.end()
    place(p, sc, top)
    p.end()
    return img


def growth():
    img, p, top = framed("FUNGI.EXE — 成长", "2 / 7", "从一个黑点开始")
    sc = stage_scene(top, 2, seed=5)
    sc.grow([("bottom", x / 10) for x in range(10)], 4800)
    sc.render_mat(full=True)
    place(p, sc, top)
    upper_ground, lower_ground = top + 470, top + sc.h * 2 - 24
    upper = [(pet(0, size=3), 190), (pet(1), 420), (pet(2), 760)]
    lower = [(pet(3), 300), (pet(4), 740)]
    sc.patches = [F.Patch(int((x - sc.ox) / 2), int((upper_ground - sc.oy) / 2) - 4, r, int(r) + 1, i * 31 + 5)
                  for i, ((_, x), r) in enumerate(zip(upper, (5, 7.5, 9)))]
    q = sc.begin()
    q.end()
    place(p, sc, top)
    for pm, x in upper:
        feet(p, big(pm, 3), x, upper_ground)
    for pm, x in lower:
        feet(p, big(pm, 3), x, lower_ground)
    p.end()
    return img


def feeding():
    img, p, top = framed("FUNGI.EXE — 喂食", "3 / 7", "喂它文件")
    sc = stage_scene(top, 4, seed=6)
    sc.grow([("bottom", 0.2), ("bottom", 0.8), ("left", 0.4), ("right", 0.5)], 2600)
    sc.render_mat(full=True)
    q = sc.begin()
    ground = sc.h - 12
    young = pet(3, mouth=True)
    cx = 128
    feet(q, young, cx, ground)
    rng = random.Random(4)
    for _ in range(16):
        ang = rng.uniform(-2.8, -0.35)
        d = rng.uniform(14, 52)
        x, y = cx + math.cos(ang) * d, ground - young.height() + 14 + math.sin(ang) * d
        q.fillRect(int(x) - 3, int(y) - 3, 6, 6, PAPER)
        q.fillRect(int(x) - 2, int(y) - 2, 4, 4, INK)
    q.drawImage(22, 96, icon(FOLDER))
    arc(q, (56, 96), (cx - 4, ground - young.height() - 4), 26, dots=9)
    doc = icon(DOC)
    q.drawImage(int(cx - 30), int(ground - young.height() - 44), doc.scaled(int(doc.width() * 0.6), int(doc.height() * 0.6)))
    q.end()
    place(p, sc, top)
    fx, fy = sc.at(cx, ground - young.height())
    label(p, "+19", fx + 200, fy + 10, px=84)
    p.end()
    return img


def overgrown():
    img, p, top = framed("FUNGI.EXE — 菌毯", "4 / 7", "放着不管\n就长满了")
    sc = stage_scene(top, 3, seed=77)
    edges(sc, 30000, 6, seed=5)
    rng = random.Random(21)
    spots = []
    while len(spots) < 8:
        x, y = rng.uniform(50, sc.w - 50), rng.uniform(45, sc.h - 45)
        if all(math.hypot(x - a, y - b) > 58 for a, b in spots) and math.hypot(x - sc.w / 2, y - sc.h / 2) > 45:
            spots.append((x, y))
    sc.patches = [F.Patch(int(x), int(y), rng.uniform(3.5, 8), 9, rng.randrange(1 << 30)) for x, y in spots]
    q = sc.begin()
    feet(q, pet(0, size=3), sc.w / 2, sc.h / 2 + 10)
    q.end()
    place(p, sc, top)
    p.end()
    return img


def spitter_page():
    img, p, top = framed("FUNGI.EXE — 喷孢菌", "5 / 7", "它会喷孢子")
    sc = stage_scene(top, 3, seed=14)
    sc.grow([("bottom", x / 6) for x in range(6)] + [("left", 0.5), ("right", 0.5), ("top", 0.5)], 6000)
    sc.render_mat(full=True)
    landings = [(55, 150, 5.5), (245, 110, 4.5), (150, 70, 0), (240, 230, 0), (60, 260, 3.5)]
    sc.patches = [F.Patch(x, y, r, int(r) + 1, i * 97) for i, (x, y, r) in enumerate(landings) if r]
    q = sc.begin()
    sx, sy = draw_spitter(q, sc, 0.5, "shoot")
    for x, y, r in landings:
        arc(q, (sx, sy), (x, y), 70, dots=14, size=4)
        if not r:
            feet(q, F.art_pixmap(0, 3), x, y + 6)
    q.end()
    place(p, sc, top)
    p.end()
    return img


def chat_page():
    img, p, top = framed("FUNGI.EXE — 聊天", "6 / 7", "双击它")
    sc = stage_scene(top, 3, seed=19)
    sc.grow([("bottom", x / 5) for x in range(5)] + [("left", 0.7), ("right", 0.3)], 3200)
    sc.render_mat(full=True)
    q = sc.begin()
    ground = sc.h - 12
    feet(q, pet(4), sc.w / 2, ground)
    q.end()
    place(p, sc, top)
    ux, uy = sc.at(sc.w - 20, 40)
    uw, uh = measure("你是 AI 吧", 56, 700)
    bubble(p, "你是 AI 吧", ux - uw / 2, uy + uh, px=56, max_w=700, dark=True, tail=False)
    ax, ay = sc.at(sc.w / 2, ground - pet(4).height())
    bubble(p, "我知道你不是真的\n在问蘑菇。", W / 2, ay + 24, px=64, max_w=800)
    text(p, QRect(96, H - 36 - 62, 500, 40), "* 风格示例", font(26, QFont.Weight.Medium), DUST)
    p.end()
    return img


def hunger_page():
    img, p, top = framed("FUNGI.EXE — 饥饿", "7 / 7", "别忘了喂它")
    sc = stage_scene(top, 3, seed=44)
    sc.grow([("bottom", 0.25), ("bottom", 0.75)], 420)
    sc.render_mat(full=True)
    q = sc.begin()
    ground = sc.h - 12
    for pm, x in ((pet(3, wither=True, mood="starving"), 52), (pet(0, wither=True, mood="dormant"), 116),
                  (pet(4, wither=True, mood="starving"), 180), (pet(0, wither=True, mood="dormant"), 250),
                  (pet(1, wither=True, mood="starving"), 274)):
        feet(q, pm, x, ground)
    q.end()
    place(p, sc, top)
    fx, fy = sc.at(180, ground - pet(4).height())
    label(p, "咕……", fx, fy - 36, px=64)
    p.end()
    return img


# ───────────── 效果动图 ─────────────

def animation(path: Path, seconds: float = 12.0, fps: int = 10):
    """延时摄影：一个小黑点 → 菌落 → 菌毯长满一整圈，喷孢菌喷出菌斑"""
    from PIL import Image
    GW, BAR = 640, 52
    sc = Scene(320, 420, seed=12)
    GH = BAR + sc.h * 2
    sc.grow([("bottom", 0.5)], 6)
    sc.render_mat(full=True)
    ground = sc.h - 12
    rng = random.Random(3)
    founder = (160, [(0.0, 0), (1.6, 1), (2.6, 2), (3.4, 3), (4.2, 4)])
    kids = [((52, ground), 5.0, [(5.6, 0), (7.6, 1), (9.6, 2)])]
    feeds = [(0.6, DOC, (70, 110)), (1.2, FOLDER, (240, 140))]
    shots = [(7.0, (110, 150)), (8.2, (220, 230)), (9.4, (80, 290)), (10.6, (190, 110))]
    frames = []
    def stage_at(t, plan):
        return max([s for at, s in plan if t >= at] or [None])
    for n in range(int(seconds * fps)):
        t = n / fps
        sc.mat.grow(int(8 + 90 * min(1.0, t / 7)), rng=rng)
        for e in ("left", "right", "top"):
            if t > 2.5 and rng.random() < 0.15:
                sc.mat.seed(sc.mat.index_at(e, rng.randrange(sc.mat.edge_len(e))))
        sc.render_mat()
        sc.patches = [F.Patch(x, y, min(6.0, max(1.0, (t - at - 0.5) * 6)), 6, i * 41 + 7)
                      for i, (at, (x, y)) in enumerate(shots) if t >= at + 0.5]
        q = sc.begin()
        for at, rows, (x, y) in feeds:                                  # 文件飞进嘴里
            if t < at:
                put_icon(q, rows, x, y)
            elif t < at + 0.6:
                k = (t - at) / 0.6
                img = icon(rows)
                s = 1 - 0.7 * k
                px_ = x + (founder[0] - x) * k
                py_ = y + (ground - 30 - y) * k - math.sin(math.pi * k) * 30
                q.drawImage(int(px_ - img.width() * s / 2), int(py_), img.scaled(max(1, int(img.width() * s)), max(1, int(img.height() * s))))
        st = stage_at(t, founder[1])
        grew = max(at for at, s in founder[1] if t >= at)
        flash = st > 0 and 0 <= t - grew < 0.5 and int((t - grew) * 10) % 2 == 0
        chew = any(0 <= t - (at + 0.6) < 0.5 for at, _, _ in feeds)
        breath = int(t / 0.8) % 2 == 1
        blink = (t % 2.7) < 0.12
        size = 3 if t < 1.2 else 5
        feet(q, F.art_pixmap(st, size, blink, chew and st > 0, flash, breath and st > 0 and not chew), founder[0], ground - (PX if chew and int(t * 8) % 2 else 0))
        for (x, y), born, plan in kids:                                  # 放出去的孢子落地后自己长大
            if t < born:
                continue
            k = min(1.0, (t - born) / 0.6)
            if k < 1:
                sx, sy = founder[0], ground - 120
                feet(q, F.art_pixmap(0, 3), sx + (x - sx) * k, sy + (y - sy) * k - math.sin(math.pi * k) * 60)
            else:
                ks = stage_at(t, plan)
                feet(q, F.art_pixmap(ks, 3 + min(2, int((t - born) * 2)), (t % 3.3) < 0.12, False, False, ks > 0 and int(t / 0.9) % 2 == 0), x, y)
        if t >= 6.0:                                                     # 喷孢菌冒出来，一次次喷
            reveal = min(99, int((t - 6.0) / 0.6 * 22))
            nxt = next((at for at, _ in shots if at + 0.25 > t), None)
            frame = "shoot" if any(0 <= t - at < 0.25 for at, _ in shots) else "charge" if nxt and nxt - t < 0.5 else "idle"
            img = F.spitter_image(frame, 0, reveal)
            bx, by = sc.spitter_base(0.1)
            q.drawImage(int(bx - img.width() / 2 + ((2 if int(t * 20) % 2 else -2) if frame == "charge" else 0)), int(by - img.height() + PX), img)
            for at, (tx, ty) in shots:
                if 0 <= t - at < 0.5:
                    k = (t - at) / 0.5
                    sx, sy = bx, by - img.height() + 12
                    cx, cy = (sx + tx) / 2, min(sy, ty) - 70
                    feet(q, F.art_pixmap(0, 3), (1 - k) ** 2 * sx + 2 * (1 - k) * k * cx + k * k * tx,
                         (1 - k) ** 2 * sy + 2 * (1 - k) * k * cy + k * k * ty)
        q.end()
        img = QImage(GW, GH, QImage.Format.Format_ARGB32)
        p = QPainter(img)
        p.fillRect(0, 0, GW, BAR, INK)
        text(p, QRect(20, 10, 400, 36), "FUNGI.EXE", mono(26), PAPER)
        for i in range(3):
            p.setPen(QPen(PAPER, 3))
            p.drawRect(GW - 40 - i * 34, 14, 22, 22)
        p.drawPixmap(0, BAR, big(sc.img, 2))
        p.end()
        rgba = img.convertToFormat(QImage.Format.Format_RGBA8888)
        pil = Image.frombuffer("RGBA", (GW, GH), bytes(rgba.constBits().asstring(rgba.sizeInBytes())), "raw", "RGBA", 0, 1)
        frames.append(pil.convert("RGB").quantize(colors=64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    frames[0].save(path, save_all=True, append_images=frames[1:] + [frames[-1]] * int(fps * 1.5),
                   duration=int(1000 / fps), loop=0, optimize=True, disposal=1)
    print("saved", path, f"{path.stat().st_size / 1e6:.1f} MB", f"占满 {sc.mat.occupied():.0%}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for n, fn in enumerate((cover, growth, feeding, overgrown, spitter_page, chat_page, hunger_page), 1):
        img = fn()
        path = OUT / f"{n:02d}_{fn.__name__}.png"
        img.save(str(path))
        print("saved", path)
    if "--gif" in sys.argv:
        animation(OUT / "preview.gif")


if __name__ == "__main__":
    main()
