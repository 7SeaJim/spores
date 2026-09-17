"""菌落 · 菜单和托盘。"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from .config import ADULT, FOOD_KINDS, MOOD_NAMES
from .sprites import art_pixmap
from .ui import MENU_QSS
from .creature_view import CreatureWidget
from .mat import Mycelium


class MenuMixin:
    """右键菜单、设置、托盘和几个全局操作"""

    def settings_menu(self, parent: QMenu) -> QMenu:
        sub = parent.addMenu("设置")
        sub.setStyleSheet(MENU_QSS)
        top = sub.addAction("总在最前")
        top.setCheckable(True)
        top.setChecked(self.on_top)
        top.toggled.connect(self.set_on_top)
        devour = sub.addAction("吞噬文件（吃掉后" + ("进回收站）" if sys.platform == "win32" else "删除）"))
        devour.setCheckable(True)
        devour.setChecked(self.devour)
        devour.toggled.connect(self.set_devour)
        self.mat_menu(sub)
        sub.addAction("聊天设置…" + ("" if self.chat_ready() else "（未设置）"), self.chat_settings)
        return sub

    # ── 菜单 ──
    def show_menu(self, widget: CreatureWidget, pos: QPoint):
        m = self.pet_menu(widget)
        m.exec(pos)

    def pet_menu(self, widget: CreatureWidget) -> QMenu:
        """右键菜单：一行概况 → 照顾它 → 撒盐 → 设置 / 退出。详细数值都在状态面板里。"""
        c = widget.c
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        mood = MOOD_NAMES[c.mood]
        head = [c.name] + ([c.stage_name] if c.stage_name != c.name else []) + [f"饱 {int(c.satiety)}%"] + ([mood] if mood else [])
        m.addAction(" · ".join(head)).setEnabled(False)
        m.addSeparator()
        m.addAction("状态面板…", lambda: self.open_panel(widget))
        m.addAction("聊天…", lambda: self.open_chat(widget))
        feed = m.addMenu("喂食")
        feed.setStyleSheet(MENU_QSS)
        feed.addAction("文件…", lambda: self.feed_dialog(widget, folder=False))
        feed.addAction("文件夹…", lambda: self.feed_dialog(widget, folder=True))
        m.addSeparator()
        m.addAction("撒盐…", self.start_salt)
        m.addAction("叫大家回来", self.gather)
        m.addSeparator()
        self.settings_menu(m)
        if len(self.creatures) > 1:
            m.addAction(f"放生 {c.name}…", lambda: self.release(widget))
        m.addAction("退出", QApplication.instance().quit)
        return m

    def feed_dialog(self, widget: CreatureWidget, folder: bool):
        if folder:
            d = QFileDialog.getExistingDirectory(None, "喂一个文件夹", str(Path.home()))
            paths = [Path(d)] if d else []
        else:
            pattern = " ".join(f"*{e}" for e in FOOD_KINDS)
            files, _ = QFileDialog.getOpenFileNames(None, "喂点文字", str(Path.home()), f"文字 ({pattern});;全部文件 (*)")
            paths = [Path(f) for f in files]
        if paths:
            self.feed(widget, paths)

    def set_on_top(self, on: bool):
        self.on_top = on
        for w in self.widgets.values():
            w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
            w.masked = None
            w.show()
            w.update_mask()
        self.save()

    def gather(self):
        screen = QGuiApplication.primaryScreen()
        a = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        for i, c in enumerate(self.creatures):
            row, col = divmod(i, 6)
            w = self.widgets[c.id]
            w.fly((c.x, c.y), (a.right() - 120 - col * 130, a.bottom() - 20 - row * 150), delay=i * 0.08, dur=0.6)

    def release(self, widget: CreatureWidget):
        c = widget.c
        ok = QMessageBox.question(None, "放生", f"让 {c.name}（{c.stage_name}）离开桌面？\n它的成长记录会被删除。")
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.creatures.remove(c)
        self.widgets.pop(c.id).close()
        if c.id in self.panels:
            self.panels.pop(c.id).close()
        self.log_event(f"{c.name} 被放生，离开了桌面")
        if self.memory.pop(c.id, None) is not None:
            self.save_memory()
        self.save()

    def reset(self):
        ok = QMessageBox.question(None, "重新开始", "清空整个菌落，从一个 3×3 spores 重新开始？")
        if ok != QMessageBox.StandardButton.Yes:
            return
        for w in self.widgets.values():
            w.close()
        self.widgets.clear()
        self.creatures, self.name_i = [], 0
        for f in self.fields.values():
            f.mat = Mycelium(f.mat.cols, f.mat.rows)
        self.stash = {}
        self.spitter, self.patches = None, []
        self.chronicle, self.moods, self.mat_mark, self.memory = [], {}, 0.0, {}
        self.save_memory()
        self.build_mat_views()
        c = self.new_creature(*self.default_spot())
        self.creatures.append(c)
        self.log_event(f"{c.name} 在桌面上冒了出来")
        self.spawn_widget(c)
        self.save()

    def make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(QIcon(art_pixmap(ADULT)))
        tray.setToolTip("FUNGI.EXE")
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        m.addAction("撒盐…", self.start_salt)
        m.addAction("叫大家回来", self.gather)
        m.addSeparator()
        self.settings_menu(m)
        m.addAction("重新开始…", self.reset)
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        m.setToolTip(f"存档：{self.save_path}")
        tray.setContextMenu(m)
        tray.show()
        tray._menu = m
        return tray
