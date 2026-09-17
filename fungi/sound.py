"""声音：标准库合成 WAV，不依赖 QtMultimedia。"""
from __future__ import annotations

import math
import os
import random
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path


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
