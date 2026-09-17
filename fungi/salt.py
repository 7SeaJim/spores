"""撒盐模式：盖住屏幕的撒盐层和撒盐工具条。"""
from __future__ import annotations

import random
import time
from functools import lru_cache

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .config import INK, PAPER, PX, SALT_HOURS, SALT_REACH
from .sprites import add_halo, HALO, paint_rows
from .ui import PixelWindow
from .mat import MatField


SALT_SHAKER = ["..####..",
               ".#o#o##.",
               ".######.",
               "#oooooo#",
               "#o####o#",
               "#oooooo#",
               "#oooooo#",
               ".######."]


@lru_cache(maxsize=1)
def salt_cursor() -> QCursor:
    img = paint_rows(add_halo(SALT_SHAKER), {"#": INK, "o": PAPER, HALO: PAPER})
    return QCursor(QPixmap.fromImage(img), img.width() // 2, 0)


class SaltOverlay(QWidget):
    """撒盐模式时盖住一块屏幕：按住拖动撒盐，右键 / Esc 退出"""

    def __init__(self, colony: "Colony", field: MatField):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("fungi · salt")
        self.colony, self.field = colony, field
        self.setGeometry(field.rect)
        self.setCursor(salt_cursor())
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pressing = False
        self.last_at: QPoint | None = None
        self.grains: list[dict] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        self.idle_since = time.time()

    def tick(self):
        now = time.time()
        self.grains = [g for g in self.grains if now - g["t0"] < 0.5]
        if now - self.idle_since > 90:                     # 忘了退出：一分半没动静自己关
            self.colony.end_salt()
            return
        self.update()

    def shake_at(self, local: QPoint):
        self.idle_since = time.time()
        g = local + self.geometry().topLeft()
        if self.last_at is not None and (local - self.last_at).manhattanLength() < PX * 3:
            return
        self.last_at = local
        self.colony.sprinkle(g.x(), g.y())
        now = time.time()
        for _ in range(6):
            self.grains.append({"x": local.x() + random.uniform(-14, 14), "y": local.y() + random.uniform(4, 12),
                                "vy": random.uniform(40, 120), "t0": now})

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.colony.end_salt()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.pressing = True
            self.last_at = None
            self.shake_at(e.position().toPoint())

    def mouseMoveEvent(self, e):
        if self.pressing:
            self.shake_at(e.position().toPoint())

    def mouseReleaseEvent(self, e):
        self.pressing = False

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.colony.end_salt()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(17, 17, 17, 40))       # 淡淡压暗一层（全透明的地方点不到）
        reach = SALT_REACH * PX
        band = QColor(250, 249, 244, 46)
        r = self.rect()
        for rect in (QRect(0, 0, r.width(), reach), QRect(0, r.height() - reach, r.width(), reach),
                     QRect(0, reach, reach, r.height() - 2 * reach), QRect(r.width() - reach, reach, reach, r.height() - 2 * reach)):
            p.fillRect(rect, band)
        now = time.time()
        for g in self.grains:
            t = now - g["t0"]
            x, y = int(g["x"]) // 2 * 2, int(g["y"] + g["vy"] * t) // 2 * 2
            p.fillRect(x - 1, y - 1, 4, 4, INK)
            p.fillRect(x, y, 2, 2, PAPER)
        p.end()

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)


class SaltBar(PixelWindow):
    """撒盐模式的工具条：说明、盐的覆盖、整圈撒盐 / 扫掉盐 / 完成"""

    def __init__(self, colony: "Colony"):
        super().__init__("撒盐")
        self.colony = colony
        hint = QLabel("按住沿屏幕边拖着撒；点菌斑、喷孢菌根部也能腌住。\n"
                      f"盐大约 {SALT_HOURS} 小时化完，菌落越旺化得越快。\n"
                      "右键或 Esc 也能退出。")
        hint.setObjectName("dim")
        self.status = QLabel()
        self.ring_btn = QPushButton("整圈撒盐")
        self.ring_btn.setToolTip("所有屏幕的菌毯、菌斑、喷孢菌都停在现在的样子")
        self.ring_btn.clicked.connect(colony.salt_ring)
        self.sweep_btn = QPushButton("扫掉盐")
        self.sweep_btn.clicked.connect(colony.sweep_salt)
        self.done_btn = QPushButton("完成")
        self.done_btn.setObjectName("primary")
        self.done_btn.clicked.connect(colony.end_salt)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.ring_btn)
        row.addWidget(self.sweep_btn)
        row.addStretch(1)
        row.addWidget(self.done_btn)
        self.body.addWidget(hint)
        self.body.addWidget(self.status)
        self.body.addLayout(row)
        self.refresh()
        self.setFixedWidth(max(380, self.sizeHint().width()))
        self.adjustSize()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

    def refresh(self):
        col = self.colony
        share = col.salted_share()
        extra = sum(p.salted > time.time() for p in col.patches) + bool(col.spitter and col.spitter.get("salted", 0) > time.time())
        self.status.setText(f"边缘腌住 {share * 100:.0f}%" + (f"，另有 {extra} 处菌斑 / 喷孢菌" if extra else ""))
        self.sweep_btn.setEnabled(bool(share or extra))

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)
        if self.colony.salt_bar is self:                    # 点了右上角 ×：也算退出撒盐
            self.colony.end_salt()
