"""B3 の実コード（2026-09-22 共有版＝b3-registered-after 3c4b710 と同一）に合成データを流し、
フロー図に描く分岐が実際にその順・その結果になるかを確かめる（図の裏付け用。業務データは使わない）。"""
import os
import sys
from datetime import date

# 使い方：python verify_b3_paths.py <プログラムのフォルダ>
#   例）python verify_b3_paths.py "…/共有用_報奨金算出_20260922/プログラム"
#   （openpyxl が入った環境で。houshoukin の b3-registered-after をチェックアウトしたなら その下の プログラム/ でもよい）
PROGRAM_DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("B3_PROGRAM_DIR", "")
if not PROGRAM_DIR:
    sys.exit("プログラムのフォルダ（houshoukin/ が入っている所）を引数で渡してください")
sys.path.insert(0, PROGRAM_DIR)
from houshoukin.b3.registration import process_rows  # noqa: E402
from houshoukin.common.rate_table import RateEntry  # noqa: E402

SEP = "_;_"
AMOUNTS = {1: 100000, 2: 70000, 3: 70000, 4: 50000, 5: 50000, 6: 50000, 7: 50000,
           8: 15000, 9: 15000, 10: 15000, 11: 3000, 12: 3000}
RATES = {f"{k:02d}": [RateEntry(v, date(2020, 1, 1), "test")] for k, v in AMOUNTS.items()}

def row(fam, key, cc, *, parents="", rels="", timing="K000", edate="2025-12-10", rank="05",
        inventors="山田", ids="E1", cos="0000003441", reg="2020-05-01", summary="要約",
        expiry="2040-01-01", disp="", disp_date="", ext=""):
    return {"データID": key, "各国ユーザ番号": key, "国コード": cc, "ファミリー番号": fam,
            "四法区分コード": "P", "登録番号": "0001234", "登録日": reg,
            "発明・考案・創作者": inventors, "従業員ID": ids, "所属会社コード": cos,
            "関連出願_各国ユーザ番号": parents, "法的関連性コード": rels,
            "評価判断時期コード": timing, "評価判断日": edate, "評価ランクコード": rank,
            "最終処分コード": disp, "最終処分日": disp_date, "権利満了日": expiry, "延長後満了日": ext,
            "発明の名称": "名称", "要約": summary}

rows = [
    # F01: 要素数不一致(E02) と K000 無し(E09) が同時 → 先に E02
    row("F01", "F01US", "US", inventors="甲" + SEP + "乙", ids="E1", cos="0000003441", timing="0000"),
    # F02: K000 はあるがランクが 01〜18 でない → E09 は通り、その後の値検査で E01
    row("F02", "F02JP", "JP", rank="ZZ"),
    # F03: K000 のランクが無い → E09
    row("F03", "F03JP", "JP", timing="F000"),
    # F04: 後回し。CN の単独の親 US（外国・ファミリー内）はソートで後ろ → CN を後回し → US 付番後に合流
    row("F04", "F04CN", "CN", parents="F04US", rels="H5"),
    row("F04", "F04US", "US"),
    # F05: 循環（DE の親 FR、FR の親 DE）→ 先頭 DE を後回し無しで決める → FR が合流
    row("F05", "F05DE", "DE", parents="F05FR", rels="H5"),
    row("F05", "F05FR", "FR", parents="F05DE", rels="H5"),
    # F06: JP-P フィルタ：CN の親 JP:H5, JP-P1:H5 → 真のH5 は JP だけ → JP と同じグループ。04（分割）は親に使わない
    row("F06", "F06JP", "JP"),
    row("F06", "F06JP-P1", "JP", parents="F06JP", rels="04"),
    row("F06", "F06CN", "CN", parents="F06JP" + SEP + "F06JP-P1", rels="H5" + SEP + "H5"),
    # F07: 従業員IDあり・所属会社コード空 → E11（B3.0 の図には無い出口）
    row("F07", "F07JP", "JP", ids="E7", cos=""),
    # F08: 採用ランク 13 → N01
    row("F08", "F08JP", "JP", rank="13"),
    # F09: 社外のみ → N03
    row("F09", "F09JP", "JP", ids="", cos="0000009999"),
    # F10: 発明者集合の不一致 → E10
    row("F10", "F10JP", "JP", inventors="甲", ids="E1"),
    row("F10", "F10US", "US", parents="F10JP", rels="H5", inventors="乙", ids="E2"),
    # F11: 採用ランクの判断日が下限（2025-12-01）より前 → 評価付与日警告（支給は続く）
    row("F11", "F11JP", "JP", edate="2024-11-20"),
    # F12: 明細表示本文の候補が全て空 → E06
    row("F12", "F12JP", "JP", summary=""),
    # F13: 同点 JP 優先・期間は最早始期〜最遅終期（US 放棄日 2025-06-30、JP は年内満了なし）
    row("F13", "F13JP", "JP", rank="05", reg="2025-03-01"),
    row("F13", "F13US", "US", parents="F13JP", rels="H5", rank="05", reg="2024-01-01",
        disp="5", disp_date="2025-06-30"),
]

outcome = process_rows(rows, 2026, "2025-01-01", "2025-12-31", RATES, date(2025, 12, 1))
cols = ["ファミリー番号", "報奨グループID", "構成案件数", "処理結果状態", "エラーコード", "状態理由コード",
        "到達ブロック", "評価付与日警告", "代表案件キー（各国ユーザ番号）", "対象期間開始日", "対象期間終了日",
        "対象日数", "基準報奨金額", "個人支給額(n)"]
for r in outcome.results:
    print(" | ".join(f"{c}={r.get(c)}" for c in cols if r.get(c) not in (None, "")))
    if r.get("エラー詳細"):
        print("     詳細:", r["エラー詳細"][:90])
print("rank_missing_rows:", [r["各国ユーザ番号"] for r in outcome.rank_missing_rows])
print("mapping rows:", len(outcome.mapping_rows))
