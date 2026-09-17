"""聊天文本：一句话截断、文件菇清洗、说法挑选、DeepSeek 请求。"""
from __future__ import annotations

import difflib
import json
import random
import re
import time
import urllib.error
import urllib.request

from .config import (CHAT_BASE_URL, CHAT_ERRORS, CHAT_MAX_CHARS, CHAT_MODELS, CHAT_SESSION_GAP, CHAT_TEMPERATURE,
                     CHAT_TIMEOUT, FILEGU_BURP, FILEGU_LUCID, FILEGU_MECHANISMS, FILEGU_NORMAL)


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
