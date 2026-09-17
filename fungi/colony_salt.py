"""菌落 · 撒盐：盐的化、撒、整圈腌、扫掉和宠物的反应。"""
from __future__ import annotations

import math
import random
import time

from PyQt6 import sip

from .config import PX, SALT_FULL, SALT_GLOOM, SALT_HOURS, SALT_REACH
from .salt import SaltBar, SaltOverlay


class SaltMixin:
    """撒盐：让菌毯停在现在的样子"""

    def melt_salt(self, seconds: float):
        """盐自己慢慢化（SALT_HOURS 小时化完）"""
        if not any(any(f.mat.salt) for f in self.fields.values()):
            self.salt_acc = 0.0
            return
        self.salt_acc += seconds / 3600 * SALT_FULL / SALT_HOURS
        units = int(self.salt_acc)
        if units:
            self.salt_acc -= units
            for f in self.fields.values():
                f.mat.decay_salt(units)

    def salted_share(self) -> float:
        cells = sum(f.mat.n for f in self.fields.values())
        return sum(f.mat.salted() for f in self.fields.values()) / max(1, cells)

    def salt_until(self) -> float:
        return time.time() + SALT_HOURS * 3600 / self.passive_mult

    def sprinkle(self, x: float, y: float) -> int:
        """在屏幕坐标 (x, y) 撒一把盐：贴边就腌边缘菌毯，点到菌斑、喷孢菌根部就腌它们。返回腌到了几处。"""
        f = self.field_at(x, y)
        a, m = f.rect, f.mat
        cx, cy = (x - a.x()) / PX, (y - a.y()) / PX
        hits = 0
        if min(cx, cy, m.cols - 1 - cx, m.rows - 1 - cy) <= SALT_REACH:
            hits += m.salt_at(m.nearest(cx, cy)) > 0
            self.render_mat()
        for p in self.patches:
            if math.hypot(p.x - x, p.y - y) < (p.r * 1.3 + 3) * PX and p.salted <= time.time():
                p.salted = self.salt_until()
                hits += 1
                if id(p) in self.patch_views:
                    self.patch_views[id(p)].render()
        if self.spitter and self.spitter.get("salted", 0) <= time.time() and math.dist(self.spitter_base(), (x, y)) < 14 * PX:
            self.spitter["salted"] = self.salt_until()
            hits += 1
            if self.spitter_view:
                self.spitter_view.update()
        if hits:
            self.salt_session.add(f.key)
            self.sound.play("salt")
        return hits

    def start_salt(self):
        """进入撒盐模式：每块屏盖一层，按住拖动撒盐，右键 / Esc 退出"""
        if self.salt_overlays:
            return
        self.salt_session = set()
        for f in self.fields.values():
            o = SaltOverlay(self, f)
            self.salt_overlays.append(o)
            o.show()
        self.salt_bar = SaltBar(self)
        a = self.fields[self.primary_key].rect
        self.salt_bar.move(a.center().x() - self.salt_bar.width() // 2, a.top() + (SALT_REACH + 4) * PX)
        self.salt_bar.show()
        self.salt_bar.raise_()
        self.salt_overlays[0].activateWindow()

    def end_salt(self):
        overlays, self.salt_overlays = self.salt_overlays, []
        bar, self.salt_bar = self.salt_bar, None
        for o in overlays:
            o.close()
        if bar and not sip.isdeleted(bar):
            bar.close()
        if self.salt_session:
            self.salt_reaction(self.salt_session)
        self.salt_session = set()

    def salt_ring(self):
        """整圈撒盐：把现在的样子整个腌住"""
        for f in self.fields.values():
            f.mat.salt_all()
        until = self.salt_until()
        for p in self.patches:
            p.salted = until
            if id(p) in self.patch_views:
                self.patch_views[id(p)].render()
        if self.spitter:
            self.spitter["salted"] = until
        self.render_mat()
        self.sound.play("salt")
        if self.salt_overlays:                             # 在撒盐模式里点的：退出时一起嘟囔
            self.salt_session |= set(self.fields)
        else:
            self.salt_reaction(set(self.fields))

    def sweep_salt(self):
        for f in self.fields.values():
            f.mat.clear_salt()
        for p in self.patches:
            p.salted = 0.0
            if id(p) in self.patch_views:
                self.patch_views[id(p)].render()
        if self.spitter:
            self.spitter["salted"] = 0.0
        self.render_mat()
        self.log_event("屏幕边上的盐被扫掉了")
        self.save()

    def salt_reaction(self, keys: set[str]):
        """被撒了盐的那几块屏上的菌：不高兴，嘟囔一句"""
        self.log_event("有人往菌毯上撒了盐，那一段长不动了")
        for c in self.creatures:
            if self.field_of(c).key not in keys:
                continue
            c.happiness = max(0.0, c.happiness - SALT_GLOOM)
            w = self.widgets.get(c.id)
            if w and c.stage and not w.asleep and c.mood != "dormant":
                w.say(random.choice(["咸……", "呸，咸的", "边上被腌住了", "……齁"]), delay=random.uniform(0.2, 1.2))
        self.save()
