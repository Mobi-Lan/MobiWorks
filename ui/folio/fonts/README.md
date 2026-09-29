# 연출 화면 글꼴 — Noto Sans KR · Space Mono

연출 디자인은 Google Fonts 링크로 `Noto Sans KR`(300·400·700·900)과 `Space Mono`(400·700)를 썼다. 연출 화면 `ui/folio/opening2.html` 은 서버의 CSP(`font-src 'self'`)를 지나므로 밖의 글꼴을 못 읽어 맑은 고딕·Consolas 로 그려졌다. 그래서 **같은 파일을 받아 여기에 싣는다.**

## 무엇이 있나

| 항목 | 값 |
|------|-----|
| `fonts.css` | Google Fonts CSS2 API 가 준 `@font-face` 규칙 502개 그대로 — `url(...)` 만 이 폴더의 파일로, `unicode-range` 는 그대로 |
| Noto Sans KR | woff2 124개, 3,519,780 바이트 — 한글은 unicode-range 조각 120개(`notosanskr-000`…`-119`) + 라틴·키릴·베트남어 4개. **가변 글꼴이라 한 파일이 300·400·700·900 을 다 낸다** (규칙은 굵기마다, 파일은 하나) |
| Space Mono | woff2 6개, 79,488 바이트 — 400·700 × latin·latin-ext·vietnamese |
| 합계 | **130개, 3,599,268 바이트 (약 3.4 MB)** |
| 라이선스 | 둘 다 **SIL Open Font License 1.1** (아래 전문). 앱에 싣고 함께 배포할 수 있다. 글꼴만 따로 팔 수는 없다 |

브라우저(WebView2)는 화면에 실제로 나온 글자가 속한 조각만 읽는다 — 파일이 많아도 한 번 연출에 읽는 양은 적다.

## 어떻게 받았나 (다시 받을 때)

1. CSS: `https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;700;900&family=Space+Mono:wght@400;700&display=swap` — 요즘 브라우저 User-Agent(Chrome 128)를 붙여야 woff2 주소를 준다.
2. 그 CSS 가 가리키는 `https://fonts.gstatic.com/...woff2` 를 **전부** 받는다 (한글 조각을 하나라도 빼면 그 글자가 대체 글꼴로 샌다).
3. 이름: 번호가 붙은 조각은 `notosanskr-NNN.woff2`(NNN = 주소 끝의 조각 번호), 나머지는 CSS 주석의 subset 이름.
4. `fonts.css` 는 받은 CSS 의 규칙을 그대로 두고 url 만 바꿔 만든다.
5. 받은 날: 2026-09-24 (Noto Sans KR v39 · Space Mono v17).

검사: `python -m unittest tests.test_opening_fonts` — 파일이 다 있고 진짜 WOFF2 인지, 굵기·한글 범위, 서버가 `font/woff2` 로 내는지, 페이지가 이 CSS 를 거는지. 빌드는 `--add-data "ui;ui"` 로 이 폴더째 싣는다. `tools/textcheck.py` 는 `.woff2` 를 이진 파일로 건너뛴다.

## 파일별 출처

| 파일 | 원본 주소 |
|------|-----------|
| `notosanskr-000.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.0.woff2 |
| `notosanskr-001.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.1.woff2 |
| `notosanskr-002.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.2.woff2 |
| `notosanskr-003.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.3.woff2 |
| `notosanskr-004.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.4.woff2 |
| `notosanskr-005.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.5.woff2 |
| `notosanskr-006.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.6.woff2 |
| `notosanskr-007.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.7.woff2 |
| `notosanskr-008.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.8.woff2 |
| `notosanskr-009.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.9.woff2 |
| `notosanskr-010.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.10.woff2 |
| `notosanskr-011.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.11.woff2 |
| `notosanskr-012.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.12.woff2 |
| `notosanskr-013.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.13.woff2 |
| `notosanskr-014.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.14.woff2 |
| `notosanskr-015.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.15.woff2 |
| `notosanskr-016.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.16.woff2 |
| `notosanskr-017.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.17.woff2 |
| `notosanskr-018.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.18.woff2 |
| `notosanskr-019.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.19.woff2 |
| `notosanskr-020.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.20.woff2 |
| `notosanskr-021.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.21.woff2 |
| `notosanskr-022.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.22.woff2 |
| `notosanskr-023.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.23.woff2 |
| `notosanskr-024.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.24.woff2 |
| `notosanskr-025.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.25.woff2 |
| `notosanskr-026.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.26.woff2 |
| `notosanskr-027.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.27.woff2 |
| `notosanskr-028.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.28.woff2 |
| `notosanskr-029.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.29.woff2 |
| `notosanskr-030.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.30.woff2 |
| `notosanskr-031.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.31.woff2 |
| `notosanskr-032.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.32.woff2 |
| `notosanskr-033.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.33.woff2 |
| `notosanskr-034.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.34.woff2 |
| `notosanskr-035.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.35.woff2 |
| `notosanskr-036.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.36.woff2 |
| `notosanskr-037.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.37.woff2 |
| `notosanskr-038.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.38.woff2 |
| `notosanskr-039.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.39.woff2 |
| `notosanskr-040.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.40.woff2 |
| `notosanskr-041.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.41.woff2 |
| `notosanskr-042.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.42.woff2 |
| `notosanskr-043.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.43.woff2 |
| `notosanskr-044.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.44.woff2 |
| `notosanskr-045.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.45.woff2 |
| `notosanskr-046.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.46.woff2 |
| `notosanskr-047.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.47.woff2 |
| `notosanskr-048.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.48.woff2 |
| `notosanskr-049.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.49.woff2 |
| `notosanskr-050.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.50.woff2 |
| `notosanskr-051.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.51.woff2 |
| `notosanskr-052.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.52.woff2 |
| `notosanskr-053.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.53.woff2 |
| `notosanskr-054.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.54.woff2 |
| `notosanskr-055.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.55.woff2 |
| `notosanskr-056.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.56.woff2 |
| `notosanskr-057.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.57.woff2 |
| `notosanskr-058.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.58.woff2 |
| `notosanskr-059.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.59.woff2 |
| `notosanskr-060.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.60.woff2 |
| `notosanskr-061.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.61.woff2 |
| `notosanskr-062.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.62.woff2 |
| `notosanskr-063.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.63.woff2 |
| `notosanskr-064.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.64.woff2 |
| `notosanskr-065.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.65.woff2 |
| `notosanskr-066.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.66.woff2 |
| `notosanskr-067.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.67.woff2 |
| `notosanskr-068.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.68.woff2 |
| `notosanskr-069.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.69.woff2 |
| `notosanskr-070.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.70.woff2 |
| `notosanskr-071.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.71.woff2 |
| `notosanskr-072.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.72.woff2 |
| `notosanskr-073.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.73.woff2 |
| `notosanskr-074.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.74.woff2 |
| `notosanskr-075.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.75.woff2 |
| `notosanskr-076.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.76.woff2 |
| `notosanskr-077.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.77.woff2 |
| `notosanskr-078.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.78.woff2 |
| `notosanskr-079.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.79.woff2 |
| `notosanskr-080.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.80.woff2 |
| `notosanskr-081.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.81.woff2 |
| `notosanskr-082.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.82.woff2 |
| `notosanskr-083.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.83.woff2 |
| `notosanskr-084.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.84.woff2 |
| `notosanskr-085.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.85.woff2 |
| `notosanskr-086.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.86.woff2 |
| `notosanskr-087.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.87.woff2 |
| `notosanskr-088.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.88.woff2 |
| `notosanskr-089.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.89.woff2 |
| `notosanskr-090.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.90.woff2 |
| `notosanskr-091.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.91.woff2 |
| `notosanskr-092.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.92.woff2 |
| `notosanskr-093.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.93.woff2 |
| `notosanskr-094.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.94.woff2 |
| `notosanskr-095.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.95.woff2 |
| `notosanskr-096.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.96.woff2 |
| `notosanskr-097.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.97.woff2 |
| `notosanskr-098.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.98.woff2 |
| `notosanskr-099.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.99.woff2 |
| `notosanskr-100.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.100.woff2 |
| `notosanskr-101.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.101.woff2 |
| `notosanskr-102.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.102.woff2 |
| `notosanskr-103.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.103.woff2 |
| `notosanskr-104.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.104.woff2 |
| `notosanskr-105.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.105.woff2 |
| `notosanskr-106.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.106.woff2 |
| `notosanskr-107.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.107.woff2 |
| `notosanskr-108.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.108.woff2 |
| `notosanskr-109.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.109.woff2 |
| `notosanskr-110.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.110.woff2 |
| `notosanskr-111.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.111.woff2 |
| `notosanskr-112.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.112.woff2 |
| `notosanskr-113.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.113.woff2 |
| `notosanskr-114.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.114.woff2 |
| `notosanskr-115.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.115.woff2 |
| `notosanskr-116.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.116.woff2 |
| `notosanskr-117.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.117.woff2 |
| `notosanskr-118.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.118.woff2 |
| `notosanskr-119.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5Cgm20xz64px_1hVWr0wuPNGmlQNMEfD4.119.woff2 |
| `notosanskr-cyrillic.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5CgmG1X7t0JM.woff2 |
| `notosanskr-vietnamese.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5CgmG3n7t0JM.woff2 |
| `notosanskr-latin-ext.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5CgmG337t0JM.woff2 |
| `notosanskr-latin.woff2` | https://fonts.gstatic.com/s/notosanskr/v39/PbykFmXiEBPT4ITbgNA5CgmG0X7t.woff2 |
| `spacemono-400-vietnamese.woff2` | https://fonts.gstatic.com/s/spacemono/v17/i7dPIFZifjKcF5UAWdDRYE58RWq7.woff2 |
| `spacemono-400-latin-ext.woff2` | https://fonts.gstatic.com/s/spacemono/v17/i7dPIFZifjKcF5UAWdDRYE98RWq7.woff2 |
| `spacemono-400-latin.woff2` | https://fonts.gstatic.com/s/spacemono/v17/i7dPIFZifjKcF5UAWdDRYEF8RQ.woff2 |
| `spacemono-700-vietnamese.woff2` | https://fonts.gstatic.com/s/spacemono/v17/i7dMIFZifjKcF5UAWdDRaPpZUFqaHjyV.woff2 |
| `spacemono-700-latin-ext.woff2` | https://fonts.gstatic.com/s/spacemono/v17/i7dMIFZifjKcF5UAWdDRaPpZUFuaHjyV.woff2 |
| `spacemono-700-latin.woff2` | https://fonts.gstatic.com/s/spacemono/v17/i7dMIFZifjKcF5UAWdDRaPpZUFWaHg.woff2 |

## 라이선스 전문

### Noto Sans KR — https://github.com/google/fonts/blob/main/ofl/notosanskr/OFL.txt

```text
Copyright 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
https://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

### Space Mono — https://github.com/google/fonts/blob/main/ofl/spacemono/OFL.txt

```text
Copyright 2016 The Space Mono Project Authors (https://github.com/googlefonts/spacemono)

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
http://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```
