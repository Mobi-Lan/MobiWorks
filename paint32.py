"""오버레이를 픽셀별 투명도(per-pixel alpha)로 그리는 얇은 층 — 표준 라이브러리만 (ctypes + GDI+).

tkinter 캔버스는 안티에일리어싱이 없고, 색 빼기(-transparentcolor) 투명은 픽셀이
「완전 투명 아니면 완전 불투명」뿐이라 둥근 모서리가 계단처럼 보인다.
그래서 GDI+ 로 32비트 ARGB 그림을 만들어 UpdateLayeredWindow 로 창에 그대로 얹는다.
모서리·원·글자 전부 부드럽게 나오고, 반투명 가장자리가 게임 화면과 자연스럽게 섞인다.

덤으로 얻는 것: 알파가 0인 곳은 마우스가 그냥 통과한다 (모서리 바깥을 눌러도 창이 안 잡힌다).
주의: UpdateLayeredWindow 와 SetLayeredWindowAttributes 는 같이 못 쓴다 —
      창 전체 불투명도는 blit(alpha=…) 로 준다 (-alpha, -transparentcolor 를 걸면 안 된다).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

_gdip = ctypes.WinDLL("gdiplus")
_gdi = ctypes.WinDLL("gdi32")
_user = ctypes.WinDLL("user32")

_PARGB = 0x000E200B          # PixelFormat32bppPARGB — 알파가 곱해진 형식 (UpdateLayeredWindow 가 원하는 것)
_RGB32 = 0x22009            # PixelFormat32bppRGB — 알파를 안 쓰는 픽셀 (영상 프레임)
_UNIT_PIXEL = 2
_SMOOTH_AA = 4               # SmoothingModeAntiAlias
_TEXT_AA = 4                 # TextRenderingHintAntiAlias (투명 배경에서는 ClearType 이 지저분하다)
_FMT_NOWRAP = 0x1000 | 0x4000
_ULW_ALPHA = 2
_AC_SRC_OVER, _AC_SRC_ALPHA = 0, 1
_token = None

# **핸들을 돌려주는 함수는 restype 을 반드시 준다.** 안 주면 ctypes 가 `c_int`(32비트)로 받아
# 64비트 핸들이 잘리고, 그 값을 되돌려 주면 조용히 실패한다 — 예외도 오류도 없이 창이
# 픽셀을 하나도 안 그린다 (`_dc` 가 음수로 나온다).
for _f, _rt, _at in (
    (_user.GetDC, ctypes.c_void_p, [ctypes.c_void_p]),
    (_user.ReleaseDC, ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]),
    (_gdi.CreateDIBSection, ctypes.c_void_p,
     [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]),
    (_gdi.CreateCompatibleDC, ctypes.c_void_p, [ctypes.c_void_p]),
    (_gdi.SelectObject, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
    (_gdi.DeleteDC, ctypes.c_int, [ctypes.c_void_p]),
    (_gdi.DeleteObject, ctypes.c_int, [ctypes.c_void_p]),
    # BitBlt 의 6번째 인자(hdcSrc)도 64비트 핸들이다 — argtypes 없이 부르면 c_int 로 바꾸다
    # 「argument 6: OverflowError: int too long to convert」 로 **매 프레임** 죽는다. 상세창을 펴는 순간
    # 캐시를 BitBlt 로 옮기는 길이라 상세창이 사라지는 것으로 나타난다.
    # 핸들 값이 우연히 2^31 아래인 환경에서는 드러나지 않는다.
    (_gdi.BitBlt, ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]),
):
    _f.restype, _f.argtypes = _rt, _at
_user.UpdateLayeredWindow.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
_user.UpdateLayeredWindow.restype = ctypes.c_int


class _StartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wt.BOOL), ("SuppressExternalCodecs", wt.BOOL)]


class _RectF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("w", ctypes.c_float), ("h", ctypes.c_float)]


class _BlendFunction(ctypes.Structure):
    _fields_ = [("Op", ctypes.c_byte), ("Flags", ctypes.c_byte), ("Alpha", ctypes.c_byte), ("Format", ctypes.c_byte)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def _start() -> None:
    global _token
    if _token is not None:
        return
    tok = ctypes.c_void_p()
    si = _StartupInput(1, None, False, False)
    _gdip.GdiplusStartup(ctypes.byref(tok), ctypes.byref(si), None)
    _token = tok


def argb(color: str, alpha: int = 255) -> int:
    """'#rrggbb' → GDI+ 색값. alpha 는 0~255."""
    c = color.lstrip("#")
    return (int(alpha) << 24) | int(c, 16)


class Surface:
    """그림 한 장. 창 하나에 대응한다. 크기가 바뀌면 resize() 로 다시 잡는다."""

    def __init__(self, w: int, h: int):
        _start()
        self.w = self.h = 0
        self._dib = self._dc = None
        self._bmp = ctypes.c_void_p()
        self._g = ctypes.c_void_p()
        self._fonts: dict = {}
        self._pens: dict = {}         # 선 펜 모음 (폴리오에서 가져온 arc 가 쓴다)
        self._ink: dict = {}          # 글리프 잉크 경계상자 (ink_box 주석 참조)
        self._scratch = None          # 그 측정에 쓰는 작은 표면
        self._fmt = ctypes.c_void_p()
        _gdip.GdipCreateStringFormat(0, 0, ctypes.byref(self._fmt))
        _gdip.GdipSetStringFormatFlags(self._fmt, _FMT_NOWRAP)
        self.resize(w, h)

    # ── 바탕 ──
    def resize(self, w: int, h: int) -> None:
        w, h = max(1, int(w)), max(1, int(h))
        if (w, h) == (self.w, self.h):
            return
        self._free()
        self.w, self.h = w, h
        bmi = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        screen = _user.GetDC(None)
        self._dib = _gdi.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        self._dc = _gdi.CreateCompatibleDC(screen)
        _user.ReleaseDC(None, screen)
        _gdi.SelectObject(self._dc, self._dib)
        if not self._dib or not self._dc:
            raise OSError(f"CreateDIBSection/CreateCompatibleDC 실패 (dib={self._dib} dc={self._dc})")
        self._bits = bits
        _gdip.GdipCreateBitmapFromScan0(w, h, w * 4, _PARGB, bits, ctypes.byref(self._bmp))
        _gdip.GdipGetImageGraphicsContext(self._bmp, ctypes.byref(self._g))
        _gdip.GdipSetSmoothingMode(self._g, _SMOOTH_AA)
        _gdip.GdipSetTextRenderingHint(self._g, _TEXT_AA)

    def clear(self) -> None:
        _gdip.GdipGraphicsClear(self._g, 0)          # 완전 투명

    def _free(self) -> None:
        for _pen_h in self._pens.values():
            _gdip.GdipDeletePen(_pen_h)
        self._pens.clear()
        if self._g:
            _gdip.GdipDeleteGraphics(self._g); self._g = ctypes.c_void_p()
        if self._bmp:
            _gdip.GdipDisposeImage(self._bmp); self._bmp = ctypes.c_void_p()
        if self._dc:
            _gdi.DeleteDC(self._dc); self._dc = None
        if self._dib:
            _gdi.DeleteObject(self._dib); self._dib = None


    # ── 아래는 모비폴리오 `paint32.py` 에서 가져온 것 ──
    # 우리 것에 없던 조각만 얹었다. 폴리오 오버레이·연출이 이것들을 쓴다.
    # **갈아 끼우지 않은 이유**: 폴리오 `ink_box` 는 잉크 기준점이 달라
    # ▾/▴ 토글 때 0.5px 튀는 문제가 되살아난다 (tests/test_paint_icons.py 가 잡는다).

    def _pen(self, color: str, alpha: int = 255, width: float = 1.0):
        """선 펜. 붓과 같은 이유로 모아 둔다."""
        key = (color, int(alpha), round(float(width), 2))
        pen = self._pens.get(key)
        if pen is None:
            pen = ctypes.c_void_p()
            _gdip.GdipCreatePen1(argb(color, alpha), ctypes.c_float(float(width)), _UNIT_PIXEL, ctypes.byref(pen))
            self._pens[key] = pen
        return pen

    def arc(self, x, y, w, h, start, sweep, color, width=1.0, alpha=255) -> None:
        """타원 호. 각도는 GDI+ 규칙(0도 = 3시, 시계 방향). 반복 아이콘의 둥근 모서리에 쓴다."""
        _gdip.GdipDrawArcI(self._g, self._pen(color, alpha, width), int(x), int(y), int(w), int(h),
                           ctypes.c_float(float(start)), ctypes.c_float(float(sweep)))

    def clip(self, x, y, w, h) -> None:
        """이 사각형 밖으로는 그리지 않는다. 알약 줄을 좌우로 굴릴 때 옆 칸으로 안 삐져나가게."""
        _gdip.GdipSetClipRectI(self._g, int(x), int(y), int(w), int(h), 0)     # 0 = Replace

    def clip_off(self) -> None:
        _gdip.GdipResetClip(self._g)

    def copy_from(self, src, x: int, y: int, w: int, h: int) -> None:
        """다른 그림의 (x, y, w, h) 칸을 **같은 자리에** 그대로 옮긴다.

        32비트 DIB 끼리의 BitBlt 라 알파까지 바이트 그대로 복사된다. 자주 바뀌지 않는
        상세창을 한 번 그려 두고 프레임마다 이걸로 옮긴다 (다시 그리면 한 프레임이 통째로 든다)."""
        if src is None or src._dc is None or self._dc is None:
            return
        _gdi.BitBlt(self._dc, int(x), int(y), int(w), int(h), src._dc, int(x), int(y), 0x00CC0020)

    # ── 창에 얹기 ──

    def _draw_img(self, img, x, y, w, h, iw, ih, alpha: int) -> None:
        """GDI+ 그림 하나를 그 자리에 늘려 얹는다. alpha 가 255 미만이면 반투명으로."""
        if alpha >= 255:
            _gdip.GdipDrawImageRectI(self._g, img, int(x), int(y), int(w), int(h))
            return
        # 반투명으로 얹기 — 색 변환표(ColorMatrix)의 알파 항만 낮춘다
        mat = (ctypes.c_float * 25)()
        for i in range(5):
            mat[i * 5 + i] = 1.0
        mat[18] = max(0.0, min(1.0, alpha / 255.0))
        attrs = ctypes.c_void_p()
        _gdip.GdipCreateImageAttributes(ctypes.byref(attrs))
        _gdip.GdipSetImageAttributesColorMatrix(attrs, 0, True, ctypes.byref(mat), None, 0)
        _gdip.GdipDrawImageRectRectI(self._g, img, int(x), int(y), int(w), int(h), 0, 0, int(iw), int(ih),
                                     2, attrs, None, None)   # 2 = UnitPixel
        _gdip.GdipDisposeImageAttributes(attrs)

    def image_bits(self, buf, iw: int, ih: int, stride: int, x, y, w, h, alpha: int = 255) -> None:
        """**파일이 아니라 픽셀 덩어리**를 얹는다 — 영상을 직접 재생할 때 쓴다.

        buf 는 BGRA(또는 BGRX) 픽셀이 담긴 bytes. 디코더가 준 것을 그대로 받는다.
        stride 가 음수면 아래에서 위로 담긴 그림이라 마지막 줄을 시작점으로 준다.
        그리는 동안만 GDI+ 그림으로 감싸고 바로 버린다 (복사가 없어 한 장에 한 번의 확대만 든다)."""
        n = abs(stride) * ih
        if not buf or len(buf) < n:
            return
        base = ctypes.cast(ctypes.create_string_buffer(buf, len(buf)), ctypes.c_void_p)
        hold = base                                  # 파이썬이 버퍼를 먼저 치우지 않게 잡아 둔다
        scan0 = base if stride > 0 else ctypes.c_void_p(base.value + (ih - 1) * abs(stride))
        img = ctypes.c_void_p()
        if _gdip.GdipCreateBitmapFromScan0(int(iw), int(ih), int(stride), _RGB32, scan0,
                                           ctypes.byref(img)) != 0:
            return
        try:
            self._draw_img(img, x, y, w, h, iw, ih, alpha)
        finally:
            _gdip.GdipDisposeImage(img)
            del hold

    # ── 글자 ──

    def lwa_locked(self, hwnd: int) -> bool:
        """이 창이 SetLayeredWindowAttributes 방식에 고정돼 있는가.
        참이면 UpdateLayeredWindow 가 True 를 돌려주면서 픽셀을 하나도 안 그린다 (조용한 실패).
        **첫 blit 뒤에** 물어야 한다 — 그 전에 물으면 헛경고가 난다."""
        try:
            key, a, fl = wt.DWORD(), ctypes.c_ubyte(), wt.DWORD()
            _user.GetLayeredWindowAttributes.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD),
                                                         ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wt.DWORD)]
            _user.GetLayeredWindowAttributes.restype = wt.BOOL
            return bool(_user.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(a), ctypes.byref(fl)))
        except Exception:
            return False

    def close(self) -> None:
        if self._scratch is not None:
            self._scratch.close()
            self._scratch = None
        self._free()

    # ── 도형 ──
    def _brush(self, color: str, alpha: int = 255):
        b = ctypes.c_void_p()
        _gdip.GdipCreateSolidFill(argb(color, alpha), ctypes.byref(b))
        return b

    def _path_round(self, x, y, w, h, r):
        # **전부 int 로 못 박는다.** `w`·`h` 가 float 이면 `min(w,h)//2` 도 float 이라
        # `r` 이 float 로 남고, `GdipAddPathArcI`(정수 인자)에 넘어가 ArgumentError 로 터진다.
        # 배율을 곱한 값(18.75 같은)을 주면 바로 걸린다 — 그러면 밴드가 통째로 안 뜬다.
        r = int(max(0, min(int(r), int(min(w, h) // 2))))
        p = ctypes.c_void_p()
        _gdip.GdipCreatePath(0, ctypes.byref(p))
        if r <= 0:
            _gdip.GdipAddPathRectangleI(p, int(x), int(y), int(w), int(h))
        else:
            d = int(r) * 2
            _gdip.GdipAddPathArcI(p, int(x), int(y), d, d, ctypes.c_float(180), ctypes.c_float(90))
            _gdip.GdipAddPathArcI(p, int(x + w - d), int(y), d, d, ctypes.c_float(270), ctypes.c_float(90))
            _gdip.GdipAddPathArcI(p, int(x + w - d), int(y + h - d), d, d, ctypes.c_float(0), ctypes.c_float(90))
            _gdip.GdipAddPathArcI(p, int(x), int(y + h - d), d, d, ctypes.c_float(90), ctypes.c_float(90))
        _gdip.GdipClosePathFigure(p)
        return p

    def round_rect(self, x, y, w, h, r, fill=None, outline=None, width=1.0, alpha=255) -> None:
        p = self._path_round(x, y, w, h, r)
        if fill:
            b = self._brush(fill, alpha)
            _gdip.GdipFillPath(self._g, b, p)
            _gdip.GdipDeleteBrush(b)
        if outline:
            pen = ctypes.c_void_p()
            _gdip.GdipCreatePen1(argb(outline, alpha), ctypes.c_float(width), _UNIT_PIXEL, ctypes.byref(pen))
            _gdip.GdipDrawPath(self._g, pen, p)
            _gdip.GdipDeletePen(pen)
        _gdip.GdipDeletePath(p)

    def rect(self, x, y, w, h, fill, alpha=255) -> None:
        b = self._brush(fill, alpha)
        _gdip.GdipFillRectangleI(self._g, b, int(x), int(y), int(w), int(h))
        _gdip.GdipDeleteBrush(b)

    def ellipse(self, x, y, w, h, fill, alpha=255) -> None:
        b = self._brush(fill, alpha)
        _gdip.GdipFillEllipseI(self._g, b, int(x), int(y), int(w), int(h))
        _gdip.GdipDeleteBrush(b)

    def line(self, x0, y0, x1, y1, color, width=1.0, alpha=255) -> None:
        pen = ctypes.c_void_p()
        _gdip.GdipCreatePen1(argb(color, alpha), ctypes.c_float(width), _UNIT_PIXEL, ctypes.byref(pen))
        _gdip.GdipDrawLineI(self._g, pen, int(x0), int(y0), int(x1), int(y1))
        _gdip.GdipDeletePen(pen)

    # ── 글자 ──
    def _font(self, family: str, size: int, bold: bool):
        key = (family, size, bold)
        f = self._fonts.get(key)
        if f is not None:
            return f
        fam = ctypes.c_void_p()
        if _gdip.GdipCreateFontFamilyFromName(ctypes.c_wchar_p(family), None, ctypes.byref(fam)) != 0:
            _gdip.GdipCreateFontFamilyFromName(ctypes.c_wchar_p("Malgun Gothic"), None, ctypes.byref(fam))
        font = ctypes.c_void_p()
        _gdip.GdipCreateFont(fam, ctypes.c_float(float(size)), 1 if bold else 0, _UNIT_PIXEL, ctypes.byref(font))
        self._fonts[key] = font
        return font

    def measure(self, text: str, family: str, size: int, bold: bool = False) -> int:
        if not text:
            return 0
        font = self._font(family, size, bold)
        layout = _RectF(0, 0, 10000, 1000)
        box = _RectF()
        _gdip.GdipMeasureString(self._g, ctypes.c_wchar_p(text), -1, font, ctypes.byref(layout),
                                self._fmt, ctypes.byref(box), None, None)
        return int(round(box.w))

    def text_h(self, family: str, size: int, bold: bool = False) -> int:
        font = self._font(family, size, bold)
        layout = _RectF(0, 0, 10000, 1000)
        box = _RectF()
        _gdip.GdipMeasureString(self._g, ctypes.c_wchar_p("가Ag"), -1, font, ctypes.byref(layout),
                                self._fmt, ctypes.byref(box), None, None)
        return int(round(box.h))

    def ink_box(self, s: str, family: str, size: int, bold: bool = False) -> tuple:
        """글리프의 **실제 잉크 경계상자** — `text(anchor="w")` 의 기준점에 대한 상대 좌표.

        돌려주는 것: `(dx, dy, w, h)`. 잉크 왼쪽 위가 기준점에서 `(dx, dy)` 만큼 떨어져 있고
        크기가 `w x h` 라는 뜻이다.

        **왜 필요한가**: 글꼴 metric(advance·줄높이)에는 글리프마다 다른 좌우·상하 여백이
        들어 있다. 특히 `Segoe MDL2 Assets` 같은 아이콘 글꼴은 `text_h` 가 재는 「가Ag」가
        아예 없어서 대체 글꼴 값이 나오고, 그 값으로 가운데를 잡으면 **늘 어긋난다**
        (톱니가 1.2px 위로 뜬다). 잉크를 직접 재면 안 틀어진다.

        한 번 재서 `(글리프, 가족, 크기, 굵게)` 로 기억해 둔다 — 그릴 때마다 재지 않는다."""
        if not s:
            return (0.0, 0.0, 0.0, 0.0)
        key = (s, family, int(size), bool(bold))
        hit = self._ink.get(key)
        if hit is not None:
            return hit
        n = max(16, int(size) * 3 + 24)
        sc = self._scratch
        if sc is None or sc.w < n or sc.h < n:
            if sc is not None:
                sc.close()
            sc = self._scratch = Surface(n, n)
        sc.clear()
        ax, ay = n / 4.0, n / 2.0
        sc.text(ax, ay, s, family, size, "#ffffff", bold)
        buf = ctypes.string_at(sc._bits, sc.w * sc.h * 4)
        x0, y0, x1, y1 = sc.w, sc.h, -1, -1
        for yy in range(sc.h):
            row = yy * sc.w
            for xx in range(sc.w):
                if buf[(row + xx) * 4 + 3] > 40:
                    if xx < x0:
                        x0 = xx
                    if xx > x1:
                        x1 = xx
                    if yy < y0:
                        y0 = yy
                    if yy > y1:
                        y1 = yy
        if x1 < 0:      # 잉크가 없다 (글리프가 없는 글꼴) — metric 그대로 쓴다
            h = self.text_h(family, size, bold)
            out = (0.0, -h / 2.0, float(self.measure(s, family, size, bold)), float(h))
        else:
            out = (x0 - ax, y0 - ay, float(x1 - x0 + 1), float(y1 - y0 + 1))
        self._ink[key] = out
        return out

    def icon(self, x, cy, s: str, family: str, size: int, color: str, bold: bool = False,
             anchor: str = "c", alpha: int = 255) -> float:
        """기호 글리프 하나를 그린다. **돌려주는 것은 advance 폭** (자리를 잡을 때 쓸 값).

        규칙은 한 줄이다 — **자리는 advance, 가운데는 잉크.**
          · `text` 는 글꼴 metric 으로 세로를 잡는데, 기호(`▾`·`▴`·`×`·아이콘 글꼴)는
            글리프마다 metric 과 잉크의 관계가 달라 **글리프를 바꾸면 위치가 튄다**
            (`▾`↔`▴` 토글에서 캐럿이 0.5px 흔들렸다).
          · 그렇다고 **자리까지 잉크로 잡으면 안 된다.** 디자인은 CSS 기준이고 CSS 는
            글자를 advance 로 눕힌다 — 잉크로 잡으면 그 차이만큼 여백이 사라진다
            (실측: 자물쇠 알약 −12px · 토스트 `×` 가 4~6px 오른쪽으로 밀림).

        그래서 이 함수는 **advance 상자를 먼저 놓고 그 안에 잉크를 앉힌다.** `x` 는 그 상자의
        자리이고 `anchor` 가 어디를 가리키는지 정한다: `c`=가운데 · `w`=왼쪽 · `e`=오른쪽.
        `cy` 는 잉크의 세로 가운데다. 폭을 잉크로 잘못 잡을 길을 아예 없앤 것이다."""
        if not s:
            return 0.0
        adv = float(self.measure(s, family, size, bold))
        x0 = x - adv if anchor == "e" else (x - adv / 2.0 if anchor == "c" else x)
        dx, dy, iw, ih = self.ink_box(s, family, size, bold)
        self.text(x0 + adv / 2.0 - iw / 2.0 - dx, cy - (dy + ih / 2.0),
                  s, family, size, color, bold, "w", alpha)
        return adv

    def text(self, x, y, s: str, family: str, size: int, color: str, bold: bool = False,
             anchor: str = "w", alpha: int = 255) -> int:
        """anchor: w=왼쪽 세로가운데 · e=오른쪽 세로가운데 · nw=왼쪽 위. 그린 글자의 폭을 돌려준다."""
        if not s:
            return 0
        font = self._font(family, size, bold)
        w = self.measure(s, family, size, bold)
        h = self.text_h(family, size, bold)
        if anchor == "e":
            x -= w
        if anchor in ("w", "e"):
            y -= h / 2
        b = self._brush(color, alpha)
        r = _RectF(float(x), float(y), float(w + 4), float(h + 2))
        _gdip.GdipDrawString(self._g, ctypes.c_wchar_p(s), -1, font, ctypes.byref(r), self._fmt, b)
        _gdip.GdipDeleteBrush(b)
        return w

    def save(self, path: str) -> bool:
        """그린 그림을 PNG 로 — 눈으로 확인할 때만 쓴다 (화면 캡처는 반투명 창을 제대로 못 담는다)."""
        class _GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16),
                        ("d4", ctypes.c_ubyte * 8)]
        png = _GUID(0x557CF406, 0x1A04, 0x11D3, (ctypes.c_ubyte * 8)(0x9A, 0x73, 0, 0, 0xF8, 0x1E, 0xF3, 0x2E))
        return _gdip.GdipSaveImageToFile(self._bmp, ctypes.c_wchar_p(path), ctypes.byref(png), None) == 0

    # ── 창에 얹기 ──
    def blit(self, hwnd: int, x: int, y: int, alpha: int = 255) -> bool:
        """그린 그림을 창에 그대로 얹는다 (창 크기·위치도 같이 정해진다)."""
        screen = _user.GetDC(None)
        try:
            pos = wt.POINT(int(x), int(y))
            size = wt.SIZE(self.w, self.h)
            src = wt.POINT(0, 0)
            blend = _BlendFunction(_AC_SRC_OVER, 0, max(0, min(255, int(alpha))), _AC_SRC_ALPHA)
            return bool(_user.UpdateLayeredWindow(wt.HWND(hwnd), screen, ctypes.byref(pos), ctypes.byref(size),
                                                  self._dc, ctypes.byref(src), 0, ctypes.byref(blend), _ULW_ALPHA))
        finally:
            _user.ReleaseDC(None, screen)


def dpi_of(hwnd: int) -> float:
    """그 창이 놓인 모니터의 배율 (1.0 = 100%)."""
    try:
        d = _user.GetDpiForWindow(hwnd)
        return (d / 96.0) if d else 1.0
    except Exception:
        return 1.0

def set_dpi_aware() -> bool:
    """이 프로세스를 「모니터별 DPI 인식」으로 바꾼다. 창을 만들기 전에 불러야 한다.

    안 켜면 윈도우가 우리 창을 화면 배율만큼 **늘려서** 보여 준다 — 125% 화면에서
    330px 그림이 412px 로 늘어나 글자가 전부 뭉개진다(포토샵에서 확대한 것과 같다).
    켜면 우리가 실제 픽셀로 직접 그리므로 또렷하게 나온다."""
    try:
        return bool(_user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))   # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            return bool(ctypes.WinDLL("shcore").SetProcessDpiAwareness(2))
        except Exception:
            return False
