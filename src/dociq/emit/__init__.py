"""Stage 5 — the deliverables (§7).

Everything written to disk is written from here, through
:func:`dociq.emit.paths.write_text_deterministic`, so the byte-identical claim
has exactly one place it can be broken.
"""
