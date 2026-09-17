"""食物规则：能不能吃、营养多少、口味、残渣，以及真的吃掉（删除 / 进回收站）。"""
from __future__ import annotations

import hashlib
import math
import os
import sys
import time
from pathlib import Path

from .config import ARCHIVED_NAME, CLOUD_DIRS, EDIBLE_EXT, FOOD_KINDS, LOG_HOT_MINUTES, MESSY_NAME
from .model import Food


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
