"""菌落 · 喂食和生长：代谢、照顾、吃东西、长大和放孢子。"""
from __future__ import annotations

import math
import os
import random
import time
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, QTimer
from PyQt6.QtGui import QGuiApplication

from .config import (CARE_AMOUNT, CARE_COOLDOWN, ENERGY_DRAIN, ENERGY_REST, HAPPY_DECAY, HUNGER_HOURS, HUNGRY_AT,
                     MAT_FEED, MAX_COLONY, MAX_ITEMS_PER_DROP, OFFLINE_CAP, OFFLINE_GLOOM_CAP, OFFLINE_STARVE_CAP,
                     PASSIVE_SECONDS, PX, SATIETY_MAX, SATIETY_PER_FOOD, STARVE_GLOOM, STARVE_LOSS)
from .model import Creature, Food
from .food import devour_file, digest, residue_of
from .creature_view import CreatureWidget
from .panel import StatusPanel


class FeedMixin:
    """代谢、照顾、喂食、长大和放孢子"""

    def metabolize(self, c: Creature, seconds: float, asleep: bool = False):
        """在线的 seconds 秒（已乘速度倍率）：吃饱正常长、饿了长一半、饿扁了掉营养；醒着掉精力、睡着回精力；快乐慢慢掉"""
        hours = seconds / 3600
        if c.satiety > 0:
            c.nutrition += seconds / PASSIVE_SECONDS * c.growth_factor * self.growth_speed
            c.satiety = max(0.0, c.satiety - hours * SATIETY_MAX / HUNGER_HOURS * self.hunger_speed)
        else:
            c.nutrition = max(0.0, c.nutrition - hours * STARVE_LOSS)
        rest = asleep or c.mood == "dormant"
        c.energy = max(0.0, min(100.0, c.energy + hours * (ENERGY_REST if rest else -ENERGY_DRAIN)))
        c.happiness = max(0.0, c.happiness - hours * (HAPPY_DECAY + (STARVE_GLOOM if c.mood == "starving" else 0)))

    def offline_metabolize(self, c: Creature, seconds: float) -> float:
        """关闭期间：先吃老本长（有上限），饿扁之后掉营养（有上限）。返回营养变化。"""
        hours, rate = seconds / 3600, SATIETY_MAX / HUNGER_HOURS
        fed_h = min(hours, c.satiety / rate)
        full_h = min(fed_h, max(0.0, (c.satiety - HUNGRY_AT) / rate))
        grow = min(OFFLINE_CAP, (full_h + (fed_h - full_h) * 0.5) * 3600 / PASSIVE_SECONDS)
        starve = min(OFFLINE_STARVE_CAP, (hours - fed_h) * STARVE_LOSS)
        before = c.nutrition
        c.satiety = max(0.0, c.satiety - hours * rate)
        c.nutrition = max(0.0, c.nutrition + grow - starve)
        c.energy = min(100.0, c.energy + hours * ENERGY_REST)             # 关着程序 = 一直在睡
        c.happiness = max(0.0, c.happiness - min(OFFLINE_GLOOM_CAP, hours * HAPPY_DECAY))
        return c.nutrition - before

    def care(self, c: Creature, kind: str) -> str:
        """浇水（+精力）/ 晒太阳（+快乐），各有冷却；返回给气泡的话"""
        now = time.time()
        stamp, attr = ("watered", "energy") if kind == "water" else ("sunned", "happiness")
        left = CARE_COOLDOWN / self.passive_mult - (now - getattr(c, stamp))
        if left > 0:
            return f"刚{'浇过水' if kind == 'water' else '晒过了'}，{max(1, math.ceil(left / 60))} 分钟后再来"
        setattr(c, stamp, now)
        setattr(c, attr, min(100.0, getattr(c, attr) + CARE_AMOUNT))
        self.sound.play(kind)
        w = self.widgets.get(c.id)
        if w:
            w.touch()
            w.play("hop")
        self.save()
        return "咕嘟……精神了一点" if kind == "water" else "暖……伞面都松开了"

    def starving(self) -> bool:
        """全体都饿扁 / 休眠了"""
        return bool(self.creatures) and all(c.growth_factor == 0 for c in self.creatures)

    def feed(self, widget: CreatureWidget, paths: list[Path]):
        c = widget.c
        before = c.stage
        widget.touch()
        was = c.mood
        eaten, rejected = [], []
        for path in paths[:MAX_ITEMS_PER_DROP]:
            food, why = digest(path)
            if food is None:
                rejected.append(why)
                continue
            safe = [t for t in food.targets if not self.is_protected(t)]
            pieces = [p for p in (residue_of(t) for t in random.sample(safe, min(2, len(safe)))) if p]
            if self.devour:
                if food.targets and all(self.is_protected(t) for t in food.targets):
                    rejected.append("这个不能吃")
                    continue
                done = self.devour_food(food)
                if food.targets and not done:
                    rejected.append("咬不动…")
                    continue
                if done < len(food.targets):
                    food.value = max(1, round(food.value * done / len(food.targets)))
            value, note = food.value, food.note or food.kind or "、".join(food.taste[:2])
            if self.devour:
                self.keep_residue(c, pieces)
            if food.key in c.eaten:
                value, note = max(1, value // 4), note or "嚼过了"
            else:
                c.eaten = (c.eaten + [food.key])[-500:]
            energy, happy = food.energy, food.happy
            if food.secret:                               # .secret：随机一种
                roll = random.random()
                if roll < 1 / 3:
                    value, note = value * 2, "？？？肥"
                elif roll < 2 / 3:
                    energy, happy, note = energy + CARE_AMOUNT, happy + CARE_AMOUNT, "？？？暖"
                else:
                    note = "……这个不能说"
                    if self.residue.get(c.id):
                        QTimer.singleShot(1500, lambda: self.burp(widget))
            value *= self.gain_mult
            c.nutrition += value
            c.satiety = min(SATIETY_MAX, c.satiety + value * SATIETY_PER_FOOD)
            c.energy = min(100.0, c.energy + energy * self.gain_mult)
            c.happiness = min(100.0, c.happiness + happy * self.gain_mult + 2 + (1 if food.taste and food.taste[0] != "干巴巴" else 0))
            c.feeds += 1
            c.log = (c.log + [[int(time.time()), food.label, value]])[-50:]
            eaten.append((value, note))
        home = self.field_of(c)
        if eaten and home.mat.active:
            home.mat.grow(int(sum(v for v, _ in eaten) * MAT_FEED), near=home.index_near(c.x, c.y))
            self.render_mat()
        if len(paths) > MAX_ITEMS_PER_DROP:
            rejected.append("吃不下了")
        if eaten:
            tastes = [n for _, n in eaten if n and n not in ("嚼过了",)]
            self.log_event(f"{c.name} 被喂了 {len(eaten)} 份食物（+{sum(v for v, _ in eaten)} 营养"
                           + (f"，{'、'.join(dict.fromkeys(tastes))}" if tastes else "") + "）")
            self.track_moods()

        if eaten:
            widget.play("eat")
            self.sound.play("eat")
            if sum(v for v, _ in eaten) >= 15:
                widget.jolt()
            widget.crumbs()
            if any(n == "已归档" for _, n in eaten):
                QTimer.singleShot(900, lambda: widget.play("shake"))
            if self.devour and self.residue.get(c.id):
                if random.random() < 0.35:
                    QTimer.singleShot(random.randint(2500, 4500), lambda: self.burp(widget))
                else:
                    widget.say("嗝", delay=2.0)
            for i, (value, note) in enumerate(eaten):
                widget.say(f"+{value} {note}".strip(), delay=i * 0.35)
            if rejected:
                widget.say(rejected[0], delay=len(eaten) * 0.35)
            if was in ("starving", "dormant"):
                widget.say("活过来了！", big=True)
        elif rejected:
            widget.play("shake")
            self.sound.play("deny")
            widget.say(rejected[0])
        self.after_growth(widget, before)
        self.save()

    def is_protected(self, path: Path) -> bool:
        """游戏自己的程序目录、存档目录里的东西不吃"""
        try:
            real = path.resolve()
        except OSError:
            return True
        return any(real == d or d in real.parents for d in self.protected_dirs)

    def devour_food(self, food: Food) -> int:
        """真的吃掉：删除（Windows 进回收站）能吃的文件，清掉吃空的目录，记进 eaten.log。返回吃掉几个文件。"""
        done = 0
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.data_dir / "eaten.log", "a", encoding="utf-8") as log:
            for f in food.targets:
                if self.is_protected(f):
                    continue
                try:
                    size = f.stat().st_size
                    devour_file(f)
                except OSError:
                    continue
                done += 1
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{size}\t{os.path.abspath(f)}\n")
        for d in food.dirs:
            if not self.is_protected(d):
                try:
                    os.rmdir(d)                            # 只删空目录
                except OSError:
                    pass
        return done

    def set_devour(self, on: bool):
        self.devour = on
        self.save()

    def set_config(self, **kw):
        for k, v in kw.items():
            if k == "sound":
                self.sound.enabled = v
            else:
                setattr(self, k, v)
        self.save()

    def open_panel(self, widget: CreatureWidget) -> "StatusPanel":
        p = self.panels.get(widget.c.id)
        if p is None or sip.isdeleted(p):
            p = self.panels[widget.c.id] = StatusPanel(self, widget)
            g = widget.frameGeometry()
            screen = widget.screen().availableGeometry() if widget.screen() else QRect(0, 0, 1280, 720)
            x = g.left() - p.width() - 8 if g.right() + p.width() + 8 > screen.right() else g.right() + 8
            p.move(max(screen.left(), x), max(screen.top(), min(g.bottom() - p.height(), screen.bottom() - p.height())))
        p.refresh()
        p.show()
        p.raise_()
        return p

    def after_growth(self, widget: CreatureWidget, before: int, quiet: bool = False):
        c = widget.c
        if c.stage < before:
            widget.refit()
            self.log_event(f"{c.name} 饿得缩回了 {c.stage_name}")
            if not quiet:
                widget.say(f"缩回 {c.stage_name}…")
        elif c.stage > before:
            widget.refit()
            self.log_event(f"{c.name} 长成了 {c.stage_name}")
            if not quiet:
                widget.play("grow")
                self.sound.play("grow")
                QTimer.singleShot(900, widget.jolt)
                widget.say(f"→ {c.stage_name}!", delay=0.9, big=True)
        due = c.spores_due()
        if due <= 0:
            return
        released = 0
        for i in range(due):
            if len(self.creatures) >= MAX_COLONY:
                if not getattr(widget, "_full_said", False):
                    widget.say("菌落满了", delay=1.2)
                    widget._full_said = True
                break
            tx, ty = self.find_spot(c)
            child = self.new_creature(tx, ty, gen=c.gen + 1)
            child.parent = c.id
            self.creatures.append(child)
            self.moods[child.id] = child.mood
            self.log_event(f"{c.name} 放出了孢子 {child.name}")
            c.released += 1
            released += 1
            start = (c.x, c.y - widget.sprite_rect.height() + PX * 2)
            child_w = self.spawn_widget(child)
            child_w.fly(start, (tx, ty), delay=1.3 + i * 0.4)
        if released:
            widget.say(f"噗—— ×{released}", delay=1.1, big=True)
            QTimer.singleShot(1100, lambda: self.sound.play("spores"))
            self.save()

    def find_spot(self, parent: Creature) -> tuple[int, int]:
        screen = QGuiApplication.screenAt(QPoint(parent.x, parent.y - 8)) or QGuiApplication.primaryScreen()
        a = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        best, best_d = (parent.x, parent.y), -1.0
        for _ in range(24):
            x = parent.x + random.choice((-1, 1)) * random.uniform(70, 230)
            y = parent.y + random.uniform(-70, 40)
            x = int(min(max(x, a.left() + 90), a.right() - 90))
            y = int(min(max(y, a.top() + 140), a.bottom() - 8))
            d = min(math.hypot(x - o.x, y - o.y) for o in self.creatures)
            if d > best_d:
                best, best_d = (x, y), d
            if d > 70:
                break
        return best
