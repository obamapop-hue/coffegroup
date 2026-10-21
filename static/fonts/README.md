# Noto Sans KR

Source: https://github.com/google/fonts/tree/main/ofl/notosanskr

Downloaded on 2026-10-21 from `NotoSansKR[wght].ttf` and `OFL.txt`.
Original TTF SHA-256:
`194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252`.

The font is distributed under SIL Open Font License 1.1. The original license
and copyright notice are included in [OFL.txt](OFL.txt).

The original variable font has a weight axis of 100–900. FontTools 4.61.1
(`fontTools.subset`, default layout closure, `notdef_outline=True`,
`recommended_glyphs=True`) and Brotli 1.2.0 were used to create two WOFF2 subsets.
The original 23,174 mapped characters are retained across the two files:

- `NotoSansKR-Korean.woff2`: Korean, Latin, numbers, punctuation and symbols;
  12,771 characters, 1,265,856 bytes. This file is preloaded.
- `NotoSansKR-Additional.woff2`: the remaining 10,403 original characters,
  including Hanja; 2,613,600 bytes. Loaded only when matching text is present.

The first subset contains mapped characters in U+0000–036F, U+1100–11FF,
U+2000–2BFF, U+3130–318F, U+A960–A97F, U+AC00–D7FF, U+FE00–FE0F and
U+FF00–FFEF. The second contains the complement of these ranges in the original
font. The matching, disjoint CSS ranges are defined in `../css/fonts.css`.

Fonts are served by the application with hashed static file URLs and browser
caching. No remote font service is requested by the site. FontTools and Brotli
are build tools and are not application dependencies.
