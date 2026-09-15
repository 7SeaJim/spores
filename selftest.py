#!/usr/bin/env python3
"""离屏自测：QT_QPA_PLATFORM=offscreen python3 selftest.py [输出预览图目录]"""
import hashlib
import json
import math
import random
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QMimeData, QPointF, QRect, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QPainter
from PyQt6.QtWidgets import QApplication

import fungi as F

app = QApplication([])
root = Path(tempfile.mkdtemp(prefix="fungi-test-"))
out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else root
failures = 0


def check(name, cond, extra=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"   ({extra})" if extra else ""))
    failures += not cond


def wait(sec):
    loop = QEventLoop()
    QTimer.singleShot(int(sec * 1000), loop.quit)
    loop.exec()


def fingerprint(path: Path):
    st = path.stat()
    return hashlib.sha1(path.read_bytes()).hexdigest(), st.st_mtime_ns, st.st_size


def shutdown(colony):
    colony.tick_timer.stop()
    colony.autosave.stop()
    for w in colony.widgets.values():
        w.timer.stop()
        w.close()
    colony.spitter_timer.stop()
    colony.fullscreen_timer.stop()
    colony.screen_debounce.stop()
    for s in colony.shots:
        s.finish()
    for b in colony.bubbles.values():
        b.finish()
    for v in list(colony.mat_views.values()) + list(colony.patch_views.values()) + [colony.spitter_view]:
        if v:
            v.close()


def drag(widget, paths, actions):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    pos = widget.sprite_rect.center()
    enter = QDragEnterEvent(pos, actions, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, enter)
    drop = QDropEvent(QPointF(pos), actions, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    if enter.isAccepted():
        QApplication.sendEvent(widget, drop)
    return enter, drop


# ── 准备食物 ──
food = root / "food"
food.mkdir()
txt = food / "note.txt"
txt.write_text("x" * 2048)
md = food / "poem.md"
md.write_text("# 下雨\n" * 40)
png = food / "photo.png"
png.write_bytes(b"\x89PNG" + b"0" * 500)
folders = []
for i in range(8):
    d = food / f"dir{i}"
    (d / "sub").mkdir(parents=True)
    for j in range(14):
        (d / ("sub" if j % 2 else "") / f"f{j}.md").write_text("hello " * 30)
    folders.append(d)
all_files = [p for p in food.rglob("*") if p.is_file()]
before_prints = {p: fingerprint(p) for p in all_files}
data = root / "save"

print("1. 出生")
colony = F.Colony(data)
colony.devour = False                   # 1–5 节反复喂同一批文件，先关掉吞噬（第 12 节专门测）
check("初始只有 1 只", len(colony.creatures) == 1)
c = colony.creatures[0]
w = colony.widgets[c.id]
check("名字 spores、阶段 spores、3×3", c.name == "spores" and c.stage == 0 and F.spore_size(c.nutrition) == 3)
check("存档已写出", (data / "save.json").exists())
spore_grab = w.grab()

print("2. 喂食规则")
colony.feed(w, [txt])
check("txt 2KB = +13", round(c.nutrition) == 13 and c.feeds == 1, f"nutrition={c.nutrition:.2f}")
colony.feed(w, [txt])
check("重复喂同一个文件只有 1/4", round(c.nutrition) == 16 and c.feeds == 2, f"nutrition={c.nutrition:.2f}")
n0, f0 = c.nutrition, c.feeds
colony.feed(w, [png])
check("png 被拒绝", c.nutrition == n0 and c.feeds == f0 and any("不吃" in f["text"] for f in w.floaters))
check("spores 方块变大到 5×5", F.spore_size(c.nutrition) == 5)

print("3. 真实拖放事件")
enter, drop = drag(w, [md], Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)
check("拖入被接受且动作为 Copy", enter.isAccepted() and enter.dropAction() == Qt.DropAction.CopyAction)
check("松手后吃掉 poem.md", drop.isAccepted() and c.feeds == f0 + 1 and c.nutrition > n0)
check("吃完进入 sprout", c.stage == 1, c.stage_name)
enter2, _ = drag(w, [txt], Qt.DropAction.MoveAction)
check("只给 Move 动作的拖放被拒绝（保护源文件）", not enter2.isAccepted())
check("吃东西时窗口尺寸跟随阶段变化", w.sprite_rect.width() == F.art_pixmap(1).width())
wait(0.2)
eat_grab = w.grab()

print("4. 长大 → 成年 → 放出 spores")
for d in folders[:5]:
    colony.feed(w, [d])
check("文件夹按 txt/md 数量给营养（最多 30）", c.log[-1][2] == 30, str(c.log[-1]))
check("到达 adult", c.stage == F.ADULT, f"{c.stage_name} {c.nutrition:.1f}")
check("首批放出 2 个 spores", len(colony.creatures) == 3 and c.released == 2)
kids = [k for k in colony.creatures if k is not c]
check("孩子是第 2 代的 spores", all(k.gen == 2 and k.stage == 0 for k in kids), [k.name for k in kids])
saved = {d["id"]: (d["x"], d["y"]) for d in json.loads((data / "save.json").read_text())["creatures"]}
check("飞行途中存档里记的是落点", all(saved[k.id] == tuple(colony.widgets[k.id].flight["b"]) for k in kids))
wait(3.0)
check("孢子飞行已落地", all(colony.widgets[k.id].flight is None for k in kids))
geo = app.primaryScreen().availableGeometry()
check("落点在屏幕内", all(geo.contains(k.x, k.y - 1) for k in kids), [(k.x, k.y) for k in kids])
check("两个孢子不落在同一处", math.dist((kids[0].x, kids[0].y), (kids[1].x, kids[1].y)) > 40)
while c.released < 3 and c.nutrition < 400:
    colony.feed(w, [folders[5 + (c.feeds % 3)]])
check("成年后每 +60 营养再放 1 个", len(colony.creatures) == 4 and c.released == 3, f"{c.nutrition:.1f}")
w.hover = True
adult_grab = w.grab()
w.hover = False
w.drag_over = True
drag_grab = w.grab()
w.drag_over = False

print("5. 关掉吞噬时文件完好")
check("所有食物文件内容/修改时间/大小都没变", all(fingerprint(p) == before_prints[p] for p in all_files))
check("食物文件一个不少", sorted(p for p in food.rglob("*") if p.is_file()) == sorted(all_files))

print("6. 存档 / 读档")
colony.save()
snap = {k.id: (k.name, k.gen, k.born, k.feeds, k.nutrition, k.released) for k in colony.creatures}
shutdown(colony)
colony2 = F.Colony(data)
check("吞噬开关随存档保存", colony2.devour is False)
snap2 = {k.id: (k.name, k.gen, k.born, k.feeds, k.nutrition, k.released) for k in colony2.creatures}
check("读档后成员一致", snap.keys() == snap2.keys())
check("出生时间/喂食次数/代数/放出数完全一致",
      all(snap[i][:4] == snap2[i][:4] and snap[i][5] == snap2[i][5] for i in snap))
check("营养一致（只多了几秒的自然生长）", all(0 <= snap2[i][4] - snap[i][4] < 0.2 for i in snap))
check("读档不会重复放孢子", len(colony2.creatures) == 4)
shutdown(colony2)

print("7. 离线生长")
raw = json.loads((data / "save.json").read_text())
raw["last_seen"] -= 3 * 3600
(data / "save.json").write_text(json.dumps(raw))
colony3 = F.Colony(data)
grown = {k.id: k.nutrition for k in colony3.creatures}
check(f"关掉 3 小时后每只 +{F.OFFLINE_CAP}（封顶）",
      all(abs(grown[i] - snap2[i][4] - F.OFFLINE_CAP) < 0.5 for i in snap2), "")
shutdown(colony3)

print("8. 存档损坏")
(data / "save.json").write_text("{not json")
colony4 = F.Colony(data)
check("损坏存档被备份并重新开始", len(colony4.creatures) == 1 and list(data.glob("save.broken-*.json")))
shutdown(colony4)

print("9. 待机动效")
from PyQt6.QtGui import QEnterEvent
for st in range(1, 5):
    rows = F.load_pxl(F.STAGES[st][0])[0]
    cap, br = F.IDLE_RIG[st]
    width = lambda r: len(r.strip("."))
    check(f"{F.STAGES[st][0]} 骨骼：第 {cap} 行起是菌柄、呼吸行 {br} 在菌柄里",
          width(rows[cap]) < width(rows[cap - 1]) and cap < br < len(rows) - 1)
base = F.art_pixmap(4)
poses = [F.art_pixmap(4, 3, False, False, False, b, tl) for b in (False, True) for tl in (-1, 0, 1)]
check("呼吸/歪头不改变精灵尺寸（窗口不抖）", all(p.size() == base.size() for p in poses))
check("呼吸帧、左歪、右歪都和静止帧不同",
      all(F.art_pixmap(4, 3, False, False, False, b, tl).toImage() != base.toImage() for b, tl in ((True, 0), (False, -1), (False, 1))))

colony5 = F.Colony(root / "idle")
a = colony5.creatures[0]
a.released = F.FIRST_BURST
a.nutrition = F.STAGES[F.ADULT][1] + 1
aw = colony5.widgets[a.id]
aw.refit()
now = time.time()
P = F.BREATH_PERIOD
aw.blinks = [now + 100]
aw.idle_at = now + 100
aw.phase = (0.1 - now) % P             # 此刻吸气（不压缩），避开周期边界的浮点误差
aw.anim = ("tilt", now - 0.1)
check("歪头动画第一拍是向左歪 1 格", aw.current_pixmap(now).toImage() == F.art_pixmap(4, 3, False, False, False, False, -1).toImage())
aw.anim = None
aw.phase = (-now + 0.8 * P) % P        # 此刻呼气
check("呼气时菌柄压缩 1 行", aw.current_pixmap(now).toImage() == F.art_pixmap(4, 3, False, False, False, True, 0).toImage())
aw.drag_over = True
check("张嘴时不叠加呼吸", aw.current_pixmap(now).toImage() == F.art_pixmap(4, 3, False, True, False, False, 0).toImage())
aw.drag_over = False
breath_grab = aw.grab()

aw.do_idle("blink2")
check("连眨两下", len(aw.blinks) == 2)
aw.do_idle("puff")
aw.update_mask()
check("成年菌飘孢子尘，窗口只放开上方窄带", sum(q["kind"] == "mote" for q in aw.particles) == 3 and aw.masked == "band")
wait(1.0)
puff_grab = aw.grab()
aw.particles = []
aw.anim = ("wiggle", time.time())
check("spores 扭一扭是左右位移", aw.body_offset(time.time())[0] != 0)
aw.anim = None
aw.idle_at = 0
aw.idle(time.time())
check("到点会自己做一个小动作", aw.anim is not None or aw.particles or len(aw.blinks) == 2)

aw.anim, aw.particles, aw.floaters = None, [], []
check("睡眠阈值：白天 600s / 深夜 120s", colony5.sleep_after() in (120, 600))
aw.last_touch = time.time() - 10_000
aw.idle(time.time())
check("很久没人理就睡着", aw.asleep)
aw.z_at = 0
aw.idle(time.time())
aw.update_mask()
check("睡着冒 z，但不整窗挡点击", any(f["ambient"] for f in aw.floaters) and aw.masked == "band")
now = time.time()
aw.phase = (0.1 - now) % (P * 1.6)     # 睡着时呼吸周期 ×1.6，此刻吸气
check("睡着时闭眼", aw.current_pixmap(now).toImage() == F.art_pixmap(4, 3, True, False, False, False, 0).toImage())
for f in aw.floaters:
    f["t0"] = time.time() - 0.6
sleep_grab = aw.grab()
QApplication.sendEvent(aw, QEnterEvent(QPointF(20, 20), QPointF(20, 20), QPointF(20, 20)))
check("鼠标碰到就醒，跳一下冒 !", not aw.asleep and aw.anim and aw.anim[0] == "hop" and any(f["text"] == "!" for f in aw.floaters))
shutdown(colony5)

strip = []
for st in range(1, 5):
    strip += [F.art_pixmap(st), F.art_pixmap(st, 3, False, False, False, True, 0),
              F.art_pixmap(st, 3, False, False, False, False, -1), F.art_pixmap(st, 3, False, False, False, False, 1),
              F.art_pixmap(st, 3, True, False, False, True, 0)]
iw = max(sum(p.width() + 16 for p in strip[i:i + 5]) for i in range(0, 20, 5)) + 16
grabs = [breath_grab, puff_grab, sleep_grab]
ih = sum(max(p.height() for p in strip[i:i + 5]) + 16 for i in range(0, 20, 5)) + max(g.height() for g in grabs) + 32
iimg = QImage(max(iw, sum(g.width() + 16 for g in grabs) + 16), ih, QImage.Format.Format_ARGB32)
iimg.fill(QColor(250, 249, 244))
ip = QPainter(iimg)
y = 16
for i in range(0, 20, 5):
    x, h = 16, max(p.height() for p in strip[i:i + 5])
    for pm in strip[i:i + 5]:
        ip.drawPixmap(x, y + h - pm.height(), pm)
        x += pm.width() + 16
    y += h + 16
ip.fillRect(0, y, iimg.width(), iimg.height() - y, QColor(38, 56, 74))
x = 16
for g in grabs:
    ip.drawPixmap(x, y + 8, g)
    x += g.width() + 16
ip.end()

print("10. 菌毯")
m = F.Mycelium(40, 30)
check("环形坐标往返", all(m.index_at(*m.edge_of(i)) == i for i in range(m.n)))
check("最近的边：上 / 右 / 下 / 左",
      m.edge_of(m.nearest(20, 1)) == ("top", 20) and m.edge_of(m.nearest(38, 15)) == ("right", 15)
      and m.edge_of(m.nearest(10, 29)) == ("bottom", 29) and m.edge_of(m.nearest(0, 5)) == ("left", 24))
check("落孢子长出第一格", m.seed(m.nearest(20, 1)) and m.d[20] == 1)
check("已有菌毯处不重复落孢子", not m.seed(20))
m.grow(1500, rng=random.Random(1))
arcs = sum(1 for i in range(m.n) if m.d[i] and not m.d[i - 1])
check("从落点连续蔓延，没有飞地", arcs <= 1, f"{arcs} 段")
check(f"厚度不超过 {F.MAT_MAX} 格", max(m.d) <= F.MAT_MAX)
check("落点附近比蔓延前沿厚", sum(m.d[18:23]) / 5 > sum(m.d[i] for i in range(m.n) if 0 < m.d[i] <= 2) / max(1, sum(1 for v in m.d if 0 < v <= 2)))
m2 = F.Mycelium(100, 60)
for i in (10, 200):
    m2.seed(i)
m2.grow(40, rng=random.Random(2))
m2.grow(800, near=10, rng=random.Random(2))
check("喂食时最近那段长得多", sum(m2.d[0:40]) > 2 * sum(m2.d[180:220]), f"{sum(m2.d[0:40])} vs {sum(m2.d[180:220])}")
m3 = m.resized(80, 60)
check("换分辨率按比例重采样", m3.n == 280 and abs(m3.coverage() - m.coverage()) < 0.05)
check("存档往返一致", bytes(F.Mycelium.from_json(json.loads(json.dumps(m.to_json()))).d) == bytes(m.d))
check("旧存档里超厚的数据被截到上限", max(F.Mycelium(40, 30, bytes([30] * 140)).d) == F.MAT_MAX)

colony6 = F.Colony(root / "mat")
views = colony6.mat_views
check("四条边各一个菌毯窗口", set(views) == {"top", "right", "bottom", "left"})
check("菌毯窗口鼠标穿透", all(v.windowFlags() & Qt.WindowType.WindowTransparentForInput for v in views.values()))
strip_px = F.MAT_STRIP * F.PX
check(f"边框窗口厚 {strip_px}px（菌毯最厚 {F.MAT_MAX * F.PX}px）",
      strip_px <= 60 and all(min(v.width(), v.height()) == strip_px for v in views.values()))
mm = colony6.mat
for x, y in ((mm.cols * 0.7, mm.rows), (0, mm.rows * 0.3), (mm.cols * 0.2, 0), (mm.cols, mm.rows * 0.8)):
    mm.seed(mm.nearest(x, y))
mm.grow(5000, rng=random.Random(3))
colony6.render_mat(full=True)
bottom = views["bottom"].image
painted = sum(1 for x in range(bottom.width()) for y in range(bottom.height()) if bottom.pixel(x, y) >> 24)
check("渲染出菌毯像素", painted > 0, f"下边 {painted} 格")
check("厚处冒出小蘑菇", any(mm.sprout_at(i) for i in range(mm.n)))
full = F.Mycelium(480, 263, bytes([F.MAT_MAX] * (2 * (480 + 263))))
profile = [full.eff(i) for i in range(full.n)]
check("铺满的地方也有高低起伏", max(profile) - min(profile) >= 3, f"{min(profile):.1f}–{max(profile):.1f} 格")
spots = [(i, full.sprout_at(i)) for i in range(full.n) if full.sprout_at(i)]
gaps = [b[0] - a[0] for a, b in zip(spots, spots[1:])]
mean_gap = sum(gaps) / len(gaps)
spread = (sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)) ** 0.5 / mean_gap
check("小蘑菇多种造型、会翻转、不挤在一起", len({sp[0] for _, sp in spots}) >= 3 and len({sp[1] for _, sp in spots}) == 2 and min(gaps) >= 6,
      f"{len(spots)} 个，{len({sp[0] for _, sp in spots})} 种")
check("小蘑菇间距长短不一（不是等距排列）", spread > 0.5, f"间距 {min(gaps)}–{max(gaps)}，离散系数 {spread:.2f}")
colony6.save()
snap_mat = bytes(mm.d)
shutdown(colony6)
colony7 = F.Colony(root / "mat")
check("读档后菌毯一致", bytes(colony7.mat.d) == snap_mat)
shutdown(colony7)
raw = json.loads((root / "mat" / "save.json").read_text())
raw["last_seen"] -= 5 * 3600
raw["mat"] = F.Mycelium(mm.cols, mm.rows).to_json()
m_seedless = F.Mycelium(mm.cols, mm.rows)
m_seedless.seed(5)
raw["mat"] = m_seedless.to_json()
(root / "mat" / "save.json").write_text(json.dumps(raw))
colony8 = F.Colony(root / "mat")
check("关掉 5 小时回来菌毯长了", colony8.mat.coverage() > m_seedless.coverage(), f"{colony8.mat.coverage():.3f}")
colony8.mat.grow(4000, rng=random.Random(4))
colony8.render_mat(full=True)


def screen_shot(colony, bg):
    a = colony.mat_area()
    shot = QImage(a.x() + a.width(), a.y() + a.height(), QImage.Format.Format_ARGB32)
    shot.fill(bg)
    sp = QPainter(shot)
    for v in colony.patch_views.values():
        sp.drawImage(v.geometry(), v.image)
    for v in colony.mat_views.values():
        sp.drawImage(v.geometry(), v.image)
    for v in [colony.spitter_view] + [s for s in colony.shots if not s.done]:
        if v:
            sp.drawPixmap(v.pos(), v.grab())
    for w in colony.widgets.values():
        sp.drawPixmap(w.pos(), w.grab())
    sp.end()
    return shot


mat_light = screen_shot(colony8, QColor(250, 249, 244))
mat_dark = screen_shot(colony8, QColor(38, 56, 74))
colony8.set_mat_layer("hidden")
check("隐藏菌毯：窗口关掉但继续长", not colony8.mat_views and colony8.mat.active)
colony8.set_mat_layer("bottom")
check("只铺在桌面层", all(v.windowFlags() & Qt.WindowType.WindowStaysOnBottomHint for v in colony8.mat_views.values()))
shutdown(colony8)

print("11. 喷孢菌")
colony9 = F.Colony(root / "spitter")
m9 = colony9.mat
a9 = colony9.mat_area()
for i in range(int(m9.n * 0.2)):
    m9.bump(i)
colony9.check_spitter()
check(f"边缘菌毯占比 < {F.SPITTER_AT:.0%} 时不长喷孢菌", colony9.spitter is None, f"{m9.occupied():.0%}")
bottom0 = m9.cols + m9.rows
for i in range(bottom0 - 40, bottom0 + m9.cols):
    while m9.d[i % m9.n] < F.MAT_MAX:
        m9.bump(i)
colony9.check_spitter()
sp = colony9.spitter
check("占比够了就长出喷孢菌，扎根在够厚的菌毯上", sp is not None and m9.d[sp["i"]] >= F.MAT_SPROUT_DEPTH, f"{m9.occupied():.0%}")

orient = []
for edge in ("bottom", "top", "left", "right"):
    i = m9.index_at(edge, m9.edge_len(edge) // 2)
    for j in range(i - 3, i + 4):
        while m9.d[j % m9.n] < F.MAT_MAX:
            m9.bump(j)
    colony9.plant_spitter(i, quiet=True)
    v = colony9.spitter_view
    bx, by = colony9.spitter_base()
    g = v.geometry()
    (ux, uy), _ = F.EDGE_POSE[edge]
    cx, cy = g.center().x(), g.center().y()
    inward = (cx - bx) * ux + (cy - by) * uy
    edge_dist = {"bottom": a9.bottom() + 1 - by, "top": by - a9.top(), "left": bx - a9.left(), "right": a9.right() + 1 - bx}[edge]
    tall = g.height() > g.width() if edge in ("top", "bottom") else g.width() > g.height()
    orient.append((edge, inward > 20 and tall and abs(edge_dist - (m9.eff(i) - 2) * F.PX) < 1))
    if edge == "bottom":
        spitter_grab = (g, v.grab())
check("四条边上都朝屏幕中心、根部埋进菌毯", all(ok for _, ok in orient), str(orient))

rng = random.Random(7)
counts = {k: 0 for k in F.OUTCOME_NAMES}
for _ in range(20000):
    counts[colony9.pick_outcome(rng)] += 1
check("落地结果概率 ≈ " + " / ".join(f"{F.OUTCOME_NAMES[k]} {p:.0%}" for k, p in F.SHOT_OUTCOMES),
      all(abs(counts[k] / 20000 - p) < 0.02 for k, p in F.SHOT_OUTCOMES), str(counts))

colony9.plant_spitter(m9.index_at("bottom", m9.cols // 2), quiet=True)
sp = colony9.spitter
mid = (a9.center().x(), a9.center().y())
n0, p0 = len(colony9.creatures), len(colony9.patches)
shot = colony9.shoot("vanish", (mid[0] - 150, mid[1]))
check("喷射时孢子弹鼠标穿透", bool(shot.windowFlags() & Qt.WindowType.WindowTransparentForInput))
wait(0.3)
flying_grab = (shot.geometry(), shot.grab())
wait(2.4)
check("消失：什么都不留下", len(colony9.creatures) == n0 and len(colony9.patches) == p0 and sp["stats"]["vanish"] == 1)
colony9.shoot("mat", mid)
wait(2.2)
check("落在桌面中间形成菌斑", len(colony9.patches) == p0 + 1 and sp["stats"]["mat"] == 1)
patch = colony9.patches[-1]
r0 = patch.r
colony9.shoot("mat", (mid[0] + 2, mid[1] + 2))
wait(2.2)
check("打中已有菌斑就让它长大，不新开一块", len(colony9.patches) == p0 + 1 and patch.r > r0)
occ0 = sum(m9.d)
colony9.shoot("mat", (a9.left() + 30, mid[1]))
wait(2.2)
check("落在边缘附近就加厚边缘菌毯", sum(m9.d) > occ0 and len(colony9.patches) == p0 + 1)
colony9.shoot("spore", (mid[0] + 200, mid[1] + 120))
wait(2.2)
new = colony9.creatures[-1]
check("变成独立小孢子（新的 spores）", len(colony9.creatures) == n0 + 1 and new.stage == 0
      and math.dist((new.x, new.y), (mid[0] + 200, mid[1] + 120)) < 2 and sp["stats"]["spore"] == 1)
while len(colony9.creatures) < F.MAX_COLONY:
    colony9.creatures.append(colony9.new_creature(10, 10))
pc = len(colony9.patches)
colony9.shoot("spore", (mid[0] - 220, mid[1] - 150))
wait(2.2)
check("菌落满了，孢子改为形成菌斑", len(colony9.creatures) == F.MAX_COLONY and len(colony9.patches) == pc + 1)
colony9.creatures = colony9.creatures[:n0 + 1]
for _ in range(400):
    colony9.grow_patches(F.PATCH_GROW)
pv = colony9.patch_views[id(patch)]
check("菌斑长到上限，窗口鼠标穿透", patch.r == patch.max and bool(pv.windowFlags() & Qt.WindowType.WindowTransparentForInput))
check("菌斑有像素、没被窗口裁掉", any(pv.image.pixel(x, y) >> 24 for x in range(pv.image.width()) for y in range(pv.image.height()))
      and not any(pv.image.pixel(x, 0) >> 24 or pv.image.pixel(0, x) >> 24 for x in range(pv.image.width())))
shots0 = sp["shots"]
sp["next_at"] = time.time() - 0.01
colony9.spitter_tick()
check("到时间自动喷，并排好下一次", sp["shots"] == shots0 + 1 and F.SPITTER_EVERY[0] - 1 <= sp["next_at"] - time.time() <= F.SPITTER_EVERY[1])
sp["shot_at"] = 0
sp["next_at"] = time.time() + 60
colony9.poke_spitter()
check("戳一下提前喷", sp["next_at"] - time.time() < 1)
wait(2.2)
colony9.save()
snap_sp = (sp["edge"], round(sp["frac"], 4), sp["shots"], dict(sp["stats"]))
snap_patches = [(p.x, p.y, round(p.r, 3), p.max, p.seed) for p in colony9.patches]
for _ in range(6):
    colony9.shoot(random.choice(["vanish", "mat"]))
wait(0.5)
screen9 = screen_shot(colony9, QColor(250, 249, 244))
screen9_dark = screen_shot(colony9, QColor(38, 56, 74))
shutdown(colony9)
raw = json.loads((root / "spitter" / "save.json").read_text())
raw["last_seen"] = time.time()
(root / "spitter" / "save.json").write_text(json.dumps(raw))
colony10 = F.Colony(root / "spitter")
sp2 = colony10.spitter
check("读档后喷孢菌和菌斑都在", sp2 and (sp2["edge"], round(sp2["frac"], 4), sp2["shots"], sp2["stats"]) == snap_sp
      and [(p.x, p.y, round(p.r, 3), p.max, p.seed) for p in colony10.patches] == snap_patches)
shutdown(colony10)

print("12. 吞噬文件")
dz = root / "devour"
dz.mkdir()
colony11 = F.Colony(root / "save12")
check("默认开启吞噬", colony11.devour)
c11 = colony11.creatures[0]
w11 = colony11.widgets[c11.id]
note = dz / "eat me.txt"
note.write_text("x" * 2048)
colony11.feed(w11, [note])
check("txt 被吃掉（文件删除）并给营养", not note.exists() and round(c11.nutrition) == 13, f"{c11.nutrition:.1f}")
check("eaten.log 记下完整路径和大小", f"2048\t{note}" in (root / "save12" / "eaten.log").read_text())
pic = dz / "cat.png"
pic.write_bytes(b"x" * 100)
colony11.feed(w11, [pic])
check("不能吃的类型原样保留", pic.exists())
folder = dz / "notes"
(folder / "deep" / "deeper").mkdir(parents=True)
(folder / "img").mkdir()
for rel in ("a.md", "b.txt", "deep/c.md", "deep/deeper/d.txt"):
    (folder / rel).write_text("hi")
(folder / "img" / "x.png").write_bytes(b"p")
(folder / ".hidden.md").write_text("h")
n = c11.nutrition
colony11.feed(w11, [folder])
check("文件夹：txt/md 被吃掉，图片和隐藏文件留下",
      not any((folder / rel).exists() for rel in ("a.md", "b.txt", "deep/c.md", "deep/deeper/d.txt"))
      and (folder / "img" / "x.png").exists() and (folder / ".hidden.md").exists() and c11.nutrition - n == 14)
check("吃空的子目录清掉，还有东西的留下", not (folder / "deep").exists() and (folder / "img").exists() and folder.exists())
only = dz / "only_notes"
(only / "sub").mkdir(parents=True)
(only / "x.md").write_text("y")
(only / "sub" / "y.txt").write_text("y")
colony11.feed(w11, [only])
check("只有文字的文件夹整个被吃掉", not only.exists())
dropped = dz / "dropped.md"
dropped.write_text("z" * 300)
_, dropev = drag(w11, [dropped], Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)
check("拖放喂食也吃掉文件，拖放动作仍然只报 Copy", not dropped.exists() and dropev.dropAction() == Qt.DropAction.CopyAction)
safe = dz / "safe"
safe.mkdir()
keep = safe / "keep.txt"
keep.write_text("k")
colony11.protected_dirs.append(safe.resolve())
n = c11.nutrition
colony11.feed(w11, [keep])
check("保护区（程序目录、存档目录）里的不吃", keep.exists() and c11.nutrition == n and any("不能吃" in f["text"] for f in w11.floaters))
check("程序目录默认受保护", colony11.is_protected(F.PROJECT_DIR / "fungi.py") and colony11.is_protected(root / "save12" / "save.json"))
locked = dz / "locked"
locked.mkdir()
stuck = locked / "stuck.txt"
stuck.write_text("s")
os.chmod(locked, 0o555)
n = c11.nutrition
colony11.feed(w11, [stuck])
os.chmod(locked, 0o755)
check("删不掉就咬不动、不给营养", stuck.exists() and c11.nutrition == n and any("咬不动" in f["text"] for f in w11.floaters))
colony11.devour = False
again = dz / "again.txt"
again.write_text("a" * 500)
n = c11.nutrition
colony11.feed(w11, [again])
check("关掉吞噬：给营养但不删文件", again.exists() and c11.nutrition > n)
shutdown(colony11)

print("13. 饥饿")
colony12 = F.Colony(root / "save13")
c = colony12.creatures[0]
w = colony12.widgets[c.id]
c.satiety, c.nutrition = 100, 120
colony12.metabolize(c, 3600)
check("吃饱时正常长（1 小时 +30）", abs(c.nutrition - 150) < 1e-6 and abs(c.satiety - (100 - 100 / F.HUNGER_HOURS)) < 1e-6)
c.satiety = 20
n = c.nutrition
colony12.metabolize(c, 3600)
check("饿了生长减半", abs(c.nutrition - n - 15) < 1e-6 and c.mood == "hungry")
c.satiety = 100
colony12.metabolize(c, F.HUNGER_HOURS * 3600)
check(f"不喂食 {F.HUNGER_HOURS} 小时从吃饱到饿扁", c.satiety == 0 and c.mood == "starving")
c.nutrition = F.STAGES[3][1] + 2
colony12.metabolize(c, 3600)
w.tick()
check("饿扁后掉营养、缩回上一阶段", c.stage == 2 and w.shown_stage == 2, c.stage_name)
now = time.time()
w.blinks = [now + 100]
w.anim = None
starving_img = w.current_pixmap(now).toImage()
check("饿扁了用专门的帧并变灰",
      starving_img in [F.art_pixmap(2, 3, False, False, False, b, 0, True, "starving").toImage() for b in (False, True)]
      and F.art_pixmap(2, 3, False, False, False, False, 0, True, "starving").toImage()
      != F.art_pixmap(2, 3, False, False, False, False, 0, True, "full").toImage())
w.idle_at = 0
w.floaters = []
w.idle(time.time())
check("饿扁了不蹦跶，只会嘟囔或眨眼", w.anim is None)
c.satiety, c.nutrition = 20, 60
w.tick()
now = time.time()
w.blinks, w.anim, w.idle_at = [now + 100], None, now + 100      # 排除眨眼和小动作的干扰
hungry_img = w.current_pixmap(now).toImage()
check("饿了用专门的帧（不变灰）",
      hungry_img in [F.art_pixmap(2, 3, False, False, False, b, 0, False, "hungry").toImage() for b in (False, True)]
      and F.art_pixmap(2, 3, False, False, False, False, 0, False, "hungry").toImage() != F.art_pixmap(2).toImage())
w.floaters = []
w.do_idle("grumble")
check("饿了会嘟囔", w.floaters and w.floaters[-1]["text"] in ("饿…", "咕…", "……", "想吃 .txt"))
c.satiety, c.nutrition = 0, 3
colony12.metabolize(c, 3600)
w.tick()
check("营养掉光变成休眠孢子（不会死）", c.mood == "dormant" and c.stage == 0)
w.idle_at, w.floaters, w.anim = 0, [], None
w.last_touch = 0
w.idle(time.time())
check("休眠时一动不动、不睡觉冒 z", w.anim is None and not w.asleep and not w.floaters)
dormant_pm = F.art_pixmap(0, 3, mood="dormant")
check("休眠孢子用专门的孢囊帧", F.load_pxl("dormant") and w.current_pixmap(time.time()).toImage() == dormant_pm.toImage()
      and dormant_pm.toImage() != F.art_pixmap(0, 3, wither=True).toImage())
check("进入休眠时窗口按孢囊尺寸对齐", w.sprite_rect.size() == dormant_pm.size())
colony12.devour = False
colony12.feed(w, [txt if txt.exists() else md])
check("喂一次就活过来", c.mood == "full" and any("活过来了" in f["text"] for f in w.floaters), f"饱腹 {c.satiety:.0f}")
w.tick()
check("醒来后窗口换回孢子尺寸", w.sprite_rect.size() == F.art_pixmap(c.stage, F.spore_size(c.nutrition)).size())
m12 = colony12.mat
for i in range(60):
    for _ in range(4):
        m12.bump(i)
c.satiety, c.nutrition = 0, 0
before = sum(m12.d)
for _ in range(300):
    colony12.mat_step()
check("全体饿扁时边缘菌毯慢慢退", sum(m12.d) < before, f"{before} → {sum(m12.d)}")
colony12.plant_spitter(10, quiet=True)
shots = colony12.spitter["shots"]
colony12.spitter["next_at"] = time.time() - 1
colony12.spitter_tick()
check("全体饿扁时喷孢菌暂停", colony12.spitter["shots"] == shots and colony12.spitter["next_at"] > time.time())
c.satiety, c.nutrition = 50, 100
colony12.save()
shutdown(colony12)
raw = json.loads((root / "save13" / "save.json").read_text())
raw["last_seen"] -= 40 * 3600
(root / "save13" / "save.json").write_text(json.dumps(raw))
colony13 = F.Colony(root / "save13")
c = colony13.creatures[0]
check("关掉 40 小时：先长后饿，掉的营养有上限", c.satiety == 0 and abs(c.nutrition - (100 + F.OFFLINE_CAP - F.OFFLINE_STARVE_CAP)) < 0.5,
      f"{c.nutrition:.1f}")
check("回来时提示饿瘦了", any("饿瘦了" in f["text"] for f in colony13.widgets[c.id].floaters))
shutdown(colony13)

print("14. Windows 适配（菌毯）")
colony14 = F.Colony(root / "save14")
m14 = colony14.mat
for i in range(m14.n):
    if i % 3 == 0:
        for _ in range(5):
            m14.bump(i)
colony14.check_spitter(quiet=True)
if not colony14.spitter:
    colony14.plant_spitter(m14.index_at("bottom", 50), quiet=True)
occupied = m14.occupied()
old_area = colony14.mat_area()
new_area = QRect(0, 0, old_area.width() - 120, old_area.height() - 48)    # 比如任务栏变宽、改了缩放
F.Colony.mat_area = lambda self: new_area
colony14.on_screen_changed()
check("桌面可用区域变了：菌毯按比例重铺", (m14 := colony14.mat).cols == new_area.width() // F.PX and abs(m14.occupied() - occupied) < 0.05)
g = colony14.mat_views["right"].geometry()
check("边缘窗口贴着新的可用区域", g.right() == new_area.width() - 1 and colony14.mat_views["bottom"].geometry().bottom() == new_area.height() - 1)
sp14 = colony14.spitter
check("喷孢菌跟着挪到对应位置", m14.edge_of(sp14["i"]) == (sp14["edge"], min(m14.edge_len(sp14["edge"]) - 1, int(sp14["frac"] * m14.edge_len(sp14["edge"])))))
F.Colony.mat_area = lambda self: old_area
colony14.on_screen_changed()
colony14.set_fullscreen_hidden(True)
views = list(colony14.mat_views.values()) + list(colony14.widgets.values()) + [colony14.spitter_view]
check("前台全屏：菌毯、喷孢菌、宠物都藏起来", not any(v.isVisible() for v in views))
colony14.shoot("spore", (300, 300))
wait(2.2)
check("藏着的时候长出来的东西也不冒出来", not any(w.isVisible() for w in colony14.widgets.values()))
F.Colony.mat_area = lambda self: new_area
colony14.on_screen_changed()
check("藏着的时候屏幕变了，先不重铺", colony14.views_stale)
colony14.set_fullscreen_hidden(False)
F.Colony.mat_area = lambda self: old_area
check("退出全屏都出来，并按新区域重铺", all(w.isVisible() for w in colony14.widgets.values())
      and colony14.spitter_view.isVisible() and not colony14.views_stale
      and colony14.mat_views["right"].geometry().right() == new_area.width() - 1)
real_fs = F.foreground_fullscreen
F.foreground_fullscreen = lambda: True
colony14.check_fullscreen()
F.foreground_fullscreen = real_fs
check("全屏检测接到隐藏", colony14.hidden_for_fullscreen)
check("非 Windows 上不检测全屏", F.foreground_fullscreen() is False and not colony14.fullscreen_timer.isActive())
shutdown(colony14)

print("15. 聊天（DeepSeek）")
import http.server
import socket
import stat
import threading as _threading

seen = []


class FakeDeepSeek(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        key = self.headers.get("Authorization", "").split()[-1]
        if key in ("bad", "poor"):
            self.send_response({"bad": 401, "poor": 402}[key])
            self.end_headers()
            return
        if key == "slow":
            time.sleep(1.5)
        reply = {"choices": [{"message": {"role": "assistant", "content": "今天的 notes.md 好好吃！我还想再来一份。明天见。"},
                              "finish_reason": "stop"}]}
        data = json.dumps(reply, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:                      # 客户端已超时放弃
            pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeDeepSeek)
_threading.Thread(target=server.serve_forever, daemon=True).start()
fake_url = f"http://127.0.0.1:{server.server_address[1]}"

check("截成一句话", F.one_sentence("你好呀！今天吃什么？") == "你好呀！" and F.one_sentence("I am 3.5 days old. Hi.") == "I am 3.5 days old."
      and F.one_sentence("嗯" * 80).endswith("…") and len(F.one_sentence("嗯" * 80)) == F.CHAT_MAX_CHARS and F.one_sentence("") == "……")

os.environ.pop("DEEPSEEK_API_KEY", None)
colony15 = F.Colony(root / "save15")
c15 = colony15.creatures[0]
w15 = colony15.widgets[c15.id]
check("没填 Key 时聊天未就绪", not colony15.chat_ready())
colony15.save_chat_cfg({"api_key": "good", "base_url": fake_url, "model": "deepseek-flash", "thinking": False})
check("Key 保存为仅本人可读（600）", stat.S_IMODE(os.stat(colony15.chat_path).st_mode) == 0o600 and colony15.chat_ready())
c15.log = [[int(time.time()), "私密日记.md", 10]]
colony15.send_chat(w15, "  你今天 吃了什么？ ")
w15.idle(time.time())
check("等回复时头上冒 …", w15.thinking and any(f["text"] == "…" for f in w15.floaters))
wait(1.5)
req = seen[-1]
check("请求 DeepSeek /chat/completions，带 Bearer Key 和模型名", req["path"] == "/chat/completions" and req["auth"] == "Bearer good"
      and req["body"]["model"] == "deepseek-flash" and req["body"]["messages"][-1] == {"role": "user", "content": "你今天 吃了什么？"})
sys_prompt = req["body"]["messages"][0]["content"]
check("人设带宠物状态、要求一句话，不含吃过的文件名", req["body"]["messages"][0]["role"] == "system" and c15.name in sys_prompt
      and "一句话" in sys_prompt and "私密日记" not in sys_prompt)
bubble = colony15.bubbles.get(c15.id)
check("回复只留一句话，显示在头顶气泡里", bubble and bubble.text == "今天的 notes.md 好好吃！" and bubble.isVisible() and not w15.thinking)
check("气泡在宠物正上方", abs(bubble.geometry().center().x() - (w15.x() + w15.sprite_rect.center().x())) <= 2
      and bubble.geometry().bottom() <= w15.y() + w15.sprite_rect.y() + 2)
bubble_grab = (bubble.grab(), w15.grab())
colony15.send_chat(w15, "还饿吗")
wait(1.2)
msgs = seen[-1]["body"]["messages"]
check("记得上一轮对话", [m["role"] for m in msgs] == ["system", "user", "assistant", "user"] and msgs[2]["content"] == "今天的 notes.md 好好吃！")
check("非思考模式：DeepSeek 关掉思考、限制长度", seen[-1]["body"]["max_tokens"] == 120 and "thinking" not in seen[-1]["body"])
colony15.chat_cfg["base_url"] = "https://api.deepseek.com"
body_probe = {}
real_urlopen = F.urllib.request.urlopen


def probe(req, timeout=None):
    body_probe.update(json.loads(req.data))
    raise F.urllib.error.URLError("probe")


F.urllib.request.urlopen = probe
try:
    F.chat_request(colony15.chat_cfg, [{"role": "user", "content": "hi"}])
except F.ChatError:
    pass
F.urllib.request.urlopen = real_urlopen
check("官方地址时传 thinking disabled（DeepSeek 默认开思考）", body_probe.get("thinking") == {"type": "disabled"})
colony15.chat_cfg["base_url"] = fake_url
for key, expect in (("bad", "API Key 不对"), ("poor", "余额不足")):
    colony15.chat_cfg["api_key"] = key
    colony15.send_chat(w15, "喂")
    wait(1.0)
    b = colony15.bubbles[c15.id]
    check(f"HTTP 错误显示原因：{expect}", expect in b.text and b.error)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
colony15.chat_cfg.update(api_key="good", base_url=f"http://127.0.0.1:{dead_port}")
colony15.send_chat(w15, "在吗")
wait(1.0)
check("连不上时气泡说连不上", "连不上" in colony15.bubbles[c15.id].text)
colony15.chat_cfg.update(api_key="slow", base_url=fake_url, timeout=0.5)
colony15.send_chat(w15, "慢慢说")
wait(1.3)
check("超时也不卡界面，显示连不上", "连不上" in colony15.bubbles[c15.id].text and not w15.thinking)
colony15.chat_cfg.pop("timeout")
colony15.chat_cfg["api_key"] = "good"
wait(1.0)
n_req = len(seen)
c15.satiety, c15.nutrition = 0, 0
colony15.send_chat(w15, "醒醒")
wait(0.8)
check("休眠孢子不发请求", len(seen) == n_req and "休眠" in colony15.bubbles[c15.id].text)
c15.satiety, c15.nutrition = 60, 10
long_text = "这是一句很长很长的话" * 4
lb = colony15.show_bubble(w15, long_text)
check("长文本在气泡里换行（宽度有上限）", lb.width() <= F.SpeechBubble.MAX_W + 2 * F.SpeechBubble.PAD + 2 and lb.height() > 60)
w15.c.x += 120
w15.place()
wait(0.2)
check("气泡跟着宠物走", abs(lb.geometry().center().x() - (w15.x() + w15.sprite_rect.center().x())) <= 2)
lb.t0 -= lb.dur + 1
wait(0.2)
check("到时间气泡消失", lb.done and not lb.isVisible())
colony15.set_fullscreen_hidden(True)
check("全屏隐藏时气泡不冒出来", not colony15.show_bubble(w15, "嘘").isVisible())
colony15.set_fullscreen_hidden(False)
os.environ["DEEPSEEK_API_KEY"] = "from-env"
colony15.chat_path.unlink()
check("没保存 Key 时读环境变量 DEEPSEEK_API_KEY", colony15.load_chat_cfg().get("api_key") == "from-env")
os.environ.pop("DEEPSEEK_API_KEY")
server.shutdown()
shutdown(colony15)

# ── 预览图 ──
shots = [("spores · 3×3", spore_grab), ("吃东西", eat_grab), ("adult · 悬停", adult_grab), ("拖入中", drag_grab)]
W = sum(max(160, s.width()) + 30 for _, s in shots) + 30
H = max(s.height() for _, s in shots) * 2 + 80
img = QImage(W, H, QImage.Format.Format_ARGB32)
img.fill(QColor(250, 249, 244))
p = QPainter(img)
p.fillRect(0, H // 2, W, H // 2, QColor(38, 56, 74))
for y0 in (10, H // 2 + 10):
    x = 30
    for label, s in shots:
        p.drawPixmap(x, y0, s)
        p.setPen(QColor(120, 120, 120))
        p.drawText(x, y0 + s.height() + 22, label)
        x += max(160, s.width()) + 30
p.end()
out_dir.mkdir(parents=True, exist_ok=True)
img.save(str(out_dir / "widgets.png"))
iimg.save(str(out_dir / "idle.png"))
mat_light.save(str(out_dir / "mat_light.png"))
bimg = QImage(max(bubble_grab[0].width(), bubble_grab[1].width()) + 40, bubble_grab[0].height() + bubble_grab[1].height() + 20,
              QImage.Format.Format_ARGB32)
bimg.fill(QColor(38, 56, 74))
bp = QPainter(bimg)
bp.drawPixmap((bimg.width() - bubble_grab[0].width()) // 2, 6, bubble_grab[0])
bp.drawPixmap((bimg.width() - bubble_grab[1].width()) // 2, bubble_grab[0].height() - F.TEXT_BAND + 6, bubble_grab[1])
bp.end()
bimg.save(str(out_dir / "chat_bubble.png"))
mat_dark.save(str(out_dir / "mat_dark.png"))
screen9.save(str(out_dir / "spitter_light.png"))
screen9_dark.save(str(out_dir / "spitter_dark.png"))
ori = QImage(4 * 140, 140, QImage.Format.Format_ARGB32)
ori.fill(QColor(250, 249, 244))
op = QPainter(ori)
for n, (edge, rot) in enumerate((("bottom", 0), ("top", 180), ("left", 90), ("right", -90))):
    img_o = F.spitter_image("idle", rot)
    op.drawImage(n * 140 + (140 - img_o.width()) // 2, (140 - img_o.height()) // 2, img_o)
op.end()
ori.save(str(out_dir / "spitter_orient.png"))
print(f"\n预览图: {out_dir / 'widgets.png'}")
shutil.rmtree(root, ignore_errors=True)
print("全部通过" if not failures else f"{failures} 项失败")
sys.exit(1 if failures else 0)
