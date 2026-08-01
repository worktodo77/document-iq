# Branding assets (LI Document IQ)

Real Long International brand art — committed with the code, shipped in the app
bundle. These are BRAND assets, not client data.

| file | used for | provenance |
|---|---|---|
| `li_logo.png` | the LI wordmark. **Not used by the shipped lockup** — D-09 ruled the header badge is composed from the monogram plus a *typeset* LONG INTERNATIONAL caption, so `make_logo.py` sets the caption rather than placing this raster. Kept because it is the authoritative wordmark art if a future surface needs it | copied unchanged from LI PDF Cleaner (`gg-cleaner-r3/poc/assets/branding/li_logo.png`), which is `logo_new.png` unchanged |
| `li_monogram_source.png` | source art for the app-icon generator — the LI monogram square with the globe disc | copied unchanged from `gg-cleaner-r3/poc/assets/logo_icon.png`, the same source the PDF Cleaner icon is generated from |
| `li_dociq_icon.ico` | the Windows Explorer / taskbar icon (16–256 px) | **generated** by `python -m dociq.branding.make_icon` |
| `li_dociq_icon.png` | 256 px render of the same artwork | **generated**, same command |
| `li_dociq_tile_512.png` | 512 px tile, for any surface that wants the badge alone | **generated**, same command |
| `li_dociq_lockup.png` | the D-09 window-header lockup, 4× the 44 px design size | **generated** by `python -m dociq.branding.make_logo` |

Every generated file is byte-identical on repeat runs (proved over 30 runs with
varying `PYTHONHASHSEED`) — the icon ships inside the exe, so a generator that
drifted would make the build unreproducible.

## App icon — generated, not hand-drawn (same recipe as LI PDF Cleaner)

The Windows explorer icon (`li_dociq_icon.ico`) follows the
PDF Cleaner icon system: the generator crops the globe disc and recolours the
monogram bars out of `li_monogram_source.png` — nothing redrawn — and writes a
multi-size .ico (16/24/32/48/64/128/256) with simplified artwork below 64 px
(plain silhouette, no lettering — small-size legibility rule per the Cleaner's
`make_brand_icon.py`). Adapt that script; verify all seven sizes are present in
the .ico; produce an `icon_preview.png` size ladder for judgment.

Icon concept selection (overlay design on the monogram tile) is Alex's ruling
D-08 in `docs/decisions/decision_register.md`.

## Brand colors (sampled from the art)

- Navy (structure, monogram tile): `#0E4D80` family
- Light blue (globe, accents, capacity gauge fill): `#2E9FD4` family
- Exact values must be sampled from `li_monogram_source.png` at build time, not
  hardcoded from this README. `dociq.branding.palette` does the sampling; as of
  the art committed here it reports navy `#044F89` and light blue `#2CA1DA`,
  and it hashes the file it sampled so a render can be traced to the exact art.
  Every other interface colour is derived from that pair by a fixed blend, so a
  brand refresh moves the whole design system at once.
