"""Acceptance probe for criterion 5, run against the REAL LI master index.

Run manually, outside the test suite, pointing at a client index that lives
**outside the repository**:

    python tools/audit_real_index.py "C:\\path\\to\\File Index.xlsx"

It prints summary numbers only — counts, widths, verdicts. No filename, path or
other client content is ever printed or written, so the output can be pasted
into a report. The index file itself must never be copied into the repo.

What it proves:

* every index row can be matched and issued an identifier;
* the issued identifiers are pairwise distinct (checked exhaustively via the
  minter *and* independently via set cardinality);
* container children and unmatched folder files, added synthetically on top of
  the real row set, still collide with nothing;
* **Tier-2 (unsupported) files are part of the same inventory** and are issued
  identifiers from the same minter — Codex review #1, finding B-7. They used to
  be assigned nothing at all, so criterion 5 was measured over a strictly
  smaller set than the one the pipeline now numbers;
* the assignment is byte-identical across repeated runs and independent of
  input order.
"""

from __future__ import annotations

import hashlib
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# The probes print typographic characters; a cp1252 console would otherwise
# crash on them and lose the whole report to an encoding error.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dociq.contracts import (
    DocumentRecord,
    PageKind,
    PageRecord,
    ProcessingStatus,
)
from dociq.docid.assign import assign_doc_ids, index_row_key
from dociq.docid.ids import DocId, assert_render_injective, parse_doc_id
from dociq.docid.masterindex import load_master_index
from dociq.docid.reconcile import reconcile
from dociq.ingest.walker import tier_of

CHILDREN_PER_CONTAINER = 3
SYNTHETIC_EXTRAS = 25
SYNTHETIC_TIER2 = 7
"""Unindexed Tier-2 files hung off the modelled folder — the shape of the real
run's seven legacy ``.doc`` files, which finding B-7 found absent from the
index deliverable entirely."""


def _page() -> PageRecord:
    return PageRecord(page_no=1, text="synthetic", kind=PageKind.NATIVE)


def build_folder_from_index(index) -> tuple[DocumentRecord, ...]:
    """Model a folder that contains exactly what the index describes.

    The folder side is derived from the index so the probe measures the *ID
    assignment*, not the operator's ability to point at the right directory.
    Root alignment is exercised separately by trimming the leading component.
    """
    docs: list[DocumentRecord] = []
    for row in index.rows:
        # Built from the RAW cells, not the normalized key: using the key would
        # lower-case every filename and manufacture 9,000 case-only
        # "discrepancies" that say nothing about the assigner.
        full = f"{row.filepath}/{row.filename}".replace("\\", "/")
        parts = [p for p in full.split("/") if p]
        rel = "/".join(parts[1:]) or "/".join(parts)  # drop the matter root
        digest = hashlib.sha256(f"{row.original_sort}".encode()).hexdigest()
        ext = "." + (row.ext or "dat").lstrip(".")
        # A row whose format DocIQ does not extract models a Tier-2 file: no
        # pages, status Unsupported. Before B-7 these were never handed to the
        # assigner at all, so this probe measured criterion 5 over a set the
        # pipeline does not actually issue.
        tier2 = tier_of(ext.lower()) == 2
        docs.append(
            DocumentRecord(
                doc_id="",
                rel_path=rel,
                filename=rel.rsplit("/", 1)[-1],
                sha256=digest,
                size_bytes=(row.size_kb or 1) * 1024,
                ext=ext,
                pages=() if tier2 else (_page(),),
                status=(ProcessingStatus.UNSUPPORTED if tier2
                        else ProcessingStatus.FULL),
                error="Unrecognized format" if tier2 else None,
            )
        )
    return tuple(docs)


def add_containers(docs: tuple[DocumentRecord, ...]) -> tuple[DocumentRecord, ...]:
    """Hang synthetic members off every archive-like parent."""
    extra: list[DocumentRecord] = []
    for doc in docs:
        if doc.ext not in (".zip", ".rar", ".msg", ".eml"):
            continue
        for i in range(CHILDREN_PER_CONTAINER):
            extra.append(
                DocumentRecord(
                    doc_id="",
                    rel_path=f"{doc.rel_path}/member{i}.pdf",
                    filename=f"member{i}.pdf",
                    sha256=hashlib.sha256(f"{doc.sha256}:{i}".encode()).hexdigest(),
                    size_bytes=1024,
                    ext=".pdf",
                    pages=(_page(),),
                    parent_doc_id=doc.rel_path,
                    container_order=i,
                )
            )
    return docs + tuple(extra)


def add_unindexed(docs: tuple[DocumentRecord, ...]) -> tuple[DocumentRecord, ...]:
    extra = tuple(
        DocumentRecord(
            doc_id="",
            rel_path=f"UNINDEXED/extra{i:04d}.pdf",
            filename=f"extra{i:04d}.pdf",
            sha256=hashlib.sha256(f"extra{i}".encode()).hexdigest(),
            size_bytes=1024,
            ext=".pdf",
            pages=(_page(),),
        )
        for i in range(SYNTHETIC_EXTRAS)
    )
    return docs + extra


def add_unindexed_tier2(docs: tuple[DocumentRecord, ...]) -> tuple[DocumentRecord, ...]:
    """Legacy ``.doc`` files that are in the folder and NOT in the index.

    The B-7 case in its purest form: inventoried, unextractable, and previously
    issued no identifier and given no index row.
    """
    extra = tuple(
        DocumentRecord(
            doc_id="",
            rel_path=f"UNINDEXED/legacy{i:03d}.doc",
            filename=f"legacy{i:03d}.doc",
            sha256=hashlib.sha256(f"legacy{i}".encode()).hexdigest(),
            size_bytes=2048,
            ext=".doc",
            status=ProcessingStatus.UNSUPPORTED,
            error="Legacy .doc is listed but not read (D-02)",
        )
        for i in range(SYNTHETIC_TIER2)
    )
    return docs + extra


def main(path: str) -> int:
    index = load_master_index(path)
    print(f"index rows loaded          : {len(index.rows)}")
    print(f"index snapshot sha256      : {index.snapshot.sha256[:16]}...")
    print(f"index warnings             : {len(index.warnings)}")
    print(f"resolved columns           : {[f for f, _ in index.resolved_columns]}")
    print(f"unmapped headers           : {len(index.unmapped_headers)}")
    print(f"max Original Sort          : {index.max_original_sort}")
    print(
        "index keys unique          : "
        f"{len({index_row_key(r) for r in index.rows})} / {len(index.rows)}"
    )

    docs = add_unindexed_tier2(
        add_unindexed(add_containers(build_folder_from_index(index))))
    tier2 = [d for d in docs if d.status is ProcessingStatus.UNSUPPORTED]
    print(f"folder documents modelled  : {len(docs)}")
    print(f"  of which Tier-2/unsupported: {len(tier2)}")

    result = assign_doc_ids(docs, index)
    ids = [d.doc_id for d in result.documents]
    unsupported_ids = [d.doc_id for d in result.documents
                       if d.status is ProcessingStatus.UNSUPPORTED]
    print(f"identifiers issued         : {len(ids)}")
    print(f"  issued to unsupported     : {len(unsupported_ids)}")
    print(f"  unsupported with NO id    : "
          f"{sum(1 for i in unsupported_ids if not i)}")
    print(f"identifiers distinct       : {len(set(ids))}")
    print(f"collisions                 : {len(ids) - len(set(ids))}")
    li = [i for i in ids if i.startswith("LI-")]
    diq = [i for i in ids if i.startswith("DIQ-")]
    print(f"LI- identifiers            : {len(li)}")
    print(f"DIQ- identifiers           : {len(diq)}")
    print(f"LI/DIQ string overlap      : {len(set(li) & set(diq))}")
    print(f"index rows matched         : {len(result.matched_rows)} / {len(index.rows)}")
    print(f"index rows unmatched       : {result.unmatched_row_count}")
    print(f"root prefix components     : {len((result.alignment.prefix or '').split('/'))}")
    print(f"root alignment matches     : {result.alignment.matched}")
    print(f"root alignment ambiguous   : {result.alignment.ambiguous}")
    print(f"assigner warnings          : {len(result.warnings)}")

    parsed = [parse_doc_id(i) for i in ids]
    print(f"parsed round-trip distinct : {len(set(parsed))}")

    # The structural half of the proof: rebuild the DocId values from the
    # rendered strings and assert the rendering is injective over the whole
    # issued set. Set cardinality above says "no duplicates happened"; this says
    # "no duplicate was reachable".
    rebuilt = [
        DocId(ns, base, len(i.split("-")[1].split(".")[0]), path, (2,) * len(path))
        for i, (ns, base, path) in zip(ids, parsed)
    ]
    assert_render_injective(rebuilt)
    print("render injectivity         : PASS")

    report = reconcile(result, index)
    print(f"reconciliation totals      : {report.totals}")

    # Determinism: repeated runs and shuffled input must agree exactly.
    baseline = {d.rel_path: d.doc_id for d in result.documents}
    rng = random.Random(20260730)
    stable = True
    for _ in range(8):
        shuffled = list(docs)
        rng.shuffle(shuffled)
        got = {d.rel_path: d.doc_id for d in assign_doc_ids(tuple(shuffled), index).documents}
        stable = stable and got == baseline
    print(f"stable over 8 shuffled runs: {stable}")

    verdict = (
        len(ids) == len(set(ids))
        and not (set(li) & set(diq))
        and stable
        and len(result.matched_rows) == len(index.rows)
        # B-7: every inventoried entry, unsupported included, carries an id.
        and all(unsupported_ids)
        and len(unsupported_ids) == len(tier2)
    )
    print(f"\nVERDICT: {'PASS' if verdict else 'FAIL'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
