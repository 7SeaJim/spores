"""菌落 · 聊天：大事记、亲缘、记忆、人设、本地回复、发请求。"""
from __future__ import annotations

import json
import math
import os
import random
import re
import threading
import time
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QTimer

from .config import (CHAT_BURST, CHAT_COOLDOWN, CHAT_HISTORY, CHAT_MEMORY, CHAT_TIMEOUT, CHRONICLE_IN_PROMPT,
                     CHRONICLE_MAX, FILEGU_ECHOES, FILEGU_SAMPLES, FILEGU_STYLE, LOCAL_DETOUR, LOCAL_DIGEST, LOCAL_LORE,
                     LOCAL_NORMAL, LOCAL_RETURN, LUCID_ABOUT_FILES, LUCID_LINES, LUCID_TRIGGER, MOOD_CN, MOOD_NAMES,
                     OFFLINE, pick_word, RESIDUE_MAX, STAGE_CN, STYLE_DIRECTIVES)
from .model import Creature
from .chat import (chat_request, ChatError, filegu_clean, one_sentence, pick_style, scrub_history, session_styles,
                   too_similar)
from .creature_view import CreatureWidget
from .chat_ui import ChatInput, ChatSettings, SpeechBubble


class ChatMixin:
    """聊天相关：大事记、亲缘、记忆、人设拼装、本地回复、发请求和收回复"""

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
            return "菌毯沿着屏幕边上" + self.ring_words(self.mat_occupied()) + "，还在往前摸。"
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
        facts.append("菌毯沿着屏幕边上" + self.ring_words(self.mat_occupied()))
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
        world = ["菌毯沿着屏幕边上" + self.ring_words(self.mat_occupied())]
        if self.salted_share():
            world.append("有一段边被人撒了盐，那里咸，长不动")
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
