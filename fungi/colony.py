"""菌落：存档、主循环、屏幕变化与全屏。聊天、喂食、菌毯、撒盐、喷孢菌、菜单分在 colony_* 里。"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QTimer
from PyQt6.QtGui import QGuiApplication

from .config import CHRONICLE_MAX, DEVOUR, MAT_TICK, NAMES, OUTCOME_NAMES, PROJECT_DIR, SLEEP_AFTER, SPEED_RANGE
from .model import Creature
from .desktop import foreground_fullscreen
from .sound import Sound
from .creature_view import CreatureWidget
from .mat import MatField, screen_key
from .spitter import EDGE_POSE, Patch, PatchView, SpitterWidget, SporeShot
from .salt import SaltBar, SaltOverlay
from .chat_ui import ChatBridge, SpeechBubble
from .panel import StatusPanel
from .colony_chat import ChatMixin
from .colony_feed import FeedMixin
from .colony_mat import MatMixin
from .colony_salt import SaltMixin
from .colony_spitter import SpitterMixin
from .colony_menu import MenuMixin


class Colony(ChatMixin, FeedMixin, MatMixin, SaltMixin, SpitterMixin, MenuMixin):
    """整个菌落：所有菌、菌毯、喷孢菌和它们的窗口"""

    def __init__(self, data_dir: Path, fast: bool = False):
        self.data_dir = data_dir
        self.save_path = data_dir / "save.json"
        self.fast = fast
        self.passive_mult = 30 if fast else 1
        self.gain_mult = 3 if fast else 1
        self.widgets: dict[str, CreatureWidget] = {}
        self.creatures: list[Creature] = []
        self.name_i = 0
        self.on_top = True
        self.last_tick = time.time()
        self.growth_speed = 1.0                           # 状态面板 CONFIG 里调
        self.hunger_speed = 1.0
        self.shake = True
        self.sound = Sound(data_dir / "sounds")
        self.panels: dict[str, StatusPanel] = {}
        self.fields: dict[str, MatField] = {}            # 每块屏幕一圈菌毯，主屏在最前
        self.primary_key = ""
        self.stash: dict[str, dict] = {}                  # 暂时没接上的屏幕，它们的菌毯先存着
        self.watched_screens: set[int] = set()
        self.mat_layer = "top"
        self.mat_acc = 0.0
        self.salt_acc = 0.0
        self.salt_overlays: list[SaltOverlay] = []
        self.salt_bar: SaltBar | None = None
        self.salt_session: set[str] = set()               # 这次撒盐撒到了哪几块屏
        self.offline_elapsed = 0.0
        self.spitter: dict | None = None
        self.spitter_view: SpitterWidget | None = None
        self.patches: list[Patch] = []
        self.patch_views: dict[int, PatchView] = {}
        self.shots: list[SporeShot] = []
        self.devour = DEVOUR
        self.protected_dirs = [PROJECT_DIR, data_dir.resolve()]
        self.offline_delta: dict[str, float] = {}
        self.hidden_for_fullscreen = False
        self.views_stale = False
        self.chat_path = data_dir / "chat.json"
        self.chat_cfg = self.load_chat_cfg()
        self.memory_path = data_dir / "memory.json"
        self.memory: dict[str, list[dict]] = self.load_memory()
        self.chronicle: list[list] = []
        self.residue_path = data_dir / "residue.json"
        self.residue: dict[str, list[dict]] = self.load_private(self.residue_path)
        self.moods: dict[str, str] = {}
        self.mat_mark = 0.0
        self.chat_pending: set[str] = set()
        self.chat_last: dict[str, float] = {}             # 每只菌上次发话的时间
        self.chat_calls: list[float] = []                 # 最近调 API 的时间
        self.bubbles: dict[str, SpeechBubble] = {}
        self.chat_bridge = ChatBridge()
        self.chat_bridge.done.connect(self.on_chat_reply)
        self.screen_debounce = QTimer()
        self.screen_debounce.setSingleShot(True)
        self.screen_debounce.timeout.connect(self.on_screen_changed)

        offline = self.load()
        self.grow_mat_offline(self.offline_elapsed)
        self.build_mat_views()
        for c in self.creatures:
            self.spawn_widget(c)
        for c in list(self.creatures):
            self.after_growth(self.widgets[c.id], c.stage, quiet=True)
            delta = self.offline_delta.get(c.id, 0.0)
            if delta >= 1:
                self.widgets[c.id].say(f"+{int(delta)} 睡觉时长的", delay=0.6)
            elif delta <= -1:
                self.widgets[c.id].say(f"{int(delta)} 饿瘦了", delay=0.6)
        self.save()

        self.tick_timer = QTimer()
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)
        self.autosave = QTimer()
        self.autosave.timeout.connect(self.save)
        self.autosave.start(30_000)
        self.spitter_timer = QTimer()
        self.spitter_timer.timeout.connect(self.spitter_tick)
        self.spitter_timer.start(100)
        self.fullscreen_timer = QTimer()
        self.fullscreen_timer.timeout.connect(self.check_fullscreen)
        if sys.platform == "win32":
            self.fullscreen_timer.start(1500)
        app = QGuiApplication.instance()
        if app:
            app.primaryScreenChanged.connect(self.schedule_screen_change)
            app.screenAdded.connect(self.schedule_screen_change)
            app.screenRemoved.connect(self.schedule_screen_change)
        self.watch_screens()
        self.tray = self.make_tray()

    def sleep_after(self, c: Creature | None = None) -> float:
        base = 40 if self.fast else SLEEP_AFTER
        base = base / 5 if time.localtime().tm_hour < 7 else base
        return base / 3 if c is not None and c.tired else base     # 困了更容易睡着

    # ── 存档 ──
    def default_spot(self) -> tuple[int, int]:
        screen = QGuiApplication.primaryScreen()
        a = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        return a.right() - 260, a.bottom() - 40

    def new_creature(self, x: int, y: int, gen: int = 1) -> Creature:
        if not self.creatures and self.name_i == 0:
            name = "spores"
        else:
            base = NAMES[(self.name_i - 1) % len(NAMES)] if self.name_i else NAMES[0]
            loops = (self.name_i - 1) // len(NAMES) if self.name_i else 0
            name = base + (f"-{loops + 1}" if loops else "")
        self.name_i += 1
        return Creature(id=f"{int(time.time() * 1000):x}{random.randrange(4096):03x}", name=name, gen=gen, x=x, y=y)

    def load(self) -> float:
        data = None
        if self.save_path.exists():
            try:
                data = json.loads(self.save_path.read_text("utf-8"))
            except (OSError, ValueError) as err:
                broken = self.save_path.with_name(f"save.broken-{int(time.time())}.json")
                self.save_path.rename(broken)
                print(f"[fungi] 存档损坏，已备份到 {broken}: {err}", file=sys.stderr)
        offline = 0.0
        if data:
            self.stash = {k: v for k, v in (data.get("mats") or {}).items() if isinstance(v, dict)}
            if data.get("mat"):                            # 主屏那一圈（老存档只有这一圈）
                self.stash[data.get("mat_screen") or screen_key(QGuiApplication.primaryScreen())] = data["mat"]
        self.sync_fields()
        if data:
            self.name_i = data.get("name_i", 0)
            self.on_top = data.get("on_top", True)
            self.devour = data.get("devour", DEVOUR)
            cfg = data.get("config", {})
            self.growth_speed = min(SPEED_RANGE[1], max(SPEED_RANGE[0], float(cfg.get("growth_speed", 1.0))))
            self.hunger_speed = min(SPEED_RANGE[1], max(SPEED_RANGE[0], float(cfg.get("hunger_speed", 1.0))))
            self.shake = bool(cfg.get("shake", True))
            self.sound.enabled = bool(cfg.get("sound", True))
            self.mat_layer = data.get("mat_layer", "top")
            self.patches = [Patch.from_dict(p) for p in data.get("patches", [])]
            sp = data.get("spitter")
            if sp and sp.get("edge") in EDGE_POSE:
                key = sp.get("screen") if sp.get("screen") in self.fields else self.primary_key
                m = self.fields[key].mat
                length = m.edge_len(sp["edge"])
                pos = min(length - 1, max(0, int(sp.get("frac", 0.5) * length)))
                self.spitter = {"i": m.index_at(sp["edge"], pos), "edge": sp["edge"], "frac": sp.get("frac", 0.5), "screen": key,
                                "born": sp.get("born", time.time()) - 99, "shots": sp.get("shots", 0),
                                "salted": sp.get("salted", 0.0),
                                "stats": {k: sp.get("stats", {}).get(k, 0) for k in OUTCOME_NAMES},
                                "next_at": time.time() + max(3.0, sp.get("next_in", 30) / self.passive_mult)}
            self.creatures = [Creature.from_dict(d) for d in data.get("creatures", [])]
            self.mat_mark = data.get("mat_mark", 0.0)
            if "chronicle" in data:
                self.chronicle = data["chronicle"][-CHRONICLE_MAX:]
            else:                                          # 旧存档：推断亲缘，补上出生记录
                self.infer_family()
            elapsed = time.time() - data.get("last_seen", time.time())
            self.offline_elapsed = max(0.0, elapsed)
            for c in self.creatures:
                self.offline_delta[c.id] = self.offline_metabolize(c, self.offline_elapsed * self.passive_mult)
                offline = max(offline, self.offline_delta[c.id])
                if QGuiApplication.screenAt(QPoint(c.x, c.y - 8)) is None:
                    c.x, c.y = self.default_spot()
        if not self.creatures:
            self.creatures = [self.new_creature(*self.default_spot())]
            self.log_event(f"{self.creatures[0].name} 在桌面上冒了出来")
        self.moods = {c.id: c.mood for c in self.creatures}
        return offline

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "last_seen": time.time(), "name_i": self.name_i, "on_top": self.on_top, "devour": self.devour,
                "config": {"growth_speed": self.growth_speed, "hunger_speed": self.hunger_speed, "shake": self.shake,
                           "sound": self.sound.enabled},
                "mat_layer": self.mat_layer, "mat": self.mat.to_json(), "mat_screen": self.primary_key,
                "mats": {**self.stash, **{k: f.mat.to_json() for k, f in self.fields.items() if k != self.primary_key}},
                "mat_mark": self.mat_mark,
                "chronicle": self.chronicle,
                "patches": [asdict(p) for p in self.patches],
                "spitter": ({k: self.spitter[k] for k in ("edge", "frac", "born", "shots", "stats", "screen") if k in self.spitter}
                            | {"salted": self.spitter.get("salted", 0.0)}
                            | {"next_in": max(0.0, self.spitter["next_at"] - time.time()) * self.passive_mult})
                if self.spitter else None,
                "creatures": [asdict(c) for c in self.creatures]}
        tmp = self.save_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.save_path)

    # ── 生长 ──
    def spawn_widget(self, c: Creature) -> CreatureWidget:
        w = CreatureWidget(self, c, self.on_top)
        self.widgets[c.id] = w
        if not self.hidden_for_fullscreen:
            w.show()
        return w

    def tick(self):
        now = time.time()
        dt = min(5.0, now - self.last_tick)
        self.last_tick = now
        for c in list(self.creatures):
            before = c.stage
            w = self.widgets.get(c.id)
            self.metabolize(c, dt * self.passive_mult, asleep=bool(w and w.asleep))
            if c.stage != before or c.spores_due() > 0:
                self.after_growth(self.widgets[c.id], before)
        self.track_moods()
        self.mat_acc += dt * self.passive_mult
        self.melt_salt(dt * self.passive_mult)
        while self.mat_acc >= MAT_TICK:
            self.mat_acc -= MAT_TICK
            self.mat_step()
        if any(f.mat.dirty for f in self.fields.values()):
            self.render_mat()

    # ── 屏幕变化（换主屏、改分辨率/缩放、挪任务栏）与全屏 ──
    def watch_screens(self):
        for screen in QGuiApplication.screens():
            if id(screen) not in self.watched_screens:
                self.watched_screens.add(id(screen))
                screen.availableGeometryChanged.connect(self.schedule_screen_change)
                screen.destroyed.connect(lambda *_, k=id(screen): self.watched_screens.discard(k))

    def schedule_screen_change(self, *_):
        self.screen_debounce.start(400)                  # 这类信号常常一次来好几个

    def on_screen_changed(self):
        """按新的桌面可用区域重新铺菌毯；跑到屏幕外的宠物拉回来"""
        self.watch_screens()
        self.sync_fields()
        if self.spitter:
            sp = self.spitter
            if sp.get("screen") not in self.fields:           # 它那块屏拔掉了：先挪到主屏同样的位置
                sp["screen"] = self.primary_key
            m = self.spitter_field().mat
            length = m.edge_len(sp["edge"])
            sp["i"] = m.index_at(sp["edge"], min(length - 1, int(sp["frac"] * length)))
        for c in self.creatures:
            if QGuiApplication.screenAt(QPoint(c.x, c.y - 8)) is None:
                c.x, c.y = self.default_spot()
                self.widgets[c.id].place()
        if self.hidden_for_fullscreen:
            self.views_stale = True                      # 退出全屏再重铺
        else:
            self.build_mat_views()
        self.save()

    def check_fullscreen(self):
        full = foreground_fullscreen()
        if full != self.hidden_for_fullscreen:
            self.set_fullscreen_hidden(full)

    def set_fullscreen_hidden(self, hidden: bool):
        """前台全屏时把菌毯、菌斑、喷孢菌和宠物都藏起来，退出全屏再出来（期间照样长）"""
        self.hidden_for_fullscreen = hidden
        views = self.all_mat_views() + list(self.patch_views.values()) + [self.spitter_view] + list(self.widgets.values())
        for v in views:
            if v:
                v.setVisible(not hidden)
        if not hidden:
            if self.views_stale:
                self.views_stale = False
                self.build_mat_views()
            for w in self.widgets.values():
                w.raise_()
