"""Brand art generators for LI Document IQ.

Every shipped brand pixel is DERIVED from ``assets/branding/li_monogram_source.png``
by a script in this package. A brand refresh is a re-run, never a hand edit, and
the provenance of any shipped asset is the generator that wrote it.
"""

from dociq.branding.palette import Palette, sample_palette

__all__ = ["Palette", "sample_palette"]
