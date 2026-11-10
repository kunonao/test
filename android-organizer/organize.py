#!/usr/bin/env python3
"""
Android スマホのファイル整理ツール
==================================

ダウンロードフォルダなどの「ぐちゃぐちゃ」を、

  1. 種類別フォルダ（画像・動画・PDF・文書 …）に振り分け、
  2. 中身がまったく同じ重複や、「xxx (1).pdf」「xxx-1.pdf」のような
     同じ名前の古い版を見つけて、いちばん新しいものだけを残し、
     ほかは「_削除候補」フォルダへ移します。

いきなり消すことはしません。「_削除候補」の中身を確認してから、
自分で（またはファイルアプリで）消してください。
何もオプションを付けずに動かすと「下見」だけで、何も変更しません。

PC から USB（ADB）でつないだスマホを整理する:
    python organize.py --adb                 # 下見（何も変更しない）
    python organize.py --adb --apply         # 実行
    python organize.py --adb --undo logs/organize-log-XXXX.json --apply   # 元に戻す

スマホの Termux で直接動かす:
    python organize.py                       # 下見
    python organize.py --apply               # 実行

Python 3.8 以上の標準ライブラリだけで動きます。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

DEFAULT_DIR = "/sdcard/Download"
TRASH_DIR_NAME = "_削除候補"
LOG_DIR = Path(__file__).resolve().parent / "logs"

CATEGORIES: List[Tuple[str, Set[str]]] = [
    ("画像", {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "svg", "tif", "tiff", "avif"}),
    ("動画", {"mp4", "mov", "mkv", "webm", "3gp", "avi", "m4v", "wmv", "ts"}),
    ("音声", {"mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "amr", "mid", "midi", "wma"}),
    ("PDF", {"pdf"}),
    ("文書", {"doc", "docx", "txt", "rtf", "odt", "md", "epub", "pages", "hwp"}),
    ("表計算", {"xls", "xlsx", "xlsm", "csv", "ods", "numbers"}),
    ("プレゼン", {"ppt", "pptx", "odp", "key"}),
    ("アプリ", {"apk", "xapk", "apks", "apkm"}),
    ("圧縮ファイル", {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "lzh"}),
]
OTHER_CATEGORY = "その他"
CATEGORY_NAMES = [name for name, _ in CATEGORIES] + [OTHER_CATEGORY]
EXT_TO_CATEGORY = {ext: name for name, exts in CATEGORIES for ext in exts}

# ダウンロード途中・一時ファイルは触らない
SKIP_EXTS = {"crdownload", "part", "partial", "download", "tmp", "pending"}

# 「report (1)」「report(2)」「report（3）」→「report」
PAREN_SUFFIX = re.compile(r"^(?P<base>.*\S)\s*[(（]\s*\d{1,3}\s*[)）]$")
# 「report-1」→「report」（ただし「report」本体も存在するときだけ）
DASH_SUFFIX = re.compile(r"^(?P<base>.+)-\d{1,3}$")


class UserError(Exception):
    """利用者向けのメッセージだけを表示して終了するエラー"""


# ---------------------------------------------------------------------------
# データ
# ---------------------------------------------------------------------------


def split_name(name: str) -> Tuple[str, str]:
    """'report (1).pdf' -> ('report (1)', '.pdf')。先頭ドットや末尾ドットは拡張子扱いしない。"""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return name, ""
    return name[:dot], name[dot:]


def strip_paren_suffix(stem: str) -> str:
    while True:
        m = PAREN_SUFFIX.match(stem)
        if not m:
            return stem
        stem = m.group("base")


@dataclass
class FileInfo:
    path: str
    dir: str
    name: str
    size: int
    mtime: int

    @property
    def stem(self) -> str:
        return split_name(self.name)[0]

    @property
    def ext(self) -> str:
        return split_name(self.name)[1]

    @property
    def ext_key(self) -> str:
        return self.ext[1:].lower()

    @property
    def category(self) -> str:
        return EXT_TO_CATEGORY.get(self.ext_key, OTHER_CATEGORY)


@dataclass
class Move:
    src: str
    dst: str
    kind: str  # "trash"（削除候補へ） / "sort"（種類別フォルダへ） / "rename"（名前の (1) などを外す）
    reason: str = ""


@dataclass
class DupGroup:
    keeper: FileInfo
    clean_name: str
    others: List[Tuple[FileInfo, str]]  # (移すファイル, 理由)


@dataclass
class Plan:
    target: str
    loose: List[FileInfo]
    organized: List[FileInfo]
    skipped: List[FileInfo]
    groups: List[DupGroup]
    moves: List[Move]
    dirs_to_create: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ファイル操作（スマホ上で直接 / PC から ADB 経由）
# ---------------------------------------------------------------------------


class LocalBackend:
    """このプログラムが動いている端末のフォルダを直接操作する（Termux など）"""

    mode = "local"
    label = "この端末で直接操作"

    def join(self, *parts: str) -> str:
        return os.path.join(*parts)

    def dirname(self, path: str) -> str:
        return os.path.dirname(path)

    def normalize(self, path: str) -> str:
        return os.path.normpath(os.path.expanduser(path))

    def check_connection(self) -> None:
        pass

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def list_files(self, d: str) -> List[FileInfo]:
        """d の直下にある、隠しファイル以外の普通のファイル"""
        out: List[FileInfo] = []
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        if not e.is_file(follow_symlinks=False):
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    out.append(FileInfo(e.path, d, e.name, st.st_size, int(st.st_mtime)))
        except (FileNotFoundError, NotADirectoryError):
            return []
        return out

    def list_names(self, d: str) -> List[str]:
        try:
            return os.listdir(d)
        except (FileNotFoundError, NotADirectoryError):
            return []

    def md5(self, paths: List[str]) -> Dict[str, str]:
        res: Dict[str, str] = {}
        for p in paths:
            h = hashlib.md5()
            try:
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            except OSError:
                continue
            res[p] = h.hexdigest()
        return res

    def make_dirs(self, dirs: List[str]) -> None:
        for d in dirs:
            os.makedirs(d, exist_ok=True)

    def remove_empty_dirs(self, dirs: List[str]) -> None:
        for d in dirs:
            try:
                os.rmdir(d)  # 空のときだけ消える
            except OSError:
                pass

    def move_many(self, moves: List[Move]) -> List[Optional[str]]:
        """1件ずつ移動。成功は None、失敗は理由の文字列。移動先に同名があれば上書きしない。"""
        results: List[Optional[str]] = []
        for m in moves:
            if os.path.lexists(m.dst):
                results.append("移動先に同じ名前のファイルがあります")
                continue
            try:
                os.rename(m.src, m.dst)
            except OSError:
                try:
                    shutil.move(m.src, m.dst)
                except OSError as e:
                    results.append(f"移動に失敗しました（{e}）")
                    continue
            results.append(None)
        return results


class AdbBackend:
    """PC から USB（ADB）でつないだ Android を adb shell で操作する"""

    mode = "adb"
    # 古い adb でも通るよう、1回に送るシェルコマンドを小さめに区切る
    MAX_CMD_BYTES = 3000

    def __init__(self, adb_path: str = "adb", serial: Optional[str] = None):
        self.base_cmd = [adb_path] + (["-s", serial] if serial else [])
        self.serial = serial
        self.label = "USB（ADB）でつないだスマホ" + (f"（{serial}）" if serial else "")

    def join(self, *parts: str) -> str:
        return posixpath.join(*parts)

    def dirname(self, path: str) -> str:
        return posixpath.dirname(path)

    def normalize(self, path: str) -> str:
        return posixpath.normpath(path.replace("\\", "/"))

    def _run(self, args: List[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                self.base_cmd + args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise UserError(
                "adb コマンドが見つかりません。\n"
                "Android SDK Platform-Tools をインストールして、adb を使えるようにしてください。\n"
                "  https://developer.android.com/tools/releases/platform-tools\n"
                "（adb の場所を指定する場合は --adb-path を使います）"
            )

    def check_connection(self) -> None:
        try:
            p = self._run(["get-state"], timeout=30)
        except subprocess.TimeoutExpired:
            raise UserError("スマホから応答がありません。USB ケーブルをつなぎ直してみてください。")
        state = p.stdout.decode("utf-8", "replace").strip()
        err = p.stderr.decode("utf-8", "replace").strip()
        if state == "device":
            return
        if "unauthorized" in (state + err):
            raise UserError(
                "スマホがまだ PC を許可していません。\n"
                "スマホの画面に「USB デバッグを許可しますか？」が出ているので「許可」を押してください。\n"
                "（出ていなければ USB ケーブルを抜き差ししてください）"
            )
        if "more than one" in err:
            raise UserError(
                "スマホ（またはエミュレータ）が複数つながっています。\n"
                "`adb devices` で番号を確認し、-s 番号 で指定してください。"
            )
        raise UserError(
            "スマホが見つかりません。次を確認してください。\n"
            "  ・USB ケーブルでつながっているか（充電専用ケーブルでは不可）\n"
            "  ・スマホの「開発者向けオプション」で「USB デバッグ」が ON か\n"
            f"（adb の出力: {err or state or 'なし'}）"
        )

    def sh(self, command: str) -> str:
        p = self._run(["shell", command])
        return p.stdout.decode("utf-8", "surrogateescape").replace("\r\n", "\n")

    def sh_batched(self, pieces: List[str]) -> str:
        outs: List[str] = []
        batch: List[str] = []
        size = 0
        for piece in pieces:
            n = len(piece.encode("utf-8", "surrogateescape")) + 3
            if batch and size + n > self.MAX_CMD_BYTES:
                outs.append(self.sh(" ; ".join(batch)))
                batch, size = [], 0
            batch.append(piece)
            size += n
        if batch:
            outs.append(self.sh(" ; ".join(batch)))
        return "\n".join(outs)

    def is_dir(self, path: str) -> bool:
        q = shlex.quote(path)
        return self.sh(f"[ -d {q} ] && echo YES").strip() == "YES"

    def list_files(self, d: str) -> List[FileInfo]:
        q = shlex.quote(d)
        out = self.sh(
            f"[ -d {q} ] && find {q} -maxdepth 1 -type f ! -name '.*' "
            f"-exec stat -c '%s %Y %n' {{}} + 2>/dev/null"
        )
        files: List[FileInfo] = []
        for line in out.split("\n"):
            parts = line.split(" ", 2)
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
                continue
            path = parts[2]
            files.append(FileInfo(path, d, posixpath.basename(path), int(parts[0]), int(parts[1])))
        return files

    def list_names(self, d: str) -> List[str]:
        q = shlex.quote(d)
        out = self.sh(f"[ -d {q} ] && ls -1A {q} 2>/dev/null")
        return [line for line in out.split("\n") if line]

    def md5(self, paths: List[str]) -> Dict[str, str]:
        out = self.sh_batched([f"md5sum {shlex.quote(p)} 2>/dev/null" for p in paths])
        res: Dict[str, str] = {}
        for line in out.split("\n"):
            m = re.match(r"^\\?([0-9a-f]{32})\s+\*?(.*)$", line)
            if m:
                res[m.group(2)] = m.group(1)
        return res

    def make_dirs(self, dirs: List[str]) -> None:
        if dirs:
            self.sh_batched([f"mkdir -p {shlex.quote(d)}" for d in dirs])

    def remove_empty_dirs(self, dirs: List[str]) -> None:
        if dirs:
            self.sh_batched([f"rmdir {shlex.quote(d)} 2>/dev/null" for d in dirs])

    def move_many(self, moves: List[Move]) -> List[Optional[str]]:
        pieces = []
        for i, m in enumerate(moves):
            s, d = shlex.quote(m.src), shlex.quote(m.dst)
            pieces.append(
                f"if [ -e {d} ]; then echo 'NG {i} exists'; "
                f"elif mv {s} {d} 2>/dev/null; then echo 'OK {i}'; "
                f"else echo 'NG {i} failed'; fi"
            )
        out = self.sh_batched(pieces)
        results: List[Optional[str]] = ["結果を確認できませんでした"] * len(moves)
        for line in out.split("\n"):
            parts = line.strip().split(" ")
            if len(parts) < 2 or not parts[1].isdigit() or int(parts[1]) >= len(moves):
                continue
            i = int(parts[1])
            if parts[0] == "OK":
                results[i] = None
            elif parts[0] == "NG":
                exists = len(parts) > 2 and parts[2] == "exists"
                results[i] = "移動先に同じ名前のファイルがあります" if exists else "移動に失敗しました"
        return results


# ---------------------------------------------------------------------------
# 計画づくり
# ---------------------------------------------------------------------------


def allocate_name(occupied: Set[str], desired: str, fallback: Optional[str] = None) -> str:
    """フォルダ内で空いている名前を決める（Android の共有ストレージは大文字小文字を区別しないので小文字で比較）"""
    for cand in (desired, fallback):
        if cand and cand.lower() not in occupied:
            occupied.add(cand.lower())
            return cand
    stem, ext = split_name(desired)
    n = 2
    while f"{stem} ({n}){ext}".lower() in occupied:
        n += 1
    name = f"{stem} ({n}){ext}"
    occupied.add(name.lower())
    return name


def find_duplicate_groups(backend, files: List[FileInfo], exact_only: bool) -> List[DupGroup]:
    n = len(files)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 1) 中身がまったく同じもの（サイズが同じものだけハッシュを計算する）
    by_size: Dict[int, List[int]] = defaultdict(list)
    for i, f in enumerate(files):
        if f.size > 0:
            by_size[f.size].append(i)
    candidates = [i for idx in by_size.values() if len(idx) > 1 for i in idx]
    hashes = backend.md5([files[i].path for i in candidates]) if candidates else {}
    digest: Dict[int, Optional[str]] = {i: hashes.get(files[i].path) for i in candidates}
    by_digest: Dict[Tuple[int, str], List[int]] = defaultdict(list)
    for i in candidates:
        if digest[i]:
            by_digest[(files[i].size, digest[i])].append(i)
    for idx in by_digest.values():
        for k in idx[1:]:
            union(idx[0], k)

    # 2) 名前が同じ版違い（「xxx (1).pdf」「xxx-1.pdf」など）
    base = [strip_paren_suffix(f.stem) for f in files]
    existing = {(b.lower(), f.ext_key) for b, f in zip(base, files)}
    for i, f in enumerate(files):
        m = DASH_SUFFIX.match(base[i])
        if m and (m.group("base").lower(), f.ext_key) in existing:
            base[i] = m.group("base")
    name_key = [(b.lower(), f.ext_key) for b, f in zip(base, files)]
    if not exact_only:
        by_key: Dict[Tuple[str, str], List[int]] = defaultdict(list)
        for i, key in enumerate(name_key):
            by_key[key].append(i)
        for idx in by_key.values():
            for k in idx[1:]:
                union(idx[0], k)

    comps: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        comps[find(i)].append(i)

    groups: List[DupGroup] = []
    for idx in comps.values():
        if len(idx) < 2:
            continue
        # いちばん新しいものを残す（同時刻なら (1) などが付いていない名前を優先）
        keeper_i = max(idx, key=lambda i: (files[i].mtime, base[i] == files[i].stem, -len(files[i].name)))
        keeper = files[keeper_i]
        others: List[Tuple[FileInfo, str]] = []
        for i in sorted(idx, key=lambda i: -files[i].mtime):
            if i == keeper_i:
                continue
            f = files[i]
            if f.size == keeper.size and digest.get(i) and digest.get(i) == digest.get(keeper_i):
                reason = "中身がまったく同じ重複"
            elif name_key[i] == name_key[keeper_i]:
                reason = "同じ名前の古い版（中身は違います）"
            else:
                reason = "同じグループの古いファイル"
            others.append((f, reason))
        groups.append(DupGroup(keeper, base[keeper_i] + keeper.ext, others))
    groups.sort(key=lambda g: g.clean_name.lower())
    return groups


def build_plan(backend, target: str, *, sort: bool = True, dedupe: bool = True,
               exact_only: bool = False) -> Plan:
    j = backend.join
    loose_all = backend.list_files(target)
    skipped = [f for f in loose_all if f.ext_key in SKIP_EXTS]
    loose = [f for f in loose_all if f.ext_key not in SKIP_EXTS]
    organized: List[FileInfo] = []
    for cat in CATEGORY_NAMES:
        organized += [f for f in backend.list_files(j(target, cat)) if f.ext_key not in SKIP_EXTS]

    groups = find_duplicate_groups(backend, loose + organized, exact_only) if dedupe else []

    occupied: Dict[str, Set[str]] = {}

    def occ(d: str) -> Set[str]:
        if d not in occupied:
            occupied[d] = {name.lower() for name in backend.list_names(d)}
        return occupied[d]

    moves: List[Move] = []

    def relocate(f: FileInfo, dest_dir: str, desired: str, kind: str, reason: str = "") -> None:
        occ(f.dir).discard(f.name.lower())
        name = allocate_name(occ(dest_dir), desired, fallback=f.name)
        if dest_dir == f.dir and name == f.name:
            return
        moves.append(Move(f.path, j(dest_dir, name), kind, reason))

    trash_dir = j(target, TRASH_DIR_NAME)
    trashed: Set[str] = set()
    clean_names: Dict[str, str] = {}
    # 名前の空きを正しく計算するため、削除候補への移動 → 改名 → 振り分け の順に決める
    for g in groups:
        for f, reason in g.others:
            # 整理済みフォルダから移すものは「_削除候補/PDF/…」のように元のフォルダ名を残す
            sub = rel(target, f.dir)
            relocate(f, j(trash_dir, sub) if sub else trash_dir, f.name, "trash", reason)
            trashed.add(f.path)
        clean_names[g.keeper.path] = g.clean_name

    for f in organized:
        desired = clean_names.get(f.path, f.name)
        if f.path not in trashed and desired != f.name:
            relocate(f, f.dir, desired, "rename", "名前の (1) などを外す")

    for f in loose:
        if f.path in trashed:
            continue
        desired = clean_names.get(f.path, f.name)
        if sort:
            relocate(f, j(target, f.category), desired, "sort")
        elif desired != f.name:
            relocate(f, f.dir, desired, "rename", "名前の (1) などを外す")

    dirs = sorted({backend.dirname(m.dst) for m in moves})
    return Plan(target, loose, organized, skipped, groups, moves, dirs)


# ---------------------------------------------------------------------------
# 表示・実行・元に戻す
# ---------------------------------------------------------------------------


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def fmt_time(t: int) -> str:
    try:
        return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "日時不明"


def basename_any(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


def rel(target: str, path: str) -> str:
    if path.startswith(target):
        return path[len(target):].lstrip("/\\")
    return path


def print_plan(plan: Plan, backend, verbose: bool) -> None:
    t = plan.target
    print(f"対象フォルダ: {t}（{backend.label}）")
    print(f"  未整理のファイル: {len(plan.loose)} 件 / 整理済みフォルダ内: {len(plan.organized)} 件")
    if plan.skipped:
        print(f"  ダウンロード途中・一時ファイル {len(plan.skipped)} 件は触りません")
    print()

    final_name = {m.src: m.dst for m in plan.moves}
    trash_moves = [m for m in plan.moves if m.kind == "trash"]
    freed = sum(f.size for g in plan.groups for f, _ in g.others)
    print(f"■ 重複・古い版: {len(plan.groups)} グループ → {len(trash_moves)} 件を「{TRASH_DIR_NAME}」へ"
          + (f"（約 {fmt_size(freed)} 空きます）" if freed else ""))
    for no, g in enumerate(plan.groups, 1):
        k = g.keeper
        renamed = ""
        dst = final_name.get(k.path)
        if dst and basename_any(dst) != k.name:
            renamed = f" → 「{basename_any(dst)}」に改名"
        print(f"  [{no}] 残す: {rel(t, k.path)}{renamed}  {fmt_time(k.mtime)}  {fmt_size(k.size)}")
        for f, reason in g.others:
            print(f"      移す: {rel(t, f.path)}  {fmt_time(f.mtime)}  {fmt_size(f.size)}  … {reason}")
    print()

    sort_moves = [m for m in plan.moves if m.kind == "sort"]
    print(f"■ 種類別フォルダへの振り分け: {len(sort_moves)} 件")
    counts: Dict[str, int] = defaultdict(int)
    for m in sort_moves:
        counts[rel(t, m.dst).replace("\\", "/").split("/")[0]] += 1
    if counts:
        print("  " + " / ".join(f"{c} {counts[c]}件" for c in CATEGORY_NAMES if counts.get(c)))
    order = {c: i for i, c in enumerate(CATEGORY_NAMES)}
    sort_moves.sort(key=lambda m: (order.get(rel(t, m.dst).replace("\\", "/").split("/")[0], 99),
                                   basename_any(m.dst).lower()))
    limit = None if verbose else 30
    for m in sort_moves[:limit]:
        print(f"  - {rel(t, m.src)} → {rel(t, m.dst)}")
    if limit is not None and len(sort_moves) > limit:
        print(f"  …ほか {len(sort_moves) - limit} 件（全部見るには --verbose）")

    renames = [m for m in plan.moves if m.kind == "rename"]
    if renames:
        print()
        print(f"■ 名前の整理（整理済みフォルダ内）: {len(renames)} 件")
        for m in renames:
            print(f"  - {rel(t, m.src)} → {rel(t, m.dst)}")


def write_log(backend, target: str, moves: List[Move], note: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / f"organize-log-{stamp}.json"
    n = 2
    while path.exists():
        path = LOG_DIR / f"organize-log-{stamp}-{n}.json"
        n += 1
    data = {
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "mode": backend.mode,
        "serial": getattr(backend, "serial", None),
        "target": target,
        "moves": [m.__dict__ for m in moves],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def execute(backend, target: str, moves: List[Move], dirs: List[str], note: str) -> int:
    backend.make_dirs(dirs)
    results = backend.move_many(moves)
    done = [m for m, err in zip(moves, results) if err is None]
    failed = [(m, err) for m, err in zip(moves, results) if err is not None]
    print(f"完了: {len(done)} 件を移動しました。")
    if failed:
        print(f"うまくいかなかったもの: {len(failed)} 件（元の場所に残っています）")
        for m, err in failed:
            print(f"  - {rel(target, m.src)}: {err}")
    if done:
        log = write_log(backend, target, done, note)
        print(f"記録: {log}")
        print(f"  元に戻すとき: python organize.py{' --adb' if backend.mode == 'adb' else ''} --undo \"{log}\" --apply")
    return 1 if failed else 0


def undo(backend, log_path: str, apply: bool) -> int:
    try:
        data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UserError(f"記録ファイルを読めませんでした: {log_path}（{e}）")
    if data.get("mode") != backend.mode:
        want = "--adb を付けて" if data.get("mode") == "adb" else "--adb を付けずに"
        raise UserError(f"この記録は別の方法で整理したときのものです。{want}実行してください。")
    target = data.get("target", "")
    moves = [Move(m["dst"], m["src"], "undo") for m in reversed(data.get("moves", []))]
    print(f"元に戻す: {len(moves)} 件（記録: {log_path}）")
    for m in moves[:30]:
        print(f"  - {rel(target, m.src)} → {rel(target, m.dst)}")
    if len(moves) > 30:
        print(f"  …ほか {len(moves) - 30} 件")
    if not apply:
        print()
        print("これは下見です。何も変更していません。元に戻すには --apply を付けてもう一度実行してください。")
        return 0
    print()
    dirs = sorted({backend.dirname(m.dst) for m in moves})
    code = execute(backend, target, moves, dirs, f"undo of {Path(log_path).name}")
    # 整理のときに作ったフォルダが空になっていれば片付ける
    backend.remove_empty_dirs(sorted({backend.dirname(m.src) for m in moves} - set(dirs)))
    return code


def check_target(target: str, force: bool) -> None:
    parts = [p.lower() for p in target.replace("\\", "/").split("/") if p]
    if "android" in parts:
        raise UserError("「Android」フォルダ（アプリのデータ置き場）の中は整理できません。")
    if "dcim" in parts and not force:
        raise UserError(
            "カメラの写真フォルダ（DCIM）を動かすと、ギャラリーやバックアップに影響することがあります。\n"
            "それでも整理する場合は --force を付けてください。"
        )


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="Android のダウンロードフォルダなどを種類別に分け、重複・古い版を「_削除候補」へ移します。"
                    "--apply を付けない限り、下見だけで何も変更しません。"
    )
    ap.add_argument("--adb", action="store_true", help="PC から USB（ADB）でつないだスマホを整理する")
    ap.add_argument("-s", "--serial", help="スマホが複数つながっているときの機種番号（adb devices で確認）")
    ap.add_argument("--adb-path", default="adb", help="adb コマンドの場所（PATH に無い場合）")
    ap.add_argument("--dir", default=DEFAULT_DIR, help=f"整理するフォルダ（既定: {DEFAULT_DIR}）")
    ap.add_argument("--apply", action="store_true", help="実際に移動する（付けなければ下見だけ）")
    ap.add_argument("--exact-only", action="store_true",
                    help="中身がまったく同じ重複だけを削除候補にする（名前が同じだけの古い版は残す）")
    ap.add_argument("--no-sort", action="store_true", help="種類別フォルダへの振り分けをしない")
    ap.add_argument("--no-dedupe", action="store_true", help="重複・古い版の整理をしない")
    ap.add_argument("--force", action="store_true", help="DCIM（カメラの写真）フォルダも対象にする")
    ap.add_argument("-v", "--verbose", action="store_true", help="振り分け予定を全件表示する")
    ap.add_argument("--undo", metavar="LOG", help="以前の整理を元に戻す（logs/ の記録ファイルを指定）")
    args = ap.parse_args(argv)

    backend = AdbBackend(args.adb_path, args.serial) if args.adb else LocalBackend()
    try:
        backend.check_connection()
        if args.undo:
            return undo(backend, args.undo, args.apply)

        target = backend.normalize(args.dir)
        check_target(target, args.force)
        if not backend.is_dir(target):
            hint = "" if args.adb else "\n（PC からスマホを整理するときは --adb を付けてください）"
            raise UserError(f"フォルダが見つかりません: {target}{hint}")

        plan = build_plan(backend, target, sort=not args.no_sort, dedupe=not args.no_dedupe,
                          exact_only=args.exact_only)
        print_plan(plan, backend, args.verbose)
        print()
        if not plan.moves:
            print("整理が必要なファイルはありませんでした。")
            return 0
        if not args.apply:
            print("これは下見です。何も変更していません。")
            print("この内容で整理するには --apply を付けてもう一度実行してください。")
            return 0
        code = execute(backend, target, plan.moves, plan.dirs_to_create, "organize")
        if any(m.kind == "trash" for m in plan.moves):
            print(f"「{TRASH_DIR_NAME}」フォルダの中身を確認して、要らなければフォルダごと削除してください。")
        return code
    except UserError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
