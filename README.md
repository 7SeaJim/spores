# FUNGI.EXE — 桌面真菌宠物 demo

桌面上常驻一只黑白像素风的小菌。它一开始只是一个 **3×3 的黑色 spores**。

> 把自己电脑里的东西拖给一个小黑色生物，看着它慢慢长大。

**最小闭环**：3×3 黑色方块 → 拖入文件 → 方块变大 → 变成蘑菇 → 放出新的 3×3 spores。

## 运行

依赖：Python 3.10+、PyQt6（Debian: `sudo apt install python3-pyqt6`）。X11 桌面（开合成器，XFCE 默认就开）。

```bash
python3 fungi.py            # 正常模式
python3 fungi.py --fast     # 调试：自然生长 ×30，喂食营养 ×3
python3 fungi.py --data-dir /tmp/fungi-test   # 用单独存档，不影响正式菌落
```

退出：右键菌 → 退出，或托盘图标 → 退出，或在终端 Ctrl+C。

## 怎么玩

| 操作 | 效果 |
| --- | --- |
| 从文件管理器 / 桌面拖文件或文件夹到菌身上 | 喂食 |
| 右键 → 喂文件… / 喂文件夹… | 不方便拖的时候用对话框喂 |
| 左键拖动 | 搬家 |
| 单击 | 戳一下 |
| 鼠标悬停 | 名字、阶段、到下一阶段的进度条 |
| 右键 | 状态（NAME / STAGE / AGE / GEN / FOOD / FEEDS）、最近吃的、置顶、叫大家回来、放生 |
| 托盘 → 重新开始… | 清空菌落 |

## 规则（v0）

**食物**（只看文件大小和目录结构，不读内容）

| 食物 | 营养 |
| --- | --- |
| `.txt` / `.md` | 按大小 +4 ~ +20（2KB ≈ +13），空文件 +2 |
| 文件夹 | +6 + 2×（里面的 txt/md 数量），最多 +30；最多往下看 3 层 / 3000 项，跳过隐藏文件 |
| 同一个文件再喂一次 | 1/4 营养（文件被修改过就算新食物） |
| 其他类型 | 摇头，不吃 |
| 一次拖太多 | 只吃前 5 个 |

另外每 2 分钟自然 +1 营养；关掉程序期间也会长，最多补 +24。

**阶段**：spores（0，3×3 → 4×4 → 5×5）→ sprout（20）→ baby（50）→ young（100）→ adult（170）

**繁殖**：刚成年放出 2 个 spores，之后每再吃 60 营养放 1 个。孢子会飞到旁边落地，是下一代（GEN+1），从 3×3 重新开始长。菌落上限 12 只。

所有数值都在 `fungi.py` 顶部的「可调参数」里。

## 文件安全

- 只调用 `stat` / `scandir`，不打开、不修改、不删除食物文件。
- 拖放只接受「复制 / 链接」动作，从不接受「移动」，所以文件管理器不会把源文件挪走。
- 存档只记食物的指纹（路径+大小+修改时间的哈希，用于判断「吃过了」）和最近 50 条文件名，不存完整路径。

## 存档

`~/.local/share/fungi/save.json`，每次喂食、搬家、成长时立刻保存，另外每 30 秒自动保存。
存档损坏会自动备份成 `save.broken-<时间>.json` 并重新开始。同一个存档目录只能开一个程序。

## 开机自启（可选）

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/fungi.desktop <<DESKTOP
[Desktop Entry]
Type=Application
Name=Fungi
Exec=python3 $HOME/Projects/spores/fungi.py
DESKTOP
```

## 自测

```bash
QT_QPA_PLATFORM=offscreen python3 selftest.py [预览图输出目录]
```

覆盖：喂食规则、真实拖放事件（含拒绝 Move）、食物文件不被改动、成长和繁殖、存档/读档、离线生长、存档损坏，并输出窗口截图 `widgets.png`。

## 之后可以加的（设计图里有，demo 没做）

- INFO / FEED LOG / CONFIG 小窗口（FUNGI.EXE 主面板）
- HUNGER / ENERGY / HAPPINESS 数值和对应的照料按钮
- 不同后缀不同营养效果（`.log` 能量+、`.poem` 快乐+、`.todo` 成长+、`.secret` ???）
- 读文件内容决定口味、性格和变异；菌落之间的互动
