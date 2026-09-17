"""菌落 · 喷孢菌：长出、喷射、落地。"""
from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QMenu

from .config import HUNGRY_AT, MAT_SPROUT_DEPTH, MAX_COLONY, OUTCOME_NAMES, PX, SHOT_OUTCOMES, SPITTER_AT, SPITTER_EVERY
from .ui import fmt_age, MENU_QSS
from .spitter import EDGE_POSE, spitter_image, SpitterWidget, SporeShot


class SpitterMixin:
    """喷孢菌：长出、喷射、落地"""

    def shot_gap(self) -> float:
        return random.uniform(*SPITTER_EVERY) / self.passive_mult

    def check_spitter(self, quiet: bool = False):
        if self.spitter:
            return
        for f in self.fields.values():                    # 主屏优先
            m = f.mat
            if m.occupied() < SPITTER_AT:
                continue
            cands = [i for i in m.active if m.d[i] >= MAT_SPROUT_DEPTH
                     and 12 <= m.edge_of(i)[1] < m.edge_len(m.edge_of(i)[0]) - 12]
            if cands:
                cands.sort(key=lambda i: m.eff(i) + random.random() * 2, reverse=True)
                self.plant_spitter(random.choice(cands[:20]), quiet, screen=f.key)
                return

    def plant_spitter(self, i: int, quiet: bool = False, screen: str = ""):
        screen = screen if screen in self.fields else self.primary_key
        m = self.fields[screen].mat
        edge, pos = m.edge_of(i)
        now = time.time()
        self.spitter = {"i": i % m.n, "edge": edge, "frac": (pos + 0.5) / m.edge_len(edge), "born": now - (99 if quiet else 0),
                        "screen": screen,
                        "shots": 0, "stats": {k: 0 for k in OUTCOME_NAMES}, "next_at": now + 3 + self.shot_gap()}
        self.log_event("屏幕边缘的菌毯上长出了一只喷孢菌")
        if self.mat_layer != "hidden":
            if self.spitter_view:
                self.spitter_view.close()
            self.spitter_view = SpitterWidget(self, self.mat_layer)
            if not self.hidden_for_fullscreen:
                self.spitter_view.show()
            for w in self.widgets.values():
                w.raise_()
        self.save()

    def spitter_base(self) -> tuple[float, float]:
        """喷孢菌根部中心的屏幕坐标：埋进菌毯表面下 2 格"""
        f = self.spitter_field()
        a, m, i = f.rect, f.mat, self.spitter["i"]
        edge, pos = m.edge_of(i)
        w, h = m.cols * PX, m.rows * PX
        u, v = (pos + 0.5) * PX, max(0.0, m.eff(i) - 2) * PX
        return {"top": (a.x() + u, a.y() + v), "bottom": (a.x() + w - u, a.y() + h - v),
                "right": (a.x() + w - v, a.y() + u), "left": (a.x() + v, a.y() + h - u)}[edge]

    def pick_outcome(self, rng=random) -> str:
        r, acc = rng.random(), 0.0
        for name, p in SHOT_OUTCOMES:
            acc += p
            if r < acc:
                return name
        return SHOT_OUTCOMES[-1][0]

    def shot_target(self, start: tuple[float, float]) -> tuple[float, float]:
        a = self.spitter_field().rect if self.spitter else self.mat_area()
        pt = start
        for _ in range(12):
            pt = (random.uniform(a.left() + 40, a.right() - 40), random.uniform(a.top() + 40, a.bottom() - 40))
            if math.dist(pt, start) > 240:
                break
        return pt

    def spitter_tick(self):
        self.shots = [s for s in self.shots if not s.done]
        if self.spitter and time.time() >= self.spitter["next_at"]:
            if self.starving() or self.spitter.get("salted", 0) > time.time():   # 全体饿扁 / 被腌着：不喷，往后推
                self.spitter["next_at"] = time.time() + self.shot_gap()
            else:
                self.shoot()

    def shoot(self, outcome: str | None = None, target: tuple[float, float] | None = None) -> SporeShot:
        sp, now = self.spitter, time.time()
        (ux, uy), _ = EDGE_POSE[self.spitter_field().mat.edge_of(sp["i"])[0]]
        bx, by = self.spitter_base()
        reach = spitter_image("idle").height() - 2 * PX
        start = (bx + ux * reach, by + uy * reach)
        shot = SporeShot(self, start, target or self.shot_target(start), outcome or self.pick_outcome())
        if not self.hidden_for_fullscreen:
            shot.show()
            self.sound.play("spit")
        self.shots.append(shot)
        sp["shots"] += 1
        sp["shot_at"], sp["next_at"] = now, now + self.shot_gap()
        return shot

    def land_spore(self, outcome: str, x: float, y: float):
        if outcome == "spore" and len(self.creatures) >= MAX_COLONY:
            outcome = "mat"
        if self.spitter:
            self.spitter["stats"][outcome] += 1
        if outcome == "spore":
            c = self.new_creature(int(x), int(y))
            c.satiety = HUNGRY_AT                         # 野生孢子，一落地就有点饿
            c.parent = "spitter"
            self.creatures.append(c)
            self.moods[c.id] = c.mood
            self.log_event(f"喷孢菌喷出的孢子落地，长成了 {c.name}")
            w = self.spawn_widget(c)
            w.play("land")
            w.say("·")
        elif outcome == "mat":
            self.mat_at(x, y)
        self.save()

    def poke_spitter(self):
        sp, now = self.spitter, time.time()
        if sp and sp["next_at"] - now > 1.2 and now - sp.get("shot_at", 0) > 5:
            sp["next_at"] = now + 0.8                    # 戳一下就提前喷

    def spitter_tip(self) -> str:
        sp = self.spitter
        if not sp:
            return ""
        st = sp["stats"]
        if sp.get("salted", 0) > time.time():
            return f"喷孢菌（被盐腌着，还要 {fmt_age(sp['salted'] - time.time())}）"
        return (f"喷孢菌\n下一次 ~{max(0, int(sp['next_at'] - time.time()))}s\n"
                f"喷了 {sp['shots']} 次：" + " · ".join(f"{OUTCOME_NAMES[k]} {st[k]}" for k in OUTCOME_NAMES))

    def spitter_menu(self, pos: QPoint):
        sp = self.spitter
        if not sp:
            return
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        salted = sp.get("salted", 0) > time.time()
        m.addAction(f"喷孢菌 · 喷了 {sp['shots']} 次 · " + ("被盐腌着" if salted else
                    f"下一次 ~{max(0, int(sp['next_at'] - time.time()))}s")).setEnabled(False)
        m.addSeparator()
        m.addAction("现在喷！", lambda: sp.__setitem__("next_at", time.time() + 0.8)).setEnabled(not salted)
        m.addAction("撒盐…", self.start_salt)
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        m.exec(pos)
