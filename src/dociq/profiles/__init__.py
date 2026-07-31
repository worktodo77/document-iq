"""§6 format profiles — the mechanism by which the expert, not the tool,
controls what is omitted (Principle 3).

Split so the authority boundary is visible in the file layout:
:mod:`~dociq.profiles.detect` may only *propose* sections, :mod:`~dociq.profiles.model`
records what an expert ruled and who they were, and :mod:`~dociq.profiles.apply`
does nothing that is not written in a ruled profile.
"""
