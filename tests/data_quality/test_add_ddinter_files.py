"""Intake script for additional DDInter category files (synthetic data, invented drug names)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from add_ddinter_files import main  # noqa: E402

HEADER = "DDInterID_A,Drug_A,DDInterID_B,Drug_B,Level\n"


def write(path: Path, rows):
    path.write_text(HEADER + "".join(",".join(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture()
def dirs(tmp_path):
    data, dl = tmp_path / "data", tmp_path / "dl"
    data.mkdir(); dl.mkdir()
    write(data / "ddinter_downloads_code_A.csv", [("DDInter1", "fakedrugol", "DDInter2", "zorbamine", "Major")])
    return data, dl


def test_valid_file_is_copied_and_reports_conflicts(dirs, capsys):
    data, dl = dirs
    f = write(dl / "ddinter_downloads_code_B.csv", [("DDInter2", "zorbamine", "DDInter1", "fakedrugol", "Minor"),
                                                   ("DDInter3", "quenatrix", "DDInter1", "fakedrugol", "Moderate")])
    assert main([str(f), "--data-dir", str(data)]) == 0
    out = capsys.readouterr().out
    assert (data / f.name).exists() and "conflict" in out and "Pairs after : 2" in out


def test_dry_run_copies_nothing(dirs):
    data, dl = dirs
    f = write(dl / "ddinter_downloads_code_B.csv", [("DDInter3", "quenatrix", "DDInter1", "fakedrugol", "Moderate")])
    assert main([str(f), "--data-dir", str(data), "--dry-run"]) == 0
    assert not (data / f.name).exists()


@pytest.mark.parametrize("name,body,why", [
    ("ddinter_downloads_code_Z.csv", HEADER, "name"),
    ("whatever.csv", HEADER, "name"),
    ("ddinter_downloads_code_B.csv", "a,b,c\n1,2,3\n", "header"),
    ("ddinter_downloads_code_B.csv", HEADER, "no data"),
])
def test_bad_files_are_rejected_and_nothing_is_copied(dirs, name, body, why):
    data, dl = dirs
    f = dl / name
    f.write_text(body, encoding="utf-8")
    assert main([str(f), "--data-dir", str(data)]) == 1
    assert sorted(p.name for p in data.iterdir()) == ["ddinter_downloads_code_A.csv"]


def test_never_overwrites_an_existing_category(dirs):
    data, dl = dirs
    before = (data / "ddinter_downloads_code_A.csv").read_bytes()
    f = write(dl / "ddinter_downloads_code_A.csv", [("DDInter9", "x", "DDInter8", "y", "Minor")])
    assert main([str(f), "--data-dir", str(data)]) == 1
    assert (data / "ddinter_downloads_code_A.csv").read_bytes() == before
