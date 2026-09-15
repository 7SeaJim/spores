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

from PyQt6.QtCore import QEventLoop, QMimeData, QPointF, Qt, QTimer, QUrl
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
    for v in colony.mat_views.values():
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

print("5. 文件完好")
check("所有食物文件内容/修改时间/大小都没变", all(fingerprint(p) == before_prints[p] for p in all_files))
check("食物文件一个不少", sorted(p for p in food.rglob("*") if p.is_file()) == sorted(all_files))

print("6. 存档 / 读档")
colony.save()
snap = {k.id: (k.name, k.gen, k.born, k.feeds, k.nutrition, k.released) for k in colony.creatures}
shutdown(colony)
colony2 = F.Colony(data)
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
check("厚处冒出小蘑菇", any(mm.has_sprout(i) for i in range(mm.n)))
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
    for v in colony.mat_views.values():
        sp.drawImage(v.geometry(), v.image)
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
mat_dark.save(str(out_dir / "mat_dark.png"))
print(f"\n预览图: {out_dir / 'widgets.png'}")
shutil.rmtree(root, ignore_errors=True)
print("全部通过" if not failures else f"{failures} 项失败")
sys.exit(1 if failures else 0)
