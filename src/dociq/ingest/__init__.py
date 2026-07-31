"""Track A — the ingestion spine.

Stages 1 and 2 of the §4 pipeline: walk a matter folder, hash and tier every
file, and turn each Tier-1 document into the frozen contract's
:class:`~dociq.contracts.DocumentRecord` of per-page
:class:`~dociq.contracts.PageRecord` objects.
"""
