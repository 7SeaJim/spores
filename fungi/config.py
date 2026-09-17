"""可调参数：所有数值、文案常量和配色都在这里。"""
from __future__ import annotations

import random
import re
from pathlib import Path

from PyQt6.QtGui import QColor


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
# 撒盐：让菌毯停在现在的样子
SALT_FULL = 255            # 一格盐满的时候的量
SALT_HOURS = 24            # 盐自己慢慢化掉要多少小时
SALT_BITE = 3              # 菌毯往撒了盐的格子里挤一次，啃掉多少盐（越旺盛的菌落啃得越快）
SALT_RADIUS = 5            # 撒一下盖住左右几格
SALT_REACH = MAT_STRIP + 4 # 离屏幕边多少格以内算撒在边缘菌毯上
SALT_GLOOM = 4             # 被撒盐时它们掉多少快乐
MAT_RECEDE = 0.3           # 全体饿扁时，边缘菌毯每次（MAT_TICK）退缩几步
MOOD_NAMES = {"full": "", "hungry": "饿", "starving": "饿扁了", "dormant": "休眠"}

DEVOUR = True              # 默认吞噬文件（右键菜单可关）
PROJECT_DIR = Path(__file__).resolve().parent.parent   # 仓库根目录（打包后是解压目录）

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

ART_DIR = PROJECT_DIR / "art"   # pixel4ai 画稿（.pxl），缺失时回退到程序生成

INK = QColor(17, 17, 17)
PAPER = QColor(250, 249, 244)
