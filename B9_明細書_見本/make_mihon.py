"""B9 明細書の見本（ダミー2人分）を作る。本番の B9（明細書出力）ではない。

打ち合わせで明細書の形を確かめるための見本（2026-09-29 速水さん「仮で、この形で作ってみて。ダミーで2人くらい」）。
形の元は同日に決めた1行の形：案件／評価ランク／元の金額／期間の按分／受け取る人数／計算／お支払額。
計算式は4報奨とも出し、日割り（期間の按分）は登録後報奨だけに出す。部署は明細書から外す。

見本の数字がそのまま今のプログラムの答えになるよう、本番と同じ部品を通して作る。
1. ダミーの①（4報奨）を、本物の列定義（houshoukin の各 glossary）と本物の書き出し（write_result_rows）で作る。
   基準報奨金額は見本の金額マスタ（設定_初期見本/金額マスタ.xlsx）から引き、配分は本物の関数
   （出願時報奨は _apply_proxy_distribution、登録後報奨は decide_amount＋c3_distribute、他は c3_distribute）で出す。
2. 本物の B7・B8（run_b7_b8）に通して、②受給者別明細と受給者別集計を作る。
3. 明細書は②だけから組み立てる。「計算」欄の式は②の値から作り、式で出した額が②の個人支給額と
   一致することを確かめる。合計は受給者別集計（B8）の合計金額とも照合する。合わなければ止める。

使い方：python make_mihon.py <houshoukin のプログラムフォルダ>
出力（このフォルダ）：見本_明細書.html（画面用・2人分）、pdf/{年}_報奨金明細書_{従業員ID}_{氏名}.pdf（1人1ファイル）、
                     見本_算出結果_ダミー.xlsx（①4報奨・②受給者別明細・受給者別集計）
PDF は Chromium で印刷する（環境変数 CHROME で場所を指定できる）。
"""

from __future__ import annotations

import csv
import html
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from fractions import Fraction
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit("使い方：python make_mihon.py <houshoukin のプログラムフォルダ>")
PROGRAM = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(PROGRAM))

# houshoukin は引数で渡したフォルダから読む（見本がどの版のコードの答えかを固定するため）
from openpyxl import Workbook, load_workbook  # noqa: E402

from houshoukin.b1 import glossary as b1_glossary  # noqa: E402
from houshoukin.b1.proxy_determination import ProxyDetermination  # noqa: E402
from houshoukin.b1.registration import HIGHLIGHT_COLUMNS as B1_HIGHLIGHT  # noqa: E402
from houshoukin.b1.registration import _apply_proxy_distribution  # noqa: E402
from houshoukin.b2 import glossary as b2_glossary  # noqa: E402
from houshoukin.b2.registration import HIGHLIGHT_COLUMNS as B2_HIGHLIGHT  # noqa: E402
from houshoukin.b3 import glossary as b3_glossary  # noqa: E402
from houshoukin.b3.amount import decide_amount  # noqa: E402
from houshoukin.b3.period import year_days  # noqa: E402
from houshoukin.b3.registration import HIGHLIGHT_COLUMNS as B3_HIGHLIGHT  # noqa: E402
from houshoukin.b4 import glossary as b4_glossary  # noqa: E402
from houshoukin.b4.registration import HIGHLIGHT_COLUMNS as B4_HIGHLIGHT  # noqa: E402
from houshoukin.b7.runner import DETAIL_SHEET, SUMMARY_SHEET, run_b7_b8  # noqa: E402
from houshoukin.b8.aggregate import REPORT_COLUMNS  # noqa: E402
from houshoukin.common.c0_base_amount import c0_base_amount  # noqa: E402
from houshoukin.common.c3_distribute import c3_distribute  # noqa: E402
from houshoukin.common.glossary import LAW_NAMES  # noqa: E402
from houshoukin.common.money import exact_amount_text, round_payment  # noqa: E402
from houshoukin.common.rate_table import RateSource  # noqa: E402
from houshoukin.common.rules import ISHIDA_COMPANY_CODE, ROUNDING_UNIT_JPY  # noqa: E402
from houshoukin.io.result_sheet import write_result_rows  # noqa: E402
from houshoukin.io.settings import REPORT_LABELS  # noqa: E402

HERE = Path(__file__).resolve().parent
RATES_XLSX = PROGRAM.parent / "設定_初期見本" / "金額マスタ.xlsx"

# ---- 見本の前提（2026年に支払う分。B3 は前年の暦年、他は 9/21〜9/20） ----
FISCAL_YEAR = 2026
ISSUED_ON = "2026/12/25"  # 明細書発行日（旧マクロの既定値と同じ 12/25）
B124_PERIOD = ("2025-09-21", "2026-09-20")
B3_PERIOD = ("2025-01-01", "2025-12-31")
OTHER_COMPANY = "0000003447"  # 別会社。人数に入らない（管理台帳 L18・L19 と同じ扱い）
ISSUER = ("京都市左京区聖護院山王町４４番地", "株式会社イシダ")  # 旧明細書と同じ発行者の表示

# ダミーの人。従業員IDは本物と同じ形（ログインIDの形。人事データの従業員番号とは別）にしてある
PEOPLE = {"t-yamada": "山田 太郎", "h-sato": "佐藤 花子", "i-suzuki": "鈴木 一郎"}
DEPARTMENTS = {"t-yamada": "K000001", "h-sato": "K000002", "i-suzuki": "K000003"}
TARGETS = ["t-yamada", "h-sato"]  # 明細書を作る2人
OUTSIDER = "outsider"  # 社外の共同発明者（別会社。支払わず、人数にも入らない）

# ---- 見本の案件（番号はすべて架空。実在の番号と重ならないよう 99… にしてある） ----
TEXTS = {
    "組合せ計量装置": "【課題】計量ホッパの数を増やさずに組合せの選択肢を増やし、計量精度を高める。"
    "【解決手段】複数の計量ホッパと、計量ホッパから排出された物品を一時的に保持するメモリホッパとを備え、"
    "制御部は、計量ホッパとメモリホッパの計量値を組み合わせて目標重量に最も近い組合せを選び、"
    "選ばれなかったメモリホッパの物品を次の組合せに持ち越す。",
    "物品振分装置": "【課題】搬送中の物品を、重量の判定結果に応じて速度を落とさずに振り分ける。"
    "【解決手段】コンベアで搬送される物品の重量を計量する計量部と、計量結果に基づいて物品を複数の排出経路の"
    "いずれかへ導く振分部とを備え、振分部は、物品の搬送速度と重量から振分けを始める時刻を求めて動作する。",
    "包装袋の製造方法": "【課題】シール不良を減らし、包装袋の製造速度を上げる。"
    "【解決手段】フィルムを筒状に成形する工程と、縦シールと横シールを行う工程とを含み、横シールの工程では、"
    "シールジョーの温度と押し付ける時間を、フィルムの厚さの測定値に応じて調整する。",
    "箱詰め装置": "【課題】大きさの異なる箱にも、物品を安定して箱詰めする。"
    "【解決手段】物品を保持して箱の上方まで移送する移送部と、箱の大きさを検出する検出部とを備え、"
    "移送部は、検出した箱の大きさに応じて物品を降ろす位置と速度を変える。",
    "計量コンベア": "【課題】コンベアの振動による計量誤差を小さくする。"
    "【解決手段】物品を搬送しながら計量する計量コンベアであって、ロードセルの出力から、コンベアの駆動に伴う"
    "振動の成分を取り除くフィルタを備え、フィルタの特性を搬送速度に応じて切り替える。",
    "Ｘ線検査装置": "【課題】包装された食品に混じった異物を、包材の模様に惑わされずに検出する。"
    "【解決手段】Ｘ線を照射して透過画像を得る撮像部と画像処理部とを備え、画像処理部は、包材の模様を学習した"
    "結果を用いて透過画像から包材の像を取り除いた後に、異物を検出する。",
    "計量ホッパ": "【課題】付着しやすい物品でも、計量ホッパから残らず排出する。"
    "【解決手段】計量ホッパのゲートの内面に物品が付着しにくい凹凸を設け、ゲートを開くときにホッパ本体を"
    "短い時間だけ振動させる。",
}
# 出願時報奨：(出願番号, 出願日, 発明の名称, 発明者, 代理作成者)
B1_CASES = [
    ("2026-990101", "2025-11-05", "組合せ計量装置", ["t-yamada", "h-sato"], None),
    ("2026-990102", "2026-01-20", "物品振分装置", ["t-yamada", "i-suzuki"], "h-sato"),
    ("2026-990103", "2026-03-12", "包装袋の製造方法", ["t-yamada", OUTSIDER], None),
]
# 登録時報奨：(登録番号, 登録日, 発明の名称, 発明者)
B2_CASES = [("9900101", "2026-03-10", "箱詰め装置", ["t-yamada", "h-sato", "i-suzuki"])]
# 登録後報奨：(登録番号, 採用評価ランク, 対象期間開始日, 対象期間終了日, 発明の名称, 発明者)
#   9900102 は 2025-04-17 登録 → 259日／365日。9900104 は 2025-04-30 に権利放棄 → 120日／365日
B3_CASES = [
    ("9900102", "10", "2025-04-17", "2025-12-31", "計量コンベア", ["t-yamada", "h-sato", "i-suzuki"]),
    ("9900103", "05", "2025-01-01", "2025-12-31", "Ｘ線検査装置", ["t-yamada", "i-suzuki"]),
    ("9900104", "12", "2025-01-01", "2025-04-30", "計量ホッパ", ["h-sato"]),
]
# 外国出願時報奨：(国コード, 出願番号, 出願日, 発明の名称, 発明者)
B4_CASES = [
    ("US", "19/990,101", "2025-12-02", "組合せ計量装置", ["t-yamada", "h-sato"]),
    ("CN", "202599900103.2", "2026-04-08", "包装袋の製造方法", ["t-yamada", OUTSIDER]),
]


def members(inventors: list[str]) -> tuple[list[bool], list[str], list[str], list[str]]:
    """発明者の並びを①の (n) 列の値にする。社外（別会社）の人は支払有無＝なし（C1 と同じ判定）"""
    flags, ids, names, codes = [], [], [], []
    for who in inventors:
        inside = who != OUTSIDER
        flags.append(inside)
        ids.append(who if inside else "")
        names.append(PEOPLE[who] if inside else "外部 発明者")
        codes.append(ISHIDA_COMPANY_CODE if inside else OTHER_COMPANY)
    return flags, ids, names, codes


def common(code: str, seq: int, key: str, law: str, number: str, title: str) -> dict[str, object]:
    """4報奨に共通の①の列（識別・案件の内容）"""
    period = B3_PERIOD if code == "B3" else B124_PERIOD
    return {
        "支払単位ID": f"{FISCAL_YEAR}-{code}-{seq:06d}",
        "報奨種別コード": code,
        "報奨種別": REPORT_LABELS[code],
        "制度対象期間開始日": period[0],
        "制度対象期間終了日": period[1],
        "代表案件キー（各国ユーザ番号）": key,
        "構成案件数": 1,
        "四法区分コード": law,
        "案件表示番号": number,
        "発明の名称": title,
        "明細表示本文": TEXTS[title],
        "エラーコード": None,
        "エラー詳細": None,
        "到達ブロック": "完了",
    }


def paid(result: dict[str, object], flags: list[bool], shares: list[int | None], total: int,
         status: str, reason: str | None, ids: list[str], names: list[str], codes: list[str]) -> None:
    result.update(
        {
            "支払対象者数": sum(flags),
            "支払有無(n)": flags,
            "従業員ID(n)": ids,
            "受給者氏名(n)": names,
            "所属会社コード(n)": codes,
            "個人支給額(n)": shares,
            "実支給額合計": total,
            "処理結果状態": status,
            "状態理由コード": reason,
        }
    )


def build_first_sheets() -> dict[str, list[dict[str, object]]]:
    """ダミーの①を報奨ごとに作る。金額は本物の金額マスタと本物の配分関数で出す"""
    rates = {code: RateSource(RATES_XLSX, label).load() for code, label in REPORT_LABELS.items()}
    sheets: dict[str, list[dict[str, object]]] = {code: [] for code in REPORT_LABELS}

    for seq, (app_no, filed, title, inventors, proxy) in enumerate(B1_CASES, 1):
        result = common("B1", seq, f"MIHON-B1-{seq:03d}JP", "P", f"特願{app_no}", title)
        base = c0_base_amount("P", date.fromisoformat(filed), rates["B1"]).amount_yen
        result.update({"出願日": filed, "出願番号": app_no, "基準報奨金額": base})
        flags, ids, names, codes = members(inventors)
        determination = ProxyDetermination(
            established=proxy is not None,
            employee_id=proxy,
            name=PEOPLE[proxy] if proxy else None,
        )
        # 本物の B1-5（代理作成なら発明者は3/4を人数で割り、代理作成者は1/4）
        _apply_proxy_distribution(result, determination, base, flags, ids, names, codes)
        sheets["B1"].append(result)

    for seq, (reg_no, registered, title, inventors) in enumerate(B2_CASES, 1):
        result = common("B2", seq, f"MIHON-B2-{seq:03d}JP", "P", f"{LAW_NAMES['P']}-{reg_no}", title)
        base = c0_base_amount("P", date.fromisoformat(registered), rates["B2"]).amount_yen
        result.update({"登録日": registered, "基準報奨金額": base, "配分対象原資額": base})
        flags, ids, names, codes = members(inventors)
        shares, total, status, reason = c3_distribute(base, flags, ROUNDING_UNIT_JPY)
        paid(result, flags, shares, total, status, reason, ids, names, codes)
        sheets["B2"].append(result)

    ydays = year_days(date.fromisoformat(B3_PERIOD[0]))
    for seq, (reg_no, rank, start, end, title, inventors) in enumerate(B3_CASES, 1):
        result = common("B3", seq, f"MIHON-B3-{seq:03d}JP", "P", f"{LAW_NAMES['P']}-{reg_no}", title)
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        # 本物の B3-6（基準額×対象日数を分子、年日数を分母にして、途中では割らない）
        amount = decide_amount(rank, days, ydays, rates["B3"], date.fromisoformat(B3_PERIOD[1]))
        assert amount.numerator is not None and amount.denominator is not None
        result.update(
            {
                "処理順": seq,
                "ファミリー番号": f"MIHON-F{seq:03d}",
                "報奨グループID": f"MIHON-G{seq:03d}",
                "採用評価ランク": rank,
                "採用評価ランク判断日": "2025-12-01",
                "対象期間開始日": start,
                "始期根拠": "登録日" if start > B3_PERIOD[0] else "制度対象期間開始日",
                "対象期間終了日": end,
                "終期候補根拠": "権利放棄日" if end < B3_PERIOD[1] else "権利満了日",
                "対象期間終了日根拠": "終期候補" if end < B3_PERIOD[1] else "制度対象期間終了日",
                "対象日数": days,
                "年日数": ydays,
                "按分率": f"{days}/{ydays}",
                "基準報奨金額": amount.base_amount,
                "配分対象原資額": exact_amount_text(amount.numerator, amount.denominator),
                "配分対象原資額_分子": amount.numerator,
                "配分対象原資額_分母": amount.denominator,
            }
        )
        flags, ids, names, codes = members(inventors)
        shares, total, status, reason = c3_distribute(
            amount.numerator, flags, ROUNDING_UNIT_JPY, denominator=amount.denominator
        )
        paid(result, flags, shares, total, status, reason, ids, names, codes)
        sheets["B3"].append(result)

    for seq, (country, app_no, filed, title, inventors) in enumerate(B4_CASES, 1):
        number = f"({country}{LAW_NAMES['P']}){app_no}"
        result = common("B4", seq, f"MIHON-B4-{seq:03d}{country}", "P", number, title)
        base = c0_base_amount("P", date.fromisoformat(filed), rates["B4"]).amount_yen
        result.update(
            {
                "出願日": filed,
                "出願番号": app_no,
                "国コード": country,
                "ファミリー番号": f"MIHON-F1{seq:02d}",
                "支払対象選定根拠": "見本",
                "基準報奨金額": base,
                "配分対象原資額": base,
            }
        )
        flags, ids, names, codes = members(inventors)
        shares, total, status, reason = c3_distribute(base, flags, ROUNDING_UNIT_JPY)
        paid(result, flags, shares, total, status, reason, ids, names, codes)
        sheets["B4"].append(result)
    return sheets


def write_workbook(sheets: dict[str, list[dict[str, object]]], path: Path) -> None:
    """①を本物の書き出し（推定列・保護・色つき）で1冊にする。シート名は本物と同じ報奨名"""
    settings = {
        "B1": (b1_glossary, B1_HIGHLIGHT),
        "B2": (b2_glossary, B2_HIGHLIGHT),
        "B3": (b3_glossary, B3_HIGHLIGHT),
        "B4": (b4_glossary, B4_HIGHLIGHT),
    }
    workbook = Workbook()
    workbook.remove(workbook.active)
    for code, label in REPORT_LABELS.items():
        glossary, highlight = settings[code]
        write_result_rows(
            workbook.create_sheet(label),
            glossary.RESULT_COLUMNS,
            sheets[code],
            highlight,
            column_groups=glossary.COLUMN_GROUPS,
            editable_columns=glossary.EDITABLE_COLUMNS,
        )
    workbook.save(path)


def write_hr_csv(path: Path) -> None:
    """ダミーの人事データ（本物と同じ見出し。所属はコードで来る）"""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["従業員ID", "氏名", "所属", "退職日付"])
        for employee_id, name in PEOPLE.items():
            writer.writerow([employee_id, name, DEPARTMENTS[employee_id], ""])


def read_table(workbook_path: Path, sheet: str) -> list[dict[str, object]]:
    workbook = load_workbook(workbook_path, data_only=True)
    try:
        rows = list(workbook[sheet].values)
    finally:
        workbook.close()
    header = [str(v) for v in rows[0]]
    return [dict(zip(header, row)) for row in rows[1:]]


# ---- ここから明細書の組み立て（②だけを使う） ----


# 列幅（%）。A4縦で左右12mmの余白の内側に収める
COLUMN_WIDTHS = {
    "B3": [19, 8, 11, 13, 11, 26, 12],
    "other": [27, 13, 12, 33, 15],
}


def yen(value: int) -> str:
    return f"{value:,}円"


def calculation(row: dict[str, object]) -> tuple[str, str]:
    """②の1行から「計算」欄の式と「受け取る人数」欄の表示を返す。

    式は、プログラムと同じ順（元の金額×割合×日数÷年日数÷人数、途中で丸めず最後に100円未満切捨て）で書く。
    式で出した額が②の個人支給額と一致しなければ止める（見本では必ず一致する。本番の扱いは仕様で決める）。
    途中の額は1円未満を切り捨てて表示する。
    """
    code = str(row["報奨種別コード"])
    base = int(str(row["基準報奨金額"]))
    people = int(str(row["支払対象者数"]))
    amount = Fraction(base)
    text = f"{base:,}"
    divide = True
    if code == "B1" and row["配分モード"] == "代理作成":
        if row["受給者区分"] == "代理作成者":
            amount *= Fraction(1, 4)
            text += "×1/4"
            divide = False  # 代理作成者の1/4は人数で割らない
        else:
            amount *= Fraction(3, 4)
            text += "×3/4"
    if code == "B3":
        days, whole = (int(v) for v in str(row["按分率"]).split("/"))
        if days != int(str(row["対象日数"])):
            raise ValueError(f"{row['明細行ID']}：按分率と対象日数が食い違っています")
        amount *= Fraction(days, whole)
        if days != whole:
            text += f"×{days}/{whole}"
    if divide:
        amount /= people
        text += f"÷{people}"
    share = round_payment(amount.numerator, amount.denominator, ROUNDING_UNIT_JPY)
    if share != row["個人支給額"]:
        raise ValueError(f"{row['明細行ID']}：式の額 {share} と個人支給額 {row['個人支給額']} が違います")
    text += f"＝{amount.numerator // amount.denominator:,}円"
    return text, ("代理作成" if not divide else f"{people}名")


def notes(rows: list[dict[str, object]]) -> list[str]:
    """欄外の注記。旧明細書の4行を元に、式を出すことに合わせて書き直した（文言は打ち合わせで決める）"""
    start, end = (date.fromisoformat(d) for d in B3_PERIOD)
    items = [
        "本報奨金は確定申告の対象となる場合があります。詳細については、法務･知的財産ポータル　知財関連ページの"
        "「報奨金の確定申告について」をご参照下さい。確定申告の対象となる場合には個別に手続きをお願い致します。",
        "「計算」欄は、お支払額の求め方です。元の金額（規程で定める額）を受け取る人数で割り、最後に100円未満を"
        "切り捨てています。受け取る人数は、発明者のうち当社に所属する人の数です。途中の額は1円未満を切り捨てて"
        "表示しています。",
    ]
    if any(r["報奨種別コード"] == "B3" for r in rows):
        items.append(
            "登録後報奨には、評価ランクが記載されています。詳細については、法務･知的財産ポータル　知財関連ページの"
            "「発明取扱規定の概略説明資料」をご参照下さい。また、登録後報奨は、報奨対象期間"
            f"（{start.year}/{start.month}/{start.day}～{end.year}/{end.month}/{end.day}）に"
            "権利が存在した日数で日割りしています（期間の按分＝権利が存在した日数／その年の日数）。"
        )
    if any(r["配分モード"] == "代理作成" for r in rows):
        items.append(
            "出願時報奨で、発明届を代理で作成した人がいる場合は、元の金額の1/4を代理作成者に、"
            "残りの3/4を発明者にお支払いしています。"
        )
    return items


def statement(employee_id: str, details: list[dict[str, object]], summary: dict[str, object]) -> str:
    """1人分の明細書（HTML の section）。並びは報奨種別コード順、その中は案件表示番号順（B9 仕様 9-2）"""
    mine = [r for r in details if r["従業員ID"] == employee_id]
    name = str(mine[0]["氏名"])
    total = sum(int(str(r["個人支給額"])) for r in mine)
    if total != summary["合計金額"]:
        raise ValueError(f"{employee_id}：明細の合計 {total} と受給者別集計の合計金額が違います")
    parts = [
        '<section class="sheet">',
        '<div class="mark">見本</div>',
        '<p class="badge">見本（ダミーデータ・架空の案件）</p>',
        '<header class="top">',
        f"<h1>{FISCAL_YEAR}年 特許等報奨金支払明細書</h1>",
        f'<div class="issuer"><div>明細書発行日 {ISSUED_ON}</div>'
        + "".join(f"<div>{html.escape(line)}</div>" for line in ISSUER)
        + "</div>",
        "</header>",
        '<div class="who">',
        f'<div class="person"><div class="id">社員番号　{html.escape(employee_id)}</div>'
        f'<div class="name">{html.escape(name)}<span>様</span></div></div>',
        f'<div class="total"><span>合計</span><b>{yen(total)}</b></div>',
        "</div>",
        '<ul class="notes">' + "".join(f"<li>{html.escape(n)}</li>" for n in notes(mine)) + "</ul>",
    ]
    for code, label in REPORT_LABELS.items():
        rows = sorted((r for r in mine if r["報奨種別コード"] == code), key=lambda r: str(r["案件表示番号"]))
        if not rows:
            continue
        subtotal = sum(int(str(r["個人支給額"])) for r in rows)
        money_column, count_column = REPORT_COLUMNS[code]
        if (subtotal, len(rows)) != (summary[money_column], summary[count_column]):
            raise ValueError(f"{employee_id}：{label}の小計・件数が受給者別集計と違います")
        # 日割りは登録後報奨だけの考え方なので、評価ランクと期間の按分の列は登録後報奨にだけ置く
        heads = ["案件", "元の金額", "受け取る人数", "計算", "お支払額"]
        if code == "B3":
            heads = ["案件", "評価ランク", "元の金額", "期間の按分", "受け取る人数", "計算", "お支払額"]
        widths = COLUMN_WIDTHS["B3" if code == "B3" else "other"]
        parts += [
            f'<section class="award award-{code.lower()}">',
            f'<h2><span class="label">{label}</span><span class="count">{len(rows)}件</span>'
            f'<span class="sub">小計 <b>{yen(subtotal)}</b></span></h2>',
            "<table>",
            "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in widths) + "</colgroup>",
            "<thead><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr></thead>",
        ]
        for row in rows:
            formula, receivers = calculation(row)
            cells = [f'<td class="case">{html.escape(str(row["案件表示番号"]))}</td>']
            if code == "B3":
                days, whole = str(row["按分率"]).split("/")
                cells.append(f'<td class="c">{html.escape(str(row["採用評価ランク"]))}</td>')
                cells.append(f'<td class="r">{yen(int(str(row["基準報奨金額"])))}</td>')
                cells.append(f'<td class="c">{days}日／{whole}日</td>')
            else:
                cells.append(f'<td class="r">{yen(int(str(row["基準報奨金額"])))}</td>')
            cells.append(f'<td class="c">{receivers}</td>')
            cells.append(f'<td class="calc">{html.escape(formula)}</td>')
            cells.append(f'<td class="pay">{yen(int(str(row["個人支給額"])))}</td>')
            parts += [
                "<tbody>",
                "<tr class=\"line\">" + "".join(cells) + "</tr>",
                f'<tr class="desc"><td colspan="{len(heads)}"><b>{html.escape(str(row["発明の名称"]))}</b>'
                f'<span>{html.escape(str(row["明細表示本文"]))}</span></td></tr>',
                "</tbody>",
            ]
        parts += ["</table>", "</section>"]
    parts.append("</section>")
    return "\n".join(parts)


CSS = """
@page { size: A4 portrait; margin: 12mm 12mm 13mm;
  @bottom-right { content: counter(page) " / " counter(pages); font-size: 8pt; color: #6b7682; } }
:root { --ink: #1c2024; --muted: #5d6873; --rule: #b9c3cc; --band: #eef3f6; --head: #234d60; --mark: #b3261e; }
* { box-sizing: border-box; }
body { margin: 0; color: var(--ink); background: #fff; font-size: 8.8pt; line-height: 1.5;
  font-family: "Yu Gothic", "YuGothic", "Meiryo", "IPAPGothic", "IPAGothic", sans-serif; }
.sheet { position: relative; }
.sheet + .sheet { break-before: page; }
.mark { position: fixed; top: 38%; left: 0; right: 0; text-align: center; font-size: 120pt; font-weight: bold;
  color: rgba(179, 38, 30, 0.06); transform: rotate(-24deg); pointer-events: none; }
.badge { display: inline-block; margin: 0 0 3mm; padding: 0.6mm 2.2mm; border: 1px solid var(--mark);
  color: var(--mark); font-size: 8pt; }
.top { display: flex; justify-content: space-between; align-items: flex-start; gap: 6mm; }
h1 { margin: 0; font-size: 16pt; font-style: italic; text-decoration: underline; font-weight: bold; }
.issuer { text-align: right; font-size: 8.8pt; }
.issuer div:first-child { margin-bottom: 1.5mm; }
.who { display: flex; justify-content: space-between; align-items: flex-end; margin: 5mm 0 3mm;
  padding-bottom: 2mm; border-bottom: 1.5px solid var(--ink); }
.person .id { font-size: 9pt; color: var(--muted); }
.person .name { font-size: 14pt; font-weight: bold; }
.person .name span { font-size: 10pt; font-weight: normal; margin-left: 3mm; }
.total { font-size: 10pt; }
.total b { font-size: 15pt; margin-left: 4mm; }
.notes { margin: 0 0 4mm; padding: 0; list-style: none; font-size: 7.8pt; color: #3c454e; }
.notes li { padding-left: 1.1em; text-indent: -1.1em; margin: 0.4mm 0; }
.notes li::before { content: "※"; }
.award { margin-top: 4mm; }
h2 { display: flex; align-items: baseline; gap: 5mm; margin: 0 0 1mm; padding: 1mm 2mm; font-size: 10pt;
  color: #fff; background: var(--head); break-after: avoid; }
h2 .count { font-weight: normal; }
h2 .sub { margin-left: auto; font-weight: normal; }
h2 .sub b { font-weight: bold; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
th { font-weight: normal; font-size: 7.8pt; color: var(--muted); text-align: center; padding: 0.8mm 1mm;
  border-bottom: 1px solid var(--rule); }
tbody { break-inside: avoid; }
td { padding: 1mm 1.2mm; vertical-align: top; }
tr.line td { border-top: 1px solid var(--rule); background: var(--band); }
tbody:first-of-type tr.line td { border-top: none; }
td.case { font-weight: bold; overflow-wrap: anywhere; }
td.c { text-align: center; white-space: nowrap; }
td.r, td.pay { text-align: right; white-space: nowrap; }
td.calc { white-space: nowrap; font-feature-settings: "tnum"; }
td.pay { font-weight: bold; }
tr.desc td { padding: 0.6mm 1.2mm 1.6mm; font-size: 7.8pt; color: #3c454e; }
tr.desc b { color: var(--ink); margin-right: 2mm; }
@media screen {
  body { background: #d9dee3; padding: 8mm 0; }
  .sheet { width: 210mm; min-height: 297mm; margin: 0 auto 8mm; padding: 12mm; background: #fff;
    box-shadow: 0 1px 6px rgba(0, 0, 0, 0.25); overflow: hidden; }
  .sheet + .sheet { break-before: auto; }
  .mark { position: absolute; }
}
"""

def page(sections: list[str], title: str, running: str | None = None) -> str:
    """HTML 1枚（画面では A4 の紙を並べた見た目、印刷では1人ずつ改ページ）。

    running：印刷したとき2ページ目以降の上に出す「誰の明細書か」（1人1ファイルの PDF のときだけ渡す）
    """
    css = CSS
    if running:
        css += (
            "@page { @top-right { content: " + f'"{running}"'
            + "; font-size: 7.5pt; color: #6b7682; } }\n@page :first { @top-right { content: none; } }\n"
        )
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{css}</style>\n</head>\n<body>\n"
        + "\n".join(sections)
        + "\n</body>\n</html>\n"
    )


def chrome() -> str:
    for candidate in (os.environ.get("CHROME"), "/opt/pw-browsers/chromium", shutil.which("chromium"),
                      shutil.which("google-chrome"), shutil.which("chrome")):
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("Chromium が見つかりません。環境変数 CHROME で場所を指定してください")


def print_pdf(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [chrome(), "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={target}", source.as_uri()],
        check=True,
        capture_output=True,
    )


def main() -> None:
    sheets = build_first_sheets()
    with tempfile.TemporaryDirectory() as work:
        master = Path(work) / f"{FISCAL_YEAR}_報奨金算出_見本.xlsx"
        hr_csv = Path(work) / "人事データ_見本.csv"
        write_workbook(sheets, master)
        write_hr_csv(hr_csv)
        result = run_b7_b8(master, hr_csv, Path(work) / "backup", "見本")
        if result.warnings:
            sys.exit(f"B7・B8 の警告が {result.warnings} 件あります。見本の作り方を確かめてください")
        details = read_table(master, DETAIL_SHEET)
        summaries = {str(r["従業員ID"]): r for r in read_table(master, SUMMARY_SHEET)}
        shutil.copyfile(master, HERE / "見本_算出結果_ダミー.xlsx")

    sections = [statement(e, details, summaries[e]) for e in TARGETS]
    # 画面で2人分を続けて見る用。PDF は仕様どおり1人1ファイル（ページ番号も1人ごとに数える）
    combined = HERE / "見本_明細書.html"
    combined.write_text(page(sections, "明細書の見本（ダミー2人分）"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as work:
        for employee_id, section in zip(TARGETS, sections):
            name = PEOPLE[employee_id].replace(" ", "")
            single = Path(work) / f"{employee_id}.html"
            running = f"{FISCAL_YEAR}年 特許等報奨金支払明細書　{PEOPLE[employee_id]} 様（続き）"
            single.write_text(page([section], f"明細書の見本 {name}", running), encoding="utf-8")
            print_pdf(single, HERE / "pdf" / f"{FISCAL_YEAR}_報奨金明細書_{employee_id}_{name}.pdf")
    for employee_id in TARGETS:
        s = summaries[employee_id]
        print(f"{PEOPLE[employee_id]}：合計 {s['合計金額']:,}円（{s['合計件数']}件）")
    print(f"②受給者別明細 {result.details}行、受給者 {result.recipients}人。出力：{HERE}")


if __name__ == "__main__":
    main()
