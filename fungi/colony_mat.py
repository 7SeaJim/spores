"""菌落 · 菌毯：每块屏幕的菌毯、生长、菌斑、绘制。"""
from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QActionGroup, QGuiApplication
from PyQt6.QtWidgets import QMenu

from .config import (MAT_MAX, MAT_MILESTONES, MAT_OFFLINE_CAP, MAT_RATE, MAT_RECEDE, MAT_SEED, MAT_STRIP, MAT_TICK,
                     MAT_VIGOR, MAX_PATCHES, PATCH_GROW, PATCH_MAX, PX)
from .model import Creature
from .ui import MENU_QSS
from .mat import MatField, MatStrip, Mycelium, screen_key
from .spitter import Patch, PatchView, SpitterWidget


class MatMixin:
    """每块屏幕一圈菌毯：生长、菌斑、绘制和显示层级"""

    def screen_areas(self) -> list[tuple[str, QRect]]:
        """每块屏幕的可用区域，主屏在最前"""
        primary = QGuiApplication.primaryScreen()
        areas = [(screen_key(primary), self.mat_area())]
        for screen in QGuiApplication.screens():
            if screen is not primary and screen_key(screen) not in dict(areas):
                areas.append((screen_key(screen), screen.availableGeometry()))
        return areas

    def sync_fields(self) -> bool:
        """按现在接着的屏幕增删菌毯：拔掉的屏幕先存起来，接回来接着长"""
        areas = self.screen_areas()
        changed = False
        for key in [k for k in self.fields if k not in dict(areas)]:
            self.stash[key] = self.fields.pop(key).mat.to_json()
            changed = True
        for key, rect in areas:
            if key in self.fields:
                changed |= self.fields[key].fit(rect)
            else:
                self.fields[key] = MatField(key, rect, Mycelium.from_json(self.stash.pop(key, None)))
                changed = True
        self.fields = {k: self.fields[k] for k, _ in areas}
        self.primary_key = areas[0][0]
        return changed

    @property
    def mat(self) -> Mycelium | None:
        """主屏那一圈"""
        f = self.fields.get(self.primary_key)
        return f.mat if f else None

    @mat.setter
    def mat(self, m: Mycelium):
        self.fields[self.primary_key].mat = m

    @property
    def mat_views(self) -> dict[str, MatStrip]:
        f = self.fields.get(self.primary_key)
        return f.views if f else {}

    def all_mat_views(self) -> list[MatStrip]:
        return [v for f in self.fields.values() for v in f.views.values()]

    def field_at(self, x: float, y: float) -> MatField:
        """(x, y) 所在屏幕的菌毯；在屏幕缝里就找最近的"""
        return min(self.fields.values(), key=lambda f: f.distance(x, y))

    def field_of(self, c: Creature) -> MatField:
        return self.field_at(c.x, c.y - 8)

    def spitter_field(self) -> MatField:
        return self.fields.get(self.spitter.get("screen")) if self.spitter and self.spitter.get("screen") in self.fields \
            else self.fields[self.primary_key]

    def mat_occupied(self) -> float:
        """长得最满的那块屏幕，边缘一圈有菌毯的占比"""
        return max((f.mat.occupied() for f in self.fields.values()), default=0.0)

    # ── 菌毯 ──
    def mat_area(self) -> QRect:
        screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)

    def mat_index(self, c: Creature) -> int:
        """离它最近的菌毯格子（在它自己那块屏幕上）"""
        return self.field_of(c).index_near(c.x, c.y)

    def mat_step(self):
        vigor, per = 0.0, {k: 0.0 for k in self.fields}
        for c in self.creatures:
            v = MAT_VIGOR[c.stage] * c.growth_factor * (0.8 + 0.4 * c.happiness / 100)   # 开心的菌落长得快一点
            vigor += v
            f = self.field_of(c)
            per[f.key] += v                               # 每块屏幕的菌毯靠待在这块屏上的菌
            if random.random() < MAT_SEED * v and f.mat.seed(f.index_near(c.x, c.y)):
                w = self.widgets.get(c.id)
                if w and c.stage and not w.asleep:
                    w.puff(2)
        for f in self.fields.values():
            budget = MAT_RATE * per[f.key] if vigor else MAT_RECEDE
            steps = int(budget) + (random.random() < budget % 1)
            if not steps:
                continue
            if vigor:
                f.mat.grow(steps)
            else:
                f.mat.shrink(steps)                       # 全体饿扁：菌毯慢慢退
        if vigor:
            self.grow_patches(PATCH_GROW * (0.5 + min(vigor, 6) / 6))
        occupied = self.mat_occupied()
        for mark in MAT_MILESTONES:
            if occupied >= mark > self.mat_mark:
                self.log_event("菌毯沿着屏幕边上" + self.ring_words(mark))
                self.mat_mark = mark
        self.check_spitter()
        if self.spitter_view:
            self.spitter_view.place()

    def grow_mat_offline(self, elapsed: float):
        self.melt_salt(elapsed * self.passive_mult)
        steps = elapsed * self.passive_mult / MAT_TICK
        if steps < 1:
            return
        per = {k: 0.0 for k in self.fields}
        for c in self.creatures:
            f = self.field_of(c)
            per[f.key] += MAT_VIGOR[c.stage] * c.growth_factor
            if random.random() < 1 - (1 - MAT_SEED * MAT_VIGOR[c.stage] * c.growth_factor) ** steps:
                f.mat.seed(f.index_near(c.x, c.y))
        vigor = sum(per.values())
        for f in self.fields.values():
            if vigor:
                f.mat.grow(int(min(MAT_OFFLINE_CAP, steps * MAT_RATE * per[f.key])))
            else:
                f.mat.shrink(int(min(MAT_OFFLINE_CAP, steps * MAT_RECEDE)))
        if vigor:
            self.grow_patches(steps * PATCH_GROW * (0.5 + min(vigor, 6) / 6))
        self.check_spitter(quiet=True)

    def build_mat_views(self):
        old = self.all_mat_views() + list(self.patch_views.values()) + [self.spitter_view]
        for v in old:
            if v:
                v.close()
        for f in self.fields.values():
            f.views = {}
        self.patch_views, self.spitter_view = {}, None
        if self.mat_layer != "hidden":
            t = MAT_STRIP * PX
            for f in self.fields.values():
                a, w, h = f.rect, f.mat.cols * PX, f.mat.rows * PX
                rects = {"top": QRect(a.x(), a.y(), w, t), "bottom": QRect(a.x(), a.y() + h - t, w, t),
                         "left": QRect(a.x(), a.y(), t, h), "right": QRect(a.x() + w - t, a.y(), t, h)}
                f.views = {edge: MatStrip(edge, rect, self.mat_layer) for edge, rect in rects.items()}
            self.render_mat(full=True)
            for p in self.patches:
                self.add_patch_view(p)
            if self.spitter:
                self.spitter_view = SpitterWidget(self, self.mat_layer)
            for v in self.all_mat_views() + [self.spitter_view]:
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
        now = time.time()
        for p in self.patches:
            if p.r < p.max and p.salted <= now:
                p.r = min(p.max, p.r + amount)
                if id(p) in self.patch_views:
                    self.patch_views[id(p)].render()

    def mat_at(self, x: float, y: float):
        """孢子在 (x, y) 落地形成菌毯：贴边就加厚边缘菌毯，打中已有菌斑就让它长大，否则长出新菌斑"""
        f = self.field_at(x, y)
        a, m = f.rect, f.mat
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
        if hit and hit.salted > time.time():               # 腌着的菌斑：孢子落上去也长不起来
            return
        if hit:
            hit.max = min(PATCH_MAX[1] + 2, hit.max + 1)
            hit.r = min(hit.max, hit.r + 1)
            if id(hit) in self.patch_views:
                self.patch_views[id(hit)].render()
            return
        p = Patch(int(x), int(y), 1.0, random.randint(*PATCH_MAX), random.randrange(1 << 30))
        self.patches.append(p)
        self.log_event("喷孢菌的孢子在桌面中间长出了一块菌斑")
        self.add_patch_view(p)

    def render_mat(self, full: bool = False):
        for f in self.fields.values():
            m = f.mat
            if full:
                todo = {j % m.n for i in m.active for j in range(i - 12, i + 13)} | {k for k, v in enumerate(m.salt) if v}
            else:
                todo = m.dirty
            m.dirty = set()
            if not f.views:
                continue
            touched = set()
            for i in todo:
                edge = m.edge_of(i)[0]
                f.views[edge].render_column(m, i)
                touched.add(edge)
            for edge in touched:
                f.views[edge].update()

    def set_mat_layer(self, layer: str):
        self.mat_layer = layer
        self.build_mat_views()
        self.save()

    def mat_coverage(self) -> float:
        cells = sum(f.mat.n for f in self.fields.values())
        return sum(f.mat.coverage() * f.mat.n for f in self.fields.values()) / max(1, cells)

    def mat_menu(self, parent: QMenu) -> QMenu:
        """菌毯怎么显示（放在「设置」里）"""
        sub = parent.addMenu("菌毯显示")
        sub.setStyleSheet(MENU_QSS)
        group = QActionGroup(sub)
        for key, label in (("top", "铺在窗口上面"), ("bottom", "只铺在桌面上"), ("hidden", "隐藏")):
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.mat_layer == key)
            act.triggered.connect(lambda _=False, k=key: self.set_mat_layer(k))
            group.addAction(act)
        return sub
