"""§6 format profiles — what remains of them after D-35.

**This package no longer decides which pages are dropped.** ``profiles/apply.py``
matched a rule's regex against every line of every page and carried the matched
section forward until the next match, so a rule written for one section governed
every page after it; D-35 ruled it replaced rather than repaired and it is
deleted. Section recognition is :mod:`dociq.sections` — Tier-1 outline spans and
Tier-3 page classes — and the only thing that can turn a KEEP into a DROP is a
:class:`dociq.sections.model.ApprovedOmission` naming a person (D-34).

What is left here is real and is not the disposition engine:

* :mod:`~dociq.profiles.model` — the profile schema, its content hash, and
  :func:`~dociq.profiles.model.operator_stamp`, which is how the product answers
  "who is running this". The stamp is what an expert's approval is signed with,
  so it outlived the engine it was written beside.
* :mod:`~dociq.profiles.detect` — may only *propose* sections for a human
  checklist. It never could drop anything, and that boundary is why it survives
  unchanged.

A profile supplied to a run is still loaded, hashed into the run identity,
copied into the matter folder and recorded in the log. If it carries DROP rules,
the run REPORTS that they dropped nothing
(:func:`dociq.pipeline._inert_profile_warnings`) rather than leaving an operator
to wonder why their profile did not bite.
"""
