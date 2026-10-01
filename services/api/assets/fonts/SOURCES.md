# Bundled fonts (overlay, slice 11)

All six are Noto fonts from the official notofonts GitHub repositories, licensed under the
SIL Open Font License 1.1 (`OFL.txt`, `OFL-Devanagari.txt`, `OFL-CJK.txt`, `OFL-Arabic.txt`,
`OFL-Thai.txt`). None declares a Reserved Font Name. All are unmodified except the Korean file,
which is a subset (below).

| File | Scripts | Source | sha256 |
|---|---|---|---|
| `NotoSans-Bold.ttf` (617 KB) | Latin, Greek, Cyrillic | https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-Bold.ttf | `1df075a380fc7cb898acf64c1f7b3b4dd780de3caa860178bf929de35817a913` |
| `NotoSansDevanagari-Bold.ttf` (244 KB) | Devanagari (+ Latin digits/punctuation) | https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Bold.ttf | `6a09c8d797cfc803d32cdc731e809424d74cbaff59f503de34ade421a08e5bc2` |
| `NotoSansJP-Bold.otf` (4.4 MB) | Japanese: kana + JIS kanji (the region subset of Noto Sans CJK) | https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/JP/NotoSansJP-Bold.otf | `1b0edfb500b73a4fa8a4fcaae1bbbd403994e08e73e3e0da37e70d3853f42c5f` |
| `NotoSansKR-Bold-Hangul.otf` (2.0 MB, subset) | Korean: Hangul syllables and jamo, ASCII, Latin-1, punctuation, CJK symbols, fullwidth forms, ₩ | https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/KR/NotoSansKR-Bold.otf (4.8 MB, sha256 `5a6ceb287ed2fc6cfc6213144ebea68cbd94b20fc9eb873d8486493bf02d9bda`) | `f94f3cab18f03eac551030e018d111a278d0f0c953d8763c41727ca2eb4dfe69` |
| `NotoSansThai-Bold.ttf` (37 KB) | Thai | https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansThai/hinted/ttf/NotoSansThai-Bold.ttf | `2ac6c6e8a478e23b15f76e4894af1fa2210f8f350e4e6e54aad530bec03efbfb` |
| `NotoSansArabic-Bold.ttf` (261 KB) | Arabic (+ digits) | https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansArabic/hinted/ttf/NotoSansArabic-Bold.ttf | `4e5462d2e8be880317b9f49b5b2da109ddb6a3563d91cc604b67f3535832a555` |

Downloaded 2026-09-24 (Latin, Devanagari, JP) and 2026-09-26 (KR, Thai, Arabic). The JP file is
already the upstream per-region subset, so it is not subset further. The KR region subset
(4.8 MB, mostly Hanja) was subset with fontTools, keeping every layout feature:

    pyftsubset NotoSansKR-Bold.otf --layout-features='*' --output-file=NotoSansKR-Bold-Hangul.otf \
      --unicodes="U+0020-007E,U+00A0-00FF,U+2000-206F,U+20A9,U+20AC,U+2190-2199,U+3000-303F,\
      U+1100-11FF,U+3130-318F,U+A960-A97F,U+AC00-D7A3,U+D7B0-D7FF,U+FF01-FF60,U+FFE0-FFE6"

The KR file is covered by `OFL-CJK.txt` (same noto-cjk project). Hanja, Hebrew, Georgian and other
scripts are not bundled: required text that needs them is refused at input with 422
`unsupported-script`.
