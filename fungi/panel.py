"""状态面板（设计图里的 FUNGI.EXE 窗口）。"""
from __future__ import annotations

import math
import sys
import time

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QSlider, QTabWidget,
                             QWidget)

from .config import CARE_AMOUNT, CARE_COOLDOWN, INK, MOOD_CN, MOOD_NAMES, PX, SATIETY_MAX, SPEED_RANGE
from .sprites import art_pixmap, spore_size
from .ui import fmt_age, PixelBar, PixelWindow
from .creature_view import CreatureWidget


class StatusPanel(PixelWindow):
    """设计图里的 FUNGI.EXE 窗口：头像、数值条、照顾按钮、INFO / FEED LOG / CONFIG"""

    def __init__(self, colony: "Colony", widget: CreatureWidget):
        super().__init__(f"FUNGI.EXE — {widget.c.name}")
        self.colony, self.widget, self.c = colony, widget, widget.c

        self.sprite = QLabel()
        self.sprite.setFixedSize(80, 80)
        self.sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sprite.setStyleSheet(f"border:2px solid {INK.name()};")
        self.facts = QLabel()
        self.facts.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.sprite)
        head.addWidget(self.facts, 1)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.bars: dict[str, tuple[PixelBar, QLabel]] = {}
        for row, key in enumerate(("FULL", "ENERGY", "HAPPY", "GROWTH")):
            name = QLabel(key)
            name.setFixedWidth(56)
            b, n = PixelBar(), QLabel()
            n.setFixedWidth(38)
            n.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(name, row, 0)
            grid.addWidget(b, row, 1)
            grid.addWidget(n, row, 2)
            self.bars[key] = (b, n)
        grid.setColumnStretch(1, 1)

        self.feed_btn = QPushButton("喂食")
        self.feed_btn.setToolTip("也可以直接把文件拖到它身上\n.txt .md 均衡　.log 精力+　.poem 快乐+\n.todo 成长+　.secret ？？？")
        self.feed_btn.clicked.connect(lambda: colony.feed_dialog(self.widget, folder=False))
        self.water_btn = QPushButton("浇水")
        self.water_btn.setToolTip(f"精力 +{CARE_AMOUNT}，{CARE_COOLDOWN // 60} 分钟一次")
        self.water_btn.clicked.connect(lambda: self.care("water"))
        self.sun_btn = QPushButton("晒太阳")
        self.sun_btn.setToolTip(f"快乐 +{CARE_AMOUNT}，{CARE_COOLDOWN // 60} 分钟一次")
        self.sun_btn.clicked.connect(lambda: self.care("sun"))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for b in (self.feed_btn, self.water_btn, self.sun_btn):
            buttons.addWidget(b, 1)

        self.tabs = QTabWidget()
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.info.setWordWrap(True)
        self.info.setContentsMargins(10, 10, 10, 10)
        self.log = QListWidget()
        self.tabs.addTab(self.info, "INFO")
        self.tabs.addTab(self.log, "FEED LOG")
        self.tabs.addTab(self.config_tab(), "CONFIG")

        self.body.addLayout(head)
        self.body.addLayout(grid)
        self.body.addLayout(buttons)
        self.body.addWidget(self.tabs, 1)
        self.resize(340, 480)

        self.log_size = -1
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

    def slider(self, value: float, on_change) -> tuple[QSlider, QLabel]:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(int(SPEED_RANGE[0] * 4), int(SPEED_RANGE[1] * 4))    # 每格 ×0.25
        s.setValue(round(value * 4))
        label = QLabel(f"×{value:.2f}")

        def changed(v):
            label.setText(f"×{v / 4:.2f}")
            on_change(v / 4)
        s.valueChanged.connect(changed)
        return s, label

    def config_tab(self) -> QWidget:
        col = self.colony
        page = QWidget()
        form = QGridLayout(page)
        form.setContentsMargins(10, 10, 10, 10)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.growth_slider, g_label = self.slider(col.growth_speed, lambda v: col.set_config(growth_speed=v))
        self.hunger_slider, h_label = self.slider(col.hunger_speed, lambda v: col.set_config(hunger_speed=v))
        form.addWidget(QLabel("成长速度"), 0, 0)
        form.addWidget(self.growth_slider, 0, 1)
        form.addWidget(g_label, 0, 2)
        form.addWidget(QLabel("饥饿速度"), 1, 0)
        form.addWidget(self.hunger_slider, 1, 1)
        form.addWidget(h_label, 1, 2)
        self.shake_box = QCheckBox("吃大餐、长大时震一下")
        self.shake_box.setChecked(col.shake)
        self.shake_box.toggled.connect(lambda on: col.set_config(shake=on))
        self.sound_box = QCheckBox("音效" + ("" if col.sound.available else "（找不到播放器）"))
        self.sound_box.setChecked(col.sound.enabled)
        self.sound_box.setEnabled(col.sound.available)
        self.sound_box.toggled.connect(lambda on: col.set_config(sound=on))
        self.devour_box = QCheckBox("吞噬文件（吃掉后" + ("进回收站）" if sys.platform == "win32" else "删除）"))
        self.devour_box.setChecked(col.devour)
        self.devour_box.toggled.connect(col.set_devour)
        for i, b in enumerate((self.shake_box, self.sound_box, self.devour_box)):
            form.addWidget(b, 2 + i, 0, 1, 3)
        form.setRowStretch(5, 1)
        return page

    def care(self, kind: str):
        self.widget.say(self.colony.care(self.c, kind))     # 说在它头上，面板里不另开一行
        self.refresh()

    def cooldown(self, stamp: float) -> int:
        return max(0, math.ceil((CARE_COOLDOWN / self.colony.passive_mult - (time.time() - stamp)) / 60))

    def refresh(self):
        c = self.c
        if c not in self.colony.creatures or sip.isdeleted(self.widget):
            self.close()
            return
        pm = art_pixmap(c.stage, spore_size(c.nutrition) if c.stage == 0 else 3,
                        wither=c.mood in ("starving", "dormant"), mood=c.mood)
        k = max(1, min(4, 68 // max(pm.width(), pm.height(), 1)))       # 整数倍放大，小孢子不糊成一大块
        self.sprite.setPixmap(pm.scaled(pm.width() * k, pm.height() * k, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.FastTransformation))
        box = self.widget.sprite_rect
        self.facts.setText(f"NAME   {c.name}\nSTAGE  {c.stage_name}" + (f" · {MOOD_NAMES[c.mood]}" if MOOD_NAMES[c.mood] else "")
                           + f"\nAGE    {fmt_age(time.time() - c.born)}\nSIZE   {box.width() // PX}×{box.height() // PX}"
                           f"\nGEN    {c.gen}")
        lo, hi = c.stage_floor(), c.next_goal()
        grow = 100 * max(0.0, min(1.0, (c.nutrition - lo) / max(1, hi - lo)))
        for key, v in (("FULL", c.satiety / SATIETY_MAX * 100), ("ENERGY", c.energy), ("HAPPY", c.happiness),
                       ("GROWTH", grow)):
            b, n = self.bars[key]
            b.set_value(v)
            n.setText(f"{int(v)}%")
        for btn, stamp, name in ((self.water_btn, c.watered, "浇水"), (self.sun_btn, c.sunned, "晒太阳")):
            left = self.cooldown(stamp)
            btn.setEnabled(left == 0)
            btn.setText(name if left == 0 else f"{name} {left}m")
        cover = 100 * self.colony.mat_occupied()
        salt = self.colony.salted_share()
        self.info.setText(f"状态　{MOOD_CN[c.mood]}，{'有点困' if c.tired else '精神'}，"
                          f"{ {'gloomy': '闷闷不乐', 'cheery': '很开心', 'plain': '心情平平'}[c.spirit] }\n"
                          f"成长　{int(c.nutrition)} / {hi}\n"
                          f"喂过　{c.feeds} 次，放出孢子 {c.released} 个\n"
                          f"菌落　{len(self.colony.creatures)} 只，菌毯 {cover:.0f}%" + (f"，盐 {salt * 100:.0f}%" if salt else ""))
        if len(c.log) != self.log_size:
            self.log_size = len(c.log)
            self.log.clear()
            for ts, label, value in reversed(c.log[-40:]):
                self.log.addItem(f"{time.strftime('%m-%d %H:%M', time.localtime(ts))}  {label}  +{value}")
            if not c.log:
                self.log.addItem("（还没吃过东西）")

    def closeEvent(self, e):
        self.timer.stop()
        if self.colony.panels.get(self.c.id) is self:
            self.colony.panels.pop(self.c.id)
        super().closeEvent(e)
