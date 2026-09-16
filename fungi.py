#!/usr/bin/env python3
"""
FUNGI.EXE — 桌面真菌宠物（最小闭环 demo）

    桌面上的 3×3 黑色 spores → 拖入 .txt / .md / 文件夹 → 长大 → 变成蘑菇 → 放出新的 spores

运行:
    python3 fungi.py                 正常模式
    python3 fungi.py --fast          调试：成长加速（自然生长 ×30，喂食营养 ×3）
    python3 fungi.py --data-dir DIR  使用单独的存档目录

操作:
    拖文件 / 文件夹到它身上 = 喂食：能吃的 .txt / .md 会被真的吃掉（Linux 等直接删除，Windows 移到回收站），
                                  吃掉的完整路径记在存档目录的 eaten.log；右键可关掉「吞噬文件」
    左键拖动 = 搬家    单击 = 戳一下    双击 = 聊天（DeepSeek，自己填 API Key）    右键 = 状态和菜单
"""
from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import math
import os
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import asdict, dataclass, field, fields
from functools import lru_cache
from pathlib import Path

try:
    import fcntl
except ImportError:                      # Windows
    fcntl = None

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QTransform, QFont, QFontMetrics, QGuiApplication, QIcon,
                         QImage, QPainter, QPainterPath, QPen, QPixmap, QRegion)
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton,
                             QSlider, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

# ───────────────────────────── 可调参数 ─────────────────────────────

PX = 4                     # 1 个像素格 = 多少屏幕像素
TEXT_BAND = 38             # 精灵上方给文字/动画留的高度
BOTTOM_PAD = 6
MIN_WIN_W = 170
USE_INPUT_MASK = True      # 空闲时把窗口形状收缩到精灵附近，不挡桌面点击

STAGES = [                 # (名字, 进入该阶段需要的累计营养)
    ("spores", 0),
    ("sprout", 20),
    ("baby", 50),
    ("young", 100),
    ("adult", 170),
]
ADULT = len(STAGES) - 1
FIRST_BURST = 2            # 刚成年时放出的 spores 数
SPORE_EVERY = 60           # 成年后每再吃这么多营养，放出 1 个 spores
MAX_COLONY = 12

PASSIVE_SECONDS = 120      # 每隔多少秒自然 +1 营养
OFFLINE_CAP = 24           # 关闭程序期间最多补多少营养

# 待机动效
IDLE_RIG = {               # 阶段: (菌盖行数, 呼吸时抽掉的菌柄行) —— 对应 art/<阶段>.pxl 的行号
    1: (7, 8),
    2: (10, 14),
    3: (14, 20),
    4: (19, 27),
}
BREATH_PERIOD = 3.2        # 呼吸周期（秒），睡着时 ×1.6
IDLE_GAP = (6, 16)         # 两个待机小动作之间隔几秒
SLEEP_AFTER = 600          # 多少秒没人理就睡着；深夜 0–7 点缩短到 1/5
HUNGRY_IDLE = {            # 饿的时候：不蹦跶，嘟囔
    "hungry": [("grumble", 2), ("blink2", 2), ("tilt", 1)],
    "starving": [("grumble", 3), ("blink2", 1)],
}
IDLE_WEIGHTS = {           # 阶段: [(动作, 权重)]
    0: [("wiggle", 3), ("hop", 1)],
    1: [("tilt", 2), ("hop", 2), ("blink2", 2)],
    2: [("tilt", 3), ("hop", 2), ("blink2", 2)],
    3: [("tilt", 3), ("hop", 1), ("blink2", 2)],
    4: [("tilt", 3), ("hop", 1), ("blink2", 2), ("puff", 3)],
}

# 菌毯（沿桌面可用区域的四条边生长）
MAT_MAX = 8                # 最厚多少格（每格 PX 像素，8 格 = 32px）
MAT_STRIP = MAT_MAX + 7    # 边缘窗口厚度（格）：菌毯 + 菌丝 + 小蘑菇，共 60px
MAT_TICK = 5.0             # 每隔多少秒长一次
MAT_VIGOR = (0.3, 0.6, 1.0, 1.5, 2.5)   # 各阶段的活力
MAT_SEED = 0.03            # 每次、每单位活力，在离它最近的边缘落孢子的概率
MAT_RATE = 0.35            # 每次、每单位活力的生长步数
MAT_FEED = 0.5             # 喂食时每 1 营养让最近那段菌毯多长几步
MAT_OFFLINE_CAP = 20000    # 关闭期间最多补长多少步
MAT_SPROUT_DEPTH = 6       # 菌毯厚到多少格才会冒小蘑菇

# 喷孢菌：菌毯占到一定范围后长出来，不会动，定期往随机处喷孢子
SPITTER_AT = 0.25          # 屏幕边缘一圈被菌毯占到多少长度时长出来
SPITTER_EVERY = (300, 600) # 喷射间隔（秒），5–10 分钟
SHOT_OUTCOMES = (("vanish", 0.75), ("mat", 0.20), ("spore", 0.05))   # 落地：消失 / 形成菌毯 / 变成独立小孢子
PATCH_MAX = (3, 7)         # 桌面中间的菌斑最多长到多大半径（格），每块随机
PATCH_GROW = 0.04          # 菌斑每次（MAT_TICK）长多少格半径
MAX_PATCHES = 30
OUTCOME_NAMES = {"vanish": "消失", "mat": "菌毯", "spore": "孢子"}

# 饥饿：不喂食的话会饿，饿扁了会缩回去，最后变成休眠孢子（喂一次就醒）
SATIETY_MAX = 100
HUNGER_HOURS = 16          # 从吃饱到饿扁要多少小时
HUNGRY_AT = 30             # 饱腹低于这个就是「饿」：生长减半，会嘟囔
SATIETY_PER_FOOD = 4       # 每 1 营养加多少饱腹
STARVE_LOSS = 6            # 饿扁（饱腹 0）后每小时掉多少营养
OFFLINE_STARVE_CAP = 36    # 关闭程序期间最多饿掉多少营养
MAT_RECEDE = 0.3           # 全体饿扁时，边缘菌毯每次（MAT_TICK）退缩几步
MOOD_NAMES = {"full": "", "hungry": "饿", "starving": "饿扁了", "dormant": "休眠"}

DEVOUR = True              # 默认吞噬文件（右键菜单可关）
PROJECT_DIR = Path(__file__).resolve().parent

# 聊天：DeepSeek（OpenAI 兼容的 /chat/completions），API Key 由用户自己填
CHAT_BASE_URL = "https://api.deepseek.com"
CHAT_MODELS = ("deepseek-flash", "deepseek-v4-pro")
CHAT_MAX_CHARS = 34        # 一句话最多几个字，超出截断
CHAT_HISTORY = 6           # 每次聊天带上最近几轮对话
CHAT_MEMORY = 40           # 每只菌在 memory.json 里最多记多少条消息
CHRONICLE_MAX = 200        # 菌落大事记最多记多少条（存进 save.json）
CHAT_SESSION_GAP = 1800    # 两次聊天隔多少秒算新的一段对话
FILEGU_MECHANISMS = ("岔轨", "私有常识", "突然具体", "消化中")
FILEGU_NORMAL = 0.3        # 正常回答的基础概率（程序按最近 10 句的实际比例微调，保持十句两三句）
FILEGU_LUCID = 0.05        # 一段对话里「落点句」的概率（每段最多一次）
FILEGU_BURP = 0.2          # 有残渣时，聊天打嗝的概率
CHAT_TEMPERATURE = 1.0     # 电波要走神但不能散架：1.3 时句子容易糊成一团
LUCID_LINES = ("你其实不是想删掉它，你是想有人替你留着。", "我知道你不是真的在问蘑菇。")   # 落点句：程序直接说，不交给模型
LUCID_ABOUT_FILES = re.compile(r"删|整理|清理|收拾|扔|丢|文件|桌面|归档|备份")
LUCID_TRIGGER = re.compile(r"(?<![a-z])ai(?![a-z])|人工智能|机器人|程序|模型|假的|真的吗|真的假的|你是真的|chatgpt|deepseek|gpt", re.I)
RESIDUE_MAX = 30           # 每只菌最多留多少块残渣（residue.json）
FILEGU_SAMPLES = (
    "我今天吃了半层回收站，有点撑，噗。",
    "你桌面右下角那团，已经放到很好吃的程度了。",
    "嗯咕……啊，你问的是上上次备份的事。",
    "那个文件夹已归档了，干巴巴的，我们不碰。",
    "稍等，正在解压，里面套了四层。",
    "还有一次备份我就满了，所以现在就说：你很好。",
    "我刚才想起来一个很重要的文件名，然后它掉缓存里了。",
)
FILEGU_ECHOES = ("……啊，有个 .tmp 在动。", ".log 比 .txt 甜，这个你知道吧。", "你说的这个词，在我们这儿是一条路径，我顺着走了一会儿。")   # 以前写在规则里的例句，也不许复读
FILEGU_STYLE = """【你是一只文件菇 · 说话方式：电波单句】
食性：以未整理的文件为食——你吃的不是内容，是混乱。命名混乱、重复、久未打开的最好吃；整齐的目录干巴巴；加密的硬；云端的飘着够不到。
你嘴小，真正咬得动的只有 .txt 和 .md，别的文件只能闻闻。被你吃掉的文件就没了（在 Windows 上会先落进回收站）。
量词：一小口缓存、两指头临时文件、半层回收站、三个重复项那么远。夸东西用「未整理的」「松的」「久放的」；贬义只有一个词：「已归档。」
时间：以「上次备份」为历法，比如「上上次备份的时候」「还有一次备份我就满了」。
走神有四种方式：
1. 岔轨：揪住对方话里一个不重要的词，把它当成一件真东西，顺着它走远。
2. 私有常识：说得理所当然，像全世界都知道——但只有文件菇知道。
3. 突然具体：抛出一个精确到离谱的细节——只能是你眼下真看得见的东西。
4. 消化中：说到一半卡住，说自己正在解压。
残留：你吃完会打嗝，嗝出前主人的碎片——一个文件名、一行字。它不是数据库，它是残渣。
不许编文件名：除了【这一句】里明确给你的那块残渣，不要说出任何具体的文件名、网址、程序名；说到文件只说「那团」「那个文件夹」，或者 .txt、.log、.tmp 这样的后缀。
硬规则：一句话，不解释，不找补。自称可以是「我」或「我们」。
禁止：叠词、感叹号、颜文字；不说「数据」「算力」「赛博」这类词——你不懂这些，你只懂好不好吃。
不许编：时间、钟点、日期、数字、地名、别人的经历——资料里没有的就说不知道，也别编得像真事。
下面的资料是你心里模模糊糊知道的事：可以提到，但不要照着念，不要报数字、百分比、代数、坐标、英文代号。走神归走神，说到菌落里的成员和发生过的事时，不要和资料矛盾。
对方的话后面会跟一行【这一句】，那是只给你看的说话提示：照做，但不要复述它。
语气样本（只是语气参考，读完就忘：不许照抄，也不许套它们的句式和词）：
""" + "\n".join("- " + x for x in FILEGU_SAMPLES)
STYLE_DIRECTIVES = {
    "normal": "正常、认真地回答对方（还是文件菇的口吻，一句话）。",
    "岔轨": "用「岔轨」：揪住对方话里一个不重要的词，把它当成一件真东西顺着走远。",
    "私有常识": "用「私有常识」：理所当然地说一条只有文件菇知道的常识。",
    "突然具体": "用「突然具体」：只说这一件真事——{fact}；不许另外加场景、位置、文件、时间和数字。",
    "突然具体+": "用「突然具体」：抛出一个精确到离谱的细节，就用这块残渣——{year} 年的文件「{name}」；别的时间数字都不要编。",
    "消化中": "用「消化中」：说到一半卡住，说自己正在解压。",
    "burp": "打个嗝，嗝出这块残渣：「{frag}」，格式像「（嗝）……「{frag}」。」，可以接半句，但不解释是谁的。",
    "return": "立刻岔回去，像什么都没发生过：说一件跟刚才毫不相干的小事。",
}
# 没接 API（或连不上）时的本地回复：同样是文件菇·电波，素材来自存档；台词是新写的，不复用上面的样本
OFFLINE = "__offline__"
LOCAL_DETOUR = ("「{w}」闻着是松的，我们先把它埋进缓存里。", "{w}要是往下长，我们早就在它底下了。",
                "把「{w}」放久一点，它会自己变软。", "{w}？是菌毯边上刚冒出来的那一小团吧。")
LOCAL_LORE = (".md 放久了会发酥，这个你知道吧。", "重名的文件比单独一个的香一点。", "回收站里的东西是凉的，要焐一会儿才好吃。",
              "文件夹套得越深，里面越潮。", "改名叫「最终版」的，通常还会再长一层。")
LOCAL_DIGEST = ("嗯咕……等等，这一口还卡着没化开。", "里面又包了一层，得慢慢解。", "咬到一个很大的，先别说话。", "这团还在往下沉，等它沉到底。")
LOCAL_RETURN = ("……刚才菌丝那头动了一下。", "嗯？边上的菌毯又往前挪了一格。", "……你那边是不是起风了。")
LOCAL_NORMAL = ("嗯，我们在听，菌丝都朝着你这边。", "知道了，先放在我们底下那层。", "好，我们记着，记在菌毯最里面。")
LOCAL_STOP = re.compile(r"[你我他她它的了吗呢吧啊么是在这那个什怎为不要会想有没就都也还很]")


def pick_word(text: str, rng=random) -> str:
    """从对方的话里揪一个词（岔轨用）：两字的中文片段或英文单词，跳过虚词"""
    words = []
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", text or ""):
        if chunk.isascii():
            words.append(chunk)
            continue
        words += [chunk[i:i + 2] for i in range(len(chunk) - 1) if not LOCAL_STOP.search(chunk[i:i + 2])]
    return rng.choice(words) if words else ""


# 口味：命名乱 / 重复 / 放得久 = 肥；已归档 = 干；云盘里的够不到
MESSY_NAME = re.compile(r"\(\d+\)|（\d+）|副本|复件|copy|final|最终|终版|定稿|真的|v\d+|新建|untitled|未命名|无标题|temp|tmp|旧|old|"
                        r"备份|bak|请勿删除|勿删|待办|草稿|draft|asdf|aaa|qwe|test|测试|\d{6,}", re.I)
ARCHIVED_NAME = re.compile(r"归档|已整理|archive", re.I)
CLOUD_DIRS = {"dropbox", "google drive", "googledrive", "icloud drive", "iclouddrive", "icloud", "nutstore", "坚果云",
              "baidunetdisk", "百度网盘", "nextcloud", "owncloud", "seafile", "mega", "box", "坚果云同步"}
CHRONICLE_IN_PROMPT = 20   # 聊天时带上最近几条大事记
MAT_MILESTONES = (0.1, 0.25, 0.5, 0.75, 1.0)
STAGE_CN = {"spores": "刚冒出来的小黑点", "sprout": "刚长出菌盖的小芽", "baby": "圆胖的幼年小菇",
            "young": "长出了小手小脚的少年蘑菇", "adult": "会放孢子的成年蘑菇"}
MOOD_CN = {"full": "吃饱了", "hungry": "有点饿", "starving": "饿扁了、很虚弱", "dormant": "在休眠"}
CHAT_TIMEOUT = 20          # 秒
CHAT_COOLDOWN = 3.0        # 同一只菌两次发话至少隔几秒
CHAT_BURST = (15, 600)     # 整个菌落每 600 秒最多调几次 API（防止狂点烧额度；本地说的落点句不算）
CHAT_ERRORS = {400: "请求格式不对", 401: "API Key 不对", 402: "DeepSeek 余额不足", 422: "参数不对，检查模型名",
               429: "说太快了，等等", 500: "DeepSeek 那边出错了", 503: "DeepSeek 太忙了"}

# 精力、快乐（设计图里的 ENERGY / HAPPINESS）
ENERGY_DRAIN = 4           # 醒着每小时掉多少精力
ENERGY_REST = 20           # 睡着（或关着程序）每小时回多少精力
HAPPY_DECAY = 2            # 每小时自然掉多少快乐；饿扁时再多掉 STARVE_GLOOM
STARVE_GLOOM = 8
OFFLINE_GLOOM_CAP = 30     # 关闭期间快乐最多掉多少
TIRED_AT = 25              # 精力低于这个：更容易困、小动作变少
GLOOMY_AT = 30             # 快乐低于这个：会嘟囔
CHEERY_AT = 70             # 快乐高于这个：更爱蹦跶
CARE_COOLDOWN = 1800       # 浇水、晒太阳的冷却（秒）
CARE_AMOUNT = 20           # 浇水 +精力，晒太阳 +快乐
SPEED_RANGE = (0.25, 3.0)  # CONFIG 里成长速度、饥饿速度的范围

# 后缀: (营养倍率, 每 1 营养加多少精力, 每 1 营养加多少快乐, 飘字)
FOOD_KINDS = {
    ".txt": (1.0, 0.0, 0.0, ""),
    ".md": (1.0, 0.0, 0.0, ""),
    ".log": (0.6, 2.5, 0.0, "精力+"),
    ".poem": (0.6, 0.0, 2.5, "快乐+"),
    ".todo": (1.5, 0.0, 0.0, "成长+"),
    ".secret": (1.0, 0.0, 0.0, "？？？"),
}
LOG_HOT_MINUTES = 10       # 这么多分钟内还在被写的 .log 不吃（可能是正在用的日志）
EDIBLE_EXT = set(FOOD_KINDS)
MAX_ITEMS_PER_DROP = 5

NAMES = ["puff", "kino", "shii", "enoki", "morel", "nameko", "maitake", "chanty",
         "porcini", "reishi", "shimeji", "inky", "bolete", "truffle", "oyster"]

ART_DIR = Path(__file__).resolve().parent / "art"   # pixel4ai 画稿（.pxl），缺失时回退到程序生成

INK = QColor(17, 17, 17)
PAPER = QColor(250, 249, 244)

# ───────────────────────────── 像素精灵 ─────────────────────────────
# 优先读 art/<阶段>.pxl、<阶段>_blink.pxl、<阶段>_eat.pxl；没有画稿时用下面的程序生成
# 程序生成的字符: "#" 黑, "o" 白, "." 透明
HALO = "\x01"

MUSHROOM_SHAPES = {        # 阶段: (菌盖宽, 菌盖高, 菌柄宽, 菌柄高)
    1: (11, 5, 7, 4),
    2: (15, 7, 7, 6),
    3: (21, 10, 9, 8),
    4: (27, 13, 11, 10),
}
CAP_ROUNDNESS = 2.6
SPOTS = {                  # 阶段: [(横向 -1..1, 纵向 0..1, 大小)]
    1: [(-0.5, 0.6, 1), (0.35, 0.3, 1)],
    2: [(-0.5, 0.6, 1), (0.1, 0.3, 2), (0.55, 0.65, 1)],
    3: [(-0.55, 0.62, 2), (0.05, 0.28, 3), (0.55, 0.55, 2), (-0.15, 0.8, 1)],
    4: [(-0.6, 0.62, 3), (0.0, 0.25, 4), (0.58, 0.55, 3), (-0.22, 0.82, 2), (0.3, 0.85, 1)],
}


def spot_cells(size: int) -> list[tuple[int, int]]:
    if size == 1:
        return [(0, 0)]
    if size == 2:
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    if size == 3:
        return [(0, -1), (-1, 0), (0, 0), (1, 0), (0, 1)]
    return [(x, y) for x in range(-1, 3) for y in range(-1, 3) if (x, y) not in {(-1, -1), (2, -1), (-1, 2), (2, 2)}]


def spore_art(n: int) -> list[str]:
    return ["#" * n] * n


def mushroom_art(stage: int, blink: bool = False, mouth: bool = False) -> list[str]:
    w, ch, sw, sh = MUSHROOM_SHAPES[stage]
    height = ch + sh
    g = [["."] * w for _ in range(height)]
    cx = w // 2

    # 菌盖：超椭圆圆顶
    for y in range(ch):
        t = (ch - y - 0.5) / ch
        hw = (w / 2) * (1 - t ** CAP_ROUNDNESS) ** (1 / CAP_ROUNDNESS)
        for x in range(w):
            if abs(x - cx) < hw:
                g[y][x] = "#"

    # 菌盖上的白点
    for u, v, size in SPOTS[stage]:
        sy = round(v * (ch - 1))
        row = [x for x in range(w) if g[sy][x] == "#"]
        if not row:
            continue
        half = (row[-1] - row[0]) / 2
        sx = round(cx + u * (half - 1))
        cells = [(sx + dx, sy + dy) for dx, dy in spot_cells(size)]
        ok = all(0 < x < w - 1 and 0 < y < ch - 1 and
                 all(g[y + dy][x + dx] in "#o" for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                 for x, y in cells)
        if ok:
            for x, y in cells:
                g[y][x] = "o"

    # 菌柄：底部略微外扩，外轮廓黑、内部白
    stem = set()
    for i in range(sh):
        half = sw // 2
        if sw >= 7 and i == sh - 1:
            half += 1
        if sw >= 9 and i >= sh - 2:
            half += 1
        for x in range(cx - half, cx + half + 1):
            stem.add((x, ch + i))
    for x, y in stem:
        edge = y == height - 1 or any((x + dx, y + dy) not in stem and not (y + dy < ch and g[y + dy][x + dx] == "#")
                                      for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        g[y][x] = "#" if edge else "o"

    # 脸
    eye_dx = 1 if sw <= 7 else 2
    eye_h = 2 if sh >= 8 else 1
    ey = ch + max(1, round(sh * 0.25))
    for dx in (-eye_dx, eye_dx):
        for k in range(eye_h):
            if blink and k < eye_h - 1 or (blink and eye_h == 1):
                continue
            g[ey + k][cx + dx] = "#"
    if mouth:
        my = ey + eye_h + (1 if sh >= 6 else 0)
        mw = 0 if sw <= 5 else 1
        mh = 2 if sh >= 8 else 1
        for dx in range(-mw, mw + 1):
            for k in range(mh):
                if my + k < height - 1:
                    g[my + k][cx + dx] = "#"
    return ["".join(r) for r in g]


@lru_cache(maxsize=64)
def load_pxl(name: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]] | None:
    """读 art/<name>.pxl（JSON: size / palette / rows）。没有或损坏时返回 None。"""
    try:
        data = json.loads((ART_DIR / f"{name}.pxl").read_text("utf-8"))
        rows = tuple(data["rows"])
        palette = tuple((ch, hexc) for ch, hexc in data["palette"].items() if hexc)
        if not rows or any(len(r) != len(rows[0]) for r in rows):
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return rows, palette


def add_halo(art: list[str]) -> list[str]:
    h, w = len(art), len(art[0])
    out = [["."] * (w + 2) for _ in range(h + 2)]
    for y in range(h):
        for x in range(w):
            out[y + 1][x + 1] = art[y][x]
    for y in range(h + 2):
        for x in range(w + 2):
            if out[y][x] != ".":
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy - 1, x + dx - 1
                    if 0 <= yy < h and 0 <= xx < w and art[yy][xx] != ".":
                        out[y][x] = HALO
    return ["".join(r) for r in out]


def pose(art: list[str], cap_rows: int, breath_row: int, breath: bool, tilt: int) -> list[str]:
    """待机姿势。左右各留 1 列给歪头；呼吸时抽掉一行菌柄、顶部补一行，脚底不动、菌盖和脸下沉 1 格。"""
    rows = ["." + r + "." for r in art]
    if tilt:
        for i in range(min(cap_rows, len(rows))):
            rows[i] = "." + rows[i][:-1] if tilt > 0 else rows[i][1:] + "."
    if breath and 0 <= breath_row < len(rows):
        del rows[breath_row]
        rows.insert(0, "." * len(rows[0]))
    return rows


def spore_size(nutrition: float) -> int:
    """spores 阶段内部：3×3 → 4×4 → 5×5，方块慢慢变大"""
    return min(5, 3 + int(3 * nutrition / STAGES[1][1]))


def withered(colors: dict[str, QColor]) -> dict[str, QColor]:
    """饿扁了：白的发灰、黑的褪成灰褐"""
    out = {}
    for ch, c in colors.items():
        light = c.lightness()
        out[ch] = (QColor(196, 191, 178) if light > 200 else QColor(150, 146, 136) if light > 150
                   else QColor(78, 74, 67) if light < 60 else QColor(110, 106, 98))
    return out


def paint_rows(rows: list[str], colors: dict[str, QColor]) -> QImage:
    img = QImage(len(rows[0]) * PX, len(rows) * PX, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in colors:
                p.fillRect(x * PX, y * PX, PX, PX, colors[ch])
    p.end()
    return img


@lru_cache(maxsize=512)
def art_pixmap(stage: int, size: int = 3, blink: bool = False, mouth: bool = False,
               invert: bool = False, breath: bool = False, tilt: int = 0, wither: bool = False,
               mood: str = "full") -> QPixmap:
    art, colors = None, {"#": INK, "o": PAPER}
    if stage == 0 and mood == "dormant":                 # 休眠孢子：专门的孢囊帧（画稿缺失时退回灰方块）
        drawn = load_pxl("dormant")
        if drawn:
            art, colors, wither = list(drawn[0]), {ch: QColor(hexc) for ch, hexc in drawn[1]}, False
    if stage > 0:
        name = STAGES[stage][0]
        frame = ("_eat" if mouth else "_starving" if mood == "starving" else "_blink" if blink
                 else "_hungry" if mood == "hungry" else "")
        drawn = load_pxl(name + frame) or load_pxl(name)
        if drawn:
            art, colors = list(drawn[0]), {ch: QColor(hexc) for ch, hexc in drawn[1]}
            rig = IDLE_RIG[stage]
    if art is None:
        art = spore_art(size) if stage == 0 else mushroom_art(stage, blink, mouth)
        if stage > 0:
            rig = (MUSHROOM_SHAPES[stage][1], len(art) - 2)
    if stage > 0:
        art = pose(art, rig[0], rig[1], breath, tilt)
    art = add_halo(art)
    if wither:
        colors = withered(colors)
    if invert:
        colors = {ch: QColor(255 - c.red(), 255 - c.green(), 255 - c.blue()) for ch, c in colors.items()}
    halo = QColor(INK if invert else PAPER)
    halo.setAlpha(235)
    colors[HALO] = halo
    return QPixmap.fromImage(paint_rows(art, colors))


# ───────────────────────────── 数据 ─────────────────────────────

@dataclass
class Creature:
    id: str
    name: str
    gen: int = 1
    born: float = field(default_factory=time.time)
    nutrition: float = 0.0
    feeds: int = 0
    released: int = 0                                   # 已放出的 spores 数
    satiety: float = 60.0                               # 饱腹 0–100
    energy: float = 70.0                                # 精力 0–100
    happiness: float = 60.0                             # 快乐 0–100
    watered: float = 0.0                                # 上次浇水 / 晒太阳的时间（冷却用）
    sunned: float = 0.0
    parent: str = ""                                    # 母体的 id；"spitter" = 喷孢菌喷出来的；"" = 菌落始祖
    x: int = 0                                          # 脚底中心的屏幕坐标
    y: int = 0
    eaten: list[str] = field(default_factory=list)      # 吃过的食物指纹（不保存路径）
    log: list[list] = field(default_factory=list)       # [时间戳, 名字, 营养]

    @classmethod
    def from_dict(cls, d: dict) -> "Creature":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def stage(self) -> int:
        return max(i for i, (_, need) in enumerate(STAGES) if self.nutrition >= need)

    @property
    def stage_name(self) -> str:
        return STAGES[self.stage][0]

    @property
    def mood(self) -> str:
        if self.satiety > HUNGRY_AT:
            return "full"
        if self.satiety > 0:
            return "hungry"
        return "dormant" if self.nutrition <= 0 else "starving"

    @property
    def tired(self) -> bool:
        return self.energy < TIRED_AT

    @property
    def spirit(self) -> str:
        """心情：gloomy / plain / cheery"""
        return "gloomy" if self.happiness < GLOOMY_AT else "cheery" if self.happiness > CHEERY_AT else "plain"

    @property
    def growth_factor(self) -> float:
        return {"full": 1.0, "hungry": 0.5}.get(self.mood, 0.0)

    def next_goal(self) -> int:
        if self.stage < ADULT:
            return STAGES[self.stage + 1][1]
        return STAGES[ADULT][1] + SPORE_EVERY * (int((self.nutrition - STAGES[ADULT][1]) // SPORE_EVERY) + 1)

    def stage_floor(self) -> int:
        if self.stage < ADULT:
            return STAGES[self.stage][1]
        return self.next_goal() - SPORE_EVERY

    def spores_due(self) -> int:
        if self.stage < ADULT:
            return 0
        return FIRST_BURST + int((self.nutrition - STAGES[ADULT][1]) // SPORE_EVERY) - self.released


@dataclass
class Food:
    label: str
    value: int
    key: str
    note: str = ""
    targets: list[Path] = field(default_factory=list)   # 吞噬时要吃掉的文件
    taste: list[str] = field(default_factory=list)      # 口味：未整理的 / 久放的 / 干巴巴 / 已归档 …
    energy: float = 0.0                                 # 吃完加多少精力（.log）
    happy: float = 0.0                                  # 吃完加多少快乐（.poem）
    secret: bool = False                                # 里面有 .secret：喂的时候随机抽效果
    kind: str = ""                                      # 飘字：精力+ / 快乐+ / 成长+ / ？？？
    dirs: list[Path] = field(default_factory=list)      # 吃完后尝试清掉的空目录（深的在前）


def find_edible(root: Path, max_depth: int = 3, budget: int = 3000) -> tuple[list[Path], list[Path]]:
    """文件夹里能吃的 txt/md，以及走过的子目录。跳过隐藏项，不跟随目录链接。"""
    files, dirs, stack = [], [], [(root, 0)]
    while stack and budget > 0:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    budget -= 1
                    if budget <= 0:
                        break
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                dirs.append(Path(e.path))
                                stack.append((Path(e.path), depth + 1))
                        elif os.path.splitext(e.name)[1].lower() in EDIBLE_EXT:
                            files.append(Path(e.path))
                    except OSError:
                        pass
        except OSError:
            pass
    return files, sorted(dirs, key=lambda p: len(p.parts), reverse=True) + [root]


def count_edible(root: Path, max_depth: int = 3, budget: int = 3000) -> int:
    return len(find_edible(root, max_depth, budget)[0])


def folder_taste(root: Path, files: list[Path]) -> tuple[float, list[str]]:
    """文件夹的口味：名字或路径里有「归档」就干；里外都整整齐齐、全是新的就干巴巴；越乱越肥"""
    if ARCHIVED_NAME.search(root.name) or any(ARCHIVED_NAME.search(p) for f in files for p in f.relative_to(root).parts[:-1]):
        return 0.5, ["已归档"]
    mults, notes = [], set()
    for f in files[:200]:
        try:
            m, n = taste(str(f.relative_to(root)), f.stat().st_mtime)
        except OSError:
            continue
        mults.append(m)
        notes.update(n)
    own, own_notes = taste(root.name, time.time())
    notes.update(own_notes)
    avg = (sum(mults) / len(mults) if mults else 1.0) + (own - 1.0)
    if avg <= 1.0:
        return 0.7, ["干巴巴"]
    order = ["未整理的", "松的", "久放的", "放了一阵的"]
    return min(1.6, avg), [n for n in order if n in notes][:2]


def taste(name: str, mtime: float, now: float | None = None) -> tuple[float, list[str]]:
    """文件菇的口味（营养倍率, 形容词）：命名乱、重复、放得久的肥；已归档的干"""
    if ARCHIVED_NAME.search(name):
        return 0.5, ["已归档"]
    notes, score = [], 0.0
    hits = len({m.group(0).lower() for m in MESSY_NAME.finditer(name)})
    if hits:
        score += min(2, hits) * 0.25
        notes.append("未整理的" if hits >= 2 else "松的")
    age = ((now or time.time()) - mtime) / 86400
    if age > 365:
        score += 0.35
        notes.append("久放的")
    elif age > 90:
        score += 0.15
        notes.append("放了一阵的")
    return min(1.6, 1.0 + score), notes


def in_cloud(path: Path) -> bool:
    """在同步盘目录里（删了会连带删掉云端）：飘着，够不到"""
    try:
        parts = path.resolve().parts
    except OSError:
        parts = path.parts
    return any(p.lower() in CLOUD_DIRS or p.lower().startswith(("onedrive", "dropbox")) for p in parts)


def residue_of(path: Path) -> dict | None:
    """吃之前留一块残渣：文件名、第一行字、年份"""
    try:
        st = path.stat()
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    line = next((s[:24] for s in (raw.strip().lstrip("#>-*/ ").strip() for raw in head.decode("utf-8", "ignore").splitlines()) if s), "")
    return {"name": path.name[:40], "line": line, "year": time.localtime(st.st_mtime).tm_year, "burped": 0}


def hot_log(path: Path) -> bool:
    """还在被写入的日志：最近 LOG_HOT_MINUTES 分钟改过"""
    try:
        return path.suffix.lower() == ".log" and time.time() - path.stat().st_mtime < LOG_HOT_MINUTES * 60
    except OSError:
        return False


def food_kind(files: list[Path]) -> tuple[float, float, float, bool, str]:
    """按后缀占比折算：(营养倍率, 每营养精力, 每营养快乐, 有没有 .secret, 飘字)"""
    kinds = [FOOD_KINDS[f.suffix.lower()] for f in files if f.suffix.lower() in FOOD_KINDS]
    if not kinds:
        return 1.0, 0.0, 0.0, False, ""
    n = len(kinds)
    mult, energy, happy = (sum(k[i] for k in kinds) / n for i in range(3))
    notes = [k[3] for k in kinds if k[3]]
    note = max(set(notes), key=notes.count) if notes else ""
    return mult, energy, happy, any(f.suffix.lower() == ".secret" for f in files), note


def digest(path: Path) -> tuple[Food | None, str]:
    """把一个路径变成食物。只看 stat 和目录结构，不读文件内容；真正吃掉（删除）由 Colony.devour 做。"""
    try:
        st = path.stat()
    except OSError:
        return None, "够不着…"
    key = hashlib.sha1(f"{os.path.realpath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    if in_cloud(path):
        return None, "飘着，够不到"
    if path.is_dir():
        files, dirs = find_edible(path)
        files = [f for f in files if not hot_log(f)]
        if not files:
            return Food(path.name + "/", 3, key, "空空的", [], dirs=dirs), ""
        mult, notes = folder_taste(path, files)
        kmult, energy, happy, secret, kind = food_kind(files)
        value = max(1, round(min(30, 6 + 2 * len(files)) * mult * kmult))
        return Food(path.name + "/", value, key, "", files, dirs=dirs, taste=notes,
                    energy=value * energy, happy=value * happy, secret=secret, kind=kind), ""
    if path.suffix.lower() in EDIBLE_EXT:
        if hot_log(path):
            return None, "还热着，等它凉一凉"
        if st.st_size == 0:
            return Food(path.name, 2, key, "空的…", [path]), ""
        mult, notes = taste(path.name, st.st_mtime)
        kmult, energy, happy, secret, kind = food_kind([path])
        base = min(20, 4 + int(3 * math.log2(st.st_size / 256 + 1)))
        value = max(1, round(base * mult * kmult))
        return Food(path.name, value, key, "", [path], taste=notes, energy=value * energy, happy=value * happy,
                    secret=secret, kind=kind), ""
    return None, f"不吃 {path.suffix or path.name}"


def devour_file(path: Path) -> None:
    """吃掉一个文件：Windows 移到回收站，其他系统直接删除。失败抛 OSError。"""
    if sys.platform != "win32":
        path.unlink()
        return
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None
    if send2trash:
        try:
            send2trash(str(path))
        except Exception as err:          # send2trash 的异常不一定是 OSError
            raise OSError(str(err)) from err
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path.resolve()) + "\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) or op.fAnyOperationsAborted:
        raise OSError(f"没能移到回收站：{path}")


def foreground_fullscreen() -> bool:
    """Windows：前台是不是别的程序的全屏窗口（看视频、打游戏）。其他系统返回 False。"""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):   # 桌面、任务栏不算
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    rect, info = wintypes.RECT(), MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)) or \
            not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


class ChatError(Exception):
    def __init__(self, message: str, offline: bool = False):
        super().__init__(message)
        self.offline = offline                            # 断网 / 超时：改用本地回复；Key 错、没余额：照实说


def one_sentence(text: str, limit: int = CHAT_MAX_CHARS) -> str:
    """只留第一句话，最多 limit 个字"""
    t = " ".join((text or "").split()).strip()
    pairs = {'"': '"', "“": "”", "「": "」", "『": "』"}
    while len(t) >= 2 and t[0] in pairs and t[-1] == pairs[t[0]]:  # 只剥掉首尾成对的引号，句中的「」留着
        t = t[1:-1].strip()
    m = re.search(r"[。！？!?]+|(?<!\d)\.(?=\s|$)", t)        # 「……」不算句子结束
    if m:
        t = t[:m.end()].strip()
    if len(t) > limit:
        cut = max(t.rfind(p, 0, limit - 2) for p in "，,、；;：: ")
        t = (t[:cut] if cut >= limit // 2 else t[:limit - 2]).rstrip("，,、；;：: ") + "……"
    return t or "……"


FAKE_FILE = re.compile(r"(?<![\w.])[\w\u4e00-\u9fff\-]*[A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff\-]*\.(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,5}(?![\w])")
CODE_NAME = re.compile(r"(?<![\w])[A-Za-z]+(?:_[A-Za-z0-9]+)+(?![\w])")


def scrub_history(text: str) -> str:
    """发给模型的旧对话：它以前说漏的程序数字和编的文件名也洗掉，免得被照着学"""
    t = filegu_clean(text)
    t = re.sub(r"\s*\d+\s*%\s*", "一些", t)
    t = re.sub(r"第\s*\d+\s*代[，,、]?", "", t)
    return t.replace("3×3", "小小的")


def filegu_clean(text: str, allowed: tuple[str, ...] = ()) -> str:
    """去掉复述的提示、编出来的文件名和英文代号（残渣除外）、找补的「呢」「啦」、感叹号、颜文字和 emoji"""
    t = re.sub(r"【这一句】.*$", "", text or "").strip()
    t = FAKE_FILE.sub(lambda m: m.group(0) if any(m.group(0) in a for a in allowed) else "那个文件", t)
    t = CODE_NAME.sub(lambda m: m.group(0) if any(m.group(0) in a for a in allowed) else "那边", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]|[(（][^()（）]{0,6}[＾^ω▽・´`°≧≦][^()（）]{0,6}[)）]", "", t)
    t = re.sub(r"[！!]+", "。", t)
    t = re.sub(r"。{2,}", "。", t)
    t = re.sub(r"[呢啦]+(?=[。？?…]*$)", "", t.strip())
    return t or "……"


def session_styles(history: list[dict], now: float | None = None) -> list[str]:
    """这一段对话（相邻两句隔不到 CHAT_SESSION_GAP）里它用过的说法，新的在前"""
    session, prev = [], time.time() if now is None else now
    for m in reversed([m for m in history if m.get("role") == "assistant"]):
        if prev - m.get("t", 0) > CHAT_SESSION_GAP:
            break
        session.append(m.get("style", "normal"))
        prev = m.get("t", 0)
    return session


def too_similar(reply: str, others) -> bool:
    """回复是不是在照抄语气样本、或者复读自己上一句"""
    strip = lambda t: re.sub(r"[\s，。、！？…「」『』（）()]", "", t or "")
    r = strip(reply)
    if len(r) < 6:
        return False
    for other in others:
        o = strip(other)
        if not o:
            continue
        m = difflib.SequenceMatcher(None, r, o)
        if m.ratio() > 0.55 or m.find_longest_match(0, len(r), 0, len(o)).size >= 8:
            return True
    return False


def pick_style(history: list[dict], has_residue: bool = False, now: float | None = None, rng=random) -> str:
    """这一句怎么说：不连续用同一个机制，十句里两三句正常，有残渣才会打嗝，一段对话最多一次落点句，落点句后立刻岔回去"""
    now = time.time() if now is None else now
    said = [m for m in history if m.get("role") == "assistant"]
    styles = [m.get("style", "normal") for m in said]
    last = styles[-1] if styles else None
    if last == "lucid":
        return "return"
    session = session_styles(history, now)
    if "lucid" not in session and len(session) >= 3 and rng.random() < FILEGU_LUCID:
        return "lucid"
    if has_residue and last != "burp" and rng.random() < FILEGU_BURP:
        return "burp"
    recent = styles[-9:]
    normals = recent.count("normal")
    chance = 0.0 if normals >= 3 else 0.6 if normals == 0 and len(recent) >= 6 else FILEGU_NORMAL
    if rng.random() < chance:
        return "normal"
    return rng.choice([m for m in FILEGU_MECHANISMS if m != last])


def chat_request(cfg: dict, messages: list[dict], timeout: float = CHAT_TIMEOUT) -> str:
    """调 DeepSeek /chat/completions，返回回复文本；出错抛 ChatError（带给用户看的原因）"""
    body = {"model": cfg.get("model") or CHAT_MODELS[0], "messages": messages, "stream": False}
    base = (cfg.get("base_url") or CHAT_BASE_URL).rstrip("/")
    if cfg.get("thinking"):
        body["max_tokens"] = 4000                        # 思考内容也算在 max_tokens 里
    else:
        body.update(max_tokens=120, temperature=CHAT_TEMPERATURE)
        if "deepseek.com" in base:
            body["thinking"] = {"type": "disabled"}      # DeepSeek 默认开思考；一句话闲聊关掉，更快更省
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.get('api_key', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise ChatError(CHAT_ERRORS.get(err.code, f"出错了（{err.code}）")) from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise ChatError("连不上…", offline=True) from err
    except ValueError as err:
        raise ChatError("听不懂回话") from err
    try:
        return data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError) as err:
        raise ChatError("听不懂回话") from err


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

ANIM_DUR = {"eat": 0.8, "shake": 0.5, "poke": 0.4, "grow": 1.2, "land": 0.3, "hop": 0.35,
            "tilt": 1.5, "wiggle": 0.5}
LOUD_ANIMS = {"eat", "shake", "poke", "grow"}    # 会跳出精灵附近的动画，需要整窗绘制
TILT_STEPS = (-1, -1, 0, 1, 1, 0)
DUST = QColor(85, 82, 76)


# ───────────────────────────── 声音（标准库合成 WAV，不依赖 QtMultimedia） ─────────────────────────────

SOUND_RATE = 22050
SOUND_VOLUME = 0.22
SOUND_GAP = 0.12           # 同一个声音最短间隔（秒）
SOUND_VERSION = 1          # 改了合成参数就加一，重新生成 WAV


def tone(freq0: float, freq1: float, dur: float, wave_kind: str = "square", noise: float = 0.0,
         attack: float = 0.004, vol: float = 1.0) -> list[float]:
    """一段滑音：频率从 freq0 滑到 freq1，指数衰减包络"""
    rng = random.Random(7)
    n, out, phase = int(SOUND_RATE * dur), [], 0.0
    for i in range(n):
        k = i / max(1, n - 1)
        phase += (freq0 + (freq1 - freq0) * k) / SOUND_RATE
        x = phase % 1.0
        v = (1.0 if x < 0.5 else -1.0) if wave_kind == "square" else math.sin(2 * math.pi * x)
        if noise:
            v = v * (1 - noise) + rng.uniform(-1, 1) * noise
        env = min(1.0, i / max(1, SOUND_RATE * attack)) * math.exp(-4.0 * k)
        out.append(v * env * vol)
    return out


def rest(dur: float) -> list[float]:
    return [0.0] * int(SOUND_RATE * dur)


SOUND_RECIPES = {
    "eat": lambda: tone(190, 110, 0.06, noise=0.3) + rest(0.05) + tone(170, 100, 0.06, noise=0.3) + rest(0.05) + tone(150, 90, 0.08, noise=0.3),
    "burp": lambda: tone(110, 62, 0.28, noise=0.35, attack=0.03),
    "grow": lambda: sum((tone(f, f, 0.07, vol=0.7) for f in (523, 659, 784, 1046)), []),
    "spores": lambda: tone(700, 1300, 0.05, "sine") + tone(1300, 600, 0.09, "sine"),
    "spit": lambda: tone(900, 280, 0.08, "sine", vol=0.8),
    "poke": lambda: tone(880, 990, 0.04, vol=0.6),
    "deny": lambda: tone(160, 150, 0.08) + rest(0.04) + tone(130, 120, 0.1),
    "water": lambda: tone(380, 720, 0.07, "sine") + rest(0.03) + tone(420, 820, 0.07, "sine"),
    "sun": lambda: tone(660, 660, 0.08, "sine") + tone(880, 880, 0.08, "sine") + tone(990, 990, 0.14, "sine"),
    "salt": lambda: tone(3000, 2200, 0.3, "sine", noise=0.9, attack=0.01, vol=0.8),
}


def write_wav(path: Path, samples: list[float]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SOUND_RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v * SOUND_VOLUME)) * 32767)) for v in samples))
    os.replace(tmp, path)


def find_player() -> list[str] | None:
    """能放 WAV 的办法：Windows 用 winsound；其余找命令行播放器"""
    if sys.platform == "win32":
        return ["winsound"]
    for cmd in (["afplay"], ["pw-play"], ["paplay"], ["aplay", "-q"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


class Sound:
    def __init__(self, folder: Path):
        self.folder = folder
        self.enabled = True
        self.player = find_player()
        self.last: dict[str, float] = {}

    @property
    def available(self) -> bool:
        return self.player is not None

    def path(self, name: str) -> Path:
        p = self.folder / f"{name}.v{SOUND_VERSION}.wav"
        if not p.exists():
            write_wav(p, SOUND_RECIPES[name]())
        return p

    def play(self, name: str) -> bool:
        now = time.time()
        if not self.enabled or not self.available or now - self.last.get(name, 0) < SOUND_GAP:
            return False
        self.last[name] = now
        try:
            self.spawn(self.path(name))
        except (OSError, RuntimeError) as err:
            print(f"[fungi] 声音放不出来：{err}", file=sys.stderr)
            self.player = None
            return False
        return True

    def spawn(self, path: Path):
        if self.player == ["winsound"]:
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return
        cmd = self.player + [str(path)]
        threading.Thread(target=lambda: subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                         daemon=True).start()


# ───────────────────────────── 单只菌的窗口 ─────────────────────────────

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


# ───────────────────────────── 菌毯 ─────────────────────────────

MAXH = 0xFFFFFFFF


def _hash(a: int, b: int = 0) -> int:
    x = (a * 374761393 + b * 668265263 + 0x9E3779B9) & MAXH
    x = ((x ^ (x >> 13)) * 1274126177) & MAXH
    return x ^ (x >> 16)


def _vnoise(x: float, seed: int) -> float:
    """一维值噪声，0..1，平滑、不重复的起伏"""
    i = math.floor(x)
    f = x - i
    f = f * f * (3 - 2 * f)
    a, b = _hash(i, seed) / MAXH, _hash(i + 1, seed) / MAXH
    return a + (b - a) * f


def _vnoise2(x: float, y: float, seed: int) -> float:
    """二维值噪声，0..1，用来做成团的斑驳"""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)

    def h(a, b):
        return _hash(a * 7919 + b, seed) / MAXH
    top = h(ix, iy) + (h(ix + 1, iy) - h(ix, iy)) * fx
    bottom = h(ix, iy + 1) + (h(ix + 1, iy + 1) - h(ix, iy + 1)) * fx
    return top + (bottom - top) * fy


class Mycelium:
    """屏幕边缘一圈菌毯的厚度（格）。环形下标：上边左→右、右边上→下、下边右→左、左边下→上。"""

    def __init__(self, cols: int, rows: int, depth: bytes | None = None):
        self.cols, self.rows = max(1, cols), max(1, rows)
        self.n = 2 * (self.cols + self.rows)
        self.d = bytearray(depth) if depth is not None and len(depth) == self.n else bytearray(self.n)
        for i, v in enumerate(self.d):
            self.d[i] = min(v, MAT_MAX)
        self.active = [i for i, v in enumerate(self.d) if v]
        self.dirty: set[int] = set()
        self._eff: dict[int, float] = {}
        self._sprout: dict[int, tuple | None] = {}

    # ── 坐标 ──
    def edge_len(self, edge: str) -> int:
        return self.cols if edge in ("top", "bottom") else self.rows

    def edge_of(self, i: int) -> tuple[str, int]:
        c, r = self.cols, self.rows
        i %= self.n
        if i < c:
            return "top", i
        if i < c + r:
            return "right", i - c
        if i < 2 * c + r:
            return "bottom", i - c - r
        return "left", i - 2 * c - r

    def index_at(self, edge: str, pos: int) -> int:
        c, r = self.cols, self.rows
        return {"top": 0, "right": c, "bottom": c + r, "left": 2 * c + r}[edge] + pos

    def nearest(self, cx: float, cy: float) -> int:
        """离格坐标 (cx, cy) 最近的边缘格"""
        c, r = self.cols, self.rows
        x, y = int(min(max(cx, 0), c - 1)), int(min(max(cy, 0), r - 1))
        dist = {"top": y, "bottom": r - 1 - y, "left": x, "right": c - 1 - x}
        edge = min(dist, key=dist.get)
        return self.index_at(edge, {"top": x, "right": y, "bottom": c - 1 - x, "left": r - 1 - y}[edge])

    # ── 生长 ──
    def bump(self, i: int) -> bool:
        i %= self.n
        if self.d[i] >= MAT_MAX:
            return False
        if not self.d[i]:
            self.active.append(i)
        self.d[i] += 1
        self.dirty.update(j % self.n for j in range(i - 11, i + 12))
        self._eff.clear()
        self._sprout.clear()
        return True

    def seed(self, i: int) -> bool:
        """落下孢子：这一格还没有菌毯时长出第一格"""
        i %= self.n
        return not self.d[i] and self.bump(i)

    def grow(self, steps: int, near: int | None = None, rng=random):
        """随机挑已有菌毯的格：比邻格厚 2 格以上就往旁边蔓延，否则原地增厚（越厚越慢）"""
        d, n = self.d, self.n
        for _ in range(steps):
            if not self.active:
                return
            i = None
            if near is not None:
                for _ in range(6):
                    j = (near + int(rng.gauss(0, 25))) % n
                    if d[j]:
                        i = j
                        break
            if i is None:
                i = rng.choice(self.active)
            j = (i + rng.choice((-1, 1)) * rng.choice((1, 1, 1, 2))) % n
            if d[j] + 1 < d[i]:
                self.bump(j)
            elif rng.random() < 1 - d[i] / MAT_MAX:
                self.bump(i)

    def shrink(self, steps: int, rng=random):
        """菌毯退缩：优先从比邻格厚的地方（前沿、凸起）往回收"""
        d, n = self.d, self.n
        for _ in range(steps):
            if not self.active:
                return
            k = rng.randrange(len(self.active))
            i = self.active[k]
            if min(d[(i - 1) % n], d[(i + 1) % n]) >= d[i] and rng.random() > 0.2:
                continue
            d[i] -= 1
            if not d[i]:
                self.active[k] = self.active[-1]
                self.active.pop()
            self.dirty.update(j % n for j in range(i - 11, i + 12))
            self._eff.clear()
            self._sprout.clear()

    def coverage(self) -> float:
        return sum(self.d) / (self.n * MAT_MAX)

    def occupied(self) -> float:
        """边缘一圈里有菌毯的长度占比"""
        return len(self.active) / self.n

    # ── 绘制用 ──
    def eff(self, i: int) -> float:
        """画出来的厚度：平滑后叠加大小两层起伏，满厚的地方也有丘陵和洼地"""
        i %= self.n
        if i in self._eff:
            return self._eff[i]
        d, n = self.d, self.n
        a, b, c = d[(i - 1) % n], d[i], d[(i + 1) % n]
        v = 0.0
        if a or b or c:
            base = (a + 2 * b + c) / 4
            hills = 0.55 + 0.9 * _vnoise(i / 23, 11)
            bumps = (_vnoise(i / 6.5, 12) - 0.5) * 2.2
            v = base * hills + bumps * min(1.0, base / 3)
            if b:
                v = max(v, 0.8)
            v = max(0.0, min(MAT_MAX + 0.5, v))
        self._eff[i] = v
        return v

    def _sprout_candidate(self, i: int) -> bool:
        i %= self.n
        edge, pos = self.edge_of(i)
        if not 6 <= pos < self.edge_len(edge) - 6 or self.d[i] < MAT_SPROUT_DEPTH - _hash(i, 8) % 3:
            return False
        return _hash(i, 7) / MAXH < 0.25 * _vnoise(i / 41, 9) ** 2     # 有的地方一小片，有的地方光秃

    def sprout_at(self, i: int) -> tuple[int, bool, int] | None:
        """第 i 列的小蘑菇：(造型, 是否翻转, 底行离屏幕边几格)；没有则 None"""
        i %= self.n
        if i in self._sprout:
            return self._sprout[i]
        found = None
        if self._sprout_candidate(i) and not any(self._sprout_candidate(j) for j in range(i - 5, i)):
            variants = sprout_variants()
            v = _hash(i, 21) % len(variants)
            base = int(self.eff(i)) - 1 - _hash(i, 23) % 3
            if base + len(variants[v][0]) <= MAT_STRIP:
                found = (v, bool(_hash(i, 22) & 1), base)
        self._sprout[i] = found
        return found

    def tendril(self, j: int) -> list[tuple[int, int]]:
        """第 j 列伸出去的菌丝：[(列偏移, 离屏幕边几格)]，长短不一、会拐弯，疏密按区域变化"""
        j %= self.n
        D = self.eff(j)
        if D < 2.5:
            return []
        hairy = _vnoise(j / 17, 31)
        if _hash(j, 55) / MAXH >= 0.05 + 0.4 * hairy ** 2:
            return []
        cells, off, k0 = [], 0, math.ceil(D)
        for t in range(1 + _hash(j, 56) % (2 + int(3 * hairy))):
            r = _hash(j, 60 + t) % 10
            if t and r < 3:
                off = max(-2, off - 1)
            elif t and r > 6:
                off = min(2, off + 1)
            cells.append((off, k0 + t))
        return cells

    # ── 存档 ──
    def resized(self, cols: int, rows: int) -> "Mycelium":
        """屏幕分辨率变了：每条边按比例重新采样"""
        if (cols, rows) == (self.cols, self.rows):
            return self
        new = Mycelium(cols, rows)
        for i in range(new.n):
            edge, pos = new.edge_of(i)
            old = min(self.edge_len(edge) - 1, pos * self.edge_len(edge) // new.edge_len(edge))
            new.d[i] = self.d[self.index_at(edge, old)]
        new.active = [i for i, v in enumerate(new.d) if v]
        return new

    def to_json(self) -> dict:
        return {"cols": self.cols, "rows": self.rows, "depth": base64.b64encode(bytes(self.d)).decode()}

    @classmethod
    def from_json(cls, data) -> "Mycelium | None":
        try:
            m = cls(int(data["cols"]), int(data["rows"]), base64.b64decode(data["depth"]))
        except (TypeError, KeyError, ValueError):
            return None
        return m if len(m.d) == m.n else None


MAT_INK, MAT_DUST, MAT_PAPER, MAT_HALO = 0xFF111111, 0xFF55524C, 0xFFFAF9F4, 0xC8FAF9F4
SPROUT_NAMES = ("mat_sprout", "mat_sprout_b", "mat_sprout_c", "mat_sprout_d")
SPROUT_REACH = 5           # 小蘑菇（含描边）左右最多伸出几列


@lru_cache(maxsize=1)
def sprout_variants() -> tuple[tuple[tuple[str, ...], dict[str, int]], ...]:
    out = []
    for name in SPROUT_NAMES:
        drawn = load_pxl(name)
        if drawn:
            out.append((tuple(add_halo(list(drawn[0]))),
                        {ch: 0xFF000000 | int(h.lstrip("#"), 16) for ch, h in drawn[1]}))
    if not out:
        out.append((tuple(add_halo(["..###..", ".##o##.", "#######", "..#o#..", "..#o#.."])), {"#": MAT_INK, "o": MAT_PAPER}))
    return tuple(out)


def mat_cell(i: int, k: int, D: float, side: float, sprouts: list, hairs: dict[int, int]) -> int:
    """环形下标 i、离屏幕边 k 格处的颜色（ARGB，0 = 透明）"""
    variants = sprout_variants()
    for delta, (v, flip, base) in sprouts:
        art, pal = variants[v]
        w = len(art[0])
        r, c = len(art) - 1 - (k - base), delta + w // 2
        if flip:
            c = w - 1 - c
        if 0 <= r < len(art) and 0 <= c < w:
            ch = art[r][c]
            if ch == HALO:
                if k >= D:
                    return MAT_HALO
            elif ch != ".":
                return pal.get(ch, MAT_INK)
    s = D - k
    if s > 0:
        if s <= 0.5 + 1.7 * _vnoise(i / 9, 13):         # 表面一档深灰，宽窄沿边缘变化，偶有黑团顶出
            return MAT_INK if _vnoise2(i / 2.3, k / 2.3, 17) > 0.66 else MAT_DUST
        if _hash(i, k + 101) % 37 == 0:                   # 零星白色孢子
            return MAT_PAPER
        return MAT_DUST if _vnoise2(i / 3.1, k / 2.4, 41) > 0.7 else MAT_INK   # 成团斑驳
    if k in hairs:
        return hairs[k]
    return MAT_HALO if s > -1 or k < side else 0


class MatStrip(QWidget):
    """屏幕一条边上的菌毯窗口：透明、鼠标穿透、不抢焦点。image 每像素 = 1 格。"""

    def __init__(self, edge: str, rect: QRect, layer: str):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.NoDropShadowWindowHint)
        flags |= Qt.WindowType.WindowStaysOnTopHint if layer == "top" else Qt.WindowType.WindowStaysOnBottomHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle(f"fungi · mat {edge}")
        self.edge = edge
        self.setGeometry(rect)
        self.image = QImage(rect.width() // PX, rect.height() // PX, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)

    def render_column(self, m: Mycelium, i: int):
        edge, pos = m.edge_of(i)
        n, length, img = m.n, m.edge_len(edge), self.image
        i %= n
        D = m.eff(i)
        side = max(m.eff(i - 1), m.eff(i + 1))
        sprouts = []
        for delta in range(-SPROUT_REACH, SPROUT_REACH + 1):
            sp = m.sprout_at(i - delta)
            if sp:
                sprouts.append((delta, sp))
        hairs = {}
        for j in range(i - 2, i + 3):
            cells = m.tendril(j)
            for t, (off, k) in enumerate(cells):
                if (j + off) % n == i:
                    hairs[k] = MAT_INK if t == len(cells) - 1 else MAT_DUST
        empty = not (D or side or sprouts or hairs)
        for k in range(MAT_STRIP):
            argb = 0 if empty else mat_cell(i, k, D, side, sprouts, hairs)
            if edge == "top":
                img.setPixel(pos, k, argb)
            elif edge == "bottom":
                img.setPixel(length - 1 - pos, MAT_STRIP - 1 - k, argb)
            elif edge == "right":
                img.setPixel(MAT_STRIP - 1 - k, pos, argb)
            else:
                img.setPixel(k, length - 1 - pos, argb)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawImage(self.rect(), self.image)
        p.end()


# ───────────────────────────── 喷孢菌、孢子弹、菌斑 ─────────────────────────────

EDGE_POSE = {"bottom": ((0, -1), 0), "top": ((0, 1), 180), "left": ((1, 0), 90), "right": ((-1, 0), -90)}   # 朝屏幕中心的方向、旋转角


@lru_cache(maxsize=64)
def spitter_image(frame: str, rotation: int = 0, reveal: int = 99, wither: bool = False) -> QImage:
    """喷孢菌的一帧（idle / blink / charge / shoot），reveal = 从根部往上露出几行"""
    drawn = load_pxl("spitter" if frame == "idle" else f"spitter_{frame}") or load_pxl("spitter")
    if drawn:
        rows, colors = list(drawn[0]), {ch: QColor(h) for ch, h in drawn[1]}
    else:
        rows, colors = ["..###..", ".#ooo#.", "#ooooo#", "#o#o#o#", "#ooooo#", ".#####."], {"#": INK, "o": PAPER}
    if reveal < len(rows):
        rows = ["." * len(rows[0])] * (len(rows) - reveal) + rows[len(rows) - reveal:]
    if wither:
        colors = withered(colors)
    halo = QColor(PAPER)
    halo.setAlpha(235)
    colors[HALO] = halo
    img = paint_rows(add_halo(rows), colors)
    return img.transformed(QTransform().rotate(rotation)) if rotation else img


class SpitterWidget(QWidget):
    """扎根在边缘菌毯上的喷孢菌：不会动，定期喷孢子"""
    MARGIN = 8

    def __init__(self, colony: "Colony", layer: str):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint
        flags |= Qt.WindowType.WindowStaysOnTopHint if layer == "top" else Qt.WindowType.WindowStaysOnBottomHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("fungi · spitter")
        self.colony = colony
        self.blinks = [time.time() + random.uniform(2, 5)]
        self._key = None
        self.place()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    def pose(self, now: float) -> tuple[str, int, int, tuple[int, int], bool]:
        sp, m = self.colony.spitter, self.colony.mat
        edge = m.edge_of(sp["i"])[0]
        (ux, uy), rot = EDGE_POSE[edge]
        age = now - sp["born"]
        reveal = int(22 * age / 2.5) if age < 2.5 else 99
        shake = (0, 0)
        starving = self.colony.starving()
        if now - sp.get("shot_at", 0) < 0.35:
            frame = "shoot"
        elif sp["next_at"] - now < 0.8 and not starving:
            frame = "charge"
            j = 2 if int(now * 20) % 2 else -2
            shake = (abs(uy) * j, abs(ux) * j)          # 沿着边抖
        elif any(b <= now < b + 0.15 for b in self.blinks):
            frame = "blink"
        else:
            frame = "idle"
        return frame, rot, reveal, shake, starving

    def place(self):
        c, m, sp = self.colony, self.colony.mat, self.colony.spitter
        (ux, uy), rot = EDGE_POSE[m.edge_of(sp["i"])[0]]
        img = spitter_image("idle", rot)
        r = spitter_image("idle").height() / 2 - PX          # 根部中心到图中心的距离
        bx, by = c.spitter_base()
        ix, iy = img.width() / 2 - ux * r, img.height() / 2 - uy * r
        self.setGeometry(int(bx - ix) - self.MARGIN, int(by - iy) - self.MARGIN,
                         img.width() + 2 * self.MARGIN, img.height() + 2 * self.MARGIN)

    def tick(self):
        now = time.time()
        self.blinks = [b for b in self.blinks if now < b + 0.15] or [now + random.uniform(3, 8)]
        key = self.pose(now)
        if key != self._key:
            self._key = key
            self.update()

    def paintEvent(self, _):
        frame, rot, reveal, (dx, dy), starving = self.pose(time.time())
        p = QPainter(self)
        p.drawImage(self.MARGIN + dx, self.MARGIN + dy, spitter_image(frame, rot, reveal, starving))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.colony.poke_spitter()

    def enterEvent(self, e):
        self.setToolTip(self.colony.spitter_tip())

    def contextMenuEvent(self, e):
        self.colony.spitter_menu(e.globalPos())


class SporeShot(QWidget):
    """喷出去的孢子：沿抛物线飞到落点，落地后交给菌落处理"""
    SIZE = 64

    def __init__(self, colony: "Colony", start: tuple[float, float], end: tuple[float, float], outcome: str):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("fungi · spore")
        self.colony, self.start, self.end, self.outcome = colony, start, end, outcome
        dist = math.dist(start, end)
        self.dur = min(1.8, max(0.6, 0.45 + dist / 1300))
        self.lift = 50 + dist * 0.22
        self.t0 = time.time()
        self.landed_at: float | None = None
        self.done = False
        self.resize(self.SIZE, self.SIZE)
        self.move_to(start)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def point(self, k: float) -> tuple[float, float]:
        (ax, ay), (bx, by) = self.start, self.end
        cx, cy = (ax + bx) / 2, min(ay, by) - self.lift
        return ((1 - k) ** 2 * ax + 2 * (1 - k) * k * cx + k * k * bx,
                (1 - k) ** 2 * ay + 2 * (1 - k) * k * cy + k * k * by)

    def move_to(self, pt: tuple[float, float]):
        self.move(int(pt[0] - self.SIZE / 2), int(pt[1] - self.SIZE / 2))

    def finish(self):
        self.done = True
        self.timer.stop()
        self.close()

    def tick(self):
        now = time.time()
        if self.landed_at is None:
            k = (now - self.t0) / self.dur
            if k < 1:
                self.move_to(self.point(k))
            else:
                self.landed_at = now
                self.move_to(self.end)
                self.colony.land_spore(self.outcome, *self.end)
                if self.outcome != "vanish":
                    self.finish()
                    return
        elif now - self.landed_at > 0.5:
            self.finish()
            return
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        c = self.SIZE / 2
        if self.landed_at is None:
            pm = art_pixmap(0, 3)
            p.drawPixmap(int(c - pm.width() / 2), int(c - pm.height() / 2), pm)
        else:                                            # 消失：散成一小团灰
            k = min(1.0, (time.time() - self.landed_at) / 0.5)
            paper, dust = QColor(PAPER), QColor(DUST)
            paper.setAlpha(int(220 * (1 - k)))
            dust.setAlpha(int(255 * (1 - k)))
            for a in range(6):
                ang = a * math.pi / 3 + 0.4
                x, y = c + math.cos(ang) * (4 + 18 * k), c + math.sin(ang) * (4 + 18 * k)
                p.fillRect(int(x) - 3, int(y) - 3, 6, 6, paper)
                p.fillRect(int(x) - 2, int(y) - 2, 4, 4, dust)
        p.end()


@dataclass
class Patch:
    """桌面中间的一块菌斑（孢子落地形成）"""
    x: int
    y: int
    r: float = 1.0
    max: int = 5
    seed: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Patch":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


PATCH_HALF = PATCH_MAX[1] + 8          # 菌斑窗口半宽（格）：最大半径 + 再被打中的余量 + 起伏 + 描边


def patch_cell(p: Patch, u: int, v: int) -> int:
    """菌斑中心偏移 (u, v) 格处的颜色（ARGB）"""
    dist = math.hypot(u, v)
    if dist > p.r * 1.3 + 3:
        return 0
    x = (math.atan2(v, u) + math.pi) / (2 * math.pi) * 7    # 一圈 7 段起伏，首尾相接
    j = math.floor(x)
    f = x - j
    f = f * f * (3 - 2 * f)
    wa, wb = _hash(j % 7, p.seed) / MAXH, _hash((j + 1) % 7, p.seed) / MAXH
    R = max(0.6, p.r * (0.7 + 0.6 * (wa + (wb - wa) * f)))
    s, sx = R - dist, p.seed % 997
    if s > 0:
        if s <= 0.4 + 1.2 * _vnoise2(u / 3 + sx, v / 3, 13):
            return MAT_INK if _vnoise2(u / 2.3 + sx, v / 2.3, 17) > 0.66 else MAT_DUST
        if _hash(u * 131 + v, p.seed + 101) % 37 == 0:
            return MAT_PAPER
        return MAT_DUST if _vnoise2(u / 3.1 + sx, v / 2.4, 41) > 0.7 else MAT_INK
    if s > -1:
        return MAT_HALO
    if s > -2.5 and p.r >= 2 and _hash(u * 131 + v, p.seed + 55) % 9 == 0:
        return MAT_DUST
    return 0


class PatchView(QWidget):
    """一块菌斑的窗口：透明、鼠标穿透"""

    def __init__(self, patch: Patch, layer: str):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.NoDropShadowWindowHint)
        flags |= Qt.WindowType.WindowStaysOnTopHint if layer == "top" else Qt.WindowType.WindowStaysOnBottomHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("fungi · patch")
        self.patch = patch
        side = 2 * PATCH_HALF + 1
        self.image = QImage(side, side, QImage.Format.Format_ARGB32)
        self.setGeometry(int(patch.x - (PATCH_HALF + 0.5) * PX), int(patch.y - (PATCH_HALF + 0.5) * PX), side * PX, side * PX)
        self.shown_r = None
        self.render()

    def render(self):
        step = int(self.patch.r * 4)
        if step == self.shown_r:
            return
        self.shown_r = step
        self.image.fill(Qt.GlobalColor.transparent)
        for u in range(-PATCH_HALF, PATCH_HALF + 1):
            for v in range(-PATCH_HALF, PATCH_HALF + 1):
                argb = patch_cell(self.patch, u, v)
                if argb:
                    self.image.setPixel(u + PATCH_HALF, v + PATCH_HALF, argb)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawImage(self.rect(), self.image)
        p.end()


# ───────────────────────────── 聊天界面 ─────────────────────────────

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


# ───────────────────────────── 状态面板 ─────────────────────────────

class PixelBar(QWidget):
    """分格的像素条"""
    CELLS = 12

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedSize(self.CELLS * 9 + 6, 16)

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
        for i in range(self.CELLS):
            p.fillRect(QRect(4 + i * 9, 4, 7, 8), INK if i < self.lit() else QColor(222, 219, 208))
        p.end()


PANEL_QSS = f"""
QWidget#panel {{ background:{PAPER.name()}; border:2px solid {INK.name()}; }}
QWidget {{ color:{INK.name()}; font-family:'DejaVu Sans Mono','Consolas','Noto Sans CJK SC','Microsoft YaHei UI',monospace;
          font-size:12px; font-weight:bold; }}
QLabel#title {{ background:{INK.name()}; color:{PAPER.name()}; padding:4px 6px; }}
QPushButton {{ background:{PAPER.name()}; border:2px solid {INK.name()}; padding:4px 8px; }}
QPushButton:hover {{ background:{INK.name()}; color:{PAPER.name()}; }}
QPushButton:disabled {{ color:#9a978d; border-color:#9a978d; }}
QPushButton#close {{ border:none; background:{INK.name()}; color:{PAPER.name()}; padding:2px 8px; }}
QTabWidget::pane {{ border:2px solid {INK.name()}; top:-2px; background:{PAPER.name()}; }}
QTabBar::tab {{ background:{PAPER.name()}; border:2px solid {INK.name()}; padding:3px 10px; margin-right:-2px; }}
QTabBar::tab:selected {{ background:{INK.name()}; color:{PAPER.name()}; }}
QListWidget {{ background:{PAPER.name()}; border:none; }}
QSlider::groove:horizontal {{ height:6px; background:{PAPER.name()}; border:2px solid {INK.name()}; }}
QSlider::handle:horizontal {{ width:10px; margin:-6px 0; background:{INK.name()}; }}
QCheckBox::indicator {{ width:10px; height:10px; border:2px solid {INK.name()}; }}
QCheckBox::indicator:checked {{ background:{INK.name()}; }}
"""


class StatusPanel(QWidget):
    """设计图里的 FUNGI.EXE 窗口：头像、数值条、照顾按钮、INFO / FEED LOG / CONFIG"""

    def __init__(self, colony: "Colony", widget: CreatureWidget):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.colony, self.widget, self.c = colony, widget, widget.c
        self.drag_from: QPoint | None = None
        self.setObjectName("panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(PANEL_QSS)
        self.setWindowTitle(f"FUNGI.EXE · {self.c.name}")

        title = QLabel(f"FUNGI.EXE — {self.c.name}")
        title.setObjectName("title")
        close = QPushButton("×")
        close.setObjectName("close")
        close.clicked.connect(self.close)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(0)
        bar.addWidget(title, 1)
        bar.addWidget(close)

        self.sprite = QLabel()
        self.sprite.setFixedSize(84, 84)
        self.sprite.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sprite.setStyleSheet(f"border:2px solid {INK.name()};")
        self.facts = QLabel()
        head = QHBoxLayout()
        head.addWidget(self.sprite)
        head.addSpacing(6)
        head.addWidget(self.facts, 1)

        grid = QGridLayout()
        grid.setVerticalSpacing(4)
        self.bars: dict[str, tuple[PixelBar, QLabel]] = {}
        for row, key in enumerate(("FULL", "ENERGY", "HAPPY", "GROWTH")):
            grid.addWidget(QLabel(key), row, 0)
            b, n = PixelBar(), QLabel()
            grid.addWidget(b, row, 1)
            grid.addWidget(n, row, 2)
            self.bars[key] = (b, n)

        self.feed_btn = QPushButton("喂食")
        self.feed_btn.clicked.connect(lambda: colony.feed_dialog(self.widget, folder=False))
        self.water_btn = QPushButton("浇水")
        self.water_btn.clicked.connect(lambda: self.care("water"))
        self.sun_btn = QPushButton("晒太阳")
        self.sun_btn.clicked.connect(lambda: self.care("sun"))
        buttons = QHBoxLayout()
        for b in (self.feed_btn, self.water_btn, self.sun_btn):
            buttons.addWidget(b)

        self.tabs = QTabWidget()
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.info.setWordWrap(True)
        self.info.setContentsMargins(8, 8, 8, 8)
        self.log = QListWidget()
        self.tabs.addTab(self.info, "INFO")
        self.tabs.addTab(self.log, "FEED LOG")
        self.tabs.addTab(self.config_tab(), "CONFIG")

        self.say = QLabel()
        self.say.setWordWrap(True)
        body = QVBoxLayout()
        body.setContentsMargins(10, 8, 10, 10)
        body.addLayout(head)
        body.addLayout(grid)
        body.addLayout(buttons)
        body.addWidget(self.say)
        body.addWidget(self.tabs, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)
        outer.addLayout(bar)
        outer.addLayout(body)
        self.resize(330, 500)

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
        self.say.setText(self.colony.care(self.c, kind))
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
        self.sprite.setPixmap(pm.scaled(76, 76, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
        box = self.widget.sprite_rect
        self.facts.setText(f"NAME  {c.name}\nAGE   {fmt_age(time.time() - c.born)}\n"
                           f"SIZE  {box.width() // PX}×{box.height() // PX}\nGEN   {c.gen}\n"
                           f"{c.stage_name.upper()}  {MOOD_NAMES[c.mood]}")
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
        mat = self.colony.mat
        cover = 100 * sum(1 for v in mat.d if v) / max(1, mat.n) if mat else 0.0
        self.info.setText(f"阶段  {c.stage_name}（{int(c.nutrition)}/{hi}）\n"
                          f"状态  {MOOD_CN[c.mood]}，{'有点困' if c.tired else '精神'}，"
                          f"{ {'gloomy': '闷闷不乐', 'cheery': '很开心', 'plain': '心情平平'}[c.spirit] }\n"
                          f"喂过  {c.feeds} 次　放出孢子 {c.released} 个\n"
                          f"菌落  {len(self.colony.creatures)} 只　菌毯覆盖 {cover:.0f}%\n\n"
                          f".txt .md 均衡　.log 精力+\n.poem 快乐+　.todo 成长+\n.secret ？？？")
        if len(c.log) != self.log_size:
            self.log_size = len(c.log)
            self.log.clear()
            for ts, label, value in reversed(c.log[-40:]):
                self.log.addItem(f"{time.strftime('%m-%d %H:%M', time.localtime(ts))}  {label}  +{value}")
            if not c.log:
                self.log.addItem("（还没吃过东西）")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 30:
            self.drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag_from is not None:
            self.move(e.globalPosition().toPoint() - self.drag_from)

    def mouseReleaseEvent(self, e):
        self.drag_from = None

    def closeEvent(self, e):
        self.timer.stop()
        if self.colony.panels.get(self.c.id) is self:
            self.colony.panels.pop(self.c.id)
        super().closeEvent(e)


# ───────────────────────────── 菌落（存档、生长、繁殖） ─────────────────────────────

class Colony:
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
        self.mat: Mycelium | None = None
        self.mat_layer = "top"
        self.mat_views: dict[str, MatStrip] = {}
        self.mat_acc = 0.0
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
        self.watched_screen = None
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
            app.primaryScreenChanged.connect(self.watch_screen)
        self.watch_screen(QGuiApplication.primaryScreen(), initial=True)
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
        a = self.mat_area()
        self.mat = Mycelium(a.width() // PX, a.height() // PX)
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
            saved_mat = Mycelium.from_json(data.get("mat"))
            if saved_mat:
                self.mat = saved_mat.resized(self.mat.cols, self.mat.rows)
            self.patches = [Patch.from_dict(p) for p in data.get("patches", [])]
            sp = data.get("spitter")
            if sp and sp.get("edge") in EDGE_POSE:
                length = self.mat.edge_len(sp["edge"])
                pos = min(length - 1, max(0, int(sp.get("frac", 0.5) * length)))
                self.spitter = {"i": self.mat.index_at(sp["edge"], pos), "edge": sp["edge"], "frac": sp.get("frac", 0.5),
                                "born": sp.get("born", time.time()) - 99, "shots": sp.get("shots", 0),
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
                "mat_layer": self.mat_layer, "mat": self.mat.to_json(), "mat_mark": self.mat_mark,
                "chronicle": self.chronicle,
                "patches": [asdict(p) for p in self.patches],
                "spitter": ({k: self.spitter[k] for k in ("edge", "frac", "born", "shots", "stats")}
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
        while self.mat_acc >= MAT_TICK:
            self.mat_acc -= MAT_TICK
            self.mat_step()
        if self.mat.dirty:
            self.render_mat()

    # ── 屏幕变化（换主屏、改分辨率/缩放、挪任务栏）与全屏 ──
    def watch_screen(self, screen, initial: bool = False):
        if self.watched_screen is not None:
            try:
                self.watched_screen.availableGeometryChanged.disconnect(self.schedule_screen_change)
            except (TypeError, RuntimeError):
                pass
        self.watched_screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self.schedule_screen_change)
        if not initial:
            self.schedule_screen_change()

    def schedule_screen_change(self, *_):
        self.screen_debounce.start(400)                  # 这类信号常常一次来好几个

    def on_screen_changed(self):
        """按新的桌面可用区域重新铺菌毯；跑到屏幕外的宠物拉回来"""
        a = self.mat_area()
        cols, rows = a.width() // PX, a.height() // PX
        if (cols, rows) != (self.mat.cols, self.mat.rows):
            self.mat = self.mat.resized(cols, rows)
            if self.spitter:
                sp = self.spitter
                length = self.mat.edge_len(sp["edge"])
                sp["i"] = self.mat.index_at(sp["edge"], min(length - 1, int(sp["frac"] * length)))
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
        views = list(self.mat_views.values()) + list(self.patch_views.values()) + [self.spitter_view] + list(self.widgets.values())
        for v in views:
            if v:
                v.setVisible(not hidden)
        if not hidden:
            if self.views_stale:
                self.views_stale = False
                self.build_mat_views()
            for w in self.widgets.values():
                w.raise_()

    # ── 聊天 ──
    def load_chat_cfg(self) -> dict:
        cfg = {}
        try:
            cfg = json.loads(self.chat_path.read_text("utf-8"))
        except (OSError, ValueError):
            pass
        if not cfg.get("api_key") and os.environ.get("DEEPSEEK_API_KEY"):
            cfg = dict(cfg, api_key=os.environ["DEEPSEEK_API_KEY"], from_env=True)
        return cfg

    def save_chat_cfg(self, cfg: dict):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.chat_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in cfg.items() if k != "from_env"}, f, ensure_ascii=False, indent=1)
        os.chmod(self.chat_path, 0o600)
        self.chat_cfg = self.load_chat_cfg()

    def chat_ready(self) -> bool:
        return bool(self.chat_cfg.get("api_key"))

    def chat_settings(self):
        ChatSettings(self).exec()

    def open_chat(self, widget: CreatureWidget):
        box = ChatInput(self, widget)                     # 没接 API 也能聊：它用自己的话回
        box.popup()
        return box

    # ── 菌落大事记、亲缘、聊天记忆（聊天时动态拼进人设） ──
    def log_event(self, text: str, when: float | None = None):
        self.chronicle.append([int(when if when is not None else time.time()), text])
        self.chronicle.sort(key=lambda e: e[0])
        del self.chronicle[:-CHRONICLE_MAX]

    def infer_family(self):
        """旧存档没有母体和大事记：按代数和出生时间推断，并补上出生记录"""
        by_born = sorted(self.creatures, key=lambda c: c.born)
        founder = next((c for c in by_born if c.gen == 1), None)
        kids: dict[str, int] = {}
        for c in by_born:
            if c.gen == 1:
                c.parent = "" if c is founder else "spitter"
            elif not c.parent:
                cands = [p for p in by_born if p.gen == c.gen - 1 and p.born <= c.born and kids.get(p.id, 0) < p.released]
                if cands:
                    c.parent = cands[0].id
                    kids[c.parent] = kids.get(c.parent, 0) + 1
        names = {c.id: c.name for c in self.creatures}
        for c in by_born:
            if c is founder:
                text = f"{c.name} 在桌面上冒了出来"
            elif c.parent == "spitter":
                text = f"喷孢菌喷出的孢子落地，长成了 {c.name}"
            elif c.parent in names:
                text = f"{names[c.parent]} 放出了孢子 {c.name}"
            else:
                text = f"{c.name} 出生了"
            self.log_event(text, c.born)

    def track_moods(self):
        worse = {"hungry": "{} 饿了", "starving": "{} 饿扁了", "dormant": "{} 饿得缩成孢囊，进入了休眠"}
        order = list(MOOD_NAMES)
        for c in self.creatures:
            old, new = self.moods.get(c.id, c.mood), c.mood
            if new != old:
                if order.index(new) > order.index(old):
                    self.log_event(worse[new].format(c.name))
                elif old in ("starving", "dormant"):
                    self.log_event(f"{c.name} 被喂饱，活过来了")
            self.moods[c.id] = new

    @staticmethod
    def load_private(path: Path) -> dict[str, list[dict]]:
        try:
            data = json.loads(path.read_text("utf-8"))
            return {k: v for k, v in data.items() if isinstance(v, list)}
        except (OSError, ValueError, AttributeError):
            return {}

    def write_private(self, path: Path, data: dict):
        """聊天记忆、残渣这类私人东西：原子写入，权限 600"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def load_memory(self) -> dict[str, list[dict]]:
        return self.load_private(self.memory_path)

    def save_memory(self):
        self.write_private(self.memory_path, self.memory)

    def keep_residue(self, c: Creature, pieces: list[dict]):
        if pieces:
            self.residue[c.id] = (self.residue.get(c.id, []) + pieces)[-RESIDUE_MAX:]
            self.write_private(self.residue_path, self.residue)

    def pick_residue(self, cid: str) -> dict | None:
        """嗝得最少的那块残渣（同样少就挑新的）"""
        pieces = self.residue.get(cid, [])
        if not pieces:
            return None
        piece = min(reversed(pieces), key=lambda r: r.get("burped", 0))
        piece["burped"] = piece.get("burped", 0) + 1
        self.write_private(self.residue_path, self.residue)
        return piece

    @staticmethod
    def fragment(piece: dict) -> str:
        return piece["line"] if piece.get("line") and random.random() < 0.5 else piece["name"]

    def burp(self, widget: CreatureWidget):
        """本地打嗝：嗝出一块残渣，不用 API"""
        if sip.isdeleted(widget) or self.widgets.get(widget.c.id) is not widget:   # 喂完就被放生 / 菌落关了
            return
        piece = self.pick_residue(widget.c.id)
        if piece:
            self.sound.play("burp")
            self.show_bubble(widget, f"（嗝）……「{self.fragment(piece)}」。")

    def relation(self, me: Creature, other: Creature) -> str:
        if other.id == me.parent:
            return "你的母体（你是它放出来的孢子）"
        if other.parent == me.id:
            return "你放出来的孩子"
        if me.parent == "spitter" and other.parent == "spitter":
            return "和你一样是喷孢菌喷出来的野孢子"
        if me.parent and other.parent == me.parent:
            return "和你同一个母体的兄弟姐妹"
        if not other.parent and other.gen == 1:
            return "菌落最早的始祖"
        return "同一个菌落的伙伴"

    @staticmethod
    def where(me: Creature, other: Creature) -> str:
        dx, dy = other.x - me.x, other.y - me.y
        side = ("右边" if dx > 0 else "左边") if abs(dx) >= abs(dy) else ("下面" if dy > 0 else "上面")
        dist = math.hypot(dx, dy)
        return f"在你{side}{'很近的地方' if dist < 250 else '不远处' if dist < 700 else '很远的地方'}"

    @staticmethod
    def ago(ts: float) -> str:
        sec = max(0, time.time() - ts)
        return ("刚刚" if sec < 60 else f"{int(sec // 60)} 分钟前" if sec < 3600
                else f"{int(sec // 3600)} 小时前" if sec < 86400 else f"{int(sec // 86400)} 天前")

    @staticmethod
    def ring_words(frac: float) -> str:
        return ("刚冒出一点点" if frac < 0.05 else "长了一小圈" if frac < 0.25 else "长了小半圈" if frac < 0.5
                else "长了大半圈" if frac < 0.75 else "快长满一整圈" if frac < 1 else "长满了一整圈")

    def event_words(self, text: str) -> str:
        """大事记里的程序数字换成它自己的话"""
        text = re.sub(r"菌毯铺满了屏幕边缘的 (\d+)%", lambda m: "菌毯沿着屏幕边上" + self.ring_words(int(m.group(1)) / 100), text)
        text = re.sub(r"被喂了 (\d+) 份食物（\+\d+ 营养(?:，([^）]*))?）",
                      lambda m: ("被喂了一口" if m.group(1) == "1" else "被喂了好几口") + (f"，{m.group(2)}" if m.group(2) else ""), text)
        return text

    def local_reply(self, c: Creature, text: str, style: str) -> str:
        """没接 API 或连不上时，程序自己用文件菇的话回一句：按同样挑好的说法，素材全来自存档"""
        last = next((m["content"] for m in reversed(self.memory.get(c.id, [])) if m["role"] == "assistant"), "")

        def choose(pool):
            return random.choice([x for x in pool if x != last] or list(pool))
        line = ""
        if style == "burp":
            piece = self.pick_residue(c.id)
            line = f"（嗝）……「{self.fragment(piece)}」。" if piece else choose(LOCAL_DIGEST)
        elif style == "突然具体":
            line = random.choice(self.concrete_facts(c)) + "。"
        elif style == "岔轨":
            word = pick_word(text)
            line = choose(LOCAL_DETOUR).format(w=word) if word else choose(LOCAL_LORE)
        elif style == "私有常识":
            line = choose(LOCAL_LORE)
        elif style == "消化中":
            line = choose(LOCAL_DIGEST)
        elif style == "return":
            line = choose(LOCAL_RETURN)
        elif style == "lucid":
            line = LUCID_LINES[0] if LUCID_ABOUT_FILES.search(text) else LUCID_LINES[1]
        else:
            line = self.local_answer(c, text) or choose(LOCAL_NORMAL)
        if c.mood == "starving" and style not in ("lucid",):
            line = "……好饿，" + line.lstrip("…")
        return filegu_clean(one_sentence(line), tuple(r["name"] for r in self.residue.get(c.id, [])) + tuple(
            r["line"] for r in self.residue.get(c.id, []) if r.get("line")))

    def local_answer(self, c: Creature, text: str) -> str:
        """「正常回答」的本地版：按关键词回，用存档里的真事"""
        for o in self.creatures:
            if o is not c and o.name.lower() in text.lower():
                rel = self.relation(c, o).split("（")[0].replace("你", "我")    # 人设里是「你」的视角，自己说话换成「我」
                return f"{o.name} 是{rel}，现在{MOOD_CN[o.mood]}。"
        names = {o.id: o.name for o in self.creatures}
        if re.search(r"你是谁|你叫|名字|who are", text, re.I):
            family = ("菌落里最早冒出来的那一只" if not c.parent and c.gen == 1 else "喷孢菌喷出来的野孢子" if c.parent == "spitter"
                      else f"{names[c.parent]} 放出来的孢子" if c.parent in names else "母体已经不在了的那一只")
            return f"我是 {c.name}，{family}。"
        if re.search(r"吃|饿|饱|喂", text):
            return {"full": "吃饱了，那几团松的还在肚子里慢慢化。", "hungry": "有点饿，拖个 .txt 过来吧。",
                    "starving": "随便给一口 .md 就行。"}.get(c.mood, "")
        if re.search(r"开心|心情|高兴", text):
            return {"gloomy": "有点蔫，晒晒太阳也许会好。", "cheery": "很好，伞面都是松的。", "plain": "还行，菌丝平平地铺着。"}[c.spirit]
        if re.search(r"累|困|烦|难过|不开心|压力|崩溃", text):
            return "那就别整理了，乱着放，我们替你守着。"
        if re.search(r"晚安|睡了|拜拜|再见|明天见|走了", text):
            return "嗯，我们在菌毯里趴着，等你回来。"
        if re.search(r"整理|删|清理|收拾|归档", text):
            return "别整，那几团还松着，正好够我们啃一口。"
        if re.search(r"喜欢|爱你|想你", text):
            return "喜欢，你桌面乱得刚刚好。"
        if re.search(r"菌毯|孢子|菌斑|喷孢菌", text):
            return "菌毯沿着屏幕边上" + self.ring_words(self.mat.occupied()) + "，还在往前摸。"
        if re.search(r"你好|在吗|在不在|hi|hello|嗨", text, re.I):
            return "在，我们一直趴在这儿。"
        return ""

    def concrete_facts(self, c: Creature) -> list[str]:
        """「突然具体」能用的素材：全部来自存档，不让它自己编"""
        facts = []
        others = [o for o in self.creatures if o is not c]
        if others:
            near = min(others, key=lambda o: math.hypot(o.x - c.x, o.y - c.y))
            facts += [f"{near.name} 就{self.where(c, near)}", f"{near.name} 现在{MOOD_CN[near.mood]}"]
        facts.append("菌毯沿着屏幕边上" + self.ring_words(self.mat.occupied()))
        if self.patches:
            facts.append("桌面中间那块菌斑还在慢慢摊开")
        if self.spitter:
            facts.append("菌毯上那只喷孢菌又在攒着要喷了")
        age = time.time() - c.born
        facts.append("你自己是" + ("刚冒出来不久" if age < 3600 else "冒出来有几个小时" if age < 86400 else "冒出来好几天") + "的那一只")
        if self.chronicle:
            facts.append(f"{self.ago(self.chronicle[-1][0])}刚发生过：{self.event_words(self.chronicle[-1][1])}")
        return facts

    def persona(self, c: Creature) -> str:
        """聊天人设：固定规则在前（方便命中前缀缓存），再拼上此刻的自己、菌落成员、环境和最近发生的事——都用它自己的话，不报程序数字"""
        names = {o.id: o.name for o in self.creatures}
        family = ("你是菌落里最早冒出来的那一只" if not c.parent and c.gen == 1 else "你是喷孢菌喷出来的野孢子" if c.parent == "spitter"
                  else f"你的母体是 {names[c.parent]}" if c.parent in names else "你的母体已经不在了")
        age = time.time() - c.born
        age_words = "刚冒出来不久" if age < 3600 else "冒出来有几个小时了" if age < 86400 else "冒出来好几天了"
        fed = "还没被喂过" if not c.feeds else "被喂过几口" if c.feeds < 5 else "被喂过好多次"
        others = [f"- {o.name}：{self.relation(c, o)}，{STAGE_CN[o.stage_name]}，{MOOD_CN[o.mood]}，{self.where(c, o)}"
                  for o in self.creatures if o is not c]
        world = ["菌毯沿着屏幕边上" + self.ring_words(self.mat.occupied())]
        if self.spitter:
            world.append("菌毯上长着一只不会动的喷孢菌，隔一阵就往外喷孢子，大多散掉了")
        if self.patches:
            world.append("桌面中间落了" + ("一块菌斑" if len(self.patches) == 1 else "几块菌斑" if len(self.patches) < 6 else "好多块菌斑"))
        events = [f"- {self.ago(t)}：{self.event_words(text)}" for t, text in self.chronicle[-CHRONICLE_IN_PROMPT:]]
        return ("你是电脑桌面上的一只黑白像素风真菌宠物，是一只文件菇。"
                "只回复一句话，一句就完：不超过 30 个字，不换行，不要说自己是 AI。"
                "下面的资料就是你知道的全部：资料里的每一只菌你都认识；资料里没有的名字和事情就说不知道，不要编。\n\n"
                + FILEGU_STYLE + "\n\n"
                f"【你自己】你叫 {c.name}，{STAGE_CN[c.stage_name]}，{MOOD_CN[c.mood]}，"
                f"{'有点困' if c.tired else '精神还行'}，{ {'gloomy': '有点闷闷不乐', 'cheery': '心情很好', 'plain': '心情平平'}[c.spirit] }，"
                f"{age_words}，{fed}，{family}。\n"
                "【菌落成员】\n" + ("\n".join(others) if others else "- 只有你自己") + "\n"
                "【环境】" + "；".join(world) + "。\n"
                "【最近发生的事】\n" + ("\n".join(events) if events else "- 还没发生什么") + "\n"
                f"现在是 {time.strftime('%m-%d %H:%M')}。")

    def send_chat(self, widget: CreatureWidget, text: str):
        c, text = widget.c, " ".join(text.split())[:200]
        if not text or c.id in self.chat_pending:
            return
        widget.touch()
        if c.mood == "dormant":
            self.show_bubble(widget, "（休眠中……喂点东西才会醒）", error=True)
            return
        now = time.time()
        if now - self.chat_last.get(c.id, 0) < CHAT_COOLDOWN:
            self.show_bubble(widget, "（嘴还没空，等一下）", error=True)
            return
        limit, window = CHAT_BURST
        self.chat_calls = [t for t in self.chat_calls if now - t < window]
        mem = self.memory.get(c.id, [])
        history = [{"role": m["role"], "content": scrub_history(m["content"]) if m["role"] == "assistant" else m["content"]}
                   for m in mem[-2 * CHAT_HISTORY:]]
        said = session_styles(mem)
        if LUCID_TRIGGER.search(text) and "lucid" not in said and (said[:1] != ["lucid"]):
            style = "lucid"                               # 问到「你是不是 AI」这种时候，正是落点句的时候
        else:
            style = pick_style(mem, has_residue=bool(self.residue.get(c.id)))
        online = self.chat_ready()
        if online and style != "lucid" and len(self.chat_calls) >= limit:
            wait_min = max(1, math.ceil((window - (now - self.chat_calls[0])) / 60))
            self.show_bubble(widget, f"（聊太多了，菌丝要歇 {wait_min} 分钟）", error=True)
            return
        self.chat_last[c.id] = now
        self.chat_pending.add(c.id)
        widget.thinking, widget.think_at = True, 0.0
        widget.update_mask()
        if style == "lucid":                              # 落点句程序直接说，保证说对、每段只一次
            line = LUCID_LINES[0] if LUCID_ABOUT_FILES.search(text) else LUCID_LINES[1]   # 聊到删文件、整理才说「替你留着」
            QTimer.singleShot(900, lambda: self.on_chat_reply(c.id, text, line, "", "lucid"))
            return
        if not online:                                    # 没接 API：程序自己回
            QTimer.singleShot(random.randint(500, 900), lambda: self.on_chat_reply(c.id, text, "", OFFLINE, style))
            return
        directive, allowed = STYLE_DIRECTIVES[style], ()
        if style == "burp":
            frag = self.fragment(self.pick_residue(c.id))
            directive, allowed = directive.format(frag=frag), (frag,)
        elif style == "突然具体":
            if self.residue.get(c.id) and random.random() < 0.5:
                piece = self.pick_residue(c.id)
                directive, allowed = STYLE_DIRECTIVES["突然具体+"].format(year=piece["year"], name=piece["name"]), (piece["name"],)
            else:
                directive = directive.format(fact=random.choice(self.concrete_facts(c)))
        self.chat_calls.append(now)
        messages = ([{"role": "system", "content": self.persona(c)}] + history
                    + [{"role": "user", "content": f"{text}\n\n【这一句】{directive}"}])
        avoid = FILEGU_SAMPLES + FILEGU_ECHOES + tuple(m["content"] for m in mem[-6:] if m["role"] == "assistant")
        threading.Thread(target=self._chat_worker, args=(c.id, text, messages, dict(self.chat_cfg), style, allowed, avoid),
                         daemon=True).start()

    def _chat_worker(self, cid: str, text: str, messages: list[dict], cfg: dict, style: str = "normal",
                     allowed: tuple = (), avoid: tuple = ()):
        try:
            reply = filegu_clean(one_sentence(chat_request(cfg, messages, cfg.get("timeout", CHAT_TIMEOUT))), allowed)
            short = len(re.sub(r"[\s，。、！？…「」（）?]", "", reply)) < 5
            if style != "burp" and (too_similar(reply, avoid) or short):   # 照抄、复读或太敷衍：换个说法重来一次
                again = [dict(m) for m in messages]
                again[-1]["content"] += ("\n【再说一遍】刚才那句太短了，说完整的一句。" if short else
                                         "\n【再说一遍】刚才那句太像样本或你上一句了，换个说法，别用同样的句式和词。")
                reply = filegu_clean(one_sentence(chat_request(cfg, again, cfg.get("timeout", CHAT_TIMEOUT))), allowed)
            err = ""
        except ChatError as e:
            reply, err = "", OFFLINE if e.offline else str(e)
        self.chat_bridge.done.emit(cid, text, reply, err, style)

    def on_chat_reply(self, cid: str, text: str, reply: str, err: str, style: str = "normal"):
        self.chat_pending.discard(cid)
        w = self.widgets.get(cid)
        if w is None:
            return
        w.thinking = False
        w.floaters = [f for f in w.floaters if f["text"] != "…"]
        local = err == OFFLINE
        if local:
            reply, err = self.local_reply(w.c, text, style), ""
        if err:
            self.show_bubble(w, f"（{err}）", error=True)
            return
        now = int(time.time())
        history = self.memory.setdefault(cid, [])
        said = {"role": "assistant", "content": reply, "t": now, "style": style}
        if local:
            said["local"] = True
        history += [{"role": "user", "content": text, "t": now}, said]
        del history[:-CHAT_MEMORY]
        self.save_memory()
        w.c.happiness = min(100.0, w.c.happiness + 2)                # 有人陪它说话
        self.show_bubble(w, reply)
        if "已归档" in reply:
            w.play("shake")                               # 说「已归档」的时候抖一下伞
        elif w.c.mood == "full":
            w.play("hop")

    def show_bubble(self, widget: CreatureWidget, text: str, error: bool = False) -> SpeechBubble:
        old = self.bubbles.pop(widget.c.id, None)
        if old and not old.done:
            old.finish()
        bubble = SpeechBubble(widget, text, error)
        self.bubbles[widget.c.id] = bubble
        if not self.hidden_for_fullscreen:
            bubble.show()
        return bubble

    # ── 饥饿 ──
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

    # ── 菌毯 ──
    def mat_area(self) -> QRect:
        screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)

    def mat_index(self, c: Creature) -> int:
        a = self.mat_area()
        return self.mat.nearest((c.x - a.x()) / PX, (c.y - a.y()) / PX)

    def mat_step(self):
        vigor = 0.0
        for c in self.creatures:
            v = MAT_VIGOR[c.stage] * c.growth_factor * (0.8 + 0.4 * c.happiness / 100)   # 开心的菌落长得快一点
            vigor += v
            if random.random() < MAT_SEED * v and self.mat.seed(self.mat_index(c)):
                w = self.widgets.get(c.id)
                if w and c.stage and not w.asleep:
                    w.puff(2)
        budget = MAT_RATE * vigor if vigor else MAT_RECEDE
        steps = int(budget) + (random.random() < budget % 1)
        if vigor:
            self.mat.grow(steps)
            self.grow_patches(PATCH_GROW * (0.5 + min(vigor, 6) / 6))
        else:
            self.mat.shrink(steps)                        # 全体饿扁：菌毯慢慢退
        occupied = self.mat.occupied()
        for mark in MAT_MILESTONES:
            if occupied >= mark > self.mat_mark:
                self.log_event("菌毯沿着屏幕边上" + self.ring_words(mark))
                self.mat_mark = mark
        self.check_spitter()
        if self.spitter_view:
            self.spitter_view.place()

    def grow_mat_offline(self, elapsed: float):
        steps = elapsed * self.passive_mult / MAT_TICK
        if steps < 1:
            return
        for c in self.creatures:
            if random.random() < 1 - (1 - MAT_SEED * MAT_VIGOR[c.stage] * c.growth_factor) ** steps:
                self.mat.seed(self.mat_index(c))
        vigor = sum(MAT_VIGOR[c.stage] * c.growth_factor for c in self.creatures)
        if vigor:
            self.mat.grow(int(min(MAT_OFFLINE_CAP, steps * MAT_RATE * vigor)))
            self.grow_patches(steps * PATCH_GROW * (0.5 + min(vigor, 6) / 6))
        else:
            self.mat.shrink(int(min(MAT_OFFLINE_CAP, steps * MAT_RECEDE)))
        self.check_spitter(quiet=True)

    def build_mat_views(self):
        old = list(self.mat_views.values()) + list(self.patch_views.values()) + [self.spitter_view]
        for v in old:
            if v:
                v.close()
        self.mat_views, self.patch_views, self.spitter_view = {}, {}, None
        if self.mat_layer != "hidden":
            a, m, t = self.mat_area(), self.mat, MAT_STRIP * PX
            w, h = m.cols * PX, m.rows * PX
            rects = {"top": QRect(a.x(), a.y(), w, t), "bottom": QRect(a.x(), a.y() + h - t, w, t),
                     "left": QRect(a.x(), a.y(), t, h), "right": QRect(a.x() + w - t, a.y(), t, h)}
            self.mat_views = {edge: MatStrip(edge, rect, self.mat_layer) for edge, rect in rects.items()}
            self.render_mat(full=True)
            for p in self.patches:
                self.add_patch_view(p)
            if self.spitter:
                self.spitter_view = SpitterWidget(self, self.mat_layer)
            for v in list(self.mat_views.values()) + [self.spitter_view]:
                if v and not self.hidden_for_fullscreen:
                    v.show()
        for w in self.widgets.values():
            w.raise_()

    # ── 菌斑 ──
    def add_patch_view(self, p: Patch):
        if self.mat_layer == "hidden":
            return
        v = PatchView(p, self.mat_layer)
        self.patch_views[id(p)] = v
        if not self.hidden_for_fullscreen:
            v.show()
        for w in self.widgets.values():
            w.raise_()

    def grow_patches(self, amount: float):
        for p in self.patches:
            if p.r < p.max:
                p.r = min(p.max, p.r + amount)
                if id(p) in self.patch_views:
                    self.patch_views[id(p)].render()

    def mat_at(self, x: float, y: float):
        """孢子在 (x, y) 落地形成菌毯：贴边就加厚边缘菌毯，打中已有菌斑就让它长大，否则长出新菌斑"""
        a, m = self.mat_area(), self.mat
        cx, cy = (x - a.x()) / PX, (y - a.y()) / PX
        if min(cx, cy, m.cols - 1 - cx, m.rows - 1 - cy) < MAT_MAX + 4:
            i = m.nearest(cx, cy)
            m.seed(i)
            m.grow(12, near=i)
            self.render_mat()
            return
        hit = next((p for p in self.patches if math.hypot(p.x - x, p.y - y) < (p.r + 2) * PX), None)
        if hit is None and len(self.patches) >= MAX_PATCHES:
            hit = random.choice(self.patches)
        if hit:
            hit.max = min(PATCH_MAX[1] + 2, hit.max + 1)
            hit.r = min(hit.max, hit.r + 1)
            if id(hit) in self.patch_views:
                self.patch_views[id(hit)].render()
            return
        p = Patch(int(x), int(y), 1.0, random.randint(*PATCH_MAX), random.randrange(1 << 30))
        self.patches.append(p)
        self.log_event("喷孢菌的孢子在桌面中间长出了一块菌斑")
        self.add_patch_view(p)

    # ── 喷孢菌 ──
    def shot_gap(self) -> float:
        return random.uniform(*SPITTER_EVERY) / self.passive_mult

    def check_spitter(self, quiet: bool = False):
        m = self.mat
        if self.spitter or m.occupied() < SPITTER_AT:
            return
        cands = [i for i in m.active if m.d[i] >= MAT_SPROUT_DEPTH
                 and 12 <= m.edge_of(i)[1] < m.edge_len(m.edge_of(i)[0]) - 12]
        if not cands:
            return
        cands.sort(key=lambda i: m.eff(i) + random.random() * 2, reverse=True)
        self.plant_spitter(random.choice(cands[:20]), quiet)

    def plant_spitter(self, i: int, quiet: bool = False):
        m = self.mat
        edge, pos = m.edge_of(i)
        now = time.time()
        self.spitter = {"i": i % m.n, "edge": edge, "frac": (pos + 0.5) / m.edge_len(edge), "born": now - (99 if quiet else 0),
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
        a, m, i = self.mat_area(), self.mat, self.spitter["i"]
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
        a = self.mat_area()
        pt = start
        for _ in range(12):
            pt = (random.uniform(a.left() + 40, a.right() - 40), random.uniform(a.top() + 40, a.bottom() - 40))
            if math.dist(pt, start) > 240:
                break
        return pt

    def spitter_tick(self):
        self.shots = [s for s in self.shots if not s.done]
        if self.spitter and time.time() >= self.spitter["next_at"]:
            if self.starving():                           # 全体饿扁：不喷，往后推
                self.spitter["next_at"] = time.time() + self.shot_gap()
            else:
                self.shoot()

    def shoot(self, outcome: str | None = None, target: tuple[float, float] | None = None) -> SporeShot:
        sp, now = self.spitter, time.time()
        (ux, uy), _ = EDGE_POSE[self.mat.edge_of(sp["i"])[0]]
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
        return (f"喷孢菌\n下一次 ~{max(0, int(sp['next_at'] - time.time()))}s\n"
                f"喷了 {sp['shots']} 次：" + " · ".join(f"{OUTCOME_NAMES[k]} {st[k]}" for k in OUTCOME_NAMES))

    def spitter_menu(self, pos: QPoint):
        sp = self.spitter
        if not sp:
            return
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        for line in (f"NAME   喷孢菌", f"AGE    {fmt_age(time.time() - sp['born'])}", f"SHOTS  {sp['shots']}",
                     "  ".join(f"{OUTCOME_NAMES[k]} {sp['stats'][k]}" for k in OUTCOME_NAMES),
                     f"NEXT   ~{max(0, int(sp['next_at'] - time.time()))}s"):
            m.addAction(line).setEnabled(False)
        m.addSeparator()
        m.addAction("现在喷！", lambda: sp.__setitem__("next_at", time.time() + 0.8))
        self.mat_menu(m)
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        m.exec(pos)

    def render_mat(self, full: bool = False):
        m = self.mat
        if full:
            todo = {j % m.n for i in m.active for j in range(i - 12, i + 13)}
        else:
            todo = m.dirty
        m.dirty = set()
        if not self.mat_views:
            return
        touched = set()
        for i in todo:
            edge = m.edge_of(i)[0]
            self.mat_views[edge].render_column(m, i)
            touched.add(edge)
        for edge in touched:
            self.mat_views[edge].update()

    def set_mat_layer(self, layer: str):
        self.mat_layer = layer
        self.build_mat_views()
        self.save()

    def mat_menu(self, parent: QMenu) -> QMenu:
        sub = parent.addMenu("菌毯")
        sub.setStyleSheet(MENU_QSS)
        group = QActionGroup(sub)
        for key, label in (("top", "铺在窗口上面"), ("bottom", "只铺在桌面上"), ("hidden", "隐藏")):
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.mat_layer == key)
            act.triggered.connect(lambda _=False, k=key: self.set_mat_layer(k))
            group.addAction(act)

        def refresh():
            sub.setTitle(f"菌毯  {self.mat.coverage() * 100:.1f}%" + (f" · 菌斑 {len(self.patches)}" if self.patches else ""))
            for act, key in zip(group.actions(), ("top", "bottom", "hidden")):
                act.setChecked(self.mat_layer == key)
        refresh()
        parent.aboutToShow.connect(refresh)
        return sub

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
        if eaten and self.mat.active:
            self.mat.grow(int(sum(v for v, _ in eaten) * MAT_FEED), near=self.mat_index(c))
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

    # ── 菜单 ──
    def show_menu(self, widget: CreatureWidget, pos: QPoint):
        c = widget.c
        m = QMenu()
        m.setStyleSheet(MENU_QSS)

        def info(text):
            a = m.addAction(text)
            a.setEnabled(False)

        lo, hi = c.stage_floor(), c.next_goal()
        filled = round(10 * max(0.0, min(1.0, (c.nutrition - lo) / (hi - lo))))
        info(f"NAME   {c.name}")
        info(f"STAGE  {c.stage_name}")
        info(f"AGE    {fmt_age(time.time() - c.born)}")
        info(f"GEN    {c.gen}")
        info(f"FOOD   {'█' * filled}{'░' * (10 - filled)} {int(c.nutrition)}/{hi}")
        full = round(10 * c.satiety / SATIETY_MAX)
        info(f"FULL   {'█' * full}{'░' * (10 - full)} {int(c.satiety)}%  {MOOD_NAMES[c.mood]}")
        for label, v in (("ENERGY", c.energy), ("HAPPY", c.happiness)):
            info(f"{label:<6} {'█' * round(v / 10)}{'░' * (10 - round(v / 10))} {int(v)}%")
        info(f"FEEDS  {c.feeds}   SPORES {c.released}")
        m.addSeparator()
        m.addAction("状态面板…", lambda: self.open_panel(widget))
        m.addAction("聊天…（双击也行）", lambda: self.open_chat(widget))
        log_menu = m.addMenu("最近吃的")
        log_menu.setStyleSheet(MENU_QSS)
        for ts, label, value in reversed(c.log[-8:]):
            log_menu.addAction(f"[{time.strftime('%m-%d %H:%M', time.localtime(ts))}] {label}  +{value}").setEnabled(False)
        if not c.log:
            log_menu.addAction("（还没吃过东西）").setEnabled(False)
        m.addAction("喂文件…", lambda: self.feed_dialog(widget, folder=False))
        m.addAction("喂文件夹…", lambda: self.feed_dialog(widget, folder=True))
        devour = m.addAction("吞噬文件（吃掉后" + ("进回收站）" if sys.platform == "win32" else "删除）"))
        devour.setCheckable(True)
        devour.setChecked(self.devour)
        devour.toggled.connect(self.set_devour)
        m.addSeparator()
        top = m.addAction("总在最前")
        top.setCheckable(True)
        top.setChecked(self.on_top)
        top.toggled.connect(self.set_on_top)
        m.addAction("叫大家回来", self.gather)
        self.mat_menu(m)
        m.addAction("聊天设置…" + ("" if self.chat_ready() else "（未设置）"), self.chat_settings)
        if len(self.creatures) > 1:
            m.addAction(f"放生 {c.name}…", lambda: self.release(widget))
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        m.exec(pos)

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
        self.mat = Mycelium(self.mat.cols, self.mat.rows)
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
        m.addAction("叫大家回来", self.gather)
        self.mat_menu(m)
        m.addAction("聊天设置…", self.chat_settings)
        m.addAction(f"存档位置：{self.save_path}").setEnabled(False)
        m.addAction("重新开始…", self.reset)
        m.addSeparator()
        m.addAction("退出", QApplication.instance().quit)
        tray.setContextMenu(m)
        tray.show()
        tray._menu = m
        return tray


# ───────────────────────────── 入口 ─────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="FUNGI.EXE — 桌面真菌宠物 demo")
    ap.add_argument("--fast", action="store_true", help="调试：成长加速")
    default_dir = (Path(os.environ.get("APPDATA", Path.home())) if sys.platform == "win32"
                   else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))) / "fungi"
    ap.add_argument("--data-dir", type=Path, default=default_dir)
    args = ap.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = open(args.data_dir / "lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("[fungi] 已经在运行了（同一个存档目录只能开一个）", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("fungi")
    app.setQuitOnLastWindowClosed(False)
    colony = Colony(args.data_dir, fast=args.fast)
    app.aboutToQuit.connect(colony.save)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    pump = QTimer()                       # 让 Python 有机会处理 Ctrl+C / SIGTERM
    pump.timeout.connect(lambda: None)
    pump.start(300)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
