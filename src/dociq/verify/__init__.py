"""Stage 6 — the post-run self-check (§4, §7).

Three things live here: the corpus-wide page-accounting reconciliation, the
output hash manifest that makes the byte-identical claim checkable rather than
asserted, and the token estimate.

The accounting gate and the manifest were Track A's half of this stage; the
token estimate was Track B's. They are one stage because they answer one
question — whether what was written can be defended.
"""
