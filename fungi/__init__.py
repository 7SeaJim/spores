"""
FUNGI.EXE — 桌面真菌宠物（最小闭环 demo）

    桌面上的 3×3 黑色 spores → 拖入 .txt / .md / 文件夹 → 长大 → 变成蘑菇 → 放出新的 spores

运行:
    python3 -m fungi                 正常模式
    python3 -m fungi --fast          调试：成长加速（自然生长 ×30，喂食营养 ×3）
    python3 -m fungi --data-dir DIR  使用单独的存档目录

操作:
    拖文件 / 文件夹到它身上 = 喂食：能吃的 .txt / .md 会被真的吃掉（Linux 等直接删除，Windows 移到回收站），
                                  吃掉的完整路径记在存档目录的 eaten.log；右键可关掉「吞噬文件」
    左键拖动 = 搬家    单击 = 戳一下    双击 = 聊天（DeepSeek，自己填 API Key）    右键 = 状态和菜单
"""
import sys
import types

from .config import *            # noqa: F401,F403  对外：fungi.PX、fungi.Colony …… 和拆分前一样能直接取
from .sprites import *           # noqa: F401,F403
from .model import *             # noqa: F401,F403
from .food import *              # noqa: F401,F403
from .desktop import *           # noqa: F401,F403
from .chat import *              # noqa: F401,F403
from .ui import *                # noqa: F401,F403
from .sound import *             # noqa: F401,F403
from .creature_view import *     # noqa: F401,F403
from .mat import *               # noqa: F401,F403
from .spitter import *           # noqa: F401,F403
from .salt import *              # noqa: F401,F403
from .chat_ui import *           # noqa: F401,F403
from .panel import *             # noqa: F401,F403
from .colony_chat import *       # noqa: F401,F403
from .colony_feed import *       # noqa: F401,F403
from .colony_mat import *        # noqa: F401,F403
from .colony_salt import *       # noqa: F401,F403
from .colony_spitter import *    # noqa: F401,F403
from .colony_menu import *       # noqa: F401,F403
from .colony import *            # noqa: F401,F403
from .app import main            # noqa: F401


class _Package(types.ModuleType):
    """`fungi.X = 新值`（自测、调试时替换参数或函数）同步到所有引用了同一个 X 的子模块，
    和拆分前在单文件里改一个全局名的效果一样。"""

    def __setattr__(self, name, value):
        old = self.__dict__.get(name, _MISSING)
        if old is not _MISSING and not isinstance(value, types.ModuleType):
            prefix = self.__name__ + "."
            for mod_name, mod in list(sys.modules.items()):
                if mod_name.startswith(prefix) and mod is not None and mod.__dict__.get(name, _MISSING) is old:
                    setattr(mod, name, value)
        super().__setattr__(name, value)


_MISSING = object()
sys.modules[__name__].__class__ = _Package
