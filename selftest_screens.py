#!/usr/bin/env python3
"""多显示器自测：用 offscreen 平台虚拟两块屏幕。python3 selftest_screens.py"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="fungi-screens-"))
conf = Path(f".fungi-screens-{os.getpid()}.json")      # 相对路径：Windows 盘符里的冒号会被 Qt 当成参数分隔
conf.write_text(json.dumps({"screens": [
    {"name": "DISPLAY-A", "x": 0, "y": 0, "width": 1280, "height": 720, "logicalDpi": 96},
    {"name": "DISPLAY-B", "x": 1280, "y": 0, "width": 1024, "height": 768, "logicalDpi": 96}]}))
os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={conf}"

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

import fungi as F

F.foreground_fullscreen = lambda: False
F.Sound.spawn = lambda self, path: None
app = QApplication([])
failures = 0


def check(name, cond, extra=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"   ({extra})" if extra else ""))
    if not cond and os.environ.get("GITHUB_ACTIONS"):
        print(f"::error title=多屏自测失败::{name} {str(extra)[:300]}")
    failures += not cond


def shutdown(colony):
    for t in (colony.tick_timer, colony.autosave, colony.spitter_timer, colony.fullscreen_timer, colony.screen_debounce):
        t.stop()
    for w in colony.widgets.values():
        w.timer.stop()
        w.close()
    for s in colony.shots:
        s.finish()
    for v in colony.all_mat_views() + list(colony.patch_views.values()) + [colony.spitter_view]:
        if v:
            v.close()
    if colony.tray:
        colony.tray.hide()


print("多显示器：每块屏幕一圈菌毯")
screens = QGuiApplication.screens()
check("虚拟出两块屏幕", len(screens) == 2 and [s.name() for s in screens] == ["DISPLAY-A", "DISPLAY-B"],
      [s.name() for s in screens])
col = F.Colony(root / "save")
A, B = QGuiApplication.screens()[0].availableGeometry(), QGuiApplication.screens()[1].availableGeometry()
check("两块屏各有一圈菌毯，主屏在前", list(col.fields) == ["DISPLAY-A", "DISPLAY-B"] and col.mat is col.fields["DISPLAY-A"].mat)
fb = col.fields["DISPLAY-B"]
check("副屏菌毯尺寸按副屏算", (fb.mat.cols, fb.mat.rows) == (B.width() // F.PX, B.height() // F.PX))
check("每块屏四条边都有窗口", len(col.all_mat_views()) == 8)
right_b = fb.views["right"].geometry()
check("副屏右边的窗口贴着副屏右边", right_b.right() == B.right() and right_b.top() == B.top(), right_b)
c = col.creatures[0]
c.nutrition, c.satiety = F.STAGES[F.ADULT][1] + 1, 100
col.after_growth(col.widgets[c.id], 0, quiet=True)
for k, d in enumerate(col.creatures):                  # 长大时放出的孩子也一起挪到副屏
    d.x, d.y = B.x() + 300 + k * 120, B.bottom() - 20
    col.widgets[d.id].place()
check("宠物在副屏：算到副屏那一圈", col.field_of(c) is fb)
for _ in range(400):
    col.mat_step()
check("待在副屏的菌只让副屏的菌毯长", fb.mat.active and not col.mat.active, (len(fb.mat.active), len(col.mat.active)))
i = col.mat_index(c)
edge, pos = fb.mat.edge_of(i)
check("落孢子的位置在离它最近的副屏底边", edge == "bottom", (edge, pos))
col.render_mat()
col.save()
occ_b = fb.mat.occupied()
shutdown(col)

col = F.Colony(root / "save")
check("存档读回：副屏菌毯还在副屏", abs(col.fields["DISPLAY-B"].mat.occupied() - occ_b) < 1e-9 and not col.mat.active)
fb = col.fields["DISPLAY-B"]
real_areas = F.Colony.screen_areas
F.Colony.screen_areas = lambda self: [("DISPLAY-A", A)]
col.on_screen_changed()
check("拔掉副屏：只剩主屏，副屏的菌毯先存起来", list(col.fields) == ["DISPLAY-A"] and "DISPLAY-B" in col.stash
      and len(col.all_mat_views()) == 4)
col.save()
saved = json.loads((root / "save" / "save.json").read_text("utf-8"))
check("拔着的时候存档也不丢副屏", "DISPLAY-B" in saved["mats"])
F.Colony.screen_areas = real_areas
col.on_screen_changed()
check("接回副屏：菌毯接着在", abs(col.fields["DISPLAY-B"].mat.occupied() - occ_b) < 1e-9 and not col.stash)
F.Colony.screen_areas = lambda self: [("DISPLAY-A", A), ("DISPLAY-B", QRect(B.x(), B.y(), B.width(), B.height() - 40))]
col.on_screen_changed()
check("副屏任务栏变高：只重铺副屏", col.fields["DISPLAY-B"].mat.rows == (B.height() - 40) // F.PX)
F.Colony.screen_areas = real_areas
col.on_screen_changed()

fb = col.fields["DISPLAY-B"]
for k in range(fb.mat.n):
    fb.mat.bump(k)
col.plant_spitter(fb.mat.index_at("bottom", 60), quiet=True, screen="DISPLAY-B")
bx, by = col.spitter_base()
check("喷孢菌长在副屏上", col.spitter["screen"] == "DISPLAY-B" and B.adjusted(0, 0, 1, 1).contains(int(bx), int(by)), (bx, by))
tx, ty = col.shot_target((bx, by))
check("副屏的喷孢菌往副屏里喷", B.contains(int(tx), int(ty)), (tx, ty))
before = len(col.patches)
col.mat_at(B.x() + 3, B.y() + 400)
check("孢子落在副屏边上：加厚副屏的菌毯", len(col.patches) == before)
col.save()
shutdown(col)
col = F.Colony(root / "save")
check("喷孢菌读档后还在副屏", col.spitter and col.spitter["screen"] == "DISPLAY-B"
      and B.adjusted(0, 0, 1, 1).contains(*map(int, col.spitter_base())))
F.Colony.screen_areas = lambda self: [("DISPLAY-A", A)]
col.on_screen_changed()
check("喷孢菌那块屏拔掉：先挪到主屏", col.spitter["screen"] == "DISPLAY-A" and A.adjusted(0, 0, 1, 1).contains(*map(int, col.spitter_base())))
F.Colony.screen_areas = real_areas
shutdown(col)

old = root / "old"
old.mkdir()
m = F.Mycelium(A.width() // F.PX, A.height() // F.PX)
for k in range(40):
    m.bump(k)
(old / "save.json").write_text(json.dumps({"version": 1, "mat": m.to_json(), "creatures": []}), "utf-8")
col = F.Colony(old)
check("老存档（只有一圈）：算主屏的", col.mat.active and not col.fields["DISPLAY-B"].mat.active)
shutdown(col)

shutil.rmtree(root, ignore_errors=True)
conf.unlink(missing_ok=True)
print("全部通过" if not failures else f"{failures} 项失败")
sys.exit(1 if failures else 0)
