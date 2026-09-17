"""共用界面件：字体、菜单 / 面板样式、像素条、像素风小窗口。"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .config import INK, PAPER


def fmt_age(sec: float) -> str:
    sec = int(max(0, sec))
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def ui_font(px: int = 12, bold: bool = True) -> QFont:
    f = QFont()
    f.setFamilies(["DejaVu Sans Mono", "Consolas", "Noto Sans Mono CJK SC", "Noto Sans CJK SC", "Microsoft YaHei UI",
                   "Microsoft YaHei", "monospace"])
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def draw_label(p: QPainter, text: str, cx: float, baseline: float, font: QFont, alpha: int = 255):
    path = QPainterPath()
    path.addText(cx - QFontMetrics(font).horizontalAdvance(text) / 2, baseline, font, text)
    paper, ink = QColor(PAPER), QColor(INK)
    paper.setAlpha(alpha)
    ink.setAlpha(alpha)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.strokePath(path, QPen(paper, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.fillPath(path, ink)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)


MENU_QSS = f"""
QMenu {{ background:{PAPER.name()}; color:{INK.name()}; border:2px solid {INK.name()}; padding:4px;
         font-family:'DejaVu Sans Mono','Consolas','Noto Sans CJK SC','Microsoft YaHei UI',monospace; font-size:12px; font-weight:bold; }}
QMenu::item {{ padding:4px 22px 4px 12px; }}
QMenu::item:selected {{ background:{INK.name()}; color:{PAPER.name()}; }}
QMenu::item:disabled {{ color:{INK.name()}; }}
QMenu::separator {{ height:2px; background:{INK.name()}; margin:4px 6px; }}
QMenu::indicator {{ width:10px; height:10px; border:2px solid {INK.name()}; margin-left:4px; }}
QMenu::indicator:checked {{ background:{INK.name()}; }}
"""


class PixelBar(QWidget):
    """分格的像素条"""
    CELLS = 12

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(16)
        self.setMinimumWidth(self.CELLS * 6 + 6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, v: float):
        v = max(0.0, min(100.0, v))
        if v != self.value:
            self.value = v
            self.update()

    def lit(self) -> int:
        return math.ceil(self.value / 100 * self.CELLS - 1e-9)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), INK)
        p.fillRect(self.rect().adjusted(2, 2, -2, -2), PAPER)
        step = (self.width() - 6) // self.CELLS
        for i in range(self.CELLS):
            p.fillRect(QRect(4 + i * step, 4, step - 2, 8), INK if i < self.lit() else QColor(222, 219, 208))
        p.end()


PANEL_QSS = f"""
QWidget#panel {{ background:{PAPER.name()}; border:2px solid {INK.name()}; }}
QWidget {{ color:{INK.name()}; font-family:'DejaVu Sans Mono','Consolas','Noto Sans CJK SC','Microsoft YaHei UI',monospace;
          font-size:12px; font-weight:bold; }}
QLabel#title {{ background:{INK.name()}; color:{PAPER.name()}; padding:4px 6px; }}
QPushButton {{ background:{PAPER.name()}; border:2px solid {INK.name()}; padding:4px 8px; }}
QPushButton:hover {{ background:{INK.name()}; color:{PAPER.name()}; }}
QPushButton:disabled {{ color:#9a978d; border-color:#9a978d; }}
QPushButton#close {{ border:none; background:{INK.name()}; color:{PAPER.name()}; padding:4px 10px; }}
QPushButton#close:hover {{ background:{PAPER.name()}; color:{INK.name()}; }}
QPushButton#primary {{ background:{INK.name()}; color:{PAPER.name()}; }}
QLabel#dim {{ color:#55524c; font-weight:normal; }}
QTabWidget::pane {{ border:2px solid {INK.name()}; top:-2px; background:{PAPER.name()}; }}
QTabBar::tab {{ background:{PAPER.name()}; border:2px solid {INK.name()}; padding:3px 10px; margin-right:-2px; }}
QTabBar::tab:selected {{ background:{INK.name()}; color:{PAPER.name()}; }}
QListWidget {{ background:{PAPER.name()}; border:none; }}
QSlider::groove:horizontal {{ height:6px; background:{PAPER.name()}; border:2px solid {INK.name()}; }}
QSlider::handle:horizontal {{ width:10px; margin:-6px 0; background:{INK.name()}; }}
QCheckBox::indicator {{ width:10px; height:10px; border:2px solid {INK.name()}; }}
QCheckBox::indicator:checked {{ background:{INK.name()}; }}
"""


class PixelWindow(QWidget):
    """像素风小窗口：黑色标题栏（可拖）+ 关闭按钮 + 内容区"""

    def __init__(self, title: str):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.drag_from: QPoint | None = None
        self.setObjectName("panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(PANEL_QSS)
        self.setWindowTitle(title)
        self.title = QLabel(title)
        self.title.setObjectName("title")
        close = QPushButton("×")
        close.setObjectName("close")
        close.clicked.connect(self.close)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(0)
        bar.addWidget(self.title, 1)
        bar.addWidget(close)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(12, 10, 12, 12)
        self.body.setSpacing(10)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)
        outer.addLayout(bar)
        outer.addLayout(self.body)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 30:
            self.drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag_from is not None:
            self.move(e.globalPosition().toPoint() - self.drag_from)

    def mouseReleaseEvent(self, e):
        self.drag_from = None
