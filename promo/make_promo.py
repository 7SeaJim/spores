#!/usr/bin/env python3
"""小红书宣传图（1080×1440）+ 效果动图。画面全部用 fungi.py 自己的渲染函数生成。

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


# ───────────── 页面 ─────────────

def cover():
    img, p = page("FUNGI.EXE")
    headline(p, 130, "我的桌面\n长蘑菇了", "一只吃「未整理文件」的像素宠物", size=118)
    sc = Scene(444, 372, seed=3)
    sc.grow([("bottom", 0.3), ("bottom", 0.8), ("left", 0.45), ("top", 0.25), ("right", 0.6), ("top", 0.75)], 3200)
    sc.render_mat(full=True)
    sc.patches = [F.Patch(370, 118, 4.5, 5, 11)]
    q = sc.begin()
    labels = [(FOLDER, "新建文件夹(3)", 85, 48), (DOC, "最终版_真的\n最终_v7.md", 205, 44), (FOLDER, "资料归档", 80, 150)]
    ends = [put_icon(q, rows, x, y) for rows, _, x, y in labels]
    ground = sc.h - 16
    sx, sy = draw_spitter(q, sc, 0.86)
    arc(q, (sx, sy), (370, 118), 70)
    for pm, x, dy in ((pet(3, mood="hungry"), 162, 2), (pet(4), 268, 0), (pet(2, blink=True), 362, 0), (pet(1), 414, -2)):
        feet(q, pm, x, ground + dy)
    q.end()
    sc.blit(p, (W - sc.w * 2) // 2, 620)
    for (_, name, x, y), end in zip(labels, ends):
        icon_label(p, sc, name, x, end + 2)
    ax, ay = sc.at(268, ground - pet(4).height())
    bubble(p, "你其实不是想删掉它，\n你是想有人替你留着。", ax, ay + 16, px=32, max_w=520)
    p.end()
    return img


def growth():
    img, p = page("FUNGI.EXE — 成长", "2 / 7")
    headline(p, 140, "从一个 3×3 的\n小黑点开始", "拖文件喂它，一路长成会放孢子的蘑菇", size=96)
    items = [(pet(0, size=3), "spores", "3×3 黑点"), (pet(1), "sprout", "冒出菌盖"), (pet(2), "baby", "豆子身体"),
             (pet(3), "young", "长出手脚"), (pet(4), "adult", "放出孢子")]
    pms = [big(pm, 2) for pm, _, _ in items]
    slots = [max(140, pm.width()) for pm in pms]
    x, ground = (W - sum(slots)) / 2, 880
    for pm, slot, (_, name, cap) in zip(pms, slots, items):
        cx = x + slot / 2
        feet(p, pm, cx, ground)
        text(p, QRect(int(cx - slot / 2), ground + 22, slot, 50), name, mono(28), INK, Qt.AlignmentFlag.AlignHCenter)
        text(p, QRect(int(cx - slot / 2), ground + 62, slot, 50), cap, font(24, QFont.Weight.Medium), DUST, Qt.AlignmentFlag.AlignHCenter)
        x += slot
    p.fillRect(96, 1030, W - 192, 4, INK)
    text(p, QRect(96, 1054, W - 192, 60), "放着不管的时候，它也在动", font(40), INK)
    frames = [(pet(4, breath=True), "呼吸"), (pet(4, tilt=-1), "晃脑袋"), (pet(4, blink=True), "眨眼"), (pet(4, blink=True, breath=True), "睡着")]
    gap2 = (W - 192 - sum(pm.width() for pm, _ in frames)) / 3
    x, ground = 96.0, 1270
    for pm, cap in frames:
        cx = x + pm.width() / 2
        feet(p, pm, cx, ground)
        text(p, QRect(int(cx - 90), ground + 8, 180, 40), cap, font(26, QFont.Weight.Bold), DUST, Qt.AlignmentFlag.AlignHCenter)
        if cap == "睡着":
            label(p, "z", cx + 38, ground - 124, px=30)
            label(p, "Z", cx + 58, ground - 152, px=38)
        x += pm.width() + gap2
    p.end()
    return img


def feeding():
    img, p = page("FUNGI.EXE — 喂食", "3 / 7")
    headline(p, 140, "它吃的不是内容\n是混乱", "越乱越肥：命名混乱、重复、久放的最好吃", size=96)
    sc = Scene(444, 236, seed=5)
    sc.grow([("bottom", 0.4), ("left", 0.6), ("right", 0.3)], 700)
    sc.render_mat(full=True)
    q = sc.begin()
    end = put_icon(q, FOLDER, 120, 58)
    cur = icon(CURSOR)
    q.drawImage(150, 100, cur)
    ground = sc.h - 14
    young = pet(3, mouth=True)
    feet(q, young, 318, ground)
    rng = random.Random(4)
    for _ in range(9):
        x, y = 318 + rng.uniform(-34, 34), ground - young.height() + rng.uniform(-14, 14)
        q.fillRect(int(x) - 3, int(y) - 3, 6, 6, PAPER)
        q.fillRect(int(x) - 2, int(y) - 2, 4, 4, INK)
    arc(q, (150, 60), (300, ground - young.height() + 10), 40)
    q.end()
    sc.blit(p, (W - sc.w * 2) // 2, 540)
    icon_label(p, sc, "新建文件夹(3)", 120, end + 2)
    fx, fy = sc.at(318, ground - young.height())
    label(p, f"+19 未整理的、久放的", fx - 40, fy - 44, px=34)
    rows = [("新建文件夹(3)/最终版_真的最终_v7/", "最肥", f"×{F.taste('新建文件夹(3)_最终版_真的最终_v7', 0)[0]:.1f}"),
            ("整整齐齐的文件夹", "干巴巴", "×0.7"), ("资料归档/", "已归档（抖一下伞）", "×0.5"), ("网盘同步目录里的", "飘着，够不到", "不吃")]
    y = 1052
    for name, verdict, mult in rows:
        p.fillRect(96, y, W - 192, 3, GREY)
        text(p, QRect(96, y + 14, 500, 60), name, font(29, QFont.Weight.Bold), INK)
        text(p, QRect(610, y + 14, 270, 60), verdict, font(29, QFont.Weight.Medium), DUST)
        text(p, QRect(W - 96 - 140, y + 12, 140, 60), mult, mono(32), INK, Qt.AlignmentFlag.AlignRight)
        y += 72
    p.end()
    return img


def colony_page():
    img, p = page("FUNGI.EXE — 菌毯", "4 / 7")
    headline(p, 140, "放着不管\n屏幕边上会长菌毯", "菌毯铺开以后，会冒出一只喷孢子的菌", size=92)
    sc = Scene(444, 334, seed=9)
    sc.grow([("bottom", 0.2), ("bottom", 0.62), ("right", 0.3), ("top", 0.5), ("left", 0.7), ("left", 0.2)], 4200)
    sc.render_mat(full=True)
    sc.patches = [F.Patch(300, 120, 5.0, 6, 21)]
    q = sc.begin()
    sx, sy = draw_spitter(q, sc, 0.5, "shoot")
    targets = [(300, 120, "patch"), (120, 140, "vanish"), (370, 230, "spore")]
    for tx, ty, what in targets:
        arc(q, (sx, sy), (tx, ty), 90)
        if what == "spore":
            feet(q, pet(0, size=3), tx, ty + 8)
        elif what == "vanish":
            for a in range(6):
                ang = a * math.pi / 3 + 0.4
                x, y = tx + math.cos(ang) * 10, ty + math.sin(ang) * 10
                q.fillRect(int(x) - 3, int(y) - 3, 6, 6, QColor(250, 249, 244, 170))
                q.fillRect(int(x) - 2, int(y) - 2, 4, 4, DUST)
    q.end()
    sc.blit(p, (W - sc.w * 2) // 2, 560)
    for tx, ty, s in ((120, 140, "75% 散掉"), (300, 120, "20% 落地成菌斑"), (370, 230, "5% 长成新孢子")):
        px_, py_ = sc.at(tx, ty)
        label(p, s, px_, py_ - 34, px=28)
    text(p, QRect(96, 1262, W - 192, 110), "喷孢菌不会动，隔几分钟喷一次；\n菌毯鼠标点得穿，不挡你干活", font(32, QFont.Weight.Medium), INK)
    p.end()
    return img


def measure(s: str, px: int, max_w: int) -> tuple[int, int]:
    box = QFontMetrics(font(px, QFont.Weight.Bold)).boundingRect(QRect(0, 0, max_w, 2000), int(Qt.TextFlag.TextWrapAnywhere), s)
    return box.width() + 44, box.height() + 40


def chat_page():
    img, p = page("FUNGI.EXE — 聊天", "5 / 7")
    headline(p, 140, "双击它\n它只回你一句话", "接入 DeepSeek；说话方式是「文件菇·电波」", size=88)
    lines = [("那个文件夹要不要整理一下", "那个文件夹已归档了，干巴巴的，我们不碰。", pet(2)),
             ("你刚刚吃了什么", "（嗝）……「待办_旧_请勿删除」。", pet(2, mouth=True)),
             ("今天好累", "还有一次备份我就满了，所以现在就说：你很好。", pet(2, blink=True)),
             ("你是 AI 吧", "我知道你不是真的在问蘑菇。", pet(2))]
    y = 530
    for you, it, face in lines:
        uw, uh = measure(you, 28, 520)
        bubble(p, you, W - 96 - uw / 2, y + uh, px=28, max_w=520, dark=True, tail=False)
        y += uh + 8
        pm = face
        bw, bh = measure(it, 30, W - 192 - pm.width() - 28 - 44)
        row = max(pm.height(), bh)
        feet(p, pm, 96 + pm.width() / 2, y + row - (row - pm.height()) / 2)
        bubble(p, it, 96 + pm.width() + 28 + bw / 2, y + row - (row - bh) / 2, px=30, max_w=W - 192 - pm.width() - 28 - 44, tail=False)
        y += row + 22
    text(p, QRect(96, H - 36 - 62, 600, 40), "* 风格示例；回复限定一句话", font(24, QFont.Weight.Medium), DUST)
    p.end()
    return img


def hunger_page():
    img, p = page("FUNGI.EXE — 饥饿", "6 / 7")
    headline(p, 140, "不喂它会饿扁\n但不会死", "饿扁会缩回小时候，最后缩成孢囊；喂一口就活过来", size=92)
    items = [(big(pet(3), 2), "吃饱", 1.0), (big(pet(3, mood="hungry"), 2), "饿了", 0.25),
             (big(pet(3, wither=True, mood="starving"), 2), "饿扁了", 0.0), (big(pet(0, wither=True, mood="dormant"), 4), "休眠", 0.0)]
    ground, xs = 860, [196, 425, 655, 885]
    for (pm, cap, full), x in zip(items, xs):
        feet(p, pm, x, ground)
        text(p, QRect(x - 110, ground + 22, 220, 60), cap, font(40), INK, Qt.AlignmentFlag.AlignHCenter)
        bw, bh, by = 170, 26, ground + 92
        p.fillRect(x - bw // 2, by, bw, bh, INK)
        p.fillRect(x - bw // 2 + 4, by + 4, bw - 8, bh - 8, PAPER)
        p.fillRect(x - bw // 2 + 4, by + 4, int((bw - 8) * full), bh - 8, INK)
    text(p, QRect(96, ground + 130, 300, 40), "↑ 饱腹", font(24, QFont.Weight.Medium), DUST)
    label(p, "咕…", xs[1] + 60, ground - 230, px=36)
    label(p, "喂一口就醒", xs[3], ground - 130, px=30)
    text(p, QRect(96, 1110, W - 192, 150), "不喂的话 16 小时饿扁；所有菌都饿扁时，\n边上的菌毯会慢慢退回去，喷孢菌也蔫掉不喷", font(32, QFont.Weight.Medium), INK)
    p.end()
    return img


def howto_page():
    img, p = page("FUNGI.EXE — 玩法", "7 / 7")
    headline(p, 140, "怎么养", "", size=100)
    items = [("喂", "把 .txt / .md 或文件夹拖到它身上"),
             ("聊", "双击它说话（自己填 DeepSeek Key）"),
             ("看", "菌毯从屏幕边长出来，点击会穿过去"),
             ("嗝", "吃完偶尔打嗝，嗝出前主人的一个文件名"),
             ("⚠", "真的会吃掉：被吃的文件会删除（Windows 进回收站），路径记在 eaten.log；右键可关掉「吞噬文件」，网盘里的不碰")]
    y = 320
    for tag, desc in items:
        p.fillRect(96, y, 76, 76, INK)
        text(p, QRect(96, y + 8, 76, 60), tag, font(44), PAPER, Qt.AlignmentFlag.AlignHCenter)
        f = font(36, QFont.Weight.Bold)
        r = QRect(200, y + 10, W - 296, 240)
        text(p, r, desc, f, INK)
        hh = QFontMetrics(f).boundingRect(r, int(Qt.TextFlag.TextWordWrap), desc).height()
        y += max(96, hh + 36)
    y += 20
    p.fillRect(96, y, W - 192, 4, INK)
    text(p, QRect(96, y + 30, W - 192, 200), "开源 · Python + PyQt6\nLinux 实测，Windows 适配中\nGitHub：7SeaJim/spores", font(36, QFont.Weight.Medium), INK)
    feet(p, big(pet(4, blink=True), 2), W - 250, H - 110)
    p.end()
    return img


# ───────────── 效果动图 ─────────────

def animation(path: Path, seconds: float = 10.0, fps: int = 10):
    from PIL import Image
    GW, GH, BAR = 640, 820, 60
    sc = Scene(320, 380, seed=12)
    sc.grow([("bottom", 0.5), ("left", 0.4), ("top", 0.7), ("right", 0.5)], 700)
    sc.render_mat(full=True)
    ground = sc.h - 14
    home = (150, ground)
    feeds = [  # (飞来的时刻, 名字, 图标, 起点, 吃完后的营养, 飘字)
        (1.0, "aaa.txt", DOC, (64, 64), 15, "+13 久放的"),
        (3.0, "新建文件夹(3)", FOLDER, (70, 150), 28, "+19 未整理的"),
        (4.8, "", DOC, (64, 64), 60, "→ baby"),
        (6.0, "", DOC, (70, 150), 110, "→ young"),
        (7.2, "", FOLDER, (64, 64), 175, "→ adult"),
    ]
    frames = []
    rng = random.Random(1)
    for n in range(int(seconds * fps)):
        t = n / fps
        sc.mat.grow(20, rng=rng)
        sc.render_mat()
        eaten = [f for f in feeds if t >= f[0] + 0.8]
        nutrition = eaten[-1][4] if eaten else 0
        stage = F.Creature(id="x", name="x", nutrition=nutrition).stage
        last_eat = eaten[-1][0] + 0.8 if eaten else -9
        eating = 0 <= t - last_eat < 0.8
        flash = stage > 0 and 0.2 <= t - last_eat < 0.8 and eaten[-1][5].startswith("→") and int((t - last_eat) * 10) % 2 == 0
        sc.patches = [F.Patch(236, 110, min(4.0, max(1.0, (t - 9.6) * 10)), 4, 9)] if t >= 9.6 else []
        q = sc.begin()
        for at, name, rows, start, _, _ in feeds:                     # 桌面上的文件：飞过去被吃掉
            if t < at:
                if name:
                    put_icon(q, rows, *start)
            elif t < at + 0.8:
                k = (t - at) / 0.8
                x = start[0] + (home[0] - start[0]) * k
                y = start[1] + (home[1] - 60 - start[1]) * k - math.sin(math.pi * k) * 40
                img = icon(rows)
                q.drawImage(int(x - img.width() / 2 * (1 - k * 0.6)), int(y), img.scaled(int(img.width() * (1 - k * 0.6)), int(img.height() * (1 - k * 0.6))))
        breath = int(t / 0.8) % 2 == 1 and not eating
        blink = (t % 3.1) < 0.15
        pm = F.art_pixmap(stage, F.spore_size(nutrition), blink, eating and stage > 0 and int(t * 8) % 2 == 0, flash, breath and stage > 0)
        bob = -PX if eating and int(t * 8) % 2 else 0
        feet(q, pm, home[0], home[1] + bob)
        if eating:
            k = t - last_eat
            for i in range(7):
                ang = -math.pi / 2 + (i - 3) * 0.45
                x = home[0] + math.cos(ang) * 60 * k
                y = home[1] - pm.height() + math.sin(ang) * 70 * k + 180 * k * k
                q.fillRect(int(x) - 3, int(y) - 3, 6, 6, PAPER)
                q.fillRect(int(x) - 2, int(y) - 2, 4, 4, INK)
        if t >= 7.9:                                                   # 成年放孢子
            for tx in (62, 262):
                k = min(1.0, (t - 7.9) / 0.7)
                x = home[0] + (tx - home[0]) * k
                y = home[1] - 110 + (ground - home[1] + 110) * k - math.sin(math.pi * k) * 70
                feet(q, F.art_pixmap(0, 3), x, y)
        if t >= 8.6:                                                   # 喷孢菌从菌毯里冒出来
            reveal = min(99, int((t - 8.6) / 0.6 * 22))
            frame = "charge" if 9.1 <= t < 9.5 else "shoot" if 9.5 <= t < 9.8 else "idle"
            img = F.spitter_image(frame, 0, reveal)
            bx, by = sc.spitter_base(0.18)
            dx = (2 if int(t * 20) % 2 else -2) if frame == "charge" else 0
            q.drawImage(int(bx - img.width() / 2 + dx), int(by - img.height() + PX), img)
            if 9.5 <= t < 9.6 + 0.3:
                k = min(1.0, (t - 9.5) / 0.4)
                sx, sy = bx, by - img.height() + 12
                cx, cy = (sx + 236) / 2, min(sy, 110) - 60
                x = (1 - k) ** 2 * sx + 2 * (1 - k) * k * cx + k * k * 236
                y = (1 - k) ** 2 * sy + 2 * (1 - k) * k * cy + k * k * 110
                feet(q, F.art_pixmap(0, 3), x, y)
        q.end()

        img = QImage(GW, GH, QImage.Format.Format_ARGB32)
        img.fill(PAPER)
        p = QPainter(img)
        p.fillRect(0, 0, GW, BAR, INK)
        text(p, QRect(22, 12, 400, 40), "FUNGI.EXE", mono(28), PAPER)
        sc.blit(p, 0, BAR)
        p.fillRect(0, BAR, GW, 0, INK)
        for at, name, rows, start, _, _ in feeds:
            if name and t < at:
                end = start[1] + icon(rows).height()
                icon_label(p, sc, name, start[0], end + 2)
        for at, _, _, _, _, words in feeds:
            k = t - (at + 0.8)
            if 0 <= k < 1.2:
                fx, fy = sc.at(home[0], home[1] - pm.height())
                F.draw_label(p, words, fx, fy - 14 - k * 30, font(30, QFont.Weight.Black), int(255 * min(1.0, (1.2 - k) * 2.5)))
        if t >= 8.8:
            ax, ay = sc.at(home[0], home[1] - pm.height())
            bubble(p, "（嗝）……「aaa.txt」。", min(max(ax, 180), GW - 180), ay + 10, px=28, max_w=360)
        p.end()
        rgba = img.convertToFormat(QImage.Format.Format_RGBA8888)
        pil = Image.frombuffer("RGBA", (GW, GH), bytes(rgba.constBits().asstring(rgba.sizeInBytes())), "raw", "RGBA", 0, 1)
        frames.append(pil.convert("RGB").quantize(colors=64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    hold = [frames[-1]] * int(fps * 1.2)                              # 结尾停一下再循环
    frames[0].save(path, save_all=True, append_images=frames[1:] + hold, duration=int(1000 / fps), loop=0, optimize=True, disposal=1)
    print("saved", path, f"{path.stat().st_size / 1e6:.1f} MB", len(frames) + len(hold), "帧")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for n, fn in enumerate((cover, growth, feeding, colony_page, chat_page, hunger_page, howto_page), 1):
        img = fn()
        path = OUT / f"{n:02d}_{fn.__name__}.png"
        img.save(str(path))
        print("saved", path)
    if "--gif" in sys.argv:
        animation(OUT / "preview.gif")


if __name__ == "__main__":
    main()
