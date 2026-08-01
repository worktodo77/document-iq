"""Stage 3b — Doc ID assignment, master-index loading, and reconciliation (D-04).

Split three ways on purpose: :mod:`dociq.docid.ids` owns the *shape* of an
identifier and is the only place one is rendered, :mod:`dociq.docid.masterindex`
owns reading LI's index, and :mod:`dociq.docid.assign` owns the matching policy.
Collision-freedom is a property of the first module alone, so it can be proven
without loading a spreadsheet.
"""
