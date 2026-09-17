"""数据：一只菌（Creature）和一份食物（Food）。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
from pathlib import Path

from .config import ADULT, CHEERY_AT, FIRST_BURST, GLOOMY_AT, HUNGRY_AT, SPORE_EVERY, STAGES, TIRED_AT


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
