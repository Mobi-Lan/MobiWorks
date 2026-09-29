# vendor — 바깥에서 받아 온 파일

| 파일 | 어디서 | 판 | SHA-256 |
|------|--------|----|---------|
| `WebView2Loader.dll` (x64) | NuGet `Microsoft.Web.WebView2` → `runtimes/win-x64/native/WebView2Loader.dll` | 1.0.4191.47 | `c66e4a92fdc7a216118e43b7a5024ea2200e8c43f9310bf20d96a0084f82c5bc` |
| `WebView2.h` | 같은 꾸러미 → `build/native/include/WebView2.h` | 1.0.4191.47 | `dff1e3181ec7ec203a34ef6efa966590e0ef0ba1a5c3fe3b69da6508c2f8a02e` |

- 받은 곳: `https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2` (2026-09-24, 판을 적지 않은 주소 = 최신 안정판).
  `.nupkg` 는 zip 이다. 다른 제품 폴더에서 DLL 을 베껴 오지 않았다.
- 쓰는 곳: `folio/opening_wv.py` (합주 시작 연출을 투명 WebView2 로 그린다).
  `WebView2.h` 는 **빌드·실행에 쓰지 않는다.** 파이썬(ctypes)이 부르는 vtable 순번·IID 의 근거가 되는
  참고 자료라 공개 저장소에는 넣지 않는다 — 같은 꾸러미에서 받을 수 있다 (위 표의 판·해시).
- 배포 zip 에는 `tools/build_embed.py` 의 `APP_FILES` 로 실린다 (`app\vendor`).
- WebView2 **런타임**(Edge)은 싣지 않는다 — 윈도우 11 에 깔려 있는 「상록」 런타임을 쓴다.

## 라이선스

꾸러미의 `LICENSE.txt` 는 BSD 형식이다. 로더를 앱과 함께 다시 배포해도 된다는 근거 (원문 그대로):

> Redistribution and use in source and binary forms, with or without
> modification, are permitted provided that the following conditions are
> met:
>
>    * Redistributions of source code must retain the above copyright
> notice, this list of conditions and the following disclaimer.
>    * Redistributions in binary form must reproduce the above
> copyright notice, this list of conditions and the following disclaimer
> in the documentation and/or other materials provided with the
> distribution.
>    * The name of Microsoft Corporation, or the names of its contributors
> may not be used to endorse or promote products derived from this
> software without specific prior written permission.

바이너리로 다시 배포할 때는 **저작권 표시와 위 조건을 문서에 같이 넣어야 한다** — 아래가 그 표시다.

```
Copyright (C) Microsoft Corporation. All rights reserved.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

`LICENSE.txt` 원본은 `vendor/LICENSE-WebView2.txt` 로 같이 둔다.
