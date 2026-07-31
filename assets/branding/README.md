# Branding assets (LI Document IQ)

Real Long International brand art — committed with the code, shipped in the app
bundle. These are BRAND assets, not client data.

| file | used for | provenance |
|---|---|---|
| `li_logo.png` | the LONG INTERNATIONAL wordmark drawn in the app window header | copied unchanged from LI PDF Cleaner (`gg-cleaner-r3/poc/assets/branding/li_logo.png`), which is `logo_new.png` unchanged |
| `li_monogram_source.png` | source art for the app-icon generator — the LI monogram square with the globe disc | copied unchanged from `gg-cleaner-r3/poc/assets/logo_icon.png`, the same source the PDF Cleaner icon is generated from |

## App icon — generated, not hand-drawn (same recipe as LI PDF Cleaner)

The Windows explorer icon (`li_dociq_icon.ico`, not yet generated) follows the
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
  hardcoded from this README.
