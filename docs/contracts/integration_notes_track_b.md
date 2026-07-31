# Track B integration notes (Sprint 1)

Written 2026-07-30 at the end of Track B's build. **None of these is an
amendment request** — the frozen contract expressed everything Track B needed,
so `docs/contracts/amendments.md` was not opened. These are the places where
Track B had to make a judgement that another track can see, or where a finding
belongs to somebody else's decision.

---

## 1. `parent_doc_id` before Stage 3b has run — Track A please read

`DocumentRecord.parent_doc_id` is typed as a Doc ID, and `doc_id` is documented
as the empty string until Stage 3b. A container member produced by Stage 1
therefore has to name its parent with something that is *not yet* a Doc ID.

Track B did not guess. `dociq.docid.assign` resolves `parent_doc_id` against
**both** plausible conventions:

* the parent's `rel_path` (what Stage 1 naturally has), and
* the parent's already-populated `doc_id`, if Track A chooses to pre-populate one.

Whichever resolves, resolves; an unresolvable parent produces a warning and the
member is identified as a top-level file rather than dropped. Nested containers
and (defensively) membership cycles are both handled.

**Ask of Track A:** state which convention Stage 1 uses. Once it is settled the
other branch can be deleted, and the contract docstring for `parent_doc_id`
should say so explicitly.

> **ANSWERED at integration, 2026-07-31.** Stage 1 uses the parent's
> `rel_path`. See `pagemodel_freeze.md` §`parent_doc_id`. Two corrections to the
> expectation above:
>
> 1. The assigner did **not** remap the field — it minted child Doc IDs and left
>    `DocumentRecord.parent_doc_id` holding the rel_path, so the index deliverable
>    shipped a filesystem path in its "Parent doc" column while every other
>    identifier column shipped a Doc ID. Fixed in `docid/assign._assign_children`,
>    with tests proven red beforehand.
> 2. The `doc_id` branch is **not** deleted. Now that Stage 3b rewrites the
>    field, a corpus re-entering Stage 3b arrives naming its parent by Doc ID;
>    deleting the branch would orphan every container member on a second pass.
>    Both branches are live and each has a test.
>
> The contract docstring is deliberately **not** amended: the field is a string
> and the question is a handover rule, not a type. The rule lives in the freeze
> document instead.

## 2. Bates zone is text position, not page geometry — disclosed limitation

§4 Stage 3 says "page corners/footers". The frozen contract carries page *text*,
not glyph coordinates, so `dociq.identify.bates` approximates the zone by
position in the text stream: the first three and last four lines of a page,
both bounds configurable and reported.

This finds header and footer stamps, which is where productions put them, and
it correctly finds nothing on an unstamped corpus. It would miss a stamp that an
extractor emits mid-stream. Adding geometry would be a contract change and was
not judged worth one for Sprint 1 — but the limitation is real and is recorded
here rather than only in a docstring.

## 3. `output_root` is outside the hashed log content — determinism scoping

Found by the Track B determinism proof: including `RunConfig.output_root` in
`processing_log.json`'s hashed `content` section made the same corpus, reduced
twice into two different destination folders, fail its own byte-identical check.

The determinism contract is "same folder + same profile + same master index",
and the destination is not one of those inputs. `output_root` therefore lives in
the log's `run` section alongside the timestamp and operator. `source_root`
stays in `content` — it *is* an input.

## 4. D-03's token ratio band is refuted by the real corpus — a decision for Alex

Measured on 40 randomly sampled PDFs of the real MPR corpus (1,201 pages,
2,221,486 characters): **3.03 characters per pre-token**. Since a BPE tokenizer
cannot emit fewer tokens than the text has pre-tokens, this corpus cannot exceed
3.03 chars/token — so D-03's expected **3.30–3.60 band is unreachable** for this
material, not merely optimistic. Using it alone would understate the token load
by roughly 15–45%.

The shipped band was **not** silently changed: D-03 is a ruling. Instead
`estimate_tokens` detects that the band is refuted by the text's own structure,
rebuilds the range from that structure, and sets `TokenEstimate.ratio_refuted`.

**Recommendation:** re-rule D-03 to roughly 2.3–3.0 chars/token for table-heavy
MPR text, once someone with network access can count real Claude tokens on a
sample. See the module docstring of `dociq.verify.tokens` for the full basis,
including the prose sanity check that reproduces the well-known ≈4 chars/token
figure for ordinary English.

## 5. D-03's "calibrated against the real Claude tokenizer" could not be honoured

Stated plainly because an evidentiary tool must not carry a false provenance
claim: **no calibration against Claude's tokenizer was performed.** DocIQ is
offline by Principle 4, no tokenizer library is installed in the build
environment (`tiktoken`, `transformers`, `tokenizers`, `sentencepiece` all
absent), and no offline Claude tokenizer artifact exists. The estimator's
provenance string says so, the run summary prints it, and the processing log
records it.
