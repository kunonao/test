#!/usr/bin/env python3
"""B3 登録後報奨の「実フロー図」を、JIS X 0121（ISO 5807）系のフローチャート記号で SVG に描く。

- 根拠：kunonao/houshoukin ブランチ b3-registered-after（コミット 3c4b710。2026-09-18）＝
  kunonao/houshoukin-project の 共有用_報奨金算出_20260922/プログラム/ と同じコード（改行コードのみ違う）。
  仕様書（改訂3a）の mermaid 図ではなく、コードが実際にたどる順序・分岐・出口を描く。
- 出力：svg/B3_実フロー図_{1,2,3}_*.svg（1ページ＝1枚）と、印刷用の print.html（A3・3ページ）。
  PNG・PDF は README の手順で Chromium に描かせる。
- 外部ライブラリは使わない（標準ライブラリだけで動く）。座標は手で決めている＝配置の意図を変えるときはここを直す。

記号（JIS X 0121）：端子・処理・判断・定義済み処理・ループ端（始／終）・準備・データ・書類・表示・手作業・結合子。
印：★＝仕様書の mermaid 図には描かれていない実際の処理（本文やコードにはある）、⚠＝技術的な不具合として処理全体を止める
（要確認にはならず、何も保存しない）。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "svg"

SOURCE_NOTE = (
    "出典：kunonao/houshoukin ブランチ b3-registered-after（3c4b710）＝ 共有用_報奨金算出_20260922/プログラム/"
    "houshoukin/b3/*.py の実際の処理順。[B3-x-y] は仕様書（改訂3a）のブロック番号。作図 2026-09-28。"
)

# ---------------------------------------------------------------- 文字と色
FONT = "'BIZ UDPGothic','Yu Gothic UI','Yu Gothic','Meiryo','Hiragino Sans','Noto Sans JP','IPAPGothic','IPAGothic',sans-serif"
FS = 13  # 本文
LH = 17  # 行送り
TAG_FS = 11  # ブロック番号
LABEL_FS = 11.5  # 線のラベル

INK = "#1f2937"
MUTED = "#64748b"
LINE = "#334155"

# 種類ごとの塗りと線（印刷しても判別できる淡色だけを使う）
KIND = {
    "proc": ("#ffffff", LINE),
    "dec": ("#fffbeb", LINE),
    "predef": ("#eff6ff", LINE),
    "loop": ("#f5f3ff", LINE),
    "human": ("#ecfeff", "#0e7490"),
    "term": ("#f1f5f9", LINE),
    "io": ("#ffffff", LINE),
    "err": ("#fef2f2", "#b91c1c"),
    "nonpay": ("#f1f5f9", "#475569"),
    "ok": ("#ecfdf5", "#047857"),
    "stop": ("#fff7ed", "#c2410c"),
    "note": ("#ffffff", MUTED),
    "conn": ("#ffffff", LINE),
}
STAR = "#0f766e"  # ★
WARN = "#c2410c"  # ⚠


def text_w(s: str, fs: float = FS) -> float:
    """表示幅の見積り（全角＝1em、半角≒0.58em）。はみ出し検査にだけ使う。"""
    w = 0.0
    for c in s:
        if ord(c) >= 0x2000 or c in "×÷":
            w += fs
        else:
            w += fs * 0.58
    return w


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ---------------------------------------------------------------- 図形
@dataclass
class Node:
    shape: str
    cx: float
    cy: float
    w: float
    h: float
    lines: list[str]
    tag: str | None = None
    kind: str = "proc"
    star: bool = False
    stop: bool = False
    dashed: bool = False  # プログラムの外（人の後工程など）

    @property
    def l(self) -> float:
        return self.cx - self.w / 2

    @property
    def r(self) -> float:
        return self.cx + self.w / 2

    @property
    def t(self) -> float:
        return self.cy - self.h / 2

    @property
    def b(self) -> float:
        return self.cy + self.h / 2

    @property
    def top(self) -> tuple[float, float]:
        return (self.cx, self.t)

    @property
    def bottom(self) -> tuple[float, float]:
        return (self.cx, self.b)

    @property
    def left(self) -> tuple[float, float]:
        return (self.l, self.cy)

    @property
    def right(self) -> tuple[float, float]:
        return (self.r, self.cy)


# 形ごとの左右の余白（文字列の幅に足す）
PAD = {
    "proc": 30,
    "term": 44,
    "predef": 46,
    "loop_start": 40,
    "loop_end": 40,
    "prep": 52,
    "data": 54,
    "doc": 32,
    "display": 60,
    "manual": 56,
}


@dataclass
class Page:
    width: float
    title: str
    subtitle: str
    key: str = "p"  # 1枚のHTMLに3枚を並べても矢印の id が衝突しないように
    nodes: list[Node] = field(default_factory=list)
    edges: list[tuple] = field(default_factory=list)
    labels: list[tuple] = field(default_factory=list)
    dots: list[tuple[float, float]] = field(default_factory=list)
    frames: list[tuple] = field(default_factory=list)
    notes: list[tuple] = field(default_factory=list)
    texts: list[tuple] = field(default_factory=list)
    height: float = 0

    # ---- 図形を置く
    def node(self, shape: str, cx: float, cy: float, lines, *, w=None, h=None, kind=None, **kw) -> Node:
        if isinstance(lines, str):
            lines = lines.split("\n")
        tw = max(text_w(s) for s in lines)
        th = len(lines) * LH
        if shape == "dec":
            h = h or max(78, th + 46)
            w = w or max(260, tw / (1 - th / h) + 28)
        elif shape == "conn":
            w = h = 30
        else:
            h = h or th + 22
            w = w or tw + PAD.get(shape, 30)
        if kind is None:
            kind = {
                "dec": "dec",
                "predef": "predef",
                "loop_start": "loop",
                "loop_end": "loop",
                "term": "term",
                "manual": "human",
                "conn": "conn",
            }.get(shape, "proc")
        n = Node(shape, cx, cy, w, h, lines, kind=kind, **kw)
        self._check_fit(n)
        self.nodes.append(n)
        return n

    def _check_fit(self, n: Node) -> None:
        if n.shape == "conn":
            return
        tw = max(text_w(s) for s in n.lines)
        th = len(n.lines) * LH
        if n.shape == "dec":
            ok = tw / n.w + th / n.h <= 1.0
        else:
            ok = tw + PAD.get(n.shape, 30) - 8 <= n.w and th <= n.h
        if not ok:
            print(f"  ! 文字がはみ出す恐れ：{n.lines[0]}（幅 {n.w:.0f}／文字 {tw:.0f}）")

    # ---- 線
    def line(self, pts, *, head=True, label=None, at=None, anchor="start", dash=False, color=LINE):
        self.edges.append((pts, head, dash, color))
        if label:
            if at is None:
                (x0, y0), (x1, y1) = pts[0], pts[1]
                if abs(y1 - y0) < 1:  # 横線：始点の少し先・線の上
                    at = (x0 + 8, y0 - 6) if x1 > x0 else (x0 - 8, y0 - 6)
                    anchor = "start" if x1 > x0 else "end"
                else:  # 縦線：始点の少し下・線の右
                    at = (x0 + 7, y0 + 13)
            self.labels.append((label, at[0], at[1], anchor))

    def dot(self, x: float, y: float) -> None:
        self.dots.append((x, y))

    def frame(self, nodes: list[Node], label: str, pad=(18, 30, 18, 14), extra=None) -> None:
        """nodes を囲む破線の枠（仕様書の subgraph に当たる区切り）。pad＝左・上・右・下"""
        xs0 = [n.l for n in nodes] + ([extra[0]] if extra else [])
        ys0 = [n.t for n in nodes] + ([extra[1]] if extra else [])
        xs1 = [n.r for n in nodes] + ([extra[2]] if extra else [])
        ys1 = [n.b for n in nodes] + ([extra[3]] if extra else [])
        self.frames.append((min(xs0) - pad[0], min(ys0) - pad[1], max(xs1) + pad[2], max(ys1) + pad[3], label))

    def note(self, x: float, cy: float, lines: list[str], target: tuple[float, float] | None = None) -> None:
        """注釈（JIS の注釈記号：左に角かっこ、対象へ破線）"""
        self.notes.append((x, cy, lines, target))

    def text(self, x, y, s, *, fs=FS, color=INK, anchor="start", weight="normal") -> None:
        self.texts.append((x, y, s, fs, color, anchor, weight))


class Col:
    """1本の縦の流れ。put() で下へ積み、前の記号から矢印を引く。右や左の枝を背骨へ合流させる線もここで引く。"""

    def __init__(self, page: Page, cx: float, y: float, gap: float = 30):
        self.p = page
        self.cx = cx
        self.y = y
        self.gap = gap
        self.prev: Node | None = None
        self.pending: list[tuple[Node, str]] = []  # (枝の記号, 出る側)

    def put(self, shape, lines, *, label=None, link=True, gap_before=None, join=12, **kw) -> Node:
        """join：枝が背骨へ合流する位置（この記号の上端からの距離）。枠の外で合流させたいときに大きくする"""
        if gap_before is not None and self.prev is not None:
            self.y = self.prev.b + gap_before
        n = self.p.node(shape, self.cx, 0, lines, **kw)
        n.cy = self.y + n.h / 2
        if link and self.prev is not None:
            self.p.line([self.prev.bottom, n.top], label=label)
        yj = n.t - join
        for m, side in self.pending:
            if side == "bottom":
                self.p.line([m.bottom, (m.cx, yj), (self.cx, yj)])
            elif side == "right":  # 枝の右から出て下へ回る
                xr = m.r + 26
                self.p.line([m.right, (xr, m.cy), (xr, yj), (self.cx, yj)])
        self.pending = []
        self.prev = n
        self.y = n.b + self.gap
        return n

    def merge(self, m: Node, side: str = "bottom") -> None:
        """m の出口を、次に置く記号の直前で背骨へ合流させる"""
        self.pending.append((m, side))


def branch(
    page: Page, dec: Node, cx: float | None, shape: str, lines, *, label="いいえ", side="right", gap=56, **kw
) -> Node:
    """判断の右（または左）に同じ高さで記号を置き、横線でつなぐ。cx=None なら判断から gap だけ離して置く"""
    n = page.node(shape, 0 if cx is None else cx, dec.cy, lines, **kw)
    if cx is None:
        n.cx = dec.r + gap + n.w / 2 if side == "right" else dec.l - gap - n.w / 2
    if side == "right":
        page.line([dec.right, n.left], label=label)
    else:
        page.line([dec.left, n.right], label=label)
    return n


def below(page: Page, above: Node, shape: str, lines, *, gap=30, label="はい", **kw) -> Node:
    """記号の真下に置き、縦線でつなぐ（枝の中の流れ）"""
    n = page.node(shape, above.cx, 0, lines, **kw)
    n.cy = above.b + gap + n.h / 2
    page.line([above.bottom, n.top], label=label)
    return n


def bus(page: Page, exits: list[tuple[Node, str]], x: float, target: Node) -> None:
    """複数の判断の出口を縦の1本線にまとめ、target（x が同じ・下にある）の上へ入れる"""
    ys = []
    for dec, lab in exits:
        page.line([dec.right, (x, dec.cy)], head=False, label=lab)
        ys.append(dec.cy)
    for y in ys[1:]:
        page.dot(x, y)
    page.line([(x, min(ys)), target.top])


# ---------------------------------------------------------------- SVG へ
def _shape_svg(n: Node) -> str:
    fill, stroke = KIND[n.kind]
    dash = ' stroke-dasharray="6 4"' if n.kind == "stop" or n.dashed else ""
    a = f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"{dash}'
    l, t, r, b, cx, cy, w, h = n.l, n.t, n.r, n.b, n.cx, n.cy, n.w, n.h
    s = n.shape
    if s == "proc":
        return f'<rect x="{l:.1f}" y="{t:.1f}" width="{w:.1f}" height="{h:.1f}" {a}/>'
    if s == "term":
        return f'<rect x="{l:.1f}" y="{t:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{h / 2:.1f}" {a}/>'
    if s == "dec":
        return f'<polygon points="{cx:.1f},{t:.1f} {r:.1f},{cy:.1f} {cx:.1f},{b:.1f} {l:.1f},{cy:.1f}" {a}/>'
    if s == "predef":
        return (
            f'<rect x="{l:.1f}" y="{t:.1f}" width="{w:.1f}" height="{h:.1f}" {a}/>'
            f'<line x1="{l + 9:.1f}" y1="{t:.1f}" x2="{l + 9:.1f}" y2="{b:.1f}" stroke="{stroke}" stroke-width="1.2"/>'
            f'<line x1="{r - 9:.1f}" y1="{t:.1f}" x2="{r - 9:.1f}" y2="{b:.1f}" stroke="{stroke}" stroke-width="1.2"/>'
        )
    if s == "loop_start":
        c = min(14, h / 3)
        return f'<polygon points="{l + c:.1f},{t:.1f} {r - c:.1f},{t:.1f} {r:.1f},{t + c:.1f} {r:.1f},{b:.1f} {l:.1f},{b:.1f} {l:.1f},{t + c:.1f}" {a}/>'
    if s == "loop_end":
        c = min(14, h / 3)
        return f'<polygon points="{l:.1f},{t:.1f} {r:.1f},{t:.1f} {r:.1f},{b - c:.1f} {r - c:.1f},{b:.1f} {l + c:.1f},{b:.1f} {l:.1f},{b - c:.1f}" {a}/>'
    if s == "prep":
        k = 18
        return f'<polygon points="{l + k:.1f},{t:.1f} {r - k:.1f},{t:.1f} {r:.1f},{cy:.1f} {r - k:.1f},{b:.1f} {l + k:.1f},{b:.1f} {l:.1f},{cy:.1f}" {a}/>'
    if s == "data":
        k = 16
        return f'<polygon points="{l + k:.1f},{t:.1f} {r:.1f},{t:.1f} {r - k:.1f},{b:.1f} {l:.1f},{b:.1f}" {a}/>'
    if s == "manual":
        k = 16
        return f'<polygon points="{l:.1f},{t:.1f} {r:.1f},{t:.1f} {r - k:.1f},{b:.1f} {l + k:.1f},{b:.1f}" {a}/>'
    if s == "doc":
        y = b - 8
        return (
            f'<path d="M{l:.1f},{t:.1f} H{r:.1f} V{y:.1f} Q{r - w / 4:.1f},{y + 14:.1f} {cx:.1f},{y:.1f} '
            f'Q{l + w / 4:.1f},{y - 14:.1f} {l:.1f},{y:.1f} Z" {a}/>'
        )
    if s == "display":
        k = 20
        return (
            f'<path d="M{l:.1f},{cy:.1f} L{l + k:.1f},{t:.1f} H{r - 26:.1f} Q{r + 8:.1f},{cy:.1f} {r - 26:.1f},{b:.1f} '
            f'H{l + k:.1f} Z" {a}/>'
        )
    if s == "conn":
        return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="15" {a}/>'
    raise ValueError(s)


def _text_block(x: float, cy: float, lines: list[str], *, fs=FS, color=INK, weight="normal") -> str:
    n = len(lines)
    y0 = cy - (n - 1) * LH / 2 + fs * 0.36
    out = [f'<text x="{x:.1f}" y="{y0:.1f}" font-size="{fs}" fill="{color}" text-anchor="middle" font-weight="{weight}">']
    for i, s in enumerate(lines):
        dy = 0 if i == 0 else LH
        out.append(f'<tspan x="{x:.1f}" dy="{dy}">{esc(s)}</tspan>')
    out.append("</text>")
    return "".join(out)


def _node_svg(n: Node) -> str:
    parts = [_shape_svg(n)]
    dx, dy = 0.0, 0.0
    if n.shape == "doc":
        dy = -4
    elif n.shape == "display":
        dx = 3
    weight = "bold" if n.shape == "conn" else "normal"
    parts.append(_text_block(n.cx + dx, n.cy + dy, n.lines, weight=weight, fs=14 if n.shape == "conn" else FS))
    if n.tag:
        if n.shape == "dec":
            parts.append(
                f'<text x="{n.cx - 12:.1f}" y="{n.t - 5:.1f}" font-size="{TAG_FS}" fill="{MUTED}" text-anchor="end">{esc(n.tag)}</text>'
            )
        else:
            parts.append(
                f'<text x="{n.l + 1:.1f}" y="{n.t - 5:.1f}" font-size="{TAG_FS}" fill="{MUTED}">{esc(n.tag)}</text>'
            )
    badges = []
    if n.star:
        badges.append(("★", STAR))
    if n.stop:
        badges.append(("⚠", WARN))
    if badges:
        if n.shape == "dec":
            x, y = n.r - 6, n.t + 14
        else:
            x, y = n.r - 2, n.t - 5
        for mark, color in reversed(badges):
            parts.append(
                f'<text x="{x:.1f}" y="{y:.1f}" font-size="13" fill="{color}" text-anchor="end" font-weight="bold">{mark}</text>'
            )
            x -= 16
    return "".join(parts)


def _label_svg(s: str, x: float, y: float, anchor: str) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{LABEL_FS}" fill="{INK}" text-anchor="{anchor}" '
        f'stroke="#ffffff" stroke-width="4" stroke-linejoin="round" paint-order="stroke">{esc(s)}</text>'
    )


def render(page: Page) -> str:
    W, H = page.width, page.height
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
        f'font-family="{esc(FONT)}" role="img" aria-label="{esc(page.title)}">',
        "<defs>",
        f'<marker id="ah-{page.key}" viewBox="0 0 10 10" refX="9.2" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>',
        "</defs>",
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="#ffffff"/>',
        f'<text x="40" y="46" font-size="22" font-weight="bold" fill="{INK}">{esc(page.title)}</text>',
        f'<text x="40" y="72" font-size="12.5" fill="{MUTED}">{esc(page.subtitle)}</text>',
    ]
    for x0, y0, x1, y1, label in page.frames:
        out.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="10" fill="#f8fafc" '
            f'stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="7 5"/>'
        )
        out.append(
            f'<text x="{x0 + 12:.1f}" y="{y0 + 19:.1f}" font-size="12.5" font-weight="bold" fill="#475569">{esc(label)}</text>'
        )
    for pts, head, dash, color in page.edges:
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        mk = f' marker-end="url(#ah-{page.key})"' if head else ""
        da = ' stroke-dasharray="5 4"' if dash else ""
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="1.35"{da}{mk}/>')
    for x, y in page.dots:
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{LINE}"/>')
    for n in page.nodes:
        out.append(_node_svg(n))
    for x, cy, lines, target in page.notes:
        hgt = len(lines) * LH + 12
        out.append(
            f'<path d="M{x + 10:.1f},{cy - hgt / 2:.1f} H{x:.1f} V{cy + hgt / 2:.1f} H{x + 10:.1f}" fill="none" stroke="{MUTED}" stroke-width="1.3"/>'
        )
        y0 = cy - (len(lines) - 1) * LH / 2 + 12 * 0.36
        out.append(f'<text x="{x + 12:.1f}" y="{y0:.1f}" font-size="12" fill="#334155">')
        for i, s in enumerate(lines):
            out.append(f'<tspan x="{x + 12:.1f}" dy="{0 if i == 0 else LH}">{esc(s)}</tspan>')
        out.append("</text>")
        if target:
            out.append(
                f'<line x1="{x:.1f}" y1="{cy:.1f}" x2="{target[0]:.1f}" y2="{target[1]:.1f}" stroke="{MUTED}" '
                f'stroke-width="1.2" stroke-dasharray="4 3"/>'
            )
    for label, x, y, anchor in page.labels:
        out.append(_label_svg(label, x, y, anchor))
    for x, y, s, fs, color, anchor, weight in page.texts:
        out.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}">{esc(s)}</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- 凡例
def legend(page: Page, y: float) -> float:
    """ページ下端に凡例を2段で置き、ページの高さを返す"""
    page.text(40, y, "凡例", fs=13, weight="bold")
    items = [
        ("term", "端子（開始・終了）", "term"),
        ("proc", "処理", "proc"),
        ("dec", "判断", "dec"),
        ("predef", "定義済み処理（別ページ・共通部品）", "predef"),
        ("loop_start", "ループ始端（条件）", "loop"),
        ("loop_end", "ループ終端", "loop"),
        ("prep", "準備（設定・初期化）", "proc"),
        ("data", "データ（入出力）", "io"),
        ("doc", "書類（Excel シート）", "io"),
        ("display", "表示（画面）", "io"),
        ("manual", "手作業（人）", "human"),
        ("conn", "結合子（同じ文字へ続く）", "conn"),
    ]
    x = 40
    yy = y + 26
    for shape, cap, kind in items:
        w = 30 if shape == "conn" else 58
        n = Node(shape, x + w / 2, yy + 15, w, 30, ["A"] if shape == "conn" else [""], kind=kind)
        page.nodes.append(n)
        page.text(x + w + 8, yy + 19, cap, fs=11.5)
        x += w + 16 + text_w(cap, 11.5) + 26
        if x > page.width - 260:
            x = 40
            yy += 44
    yy += 48
    swatches = [
        ("err", "要確認（エラー脱出 C2）"),
        ("nonpay", "正常不支給（N01・N03）"),
        ("ok", "正常支給・正常終了"),
        ("stop", "⚠ 停止（技術的な不具合）"),
    ]
    x = 40
    for kind, cap in swatches:
        n = Node("proc", x + 29, yy + 13, 58, 26, [""], kind=kind)
        page.nodes.append(n)
        page.text(x + 66, yy + 17, cap, fs=11.5)
        x += 66 + text_w(cap, 11.5) + 30
    page.text(x, yy + 17, "★ 仕様書の mermaid 図には描かれていない実際の処理（本文やコードにはある）", fs=11.5, color=STAR)
    yy += 30
    page.text(
        40,
        yy + 17,
        "⚠ 技術的な不具合として処理全体を止める（要確認にはならず、結果ブックは保存しない。bat は「異常終了」と表示）",
        fs=11.5,
        color=WARN,
    )
    page.text(40, yy + 40, SOURCE_NOTE, fs=11, color=MUTED)
    return yy + 60


# ================================================================ 1/3 全体
def page1() -> Page:
    p = Page(
        1840,
        "B3 登録後報奨 — 実フロー図 1/3　全体（起動から保存まで）",
        "実行\\b3_登録後報奨を算出.bat → main.py b3 → houshoukin/b3/runner.py → registration.py の順に、実際に通る処理を描いた。"
        "ファミリー単位のループの中身は 2/3・3/3。",
        key="p1",
    )
    # ---- 左の列：人の準備・起動・事前点検・取込と前処理
    X1 = 330
    c = Col(p, X1, 110)
    c.put("term", ["開始"])
    n_rank = c.put("manual", ["当年度の評価ランク付与（K000）を済ませる"], tag="[B3-0-1]", star=True)
    p.note(
        X1 + n_rank.w / 2 + 34,
        n_rank.cy,
        ["プログラムはこれを確認しない（運用上の前提）。", "仕様書の図の判断記号は実装されていない。", "未完了で実行すると E09 が多数出て、終了時の一覧で分かる"],
        target=(n_rank.r, n_rank.cy),
    )
    c.put("manual", ["TOPAM「登録後報奨」の CSV を1つだけ", "入力\\登録後報奨\\ に置く（自動取得でも手作業でも可）"])
    c.put("manual", ["実行\\b3_登録後報奨を算出.bat を実行する"])
    d_env = c.put("dec", ["仮想環境・セットアップ合格の記録・", "入力 CSV が揃っているか"], star=True)
    d_set = c.put("dec", ["設定.xlsx（今年の設定）が読めるか"], label="はい", star=True)
    c.put("manual", ["画面の「報奨金支払年」を確かめて Enter", "（違えば閉じて 設定.xlsx を直す）"], label="はい", star=True)
    d_open = c.put("dec", ["結果ブック・設定.xlsx・入力補正マスタを", "Excel で開いていないか"], star=True)
    c.put(
        "predef",
        ["事前点検（main.py の preflight）", "入力補正マスタ／年度から決まる設定／入力 CSV", "（1つ・読める・必須の見出し）／金額マスタ"],
        label="はい",
        star=True,
    )
    d_pre = c.put("dec", ["不足は無いか"], star=True)
    d_exist = c.put("dec", ["「登録後報奨」シートは", "まだ無いか"], label="はい", star=True, tag="main.py・runner.py")
    c.put(
        "prep",
        ["年度から決まる設定を読む", "制度対象期間＝前年 1/1〜12/31", "評価付与日下限＝前年 12/1（既定）"],
        label="はい",
    )
    c.put("data", ["CSV を「Raw_登録後」シートへそのまま転写"], star=True)
    c.put("proc", ["取込ログに1行（コード版・金額マスタ版・補正マスタ版）"], star=True)
    c.put(
        "proc",
        ["Raw を1行ずつ読む（2回目に出てくる各国ユーザ番号・", "国コード・四法区分コードは関連出願（親）の列として持つ）"],
        star=True,
    )
    c.put(
        "predef",
        [
            "[B0] 入力補正（設定\\入力補正マスタ.xlsx）",
            "親の関係コード修正 → 親の追加 →",
            "従業員ID の別名 → 受給者の書き換え",
            "記録＝「前処理ログ_登録後」・Raw の該当セルを着色",
        ],
        star=True,
        stop=True,
    )
    c.put("proc", ["金額マスタ（B3：評価ランク → 基準報奨金額）を読む"], star=True)
    d_n02 = c.put("dec", ["制度対象期間は同一年の", "1/1〜12/31 か"], star=True, tag="[N-02]")
    c.put("proc", ["全行をファミリー番号順に並べる"], tag="[B3-0-0]", label="はい")
    c.put("conn", ["A"])
    p.text(X1 + 24, c.prev.cy + 5, "2列目の A へ", fs=11.5, color=MUTED)

    # 起動・点検の出口（計算せずに終わる）
    BX = 760
    t_nc = p.node(
        "term",
        BX,
        d_exist.cy + 96,
        ["理由を表示して終了（計算しない）", "終了コード 2（bat 自身の点検は 1）", "画面の文は 結果\\実行ログ\\ にも残る"],
    )
    bus(p, [(d_env, "いいえ"), (d_set, "いいえ"), (d_open, "開いている"), (d_pre, "ある"), (d_exist, "ある（人の編集を守る）")], BX, t_nc)
    branch(p, d_n02, BX, "term", ["⚠ 停止（異常終了。何も保存しない）"], kind="stop")

    # ---- 右の列：ファミリーのループ・出力
    X2 = 1210
    c2 = Col(p, X2, 118)
    c2.put("conn", ["A"])
    c2.put("loop_start", ["ファミリーのループ", "（ファミリー番号順に1つずつ）"])
    c2.put(
        "proc",
        ["K000 のランクが無い行を控える（終了時の一覧用。", "ゲートの結果に関係なく全ファミリーぶん）", "処理順（通し番号）を行数ぶん確保する"],
        star=True,
    )
    g2 = c2.put("dec", ["全行で必須の7項目が空でなく、", "発明者関連の3列があるか"], tag="[B3-0-2]", gap_before=64)
    g3 = c2.put("dec", ["繰返し項目の要素数が", "系列ごとに一致するか"], tag="[B3-0-3]", label="はい")
    g4 = c2.put("dec", ["全行に K000 の", "評価ランクがあるか"], tag="[B3-0-4]", label="はい")
    g5 = c2.put(
        "dec", ["K000 の判断日が日付・ランクが 01〜18・", "同じ日に別のランクが無いか"], tag="[B3-0-4] 続き", label="はい", star=True
    )
    c2.put(
        "predef",
        ["3-1・3-2　真のH5 の確定と報奨グループの付番", "（詳細は 2/3）"],
        label="はい",
        tag="[B3-1-1]〜[B3-2-11]",
    )
    c2.put("loop_start", ["報奨グループのループ", "（ソート順で先に現れるグループから）"])
    c2.put(
        "predef",
        ["3-3〜3-8　報奨グループの処理", "①を1行・対応表を全案件ぶん作る（詳細は 3/3）"],
        tag="[B3-3-1]〜[B3-8-1]",
    )
    c2.put("loop_end", ["報奨グループのループ"])
    E_fam = c2.put("loop_end", ["ファミリーのループ"])
    c2.put("doc", ["①「登録後報奨」シートを書く", "（要確認の行は判断に要る列を薄赤）"], star=True)
    c2.put("doc", ["「登録後_案件対応表」シートを書く", "（グループ内の全案件を1行ずつ）"], star=True)
    c2.put(
        "display",
        ["件数を表示：正常支給／正常不支給／要確認／ファミリー数／", "グループ数／ランク未付与の行／前年評価の疑い"],
        star=True,
    )
    d_miss = c2.put("dec", ["K000 のランクが無い行が", "あったか"], tag="[B3-0-4]")
    n_miss = branch(
        p,
        d_miss,
        1640,
        "display",
        ["注意：該当行の一覧を表示", "（ファミリー番号・各国ユーザ番号・国）"],
        label="あった",
    )
    c2.merge(n_miss)
    d_stale = c2.put(
        "dec", ["前年評価の疑い（採用ランクの判断日", "＜評価付与日下限）があったか"], label="なし", star=True, gap_before=52, join=26
    )
    n_stale = branch(
        p,
        d_stale,
        1640,
        "display",
        ["注意：件数と先頭20件を表示", "（①「評価付与日警告」列で絞り込む）"],
        label="あった",
    )
    c2.merge(n_stale)
    c2.put("proc", ["安全に保存（バックアップ → 一時ファイル → 置き換え）"], label="なし", star=True, gap_before=56, join=30)
    t_end = c2.put("term", ["終了（正常終了。bat は「処理結果状態列と注意を確認」）"], kind="ok")
    n_b6 = c2.put(
        "manual",
        ["後工程 [B6]：①の要確認・注意を人が確認し、TOPAM を直す／", "入力補正マスタに行を足す → 結果シートを消して再実行", "（そのファミリーは 3-0 からやり直しになる）"],
        link=False,
        dashed=True,
    )
    p.line([t_end.bottom, n_b6.top], dash=True)

    # 3-0 ゲートの出口：①にファミリー単位の要確認行
    GX = 1650
    e_gate = p.node(
        "proc",
        GX,
        g5.b + 70,
        ["①に要確認の行を1行（ファミリー単位。C2）", "処理順・ファミリー番号・構成案件数・", "エラーコード・エラー詳細・到達ブロック＝3-0", "（支払単位ID は振らない）"],
        kind="err",
    )
    bus(p, [(g2, "いいえ（E01）"), (g3, "いいえ（E02）"), (g4, "いいえ（E09）"), (g5, "いいえ（E01）")], GX, e_gate)
    p.line([e_gate.bottom, (GX, E_fam.cy), E_fam.right], label="次のファミリーへ", at=(GX + 8, e_gate.b + 30))
    p.frame([g2, g3, g4, g5], "3-0 入力ゲート（ファミリー単位）", pad=(22, 40, 22, 14))

    p.height = legend(p, max(n_b6.b, c.prev.b) + 60)
    return p


# ================================================================ 2/3 グループ付番
def page2() -> Page:
    p = Page(
        1880,
        "B3 登録後報奨 — 実フロー図 2/3　3-1 真のH5 の確定・3-2 報奨グループの付番（ファミリー単位）",
        "houshoukin/b3/family.py（sort_family・true_h5_parents・assign_groups）。3-0 ゲートを通ったファミリーの行群ごとに1回。"
        "ここで初めて支払単位（報奨グループ）が決まる。",
        key="p2",
    )
    X = 440
    c = Col(p, X, 118)
    c.put("term", ["開始：3-0 を通ったファミリーの行群（1/3 から）"])
    c.put(
        "proc", ["ファミリー内を並べる：JP を先頭、次に国コード順、", "同じ国コード内は各国ユーザ番号の昇順"], tag="[B3-1-1]"
    )
    # ---- 3-1
    L_row = c.put("loop_start", ["行のループ（ファミリーの全行）"], gap_before=62)
    s12 = c.put(
        "proc",
        ["関連出願の親の各国ユーザ番号(m)と法的関連性コード(m)を", "組に分ける（親が無ければ 0 組）"],
        tag="[B3-1-2]",
    )
    L_par = c.put("loop_start", ["親の組のループ"])
    dH5 = c.put("dec", ["法的関連性コードは", "H5（優先権主張）か"], star=True)
    dJP = c.put("dec", ["親の各国ユーザ番号に", "「JP-P」を含むか"], tag="[B3-1-3]", label="はい")
    s15 = c.put("proc", ["真のH5 の親として残す"], tag="[B3-1-5]", label="いいえ")
    n_skip = p.node(
        "proc",
        1000,
        (dH5.cy + dJP.cy) / 2 + 40,
        ["真のH5 に数えない", "・H5 以外の関係（04 分割・20・01 など）", "・「JP-P」を含む親の H5 ＝見かけの複合優先", "　（[B3-1-4]「親子」に読み替えて除外）"],
        tag="[B3-1-4] ほか",
        star=True,
    )
    BX = 760
    p.line([dH5.right, (BX, dH5.cy)], head=False, label="いいえ")
    p.line([dJP.right, (BX, dJP.cy)], head=False, label="はい")
    p.dot(BX, dJP.cy)
    p.line([(BX, dH5.cy), (BX, n_skip.cy), n_skip.left])
    c.merge(n_skip)
    E_par = c.put("loop_end", ["親の組のループ"], gap_before=46)
    s17 = c.put(
        "proc",
        ["残った親を並べて「親構成」とする", "（0件＝親なし／1件＝単独の親／2件以上＝真の複合優先）"],
        tag="[B3-1-6]・[B3-1-7]",
    )
    E_row = c.put("loop_end", ["行のループ"])
    p.frame([L_row, s12, L_par, dH5, dJP, s15, n_skip, E_par, s17, E_row], "3-1 真のH5 の確定", pad=(24, 40, 30, 16))

    # ---- 3-2
    prep = c.put(
        "prep", ["後回しリスト ← ファミリーの全行（ソート順）", "付番済みリスト ← 空"], star=True, gap_before=64
    )
    L_pass = c.put("loop_start", ["パスのループ", "（後回しの行が残っている間）"], star=True)
    L_r = c.put("loop_start", ["後回しリストの行を上から1行ずつ"])
    c.gap = 36
    d21 = c.put("dec", ["国コードは JP か"], tag="[B3-2-1]")
    d23 = c.put("dec", ["真のH5 の親があるか"], tag="[B3-2-3]", label="いいえ")
    d24 = c.put("dec", ["親は1件だけか", "（単独の親）"], tag="[B3-2-4]", label="はい")
    d25 = c.put("dec", ["その親はファミリー内の行か", "（JP に限らない）"], tag="[B3-2-5]", label="はい")
    dasg = c.put("dec", ["その親は付番済みか"], label="はい", star=True)
    s26 = c.put("proc", ["親と同じグループを付与"], tag="[B3-2-6]", label="はい")
    c.gap = 30

    R1, R2, R3, CX = 890, 1300, 1660, 1838
    s22 = branch(p, d21, R1, "proc", ["新規グループ nJPm を付番"], label="はい", tag="[B3-2-2]")
    d29 = branch(
        p, d23, R2, "dec", ["この案件だけを真のH5 の親とする", "JP 案件があるか"], label="いいえ", tag="[B3-2-9]"
    )
    s210 = below(p, d29, "proc", ["その JP と同じグループを付与", "（複数なら各国ユーザ番号の昇順で先頭）"], tag="[B3-2-10]")
    s211a = branch(p, d29, R3, "proc", ["新規グループ nFOm を付番"], tag="[B3-2-11]")
    d27 = p.node(
        "dec", R1, d25.cy, ["同じ親構成で付番済みの案件があるか", "（JP 案件も対象。最初のものを使う）"], tag="[B3-2-7]"
    )
    JX = d27.l - 34
    p.line([d24.right, (JX, d24.cy), (JX, d25.cy)], head=False, label="いいえ")
    p.line([d25.right, d27.left], label="いいえ")
    p.dot(JX, d25.cy)
    s28 = branch(p, d27, R2, "proc", ["その案件と同じグループを付与"], label="はい", tag="[B3-2-8]")
    s211b = below(p, d27, "proc", ["新規グループ nFOm を付番"], label="いいえ", tag="[B3-2-11]")
    n_def = branch(p, dasg, 170, "proc", ["後回しにする", "（次のパスで決める）"], side="left", star=True)

    # 右の結果は縦の1本線にまとめて「次の行へ」
    E_r = c.put("loop_end", ["後回しリストの行"], gap_before=58)
    for n in (s22, s211a, s210, s28, s211b):
        p.line([n.right, (CX, n.cy)], head=False)
    ys = sorted(n.cy for n in (s22, s211a, s210, s28, s211b))
    for y in ys[1:]:
        p.dot(CX, y)
    p.line([(CX, ys[0]), (CX, E_r.cy), E_r.right], label="次の行へ", at=(CX - 8, E_r.cy - 7), anchor="end")
    p.line([n_def.bottom, (n_def.cx, E_r.cy), E_r.left])

    dstuck = c.put("dec", ["このパスで1行も決まらなかったか", "（親どうしの循環・自分が親など）"], star=True)
    n_force = branch(
        p,
        dstuck,
        R1 + 60,
        "proc",
        ["後回しの先頭（ソート順で最上位）の1行だけを", "後回しせずに決める", "（未付番の親は見ずに [B3-2-7] → [B3-2-8]／[B3-2-11]）"],
        label="はい",
        star=True,
    )
    c.merge(n_force)
    E_pass = c.put("loop_end", ["パスのループ"], label="いいえ", gap_before=50)
    c.put(
        "proc",
        ["並びを 3-1-1 のソート順に戻す", "（n＝全ファミリー通しの番号、m＝ファミリー内の JP／FO 別の番号）"],
    )
    t_end = c.put(
        "term",
        ["戻る：各行に報奨グループID（{ファミリー番号}-{n}JP{m}／-{n}FO{m}）", "→ 1/3 の「報奨グループのループ」へ"],
    )
    p.frame(
        [prep, L_pass, L_r, d21, d23, d24, d25, dasg, s26, s22, d29, s210, s211a, d27, s28, s211b, n_def, E_r, dstuck, n_force, E_pass],
        "3-2 報奨グループの付番（B3.2）",
        pad=(22, 40, 18, 16),
        extra=(n_def.l, prep.t, CX, E_pass.b),
    )
    p.height = legend(p, t_end.b + 60)
    return p


# ================================================================ 3/3 グループ処理
def page3() -> Page:
    p = Page(
        1900,
        "B3 登録後報奨 — 実フロー図 3/3　3-3〜3-8 報奨グループの処理（①を1行作る）",
        "houshoukin/b3/registration.py の _process_group（identify・rank・period・amount・recipients と共通部品 C0・C1・C3）。"
        "報奨グループごとに1回。",
        key="p3",
    )
    X1, BR1 = 330, 770  # 左の列の背骨・枝
    X2, NX = 1300, 1640  # 右の列の背骨・正常不支給の枝
    CX2, BUSX = 1690, 1862  # エラー脱出（C2）の箱・出口の縦線

    # ---- 左の列：3-3・3-4・3-5
    c = Col(p, X1, 110)
    c.put("term", ["開始：1つの報奨グループの案件行（ソート順。1/3 から）"])
    s331 = c.put(
        "proc",
        [
            "①の行を用意する",
            "支払単位ID＝{報奨金支払年}-B3-{グループの連番6桁}",
            "報奨グループID・ファミリー番号・制度対象期間（前年 1/1〜12/31）",
            "処理順＝グループ内の最小・構成案件数",
        ],
        tag="[B3-3-1]",
        gap_before=64,
    )
    s332 = c.put(
        "proc",
        [
            "対応表の行を全案件ぶん用意する",
            "案件表示番号＝四法名-登録番号（先頭の 0 を除く）",
            "明細表示本文＝要約→作用効果→請求の範囲→意匠の説明の",
            "最初の空でない値（四法区分コードが P・U・D 以外なら⚠）",
        ],
        tag="[B3-3-2]",
        stop=True,
    )
    d332 = c.put("dec", ["どれか1案件に", "明細表示本文があるか"], tag="[B3-3-2]")
    p.frame([s331, s332, d332], "3-3 支払単位の特定", pad=(22, 40, 22, 14))
    cE = branch(p, d332, BR1, "conn", ["E"], label="いいえ（E06）")
    p.text(cE.r + 8, cE.cy + 5, "右列の E（C2）へ", fs=11.5, color=MUTED)

    L4 = c.put("loop_start", ["案件のループ（ソート順）"], label="はい", gap_before=66)
    s43 = c.put(
        "proc", ["案件の評価ランク＝K000 のうち評価判断日が", "最新のランク（案件のランク・判断日を対応表へ）"], tag="[B3-4-3]"
    )
    d44a = c.put("dec", ["最初の案件か、または", "ランクの数字が採用中より小さいか"], tag="[B3-4-4]")
    d44b = c.put("dec", ["同じランクで、この案件が JP・", "採用中が外国か"], tag="[B3-4-4] 続き", label="いいえ")
    s45 = p.node("proc", BR1, (d44a.cy + d44b.cy) / 2, ["採用評価ランク・評価採用元・", "判断日を更新"], tag="[B3-4-5]")
    JX = s45.l - 34
    p.line([d44a.right, (JX, d44a.cy)], head=False, label="はい")
    p.line([d44b.right, (JX, d44b.cy)], head=False, label="はい")
    p.line([(JX, d44a.cy), (JX, d44b.cy)], head=False)
    p.line([(JX, s45.cy), s45.left])
    p.dot(JX, s45.cy)
    c.merge(s45)
    E4 = c.put("loop_end", ["案件のループ"], label="いいえ", gap_before=48)
    s47 = c.put(
        "proc",
        [
            "代表案件＝評価採用元。①に採用評価ランク・判断日・",
            "代表案件キー・四法区分コード・案件表示番号・発明の名称・",
            "明細表示本文（代表案件の本文。空ならグループ内で最初の本文）",
        ],
        tag="[B3-4-7]",
        star=True,
    )
    dst = c.put("dec", ["採用ランクの判断日が", "評価付与日下限より前か"], star=True)
    s_st = branch(p, dst, BR1, "proc", ["①「評価付与日警告」", "＝前年評価の疑い", "（要確認にはしない）"], label="はい", star=True)
    c.merge(s_st)
    p.frame([L4, s43, d44a, d44b, s45, E4, s47, dst, s_st], "3-4 採用評価ランクの決定", pad=(22, 40, 22, 14))

    L5 = c.put("loop_start", ["案件のループ（ソート順）"], label="いいえ", gap_before=80, join=54)
    s53 = c.put("proc", ["始期＝登録日と 1/1 の遅い方（根拠を控える）"], tag="[B3-5-3]")
    s56 = c.put(
        "proc",
        [
            "終期候補＝放棄日（最終処分コード 5 の最終処分日）",
            "＞延長後満了日＞権利満了日（どれも無ければ⚠）",
            "終期＝終期候補と 12/31 の早い方（根拠を控える）",
        ],
        tag="[B3-5-6]",
        stop=True,
    )
    s58 = c.put(
        "proc",
        ["より早い始期／より遅い終期なら、最早始期・最遅終期と", "根拠を更新（対応表に案件の始期・終期）"],
        tag="[B3-5-4]〜[B3-5-8]",
    )
    E5 = c.put("loop_end", ["案件のループ"])
    s510 = c.put(
        "proc",
        ["対象日数＝最遅終期−最早始期＋1（0 以下なら⚠）", "年日数＝365／366、按分率＝「日数/年日数」（表示用）"],
        tag="[B3-5-10]",
        stop=True,
    )
    p.frame([L5, s53, s56, s58, E5, s510], "3-5 対象期間と日割の決定", pad=(22, 40, 22, 14))
    c.put("conn", ["G"], gap_before=44)
    p.text(X1 + 24, c.prev.cy + 5, "右列の G へ", fs=11.5, color=MUTED)

    # ---- 右の列：3-6・3-7・3-8
    c2 = Col(p, X2, 110)
    c2.put("conn", ["G"])
    d61 = c2.put("dec", ["採用評価ランクは 13〜18 か"], tag="[B3-6-1]", gap_before=64)
    n01 = branch(
        p, d61, NX, "proc", ["正常不支給 N01（到達＝完了）", "基準報奨金額から後の列は空"], label="はい", tag="[B3-6-3]", kind="nonpay"
    )
    cF1 = p.node("conn", n01.cx, n01.b + 40, ["F"])
    p.line([n01.bottom, cF1.top])
    p62 = c2.put(
        "predef",
        ["C0：金額マスタから基準報奨金額を引く", "（基準日＝制度対象期間の終了日 12/31。無ければ⚠）"],
        tag="[B3-6-2]",
        label="いいえ",
        stop=True,
    )
    s64 = c2.put("proc", ["原資の分子＝基準報奨金額×対象日数", "分母＝年日数（ここでは割らない）"], tag="[B3-6-4]")
    p.frame([d61, p62, s64, n01, cF1], "3-6 基準報奨金額と原資（C0）", pad=(22, 40, 22, 14))

    d71a = c2.put("dec", ["同じ発明者が1案件に", "2回登場していないか"], tag="[B3-7-1]", star=True, gap_before=66)
    d71b = c2.put(
        "dec", ["全案件の発明者集合が同じか", "（従業員ID、無ければ氏名。順不同）"], tag="[B3-7-1]", label="はい"
    )
    d71c = c2.put("dec", ["同じ氏名に異なる", "従業員ID が付いていないか"], tag="[B3-7-1]", label="はい", star=True)
    LC1 = c2.put("loop_start", ["C1：代表案件の発明者のループ（並び順どおり）"], label="はい")
    dC2a = c2.put("dec", ["従業員ID があるのに", "所属会社コードが空か"], tag="[C1-2a]", star=True)
    dC3 = c2.put("dec", ["所属会社コードは", "0000003441（自社）か"], tag="[C1-3]", label="いいえ")
    n_out = branch(p, dC3, None, "proc", ["支払なし（社外）", "氏名は残す"], label="いいえ", side="left", tag="[C1-5]")
    c2.merge(n_out)
    dC6 = c2.put("dec", ["従業員ID があるか"], tag="[C1-6]", label="はい", gap_before=52, join=28)
    sC4 = c2.put("proc", ["支払あり（社内）"], tag="[C1-4]", label="はい")
    EC1 = c2.put("loop_end", ["C1：発明者のループ"], gap_before=48)
    sC8 = c2.put(
        "proc",
        ["支払対象者数＝支払ありの人数", "①に支払有無(n)・従業員ID(n)・受給者氏名(n)・所属会社コード(n)"],
        tag="[C1-8]",
    )
    p.frame(
        [d71a, d71b, d71c, LC1, dC2a, dC3, n_out, dC6, sC4, EC1, sC8],
        "3-7 受給者候補の特定（発明者集合の確認 → C1）",
        pad=(22, 40, 22, 14),
    )

    dC31 = c2.put("dec", ["支払対象者は1人以上か"], tag="[C3-1]", gap_before=66)
    n03 = branch(
        p, dC31, NX, "proc", ["正常不支給 N03（到達＝完了）", "個人支給額(n)は空"], label="いいえ", tag="[C3-2]", kind="nonpay"
    )
    cF2 = p.node("conn", n03.cx, n03.b + 40, ["F"])
    p.line([n03.bottom, cF2.top])
    sC33 = c2.put(
        "proc",
        ["個人支給額＝⌊分子 ÷（分母×人数×100）⌋×100", "（割るのはここで1回だけ。途中で丸めない）"],
        tag="[C3-3]",
        label="はい",
    )
    sC34 = c2.put("proc", ["実支給額合計＝個人支給額の合計", "①＝正常支給（到達＝完了）"], tag="[C3-4]", kind="ok")
    p.frame([dC31, n03, cF2, sC33, sC34], "3-8 配分（C3）", pad=(22, 40, 22, 14))
    t_end = c2.put(
        "term", ["終了：①の1行と対応表の行（全案件）を返す", "→ 1/3 の「報奨グループのループ」へ"], gap_before=50
    )
    cF = p.node("conn", t_end.r + 70, t_end.cy, ["F"])
    p.line([cF.left, t_end.right])

    # エラー脱出（C2）：E10・E11・E03 は右端の縦線から、E06 は結合子 E から入る
    e_c2 = p.node(
        "proc",
        CX2,
        dC6.cy + 138,
        ["エラー脱出（C2）", "①＝要確認・エラーコード・エラー詳細・", "到達ブロック（3-3／3-7(C1)）", "そこまでに確定した列は残す"],
        kind="err",
    )
    exits = [(d71a, "登場する（E10）"), (d71b, "違う（E10）"), (d71c, "付いている（E10）"), (dC2a, "はい（E11）"), (dC6, "いいえ（E03）")]
    for dec, lab in exits:
        p.line([dec.right, (BUSX, dec.cy)], head=False, label=lab)
    for dec, _ in exits[1:]:
        p.dot(BUSX, dec.cy)
    p.line([(BUSX, d71a.cy), (BUSX, e_c2.cy), e_c2.right])
    cE2 = p.node("conn", CX2, e_c2.t - 42, ["E"])
    p.line([cE2.bottom, e_c2.top])
    cF3 = p.node("conn", CX2, e_c2.b + 40, ["F"])
    p.line([e_c2.bottom, cF3.top])
    p.height = legend(p, max(t_end.b, c.prev.b) + 60)
    return p


# ================================================================ 閲覧用 HTML・印刷用 HTML
MD_FILE = HERE / "B3_実フロー図_mermaid.md"
VIEWER_FILE = HERE / "B3_実フロー図.html"
PRINT_FILE = HERE / "B3_実フロー図_印刷用.html"

SHEETS = [
    (
        "sheet1",
        "1/3 全体",
        "起動から保存まで",
        "bat を実行してから結果ブックを保存するまで。ファミリーのループの中で 3-0 入力ゲートを通し、"
        "グループ付番（2/3）と報奨グループごとの処理（3/3）を呼ぶ。",
    ),
    (
        "sheet2",
        "2/3 グループ付番",
        "3-1 真のH5・3-2 報奨グループの付番",
        "ファミリーの行群ごとに1回。親を数える規則と、親が未付番の行を後回しにするパスのループで、各行に報奨グループID を振る。",
    ),
    (
        "sheet3",
        "3/3 グループ処理",
        "3-3〜3-8 ①を1行作る",
        "報奨グループごとに1回。採用ランク・対象期間・基準額・受給者・配分を順に決め、要確認はエラー脱出（C2）で①に記録する。",
    ),
]


def _inline_md(text: str) -> str:
    """表のセル用の最小限の Markdown（`code` と **太字**）を HTML にする"""
    import re

    out = esc(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


def md_table(md: str, heading: str) -> str:
    """mermaid 版の md から、見出しの直後にある表を HTML の表にする（表の中身は md の1か所だけで持つ）"""
    lines = md.split("\n")
    i = lines.index(heading)
    rows = []
    for line in lines[i + 1 :]:
        if line.startswith("## "):
            break
        if line.startswith("|"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    head, body = rows[0], [r for r in rows[2:]]
    th = "".join(f"<th scope=\"col\">{_inline_md(c)}</th>" for c in head)
    trs = "".join("<tr>" + "".join(f"<td>{_inline_md(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def md_paragraph_after(md: str, heading: str) -> str:
    """見出しの直後の段落（表の前の説明文）を1つの段落にする"""
    lines = md.split("\n")
    i = lines.index(heading)
    para = []
    for line in lines[i + 1 :]:
        if line.startswith("|") or line.startswith("## "):
            break
        if line.strip():
            para.append(line.strip())
    return _inline_md("".join(para))


VIEWER_CSS = """
:root{
  --ground:#edf0f3; --surface:#ffffff; --paper:#ffffff; --ink:#1d252d; --muted:#56616d; --line:#d3d9df;
  --accent:#0f766e; --accent-ink:#ffffff; --warn:#c2410c; --err:#b91c1c; --code-bg:#e4e9ee; --shadow:0 1px 2px rgba(20,30,40,.08),0 6px 20px rgba(20,30,40,.06);
  color-scheme:light;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#121619; --surface:#1a2026; --paper:#ffffff; --ink:#e3e8ec; --muted:#9ba6b1; --line:#2d363f;
    --accent:#2dd4bf; --accent-ink:#06201d; --warn:#fb923c; --err:#f87171; --code-bg:#242c35; --shadow:0 1px 2px rgba(0,0,0,.4);
    color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --ground:#121619; --surface:#1a2026; --paper:#ffffff; --ink:#e3e8ec; --muted:#9ba6b1; --line:#2d363f;
  --accent:#2dd4bf; --accent-ink:#06201d; --warn:#fb923c; --err:#f87171; --code-bg:#242c35; --shadow:0 1px 2px rgba(0,0,0,.4);
  color-scheme:dark;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:"BIZ UDPGothic","Yu Gothic UI","Meiryo","Hiragino Sans","Noto Sans JP",sans-serif;
  font-size:15px;line-height:1.75;padding-inline:clamp(16px,3vw,40px);padding-block:0 48px}
.text{max-width:62rem}
header.top{padding-block:36px 8px}
.eyebrow{font-size:12.5px;letter-spacing:.08em;color:var(--muted);margin:0 0 6px}
h1{font-size:clamp(22px,3.2vw,30px);line-height:1.35;margin:0 0 12px;text-wrap:balance}
h2{font-size:19px;line-height:1.4;margin:0 0 10px;text-wrap:balance}
p{margin:0 0 12px}
.lead{font-size:16px}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;font-size:13.5px;color:var(--muted);margin:14px 0 0}
.meta dt{font-weight:700}
.meta dd{margin:0}
code{font-family:"BIZ UDGothic","Consolas","MS Gothic",monospace;font-size:.88em;background:var(--code-bg);padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}
nav.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--ground);border-bottom:1px solid var(--line);
  display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;padding-block:10px;margin-block:18px 22px}
nav.bar .jump{display:flex;flex-wrap:wrap;gap:6px}
nav.bar a{color:var(--ink);text-decoration:none;font-size:14px;padding:5px 12px;border:1px solid var(--line);border-radius:999px;background:var(--surface)}
nav.bar a:hover{border-color:var(--accent)}
.zoom{display:flex;gap:0;margin-left:auto;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.zoom button{font:inherit;font-size:13.5px;padding:5px 12px;border:0;background:var(--surface);color:var(--ink);cursor:pointer}
.zoom button[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink)}
a:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
figure.sheet{margin:0 0 36px}
figure.sheet figcaption{max-width:62rem;margin:0 0 10px}
figure.sheet figcaption .no{font-size:12.5px;letter-spacing:.08em;color:var(--muted);display:block}
figure.sheet figcaption strong{font-size:18px}
figure.sheet figcaption span.desc{display:block;color:var(--muted);font-size:14px}
.paper{overflow-x:auto;background:var(--paper);border:1px solid var(--line);border-radius:6px;box-shadow:var(--shadow);scroll-margin-top:70px}
.paper svg{display:block;width:100%;height:auto}
body.actual .paper svg{width:auto;max-width:none}
section{margin-block:40px 0}
ul.read{margin:0;padding-left:1.3em}
ul.read li{margin-bottom:6px}
.mark-star{color:var(--accent);font-weight:700}
.mark-warn{color:var(--warn);font-weight:700}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--surface)}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:13.5px;line-height:1.6}
th,td{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line)}
thead th{background:var(--code-bg);font-weight:700;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
td:first-child{font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--muted)}
ul.files{list-style:none;margin:0;padding:0;display:grid;gap:6px}
ul.files li{display:grid;grid-template-columns:minmax(0,22rem) 1fr;gap:4px 16px}
@media (max-width:640px){ul.files li{grid-template-columns:1fr}.zoom{margin-left:0}}
footer{margin-top:44px;color:var(--muted);font-size:12.5px}
"""

VIEWER_JS = """
(function(){
  var KEY='b3-flow-zoom';
  var btns=document.querySelectorAll('.zoom button');
  function apply(mode){
    document.body.classList.toggle('actual',mode==='actual');
    btns.forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.mode===mode));});
  }
  var saved='fit';
  try{saved=localStorage.getItem(KEY)||'fit';}catch(e){}
  apply(saved);
  btns.forEach(function(b){b.addEventListener('click',function(){
    apply(b.dataset.mode);
    try{localStorage.setItem(KEY,b.dataset.mode);}catch(e){}
  });});
})();
"""


def build_viewer(svgs: list[str]) -> str:
    md = MD_FILE.read_text(encoding="utf-8")
    diff_heading = "## 仕様書の図との違い（★の中身）"
    check_heading = "## 合成データでの確かめ（図の裏付け）"
    figs = []
    for (sid, no, name, desc), svg in zip(SHEETS, svgs):
        figs.append(
            f'<figure class="sheet" id="{sid}"><figcaption><span class="no">{esc(no)}</span>'
            f'<strong>{esc(name)}</strong><span class="desc">{esc(desc)}</span></figcaption>'
            f'<div class="paper" tabindex="0" aria-label="{esc(no)} {esc(name)}（横にスクロールできます）">{svg}</div></figure>'
        )
    jump = "".join(f'<a href="#{sid}">{esc(no)}</a>' for sid, no, _, _ in SHEETS)
    files = [
        ("B3_実フロー図.html", "このページ（3枚の図と、仕様書の図との違い）"),
        ("svg/B3_実フロー図_1_全体.svg ほか2枚", "図そのもの。ブラウザ・Word・PowerPoint に貼れる"),
        ("B3_実フロー図.pdf", "A3 縦・3ページ（印刷・配布用）"),
        ("png/B3_実フロー図_*.png", "画像版（2倍の解像度）"),
        ("B3_実フロー図_mermaid.md", "同じ内容の mermaid 版。ほかの LLM での照合用。差分表・確認表の元"),
        ("make_b3_flow.py", "作図スクリプト（標準ライブラリだけ）。直したら再実行すると svg と HTML が作り直される"),
        ("検証/verify_b3_paths.py", "合成データで分岐を確かめるスクリプト"),
    ]
    file_items = "".join(f"<li><code>{esc(a)}</code><span>{esc(b)}</span></li>" for a, b in files)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>B3 登録後報奨 実フロー図</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDGothic:wght@400;700&family=BIZ+UDPGothic:wght@400;700&display=swap">
<style>{VIEWER_CSS}</style>
</head>
<body>
<header class="top text">
<p class="eyebrow">報奨金算出プログラム　B3 登録後報奨</p>
<h1>B3 登録後報奨 実フロー図</h1>
<p class="lead">仕様書の mermaid 図ではなく、<strong>プログラムが実際にたどる順序・分岐・出口</strong>を、JIS X 0121 の記号で描いたフローチャートです。
ループは戻り矢印ではなくループ端（始端と終端の対）で表し、要確認・正常不支給・処理全体の停止を出口ごとに描き分けました。</p>
<dl class="meta">
<dt>根拠のコード</dt><dd><code>kunonao/houshoukin</code> ブランチ <code>b3-registered-after</code>（<code>3c4b710</code>）＝ 共有用_報奨金算出_20260922 のプログラム</dd>
<dt>対応する仕様書</dt><dd>B3_登録後報奨フロー.md・B3.0_ファミリー処理.md・B3.2_報奨グループ特定.md・C_共通部品.md（改訂3a）。図の [B3-x-y] はそのブロック番号</dd>
<dt>作図</dt><dd>2026-09-28</dd>
</dl>
</header>
<nav class="bar" aria-label="図の切り替え">
<div class="jump">{jump}<a href="#diff">仕様書の図との違い</a></div>
<div class="zoom" role="group" aria-label="図の大きさ"><button type="button" data-mode="fit" aria-pressed="true">幅に合わせる</button><button type="button" data-mode="actual" aria-pressed="false">原寸</button></div>
</nav>
<main>
{"".join(figs)}
<section class="text" id="read">
<h2>図の読み方</h2>
<ul class="read">
<li>上から下へ読む。判断の「はい」は下へ、「いいえ」やエラーは横へ出る。横へ出た線が背骨に戻る所は矢印で合流させた。</li>
<li>角を切った六角形の対がループ端。上の記号（始端）に回す対象と順序、下の記号（終端）で1回分が終わる。</li>
<li>丸に英字は結合子。同じ英字の所へ続く（A＝1/3 の左列から右列、E＝E06 からエラー脱出、F＝グループの終了、G＝3/3 の左列から右列）。</li>
<li>二重線の箱は定義済み処理（別ページの図か、B0・C0 などの共通部品）。台形は人の手作業。</li>
<li><span class="mark-star">★</span> は仕様書の mermaid 図には描かれていない実際の処理（本文やコードにはある）。中身は下の表。</li>
<li><span class="mark-warn">⚠</span> は技術的な不具合として処理全体を止める所。要確認にはならず、結果ブックは保存されない（bat は「異常終了」と表示）。</li>
</ul>
</section>
<section class="text" id="diff">
<h2>仕様書の図との違い</h2>
<p>{md_paragraph_after(md, diff_heading)}</p>
</section>
<div class="tablewrap" style="margin-top:10px">{md_table(md, diff_heading)}</div>
<section class="text" id="check">
<h2>合成データでの確かめ</h2>
<p>{md_paragraph_after(md, check_heading)}</p>
</section>
<div class="tablewrap" style="margin-top:10px">{md_table(md, check_heading)}</div>
<section class="text" id="files">
<h2>ファイル</h2>
<ul class="files">{file_items}</ul>
</section>
</main>
<footer class="text">{esc(SOURCE_NOTE)}</footer>
<script>{VIEWER_JS}</script>
</body>
</html>
"""


def build_print(names: list[str]) -> str:
    """A3 縦・1枚1ページの印刷用。Chromium の --print-to-pdf で PDF にする（README 参照）"""
    imgs = "".join(f'<div class="pg"><img src="svg/{esc(n)}" alt=""></div>' for n in names)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>B3 実フロー図（印刷用）</title>
<style>
@page {{ size: A3 portrait; margin: 8mm; }}
html,body {{ margin:0; background:#fff; }}
.pg {{ width:281mm; height:404mm; display:flex; align-items:flex-start; justify-content:center; page-break-after:always; break-after:page; }}
.pg:last-child {{ page-break-after:auto; break-after:auto; }}
.pg img {{ max-width:100%; max-height:100%; width:auto; height:auto; }}
</style></head>
<body>{imgs}</body></html>
"""


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    pages = [
        ("B3_実フロー図_1_全体.svg", page1()),
        ("B3_実フロー図_2_グループ付番.svg", page2()),
        ("B3_実フロー図_3_グループ処理.svg", page3()),
    ]
    svgs = []
    for name, page in pages:
        svg = render(page)
        (OUT_DIR / name).write_text(svg, encoding="utf-8")
        svgs.append(svg)
        print(f"{name}: {page.width:.0f} x {page.height:.0f}")
    VIEWER_FILE.write_text(build_viewer(svgs), encoding="utf-8")
    PRINT_FILE.write_text(build_print([n for n, _ in pages]), encoding="utf-8")
    print(f"{VIEWER_FILE.name}・{PRINT_FILE.name} を作り直した")


if __name__ == "__main__":
    main()
