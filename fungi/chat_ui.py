"""聊天界面：气泡、输入框、聊天设置。"""
from __future__ import annotations

import time

from PyQt6.QtCore import pyqtSignal, QObject, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QFontMetrics, QGuiApplication, QPainter
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                             QVBoxLayout, QWidget)

from .config import CHAT_BASE_URL, CHAT_MODELS, INK, PAPER
from .ui import ui_font
from .creature_view import DUST


class ChatBridge(QObject):
    """后台线程把回复送回界面线程"""
    done = pyqtSignal(str, str, str, str, str)           # 菌 id, 你说的话, 回复, 错误, 这一句的说法


class SpeechBubble(QWidget):
    """宠物头顶的像素对话气泡：自动换行、跟着宠物走、几秒后淡出，鼠标穿透"""
    MAX_W, PAD, TAIL = 240, 10, 8

    def __init__(self, owner: "CreatureWidget", text: str, error: bool = False):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("fungi · bubble")
        self.owner, self.text, self.error = owner, text, error
        self.font_ = ui_font(13)
        box = QFontMetrics(self.font_).boundingRect(QRect(0, 0, self.MAX_W, 2000),
                                                    int(Qt.TextFlag.TextWrapAnywhere), text)
        self.text_rect = QRect(self.PAD, self.PAD, box.width() + 2, box.height())
        self.resize(box.width() + 2 + 2 * self.PAD, box.height() + 2 * self.PAD + self.TAIL)
        self.t0, self.dur = time.time(), min(10.0, 3.0 + len(text) * 0.15)
        self.done = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)
        self.follow()

    def follow(self):
        o = self.owner
        try:
            x = o.x() + o.sprite_rect.center().x() - self.width() / 2
            y = o.y() + o.sprite_rect.y() - self.height() + 2
        except RuntimeError:                              # 宠物窗口已经没了
            self.finish()
            return
        screen = QGuiApplication.screenAt(QPoint(int(x + self.width() / 2), int(y + self.height()))) or QGuiApplication.primaryScreen()
        if screen:
            a = screen.availableGeometry()
            x = min(max(x, a.left() + 4), a.right() - self.width() - 4)
            y = max(y, a.top() + 4)
        self.move(int(x), int(y))

    def finish(self):
        self.done = True
        self.timer.stop()
        self.close()

    def tick(self):
        k = time.time() - self.t0
        if k > self.dur:
            self.finish()
            return
        self.setWindowOpacity(1.0 if k < self.dur - 0.6 else max(0.0, (self.dur - k) / 0.6))
        self.follow()

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height() - self.TAIL
        p.fillRect(2, 0, w - 4, h, INK)                   # 描边（切掉四角，像素圆角）
        p.fillRect(0, 2, w, h - 4, INK)
        p.fillRect(2, 2, w - 4, h - 4, PAPER)
        cx = w // 2
        for i, half in enumerate((6, 4, 2)):             # 往下的小尾巴
            p.fillRect(cx - half, h - 2 + i * 3, 2 * half, 3, INK)
            if half > 2:
                p.fillRect(cx - half + 2, h - 2 + i * 3, 2 * half - 4, 3, PAPER)
        p.setPen(DUST if self.error else INK)
        p.setFont(self.font_)
        p.drawText(self.text_rect, int(Qt.TextFlag.TextWrapAnywhere | Qt.AlignmentFlag.AlignLeft), self.text)
        p.end()


class ChatLine(QLineEdit):
    def __init__(self, box: "ChatInput"):
        super().__init__(box)
        self.box = box

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.box.close()
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        QTimer.singleShot(150, self.box.close)


class ChatInput(QWidget):
    """宠物脚下弹出的输入框：回车发送，Esc / 点别处关闭"""

    def __init__(self, colony: "Colony", owner: "CreatureWidget"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("fungi · chat")
        self.colony, self.owner = colony, owner
        self.line = ChatLine(self)
        self.line.setMaxLength(200)
        self.line.setPlaceholderText(f"对 {owner.c.name} 说…（回车发送）" if colony.chat_ready()
                                     else f"对 {owner.c.name} 说…（没接 API，它用自己的话回你）")
        self.line.setStyleSheet(f"QLineEdit {{ background:{PAPER.name()}; color:{INK.name()}; border:2px solid {INK.name()};"
                                f" padding:4px 6px; font-family:'DejaVu Sans Mono','Consolas','Noto Sans CJK SC','Microsoft YaHei UI',monospace;"
                                f" font-size:13px; font-weight:bold; selection-background-color:{INK.name()}; }}")
        self.line.returnPressed.connect(self.send)
        self.resize(260, 34)
        self.line.setGeometry(0, 0, 260, 34)
        r = owner.sprite_rect
        self.move(int(owner.x() + r.center().x() - 130), int(owner.y() + r.bottom() + 6))

    def send(self):
        text = self.line.text()
        self.close()
        self.colony.send_chat(self.owner, text)

    def popup(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.line.setFocus()


class ChatSettings(QDialog):
    def __init__(self, colony: "Colony"):
        super().__init__(None)
        self.setWindowTitle("聊天设置 · FUNGI.EXE")
        self.colony = colony
        cfg = colony.chat_cfg
        self.key = QLineEdit(cfg.get("api_key", ""))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-…（也可以设环境变量 DEEPSEEK_API_KEY）")
        self.base = QLineEdit(cfg.get("base_url") or CHAT_BASE_URL)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(CHAT_MODELS)
        self.model.setCurrentText(cfg.get("model") or CHAT_MODELS[0])
        self.thinking = QCheckBox("思考模式（回答更慢、更贵，一句话闲聊一般不需要）")
        self.thinking.setChecked(bool(cfg.get("thinking")))
        form = QFormLayout()
        form.addRow("API Key", self.key)
        form.addRow("接口地址", self.base)
        form.addRow("模型", self.model)
        form.addRow("", self.thinking)
        note = QLabel(f"Key 只保存在本机：{colony.chat_path}（仅本人可读）。\n"
                      "发给 DeepSeek 的是你说的话、菌落的状态；它打嗝的那一句会带上一块残渣（吃掉的某个文件名或第一行字）。")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addLayout(form)
        box.addWidget(note)
        box.addWidget(buttons)
        self.setStyleSheet(f"QDialog {{ background:{PAPER.name()}; }} QLabel, QCheckBox {{ color:{INK.name()}; }}"
                           f" QLineEdit, QComboBox {{ background:white; color:{INK.name()}; border:2px solid {INK.name()}; padding:3px; }}")
        self.resize(460, 0)

    def accept(self):
        self.colony.save_chat_cfg({"api_key": self.key.text().strip(), "base_url": self.base.text().strip() or CHAT_BASE_URL,
                                   "model": self.model.currentText().strip() or CHAT_MODELS[0],
                                   "thinking": self.thinking.isChecked()})
        super().accept()
