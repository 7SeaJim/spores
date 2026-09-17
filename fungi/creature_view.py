"""单只菌的窗口：绘制、待机动效、拖放喂食、戳、拖动搬家。"""
from __future__ import annotations

import math
import random
import time
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QRegion
from PyQt6.QtWidgets import QWidget

from .config import (BOTTOM_PAD, BREATH_PERIOD, HUNGRY_IDLE, IDLE_GAP, IDLE_WEIGHTS, INK, MIN_WIN_W, MOOD_NAMES, PAPER,
                     PX, TEXT_BAND, USE_INPUT_MASK)
from .sprites import art_pixmap, spore_size
from .model import Creature
from .ui import draw_label, ui_font


ANIM_DUR = {"eat": 0.8, "shake": 0.5, "poke": 0.4, "grow": 1.2, "land": 0.3, "hop": 0.35,
            "tilt": 1.5, "wiggle": 0.5}
LOUD_ANIMS = {"eat", "shake", "poke", "grow"}    # 会跳出精灵附近的动画，需要整窗绘制
TILT_STEPS = (-1, -1, 0, 1, 1, 0)
DUST = QColor(85, 82, 76)


class CreatureWidget(QWidget):
    def __init__(self, colony: "Colony", creature: Creature, on_top: bool = True):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.colony, self.c = colony, creature
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        self.setWindowTitle(f"fungi · {creature.name}")

        self.anim: tuple[str, float] | None = None
        self.floaters: list[dict] = []       # 飘字
        self.particles: list[dict] = []      # 碎屑
        self.flight: dict | None = None      # 孢子飞行
        self.hover = self.drag_over = False
        self.press: tuple[QPoint, int, int] | None = None
        self.moved = False
        self.masked: str | None = None
        self.shown_stage = creature.stage
        now = time.time()
        self.blinks = [now + random.uniform(2, 6)]
        self.idle_at = now + random.uniform(*IDLE_GAP)
        self.phase = random.uniform(0, BREATH_PERIOD)   # 错开每只菌的呼吸
        self.last_touch = now
        self.asleep = False
        self.jolt_until = 0.0
        self.z_at = 0.0
        self.z_n = 0
        self.shown_mood = creature.mood
        self.thinking = False                 # 正在等聊天回复
        self.think_at = 0.0
        self._key = None
        self.sprite_rect = QRect()

        self.refit()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    # ── 几何 ──
    def base_pixmap(self) -> QPixmap:
        return art_pixmap(self.c.stage, spore_size(self.c.nutrition), mood="dormant" if self.c.mood == "dormant" else "full")

    def refit(self):
        pm = self.base_pixmap()
        w, h = max(MIN_WIN_W, pm.width() + 40), pm.height() + TEXT_BAND + BOTTOM_PAD
        self.resize(w, h)
        self.sprite_rect = QRect((w - pm.width()) // 2, h - BOTTOM_PAD - pm.height(), pm.width(), pm.height())
        self.shown_stage = self.c.stage
        self.shown_dormant = self.c.mood == "dormant"
        self.masked = None
        if not self.flight:
            self.place()
        self.update_mask()

    def place(self, x: float | None = None, y: float | None = None):
        # 精灵底部 = 脚底锚点；描边占 1 格。飞行中传入显示位置，存档里的 c.x/c.y 始终是落点
        x = self.c.x if x is None else x
        y = self.c.y if y is None else y
        left = self.jolt_until - time.time()
        if left > 0:                                            # 震一下：左右抖，越抖越小
            x += (PX if int(left * 40) % 2 else -PX) * (1 + int(left * 6))
        self.move(int(x - self.width() / 2), int(y - (self.height() - BOTTOM_PAD) + PX))

    def jolt(self, dur: float = 0.35):
        if not sip.isdeleted(self) and self.colony.shake:
            self.jolt_until = time.time() + dur

    def update_mask(self):
        """tight: 只有精灵附近；band: 再加精灵正上方一条（z、孢子尘）；full: 整窗（飘字、跳跃、悬停）"""
        if not USE_INPUT_MASK:
            return
        loud = (self.hover or self.flight or (self.anim and self.anim[0] in LOUD_ANIMS)
                or any(not f["ambient"] for f in self.floaters)
                or any(q["kind"] == "crumb" for q in self.particles))
        mode = "full" if loud else "band" if (self.asleep or self.floaters or self.particles) else "tight"
        if mode == self.masked:
            return
        self.masked = mode
        if mode == "full":
            self.clearMask()
            return
        r = self.sprite_rect
        region = QRegion(r.adjusted(-14, -14, 14, BOTTOM_PAD))
        if mode == "band":
            region = region.united(QRegion(QRect(r.x() - 14, 0, r.width() + 28, r.y())))
        self.setMask(region)

    # ── 动画 ──
    def play(self, name: str):
        self.anim = (name, time.time())
        self.update_mask()

    def say(self, text: str, delay: float = 0.0, big: bool = False, ambient: bool = False):
        t0 = time.time() + delay
        queue = [f for f in self.floaters if not f["ambient"]]
        if queue and not ambient:
            t0 = max(t0, queue[-1]["t0"] + 0.45)
        self.floaters.append({"text": text, "t0": t0, "dur": 2.2 if ambient else 1.6, "big": big, "ambient": ambient})
        self.update_mask()

    def crumbs(self, n: int = 7):
        now = time.time()
        r = self.sprite_rect
        for _ in range(n):
            self.particles.append({"kind": "crumb", "x": r.center().x(), "y": r.y() + PX * 2, "life": 0.6,
                                   "vx": random.uniform(-70, 70), "vy": random.uniform(-150, -60), "t0": now})

    def puff(self, n: int = 3):
        """成年菌从菌盖顶上飘出几粒孢子尘"""
        now = time.time()
        r = self.sprite_rect
        for i in range(n):
            self.particles.append({"kind": "mote", "x": r.x() + random.uniform(0.3, 0.7) * r.width(),
                                   "y": r.y() + PX * 3, "vx": random.uniform(-6, 6), "vy": random.uniform(-17, -11),
                                   "t0": now + i * 0.3, "life": 2.4, "wob": random.uniform(0, 6.3)})
        self.update_mask()

    # ── 待机 ──
    def touch(self):
        """有人理它：刷新计时，睡着的话醒过来"""
        self.last_touch = time.time()
        if self.asleep:
            self.asleep = False
            self.floaters = [f for f in self.floaters if not f["ambient"]]
            self.idle_at = self.last_touch + random.uniform(*IDLE_GAP)
            self.play("hop")
            self.say("!")

    def breathing_out(self, now: float) -> bool:
        period = BREATH_PERIOD * (1.6 if self.asleep else 1.0)
        return (now + self.phase) % period > period * 0.55

    def idle(self, now: float):
        if self.thinking and now >= self.think_at:
            self.say("…", ambient=True)
            self.think_at = now + 0.9
        if self.c.mood == "dormant":                    # 休眠孢子：一动不动，等人喂
            self.asleep = False
            return
        busy = self.anim or self.press or self.flight or self.drag_over or self.hover
        if not self.asleep and not busy and now - self.last_touch > self.colony.sleep_after(self.c):
            self.asleep = True
            self.z_at = now + 0.8
        if self.asleep:
            if now >= self.z_at:
                self.z_n += 1
                self.say("Z" if self.z_n % 3 == 0 else "z", ambient=True)
                self.z_at = now + 1.4
            return
        if busy or now < self.idle_at:
            return
        self.idle_at = now + random.uniform(*IDLE_GAP) * (2 if self.c.tired else 1)
        acts = HUNGRY_IDLE.get(self.c.mood)
        if acts is None:
            acts = list(IDLE_WEIGHTS[self.c.stage])
            if self.c.spirit == "gloomy":
                acts = [("sulk", 3), ("blink2", 2)]
            elif self.c.spirit == "cheery":
                acts += [("hop", 3)]
            if self.c.tired:
                acts = [(a, w) for a, w in acts if a not in ("hop", "puff")] + [("yawn", 2)]
            if self.c.stage == 0:                                  # 孢子不会说话
                acts = [(a, w) for a, w in acts if a not in ("sulk", "yawn")] or [("wiggle", 1)]
        self.do_idle(random.choices([a for a, _ in acts], [w for _, w in acts])[0])

    def do_idle(self, act: str):
        now = time.time()
        if act == "blink2":
            self.blinks = [now, now + 0.3]
        elif act == "puff":
            self.puff()
        elif act == "sulk":
            self.say(random.choice(["……不开心", "今天的菌丝有点蔫", "……"]))
        elif act == "yawn":
            self.say(random.choice(["哈……", "困……", "眼皮好沉"]))
        elif act == "grumble":
            self.say(random.choice(["饿…", "咕…", "……", "想吃 .txt"]) if self.c.mood == "hungry"
                     else random.choice(["好饿……", "咕噜……", "……"]))
        else:
            self.play(act)

    def fly(self, start: tuple[int, int], end: tuple[int, int], delay: float = 0.0, dur: float = 0.75):
        self.flight = {"a": start, "b": end, "t0": time.time() + delay, "dur": dur}
        self.c.x, self.c.y = end
        self.place(*start)
        self.update_mask()

    def body_offset(self, now: float) -> tuple[int, int]:
        if not self.anim:
            return 0, 0
        name, t0 = self.anim
        t = now - t0
        if name == "eat":
            return 0, -PX if int(t * 8) % 2 else 0
        if name == "shake":
            return (PX if int(t * 16) % 2 else -PX), 0
        if name == "wiggle":
            return (PX if int(t * 8) % 2 == 0 else -PX) if t < ANIM_DUR["wiggle"] else 0, 0
        if name in ("poke", "hop", "land"):
            k = {"poke": 3, "hop": 1, "land": 1}[name]
            return 0, -round(math.sin(math.pi * min(1.0, t / ANIM_DUR[name])) * k) * PX
        return 0, 0

    def current_pixmap(self, now: float) -> QPixmap:
        c = self.c
        anim = self.anim[0] if self.anim else None
        t = now - self.anim[1] if self.anim else 0
        invert = anim == "grow" and int(t / 0.15) % 2 == 0
        wither = c.mood in ("starving", "dormant")
        if c.stage == 0:
            return art_pixmap(0, spore_size(c.nutrition), invert=invert, wither=wither, mood=c.mood)
        mouth = self.drag_over or (anim == "eat" and int(t * 8) % 2 == 0)
        blink = self.asleep or any(b <= now < b + 0.15 for b in self.blinks)
        calm = not (mouth or anim in LOUD_ANIMS or self.flight or self.press)
        breath = calm and self.breathing_out(now)
        tilt = TILT_STEPS[min(len(TILT_STEPS) - 1, int(t / ANIM_DUR["tilt"] * len(TILT_STEPS)))] if anim == "tilt" else 0
        return art_pixmap(c.stage, 3, blink, mouth, invert, breath, tilt, wither, c.mood)

    def tick(self):
        now = time.time()
        if self.jolt_until and not self.flight:
            if now > self.jolt_until:
                self.jolt_until = 0.0
            self.place()
        if self.flight and now >= self.flight["t0"]:
            f = self.flight
            k = min(1.0, (now - f["t0"]) / f["dur"])
            (ax, ay), (bx, by) = f["a"], f["b"]
            self.place(ax + (bx - ax) * k, ay + (by - ay) * k - math.sin(math.pi * k) * 90)
            if k >= 1.0:
                self.flight = None
                self.place()
                self.play("land")
                self.colony.save()
        if self.anim and now - self.anim[1] > ANIM_DUR[self.anim[0]]:
            self.anim = None
        self.floaters = [f for f in self.floaters if now - f["t0"] < f["dur"]]
        self.particles = [q for q in self.particles if now - q["t0"] < q["life"]]
        self.blinks = [b for b in self.blinks if now < b + 0.15]
        if not self.blinks:
            first = now + random.uniform(2.5, 7)
            self.blinks = [first, first + 0.3] if random.random() < 0.2 else [first]
        self.idle(now)
        if self.c.stage != self.shown_stage or (self.c.mood == "dormant") != self.shown_dormant:
            self.refit()
        mood = self.c.mood
        if mood != self.shown_mood:
            order = list(MOOD_NAMES)
            if order.index(mood) > order.index(self.shown_mood):
                self.say({"hungry": "饿了…", "starving": "好饿……", "dormant": "（休眠了）"}[mood])
            self.shown_mood = mood
        self.update_mask()

        loud = self.flight or any(not f["ambient"] for f in self.floaters) or any(q["kind"] == "crumb" for q in self.particles)
        fps = 20 if loud else 10 if (self.floaters or self.particles) else 0
        key = (self.current_pixmap(now).cacheKey(), self.body_offset(now), self.hover, self.drag_over,
               int(now * fps) if fps else 0, int(now) if self.hover else 0)
        if key != self._key:
            self._key = key
            self.update()

    # ── 绘制 ──
    def paintEvent(self, _):
        now = time.time()
        p = QPainter(self)
        r = self.sprite_rect
        dx, dy = self.body_offset(now)

        if self.drag_over:
            box = r.adjusted(-8, -8, 8, 4)
            p.setPen(QPen(PAPER, 4))
            p.drawRect(box)
            p.setPen(QPen(INK, 2, Qt.PenStyle.DashLine))
            p.drawRect(box)

        p.drawPixmap(r.x() + dx, r.y() + dy, self.current_pixmap(now))

        for q in self.particles:
            t = now - q["t0"]
            if t < 0:
                continue
            if q["kind"] == "crumb":
                x, y, paper, ink = q["x"] + q["vx"] * t, q["y"] + q["vy"] * t + 380 * t * t, PAPER, INK
            else:
                x = q["x"] + q["vx"] * t + math.sin(q["wob"] + t * 3) * 3
                y = q["y"] + q["vy"] * t
                alpha = int(255 * min(1.0, (1 - t / q["life"]) * 2.5))
                paper, ink = QColor(PAPER), QColor(DUST)
                paper.setAlpha(alpha)
                ink.setAlpha(alpha)
            p.fillRect(int(x) - 3, int(y) - 3, 6, 6, paper)
            p.fillRect(int(x) - 2, int(y) - 2, 4, 4, ink)

        for f in self.floaters:
            if f["ambient"] and now >= f["t0"]:
                k = (now - f["t0"]) / f["dur"]
                draw_label(p, f["text"], r.right() - 2 + k * 8, r.y() + 8 - k * 28,
                           ui_font(15 if f["text"] == "Z" else 12), int(255 * min(1.0, (1 - k) * 2)))

        top = r.y() - 6
        active = [f for f in self.floaters if not f["ambient"] and now >= f["t0"]][-1:]
        for f in active:
            k = (now - f["t0"]) / f["dur"]
            alpha = 255 if k < 0.6 else int(255 * (1 - (k - 0.6) / 0.4))
            draw_label(p, f["text"], self.width() / 2, top - k * 16, ui_font(15 if f["big"] else 13), alpha)

        if self.hover and not active and not self.drag_over:
            c = self.c
            mood = {"dormant": "休眠 · 喂点东西吧"}.get(c.mood, MOOD_NAMES[c.mood])
            draw_label(p, f"{c.name} · {c.stage_name}" + (f" · {mood}" if mood else ""), self.width() / 2, top - 16, ui_font(12))
            lo, hi = c.stage_floor(), c.next_goal()
            frac = max(0.0, min(1.0, (c.nutrition - lo) / (hi - lo)))
            bw, bh = 64, 8
            bx, by = int(self.width() / 2 - bw / 2), top - 10
            p.fillRect(bx - 2, by - 2, bw + 4, bh + 4, PAPER)
            p.fillRect(bx, by, bw, bh, INK)
            p.fillRect(bx + 2, by + 2, bw - 4, bh - 4, PAPER)
            p.fillRect(bx + 2, by + 2, int((bw - 4) * frac), bh - 4, INK)
        p.end()

    # ── 拖放喂食 ──
    @staticmethod
    def _local_paths(e) -> list[Path]:
        md = e.mimeData()
        if not md.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]

    @staticmethod
    def _safe_action(e):
        """只接受「复制/链接」，绝不接受「移动」——避免文件管理器把源文件删掉。"""
        acts = e.possibleActions()
        if acts & Qt.DropAction.CopyAction:
            return Qt.DropAction.CopyAction
        if acts & Qt.DropAction.LinkAction:
            return Qt.DropAction.LinkAction
        return None

    def dragEnterEvent(self, e):
        action = self._safe_action(e)
        if self._local_paths(e) and action is not None:
            self.touch()
            e.setDropAction(action)
            e.accept()
            self.drag_over = True
            self.update()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        action = self._safe_action(e)
        if action is not None:
            e.setDropAction(action)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.drag_over = False
        self.update()

    def dropEvent(self, e):
        self.drag_over = False
        action = self._safe_action(e)
        paths = self._local_paths(e)
        if action is None or not paths:
            e.ignore()
            return
        e.setDropAction(action)
        e.accept()
        self.colony.feed(self, paths)

    # ── 鼠标 ──
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.flight:
            self.touch()
            self.press = (e.globalPosition().toPoint(), self.c.x, self.c.y)
            self.moved = False

    def mouseMoveEvent(self, e):
        if not self.press:
            return
        start, ax, ay = self.press
        d = e.globalPosition().toPoint() - start
        if not self.moved and d.manhattanLength() < 5:
            return
        self.moved = True
        self.c.x, self.c.y = ax + d.x(), ay + d.y()
        self.place()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.press:
            return
        self.press = None
        if self.moved:
            self.colony.save()
        else:
            self.play("poke")
            self.colony.sound.play("poke")
            self.say(random.choice(["?", "…", "!", "嗯？"]) if self.c.stage else "·")
            self.c.happiness = min(100.0, self.c.happiness + 2)       # 被戳一下有点开心

    def enterEvent(self, e):
        self.touch()
        self.hover = True
        self.update_mask()
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.colony.open_chat(self)

    def contextMenuEvent(self, e):
        self.colony.show_menu(self, e.globalPos())
