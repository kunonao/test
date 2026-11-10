"""organize.py のテスト（python -m unittest test_organize -v）"""

import io
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import organize  # noqa: E402

FAKE_ADB = """#!/bin/sh
[ "$1" = "-s" ] && shift 2
case "$1" in
  get-state) echo device ;;
  shell) shift; exec sh -c "$*" ;;
  *) exit 1 ;;
esac
"""


def make(root: Path, rel: str, content: str, days_ago: float) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    t = time.time() - days_ago * 86400
    os.utime(p, (t, t))


def tree(root: Path):
    return sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in root.rglob("*") if p.is_file())


class OrganizeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dl = self.tmp / "Download"
        self.dl.mkdir()
        self.logdir = self.tmp / "logs"
        self._old_log_dir = organize.LOG_DIR
        organize.LOG_DIR = self.logdir

    def tearDown(self):
        organize.LOG_DIR = self._old_log_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = organize.main(["--dir", str(self.dl), *args])
        return code, buf.getvalue()


class LocalTests(OrganizeTestBase):
    def test_dry_run_changes_nothing(self):
        make(self.dl, "a.pdf", "1", 2)
        make(self.dl, "a (1).pdf", "2", 1)
        before = tree(self.dl)
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("下見", out)
        self.assertEqual(tree(self.dl), before)

    def test_sort_and_keep_newest(self):
        make(self.dl, "report.pdf", "v1", 3)
        make(self.dl, "report (1).pdf", "v2!", 2)
        make(self.dl, "report (2).pdf", "v3!!", 1)
        make(self.dl, "photo.jpg", "img", 1)
        make(self.dl, "same.jpg", "img", 2)  # photo.jpg と中身が同じで古い
        make(self.dl, "chapter-1.pdf", "c1", 1)  # 「chapter.pdf」が無いので版違い扱いしない
        make(self.dl, "chapter-2.pdf", "c2", 1)
        make(self.dl, "movie.mp4.crdownload", "x", 0)  # ダウンロード途中は触らない
        make(self.dl, "MyFolder/report (9).pdf", "mine", 5)  # 自分で作ったフォルダは触らない
        code, _ = self.run_main("--apply")
        self.assertEqual(code, 0)
        self.assertEqual((self.dl / "PDF" / "report.pdf").read_text(), "v3!!")
        self.assertEqual(tree(self.dl), sorted([
            "MyFolder/report (9).pdf",
            "PDF/chapter-1.pdf",
            "PDF/chapter-2.pdf",
            "PDF/report.pdf",
            "_削除候補/report (1).pdf",
            "_削除候補/report.pdf",
            "_削除候補/same.jpg",
            "movie.mp4.crdownload",
            "画像/photo.jpg",
        ]))
        # 2回目は何もしない
        code, out = self.run_main("--apply")
        self.assertIn("整理が必要なファイルはありませんでした", out)

    def test_redownload_after_organizing(self):
        make(self.dl, "PDF/bill.pdf", "old", 10)
        make(self.dl, "bill (1).pdf", "new", 0)
        self.run_main("--apply")
        self.assertEqual((self.dl / "PDF" / "bill.pdf").read_text(), "new")
        self.assertEqual((self.dl / "_削除候補" / "PDF" / "bill.pdf").read_text(), "old")

    def test_dash_suffix_needs_base(self):
        make(self.dl, "memo.txt", "a", 2)
        make(self.dl, "memo-1.txt", "bb", 1)
        self.run_main("--apply")
        self.assertEqual((self.dl / "文書" / "memo.txt").read_text(), "bb")
        self.assertTrue((self.dl / "_削除候補" / "memo.txt").exists())

    def test_exact_only_keeps_different_versions(self):
        make(self.dl, "a.pdf", "v1", 2)
        make(self.dl, "a (1).pdf", "v2", 1)
        make(self.dl, "b.pdf", "same", 2)
        make(self.dl, "b (1).pdf", "same", 1)
        self.run_main("--apply", "--exact-only")
        self.assertEqual(tree(self.dl), sorted([
            "PDF/a (1).pdf", "PDF/a.pdf", "PDF/b.pdf", "_削除候補/b.pdf",
        ]))

    def test_case_insensitive_names_do_not_clash(self):
        # Android の共有ストレージは大文字小文字を区別しないので「Report.pdf」と「report.pdf」は同じ名前扱い
        make(self.dl, "PDF/Report.pdf", "keep-me", 30)
        make(self.dl, "report.pdf", "new", 1)
        self.run_main("--apply", "--no-dedupe")
        self.assertEqual((self.dl / "PDF" / "Report.pdf").read_text(), "keep-me")
        self.assertEqual((self.dl / "PDF" / "report (2).pdf").read_text(), "new")

    def test_undo_restores_everything(self):
        make(self.dl, "a.pdf", "1", 2)
        make(self.dl, "a (1).pdf", "22", 1)
        make(self.dl, "x.zip", "z", 1)
        before = {p: (self.dl / p).stat().st_mtime for p in tree(self.dl)}
        self.run_main("--apply")
        log = sorted(self.logdir.glob("*.json"))[-1]
        self.run_main("--undo", str(log), "--apply")
        after = {p: (self.dl / p).stat().st_mtime for p in tree(self.dl)}
        self.assertEqual(before, after)
        self.assertFalse((self.dl / "PDF").exists())
        self.assertFalse((self.dl / "_削除候補").exists())

    def test_refuses_android_folder(self):
        android = self.tmp / "Android" / "data"
        android.mkdir(parents=True)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = organize.main(["--dir", str(android)])
        self.assertEqual(code, 1)
        self.assertIn("Android", err.getvalue())


@unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "疑似 adb には sh が必要")
class FakeAdbTests(OrganizeTestBase):
    def setUp(self):
        super().setUp()
        self.adb = self.tmp / "adb"
        self.adb.write_text(FAKE_ADB)
        self.adb.chmod(self.adb.stat().st_mode | stat.S_IEXEC)

    def run_adb(self, *args):
        return self.run_main("--adb", "--adb-path", str(self.adb), *args)

    def test_tricky_names_and_many_files(self):
        names = ["it's $HOME `x`.pdf", "スペース 入り（1）.png", "スペース 入り.png", 'q"uote.txt']
        names += [f"file{i:03d}.jpg" for i in range(150)]  # 1回のコマンドに収まらない件数
        for i, n in enumerate(names):
            make(self.dl, n, f"content-{i}", 1 + i / 1000)
        code, out = self.run_adb("--apply")
        self.assertEqual(code, 0, out)
        files = tree(self.dl)
        self.assertIn("PDF/it's $HOME `x`.pdf", files)
        self.assertIn("画像/スペース 入り.png", files)
        self.assertIn("_削除候補/スペース 入り.png", files)
        self.assertIn('文書/q"uote.txt', files)
        self.assertEqual(sum(f.startswith("画像/file") for f in files), 150)

        log = sorted(self.logdir.glob("*.json"))[-1]
        self.run_adb("--undo", str(log), "--apply")
        self.assertEqual(sorted(tree(self.dl)), sorted(names))


if __name__ == "__main__":
    unittest.main()
