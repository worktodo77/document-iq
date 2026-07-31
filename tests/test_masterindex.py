"""Master-index loading (§5, D-04).

The header shapes exercised here are the ones the real Project 495 file has —
trailing spaces, an embedded newline — reproduced as *synthetic* data. The real
index is client material and never enters the repo.
"""

from __future__ import annotations

import pytest

from dociq.docid.masterindex import (
    COLUMN_ALIASES,
    MasterIndexError,
    load_master_index,
    normalize_header,
)


def _write_csv(tmp_path, rows, headers=None):
    headers = headers or [
        "Original Sort ",
        "Filename",
        "File Extension",
        "Filepath",
        "Size\n(KB)",
        "Date",
        "Source Received ",
        "Date Received",
    ]
    lines = [",".join(f'"{h}"' for h in headers)]
    for row in rows:
        lines.append(",".join(f'"{c}"' for c in row))
    path = tmp_path / "index.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


ROWS = [
    ["1", "1 - Main Contract.zip", "zip", r"P 495\20260521", "7118965", "5/22/2026", "Client Link", "2026-05-22"],
    ["2", "Letter 001.pdf", "pdf", r"P 495\20260521\LETTERS", "1879", "5/22/2026", "Client Link", "2026-05-22"],
    ["3", "Letter 002.pdf", "pdf", r"P 495\20260521\LETTERS", "1185", "6/7/2026", "Client Link", "2026-06-08"],
]


def test_headers_with_trailing_space_and_newline_resolve(tmp_path):
    index = load_master_index(_write_csv(tmp_path, ROWS))
    resolved = dict(index.resolved_columns)
    assert resolved["original_sort"] == "Original Sort "
    assert resolved["size_kb"] == "Size\n(KB)"
    assert index.snapshot.row_count == 3
    assert index.max_original_sort == 3
    assert not index.has_bates
    assert not index.has_hashes


def test_normalize_header_collapses_every_kind_of_whitespace():
    assert normalize_header("Original Sort ") == "original sort"
    assert normalize_header("Size\n(KB)") == "size (kb)"
    assert normalize_header("  FILE  path ") == "file path"
    assert normalize_header(None) == ""


def test_specific_alias_beats_generic_when_both_present(tmp_path):
    path = _write_csv(
        tmp_path,
        [["1", "a.pdf", "pdf", "dir", "10", "1/1/2026", "x", "2026-01-01", "99"]],
        headers=[
            "Original Sort", "Filename", "File Extension", "Filepath",
            "Size (KB)", "Date", "Source Received", "Date Received", "Size",
        ],
    )
    index = load_master_index(path)
    assert dict(index.resolved_columns)["size_kb"] == "Size (KB)"
    assert index.rows[0].size_kb == 10


def test_missing_required_column_is_actionable(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Name,Where\nfoo.pdf,dir\n", encoding="utf-8", newline="\n")
    with pytest.raises(MasterIndexError) as exc:
        load_master_index(path)
    assert "Original Sort" in str(exc.value)


def test_unusable_rows_are_skipped_with_a_warning_not_silently(tmp_path):
    rows = ROWS + [["not-a-number", "x.pdf", "pdf", "dir", "1", "", "", ""]]
    index = load_master_index(_write_csv(tmp_path, rows))
    assert len(index.rows) == 3
    assert any("skipped" in w for w in index.warnings)


def test_duplicate_original_sort_is_reported(tmp_path):
    rows = ROWS + [["2", "dupe.pdf", "pdf", "dir", "1", "", "", ""]]
    index = load_master_index(_write_csv(tmp_path, rows))
    assert len(index.rows) == 3
    assert any("duplicate Original Sort" in w for w in index.warnings)


def test_snapshot_hash_is_the_file_hash(tmp_path):
    import hashlib

    path = _write_csv(tmp_path, ROWS)
    index = load_master_index(path)
    assert index.snapshot.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_combined_bates_column_splits(tmp_path):
    path = _write_csv(
        tmp_path,
        [["1", "a.pdf", "pdf", "dir", "10", "", "", "", "MNFV 0391 - MNFV 0696"]],
        headers=[
            "Original Sort", "Filename", "File Extension", "Filepath",
            "Size (KB)", "Date", "Source Received", "Date Received", "Bates Range",
        ],
    )
    index = load_master_index(path)
    assert index.has_bates
    assert index.rows[0].bates_start == "MNFV 0391"
    assert index.rows[0].bates_end == "MNFV 0696"


def test_unsupported_extension_is_refused_with_guidance(tmp_path):
    path = tmp_path / "index.docx"
    path.write_bytes(b"not really")
    with pytest.raises(MasterIndexError) as exc:
        load_master_index(path)
    assert ".xlsx" in str(exc.value)


def test_every_alias_set_is_disjoint():
    """A shared alias would make column resolution order-dependent."""
    seen: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            assert alias not in seen, f"{alias!r} claimed by {seen.get(alias)} and {field}"
            seen[alias] = field
