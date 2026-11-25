# -*- coding: utf-8 -*-
"""
Промо-ролик YouTube Shorts / TikTok для Vibe Music (Telegram-бот).
1080x1920 (9:16), 30 fps, ~24 c, H.264 yuv420p + AAC.

Пайплайн:
  1) PIL: покадровый рендер 720 кадров в promo/frames/%05d.png
  2) numpy: музыка promo/beat.wav (120 BPM, кик/хэт/бас-арпеджио Am, сидчейн)
  3) ffmpeg: promo/shorts_9x16.mp4
  4) ffprobe + preview-кадры, удаление promo/frames/

Эффекты: дрейф фона (параллакс), пульсация неона в такт биту (120 BPM),
летящие частицы, шайн-свип по карточкам/плашкам, ripple-нажатия,
анимированный эквалайзер, искры у чипа, вращающееся кольцо у CTA,
«живой» UI телефона (статус-бар, шапка приложения, таб-бар).
"""
import math
import os
import shutil
import subprocess
import wave

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont

# ---------------------------------------------------------------- пути
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # F:/TGMusicBot
PROMO = os.path.join(ROOT, "promo")
FRAMES = os.path.join(PROMO, "frames")
HERO = os.path.join(ROOT, "frontend", "src", "assets", "hero-wave.jpg")
WAV = os.path.join(PROMO, "beat.wav")
OUT = os.path.join(PROMO, "shorts_9x16.mp4")

FONT_B = "C:/Windows/Fonts/segoeuib.ttf"   # Segoe UI Bold
FONT_L = "C:/Windows/Fonts/segoeuil.ttf"   # Segoe UI Light

W, H = 1080, 1920
FPS = 30
DUR = 24.0
NF = int(FPS * DUR)  # 720

# ---------------------------------------------------------------- палитра
BG    = (10, 11, 16)
ACC1  = (124, 92, 255)    # violet
ACC2  = (255, 92, 138)    # pink
TXT   = (238, 240, 246)
MUT   = (154, 160, 180)
GREEN = (74, 222, 128)
CARD  = (28, 30, 42)
CARD2 = (33, 36, 50)
PH_BG = (21, 23, 32)

# границы сцен (сек)
S_HOOK  = (0.0, 3.5)
S_DEMO  = (3.5, 15.5)
S_CARDS = (15.5, 21.0)
S_CTA   = (21.0, 24.0)

_fonts = {}

def F(bold, size):
    key = (bold, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(FONT_B if bold else FONT_L, size)
    return _fonts[key]

def fit_font(text, bold, size, max_w):
    while size > 12:
        f = F(bold, size)
        b = f.getbbox(text)
        if b[2] - b[0] <= max_w:
            return f
        size -= 2
    return F(bold, size)

def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3

def ease_out_back(x):
    x = min(max(x, 0.0), 1.0)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2

def with_alpha(img, f):
    if f >= 0.999:
        return img
    a = img.getchannel("A").point(lambda v: int(v * f))
    img = img.copy()
    img.putalpha(a)
    return img

def glow_text(d, pos, text, font, fill, glow=None, anchor="mm",
              rings=((14, 30), (7, 60), (3, 100))):
    if glow:
        for r, a in rings:
            for k in range(10):
                ang = k * 2 * math.pi / 10
                d.text((pos[0] + r * math.cos(ang), pos[1] + r * math.sin(ang)),
                       text, font=font, fill=glow + (a,), anchor=anchor)
    d.text(pos, text, font=font, fill=fill + (255,), anchor=anchor)

def grad_h(w, h, c1, c2):
    x = np.linspace(0.0, 1.0, max(w, 2))[None, :, None]
    a = np.array(c1, dtype=np.float32)[None, None, :] * (1 - x) + \
        np.array(c2, dtype=np.float32)[None, None, :] * x
    return Image.fromarray(np.repeat(a, h, axis=0).astype(np.uint8), "RGB")

def paste_round_grad(canvas, grad, box, radius):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    g = grad.resize((w, h)) if grad.size != (w, h) else grad
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           min(radius, w // 2, h // 2), fill=255)
    canvas.paste(g, (x0, y0), mask)

def soft_circle(d, xy, r, color, alpha, layers=3):
    x, y = xy
    for i in range(layers, 0, -1):
        rr = r * (1 + 0.55 * i)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=color + (int(alpha / (i + 1)),))
    d.ellipse([x - r, y - r, x + r, y + r], fill=color + (alpha,))

def sparkle(d, xy, s, color, alpha):
    x, y = xy
    k = 0.24 * s
    pts = [(x, y - s), (x + k, y - k), (x + s, y), (x + k, y + k),
           (x, y + s), (x - k, y + k), (x - s, y), (x - k, y - k)]
    d.polygon(pts, fill=color + (alpha,))

def eq_bars(d, cx, cy, t, color, n=4, maxh=26, alpha=255, bw=6, gap=4, speed=9.0):
    total = n * bw + (n - 1) * gap
    x0 = cx - total / 2
    for i in range(n):
        hh = maxh * (0.35 + 0.65 * abs(math.sin(t * speed + i * 1.3)))
        d.rounded_rectangle([x0 + i * (bw + gap), cy - hh / 2,
                             x0 + i * (bw + gap) + bw, cy + hh / 2], 3,
                            fill=color + (alpha,))

def ripple(d, cx, cy, u, color, max_r=95, max_a=170):
    if u <= 0 or u >= 1:
        return
    r = ease_out(u) * max_r
    d.ellipse([cx - r, cy - r, cx + r, cy + r],
              outline=color + (int(max_a * (1 - u)),), width=5)
    r2 = r * 0.62
    d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2],
              outline=color + (int(max_a * 0.5 * (1 - u)),), width=3)

def shine_sweep(img, box, radius, u, width=150, max_alpha=110):
    """Вертикальный блик, проезжающий по скруглённой области. u: 0..1."""
    if u <= 0 or u >= 1:
        return
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    w, h = x1 - x0, y1 - y0
    bx = -width + u * (w + 2 * width)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    for k in range(width):
        xx = int(bx) + k
        if 0 <= xx < w:
            md.line([xx, 0, xx, h], fill=int(max_alpha * math.sin(math.pi * k / width)))
    clip = Image.new("L", (w, h), 0)
    ImageDraw.Draw(clip).rounded_rectangle([0, 0, w - 1, h - 1],
                                           min(radius, w // 2, h // 2), fill=255)
    mask = ImageChops.multiply(mask, clip)
    img.paste(Image.new("RGBA", (w, h), (255, 255, 255, 255)), (x0, y0), mask)

def beat_pulse(t):
    """1.0 в момент удара (120 BPM -> каждые 0.5 c), экспоненциальный спад."""
    return math.exp(-(t % 0.5) * 11.0)

# ---------------------------------------------------------------- фон
BGW, BGH = W + 150, H + 210  # запас для дрейфа/параллакса

def build_background():
    src = Image.open(HERO).convert("RGB")
    sc = max(BGW / src.width, BGH / src.height)
    nw, nh = round(src.width * sc), round(src.height * sc)
    src = src.resize((nw, nh), Image.LANCZOS)
    l, t = (nw - BGW) // 2, (nh - BGH) // 2
    bg = src.crop((l, t, l + BGW, t + BGH))
    bg = ImageEnhance.Brightness(bg).enhance(0.35)  # затемнить до ~35%
    arr = np.asarray(bg).astype(np.float32)
    yy, xx = np.mgrid[0:BGH, 0:BGW].astype(np.float32)
    corners = [
        (0, 0, ACC1, 0.60),          # violet из левого верхнего угла
        (BGW, BGH, ACC2, 0.55),      # pink из правого нижнего
        (BGW, 0, ACC2, 0.22),
        (0, BGH, ACC1, 0.22),
    ]
    for cx, cy, col, strength in corners:
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        inten = np.clip(1.0 - dist / 1500.0, 0.0, 1.0) ** 2 * strength
        for c in range(3):
            arr[:, :, c] += (255.0 - arr[:, :, c]) * inten * (col[c] / 255.0)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

def radial_alpha_img(color, cx, cy, strength, radius=1500.0, w=W, h=H):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    inten = np.clip(1.0 - dist / radius, 0.0, 1.0) ** 2 * strength
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[:, :, 0], img[:, :, 1], img[:, :, 2] = color
    img[:, :, 3] = (inten * 255).astype(np.uint8)
    return Image.fromarray(img, "RGBA")

# частицы (детерминированные)
_rng = np.random.default_rng(11)
PARTICLES = []
for _i in range(20):
    PARTICLES.append(dict(
        x0=float(_rng.uniform(40, W - 40)),
        y0=float(_rng.uniform(0, H)),
        speed=float(_rng.uniform(18, 55)),
        r=float(_rng.uniform(2, 4.5)),
        color=ACC1 if _i % 2 == 0 else ACC2,
        alpha=int(_rng.uniform(45, 90)),
        phase=float(_rng.uniform(0, 6.28)),
    ))

def draw_particles(d, t, alpha_scale=1.0):
    for p in PARTICLES:
        y = (p["y0"] - p["speed"] * t) % (H + 120) - 60
        x = p["x0"] + 26 * math.sin(t * 0.7 + p["phase"])
        a = p["alpha"] * alpha_scale * (0.6 + 0.4 * math.sin(t * 2 + p["phase"]))
        soft_circle(d, (x, y), p["r"], p["color"], int(a))

def bg_frame(bgc_big, t):
    """Дрейф + параллакс фона."""
    mx, my = BGW - W, BGH - H
    x = int(mx * (t / DUR) + 14 * math.sin(2 * math.pi * t / 9))
    y = int(my * (t / DUR) + 10 * math.sin(2 * math.pi * t / 7 + 1.0))
    x = min(max(x, 0), mx)
    y = min(max(y, 0), my)
    return bgc_big.crop((x, y, x + W, y + H))

# ================================================================ СЦЕНА 1
def render_hook(t, pulse):
    a, b = S_HOOK
    if not (a <= t < b):
        return None
    lt = t - a
    img = Image.new("RGBA", (W, 560), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f1 = fit_font("Скачивать музыку", True, 96, 960)
    f2 = fit_font("бесплатно — можно.", True, 96, 960)
    f3 = F(False, 46)

    # построчный стаггер: fade + rise
    def elem(cy, text, font, fill, glow, delay, rise=34):
        u = ease_out((lt - delay) / 0.55)
        if u <= 0:
            return
        glow_text(d, (540, cy + (1 - u) * rise), text, font, fill, glow,
                  anchor="mm" if glow is None else "mm")
        return u

    elem(150, "Скачивать музыку", f1, TXT, ACC1, 0.02)
    elem(272, "бесплатно — можно.", f2, TXT, ACC2, 0.14)
    elem(396, "без подписок и рекламы", f3, MUT, None, 0.30)
    # неоновая плашка-разделитель с бегущим бликом
    pu = ease_out((lt - 0.44) / 0.5)
    if pu > 0:
        pb = (360, 448 + (1 - pu) * 30, 720, 458 + (1 - pu) * 30)
        paste_round_grad(img, grad_h(360, 10, ACC1, ACC2),
                         (pb[0], pb[1], pb[2], pb[3]), 5)
        img.putalpha(img.getchannel("A"))
        shine_sweep(img, pb, 5, ((lt - 0.5) / 1.1) % 1.0 if lt > 0.5 else -1,
                    width=90, max_alpha=90)

    # fade + scale появление всего блока
    u = ease_out(lt / 0.7)
    scale = 0.86 + 0.14 * u
    alpha = u
    out_u = (lt - 2.7) / 0.3
    if 0 < out_u < 1:
        alpha *= 1 - out_u
    elif out_u >= 1:
        return None
    nw, nh = int(W * scale), int(560 * scale)
    r = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(with_alpha(r, alpha), (int(540 - nw / 2), int(905 - nh / 2)))
    return canvas

# ================================================================ СЦЕНА 2
TYPING_TXT = "Кино — Пачка сигарет"
ROWS = [
    ("Кино — Пачка сигарет", "Яндекс Музыка", "3:45"),
    ("Пачка сигарет (cover)", "SoundCloud", "4:02"),
    ("Пачка сигарет (Remix)", "zaycev", "3:58"),
]
PROG_START, PROG_END = 4.5, 8.5
CHIP_T = 8.7
HL_T = 4.2

def rounded_fill(d, box, radius, fill, outline=None, width=1):
    d.rounded_rectangle(box, radius, fill=fill, outline=outline, width=width)

def render_demo(t, pulse):
    a, b = S_DEMO
    if not (a <= t < b):
        return None
    lt = t - a
    alpha = 1.0
    if lt < 0.3:
        alpha = lt / 0.3
    out_u = (lt - 11.7) / 0.3
    if out_u >= 1:
        return None
    if out_u > 0:
        alpha *= 1 - out_u
    gp = 0.7 + 0.6 * pulse  # свечение в такт

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- телефон
    px0, py0, px1, py1 = 210, 400, 870, 1520
    for i, base_al in enumerate((34, 22, 12)):
        off = 7 * (i + 1)
        d.rounded_rectangle([px0 - off, py0 - off, px1 + off, py1 + off], 56,
                            outline=(ACC1 if i % 2 == 0 else ACC2) +
                                    (int(base_al * gp),), width=4)
    d.rounded_rectangle([px0, py0, px1, py1], 48, fill=PH_BG + (255,),
                        outline=(72, 76, 100, 255), width=3)
    d.rounded_rectangle([500, 424, 580, 434], 5, fill=(52, 56, 74, 255))  # динамик

    ix0, ix1 = px0 + 36, px1 - 36

    # ---- статус-бар (как в реальном телефоне)
    d.text((ix0 + 4, 428), "9:41", font=F(True, 26), fill=TXT + (255,), anchor="lm")
    bx = ix1
    d.rounded_rectangle([bx - 44, 419, bx - 6, 437], 5, outline=MUT + (255,), width=2)
    d.rounded_rectangle([bx - 41, 422, bx - 18, 434], 3, fill=MUT + (255,))
    d.rounded_rectangle([bx - 4, 424, bx - 1, 432], 1, fill=MUT + (255,))
    wx = bx - 62
    d.arc([wx - 12, 414, wx + 12, 440], 205, 335, fill=MUT + (255,), width=3)
    d.arc([wx - 6, 421, wx + 6, 433], 205, 335, fill=MUT + (255,), width=3)
    sx = wx - 22
    for k, hh in enumerate((7, 11, 15, 19)):
        d.rounded_rectangle([sx + k * 7, 437 - hh, sx + k * 7 + 5, 437], 2,
                            fill=(MUT if k < 3 else (90, 94, 112)) + (255,))

    # ---- шапка приложения
    d.ellipse([ix0, 452, ix0 + 64, 516], fill=(30, 32, 44, 255),
              outline=(60, 63, 84, 255), width=2)
    paste_round_grad(img, AVATAR_GRAD, (ix0 + 5, 457, ix0 + 59, 511), 27)
    # нота на аватаре
    nc = (ix0 + 32, 492)
    d.ellipse([nc[0] - 8, nc[1] + 2, nc[0] + 6, nc[1] + 14], fill=(255, 255, 255, 255))
    d.line([nc[0] + 5, nc[1] - 12, nc[0] + 5, nc[1] + 8], fill=(255, 255, 255, 255), width=4)
    d.line([nc[0] + 5, nc[1] - 12, nc[0] + 16, nc[1] - 7], fill=(255, 255, 255, 255), width=4)
    d.text((ix0 + 82, 472), "Vibe Music", font=F(True, 34), fill=TXT + (255,), anchor="lm")
    d.text((ix0 + 82, 504), "музыкальный бот", font=F(False, 24), fill=MUT + (255,), anchor="lm")
    for k in range(3):  # меню "..."]
        d.ellipse([ix1 - 12, 486 + k * 14 - 3, ix1 - 4, 486 + k * 14 + 5], fill=MUT + (255,))

    # ---- поле поиска + печать
    d.rounded_rectangle([ix0, 548, ix1, 624], 22, fill=CARD2 + (255,))
    d.ellipse([ix0 + 20, 566, ix0 + 48, 594], outline=MUT + (255,), width=4)
    d.line([ix0 + 45, 591, ix0 + 56, 602], fill=MUT + (255,), width=4)
    nch = int(min(lt / 2.5, 1.0) * len(TYPING_TXT))
    shown = TYPING_TXT[:nch]
    ft = F(True, 32)
    tx = ix0 + 72
    if shown:
        glow_text(d, (tx, 586), shown, ft, TXT, None, anchor="lm")
        tw = d.textlength(shown, font=ft)
        # кнопка очистки "x"
        ccx = ix1 - 30
        d.ellipse([ccx - 12, 574, ccx + 12, 598], outline=(100, 104, 124, 255), width=2)
        d.line([ccx - 5, 581, ccx + 5, 591], fill=(100, 104, 124, 255), width=3)
        d.line([ccx + 5, 581, ccx - 5, 591], fill=(100, 104, 124, 255), width=3)
    else:
        tw = 0
        d.text((tx, 586), "Поиск трека…", font=F(False, 30), fill=(110, 115, 134, 255),
               anchor="lm")
    if (lt * 2) % 1.0 < 0.55 and lt < 3.0:
        d.line([tx + tw + 8, 562, tx + tw + 8, 610], fill=ACC1 + (255,), width=3)

    # ---- список результатов
    row_h, gap = 104, 14
    ry = 648
    hl = lt >= HL_T
    for i, (title, src, dur) in enumerate(ROWS):
        ru = (lt - (2.3 + 0.28 * i)) / 0.3
        if ru <= 0:
            continue
        ra = ease_out(min(ru, 1.0))
        slide = (1 - ra) * 40
        y0 = ry + i * (row_h + gap) + slide
        y1 = y0 + row_h
        if i == 0 and hl:
            ha = 140 + int(80 * pulse)
            d.rounded_rectangle([ix0 - 4, y0 - 4, ix1 + 4, y1 + 4], 22,
                                outline=ACC1 + (ha,), width=3)
            d.rounded_rectangle([ix0, y0, ix1, y1], 18, fill=(38, 36, 66, int(255 * ra)))
        else:
            d.rounded_rectangle([ix0, y0, ix1, y1], 18,
                                fill=(CARD[0], CARD[1], CARD[2], int(255 * ra)))
        if i == 0 and hl:  # ripple «нажатия» — под контентом строки
            ripple(d, ix0 + 34, (y0 + y1) / 2, (lt - HL_T) / 0.55, ACC1, max_r=62)
        cxm, cym = ix0 + 34, (y0 + y1) / 2
        col = ACC1 if i == 0 else (ACC2 if i == 1 else MUT)
        d.ellipse([cxm - 7, cym + 2, cxm + 7, cym + 16], fill=col + (int(255 * ra),))
        d.line([cxm + 6, cym - 12, cxm + 6, cym + 9], fill=col + (int(255 * ra),), width=4)
        d.line([cxm + 6, cym - 12, cxm + 16, cym - 8], fill=col + (int(255 * ra),), width=4)
        fti = fit_font(title, True, 32, ix1 - ix0 - 150)
        fsrc = F(False, 24)
        d.text((ix0 + 60, y0 + 32), title, font=fti, fill=TXT + (int(255 * ra),), anchor="lm")
        d.text((ix0 + 60, y0 + 72), src, font=fsrc, fill=MUT + (int(230 * ra),), anchor="lm")
        d.text((ix1 - 18, y0 + 32), dur, font=F(False, 26), fill=MUT + (int(230 * ra),),
               anchor="rm")
        if i == 0 and hl:  # живой эквалайзер у активной строки
            eq_bars(d, ix1 - 44, y0 + 72, lt, ACC1, n=4, maxh=22, alpha=int(255 * ra))

    # ---- панель скачивания (карточка, как в приложении)
    if PROG_START <= lt < CHIP_T + 0.5:
        pa = 1.0
        if lt > CHIP_T:
            pa = max(0.0, 1.0 - (lt - CHIP_T) / 0.5)
        if pa > 0:
            pu_ = ease_out(min((lt - PROG_START) / 0.3, 1.0))
            box = [ix0, 1012, ix1, 1156]
            d.rounded_rectangle([box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3],
                                24, outline=ACC1 + (int(60 * pu_ * gp * pa),), width=3)
            d.rounded_rectangle(box, 20, fill=(30, 33, 46, int(255 * pu_ * pa)))
            ppu = min((lt - PROG_START) / (PROG_END - PROG_START), 1.0)
            p = ppu ** 1.7
            pct = int(p * 100)
            fpl = F(True, 30)
            d.text((ix0 + 24, 1044), "Скачивание", font=fpl,
                   fill=TXT + (int(255 * pu_ * pa),), anchor="lm")
            d.text((ix1 - 24, 1044), f"{pct}%", font=fpl,
                   fill=TXT + (int(255 * pu_ * pa),), anchor="rm")
            by0, by1 = 1074, 1090
            d.rounded_rectangle([ix0 + 24, by0, ix1 - 24, by1], 8,
                                fill=(38, 41, 56, int(255 * pu_ * pa)))
            bw = int((ix1 - ix0 - 48) * p)
            if bw > 4:
                paste_round_grad(img, PROG_GRAD, (ix0 + 24, by0, ix0 + 24 + bw, by1), 8)
                shine_sweep(img, (ix0 + 24, by0, ix0 + 24 + max(bw, 20), by1), 8,
                            ((lt - PROG_START) * 0.9) % 1.0, width=60, max_alpha=70)
            d.text((ix0 + 24, 1122), "FLAC · 38 МБ", font=F(False, 26),
                   fill=MUT + (int(255 * pu_ * pa),), anchor="lm")
            spd = 1.6 + 2.2 * p
            d.text((ix1 - 24, 1122), "%.1f МБ/с" % spd, font=F(True, 26),
                   fill=GREEN + (int(255 * pu_ * pa),), anchor="rm")

    # ---- чип результата + галочка + искры
    if lt >= CHIP_T:
        cu = min((lt - CHIP_T) / 0.3, 1.0)
        cs = 1.15 - 0.15 * ease_out_back(cu)
        chip_txt = "Пачка сигарет · FLAC · 38 МБ"
        fc = fit_font(chip_txt, True, 34, 560)
        tb = fc.getbbox(chip_txt)
        twd = tb[2] - tb[0]
        cw = twd + 130
        ch = 84
        ccx, ccy = 540, 1230
        cimg = Image.new("RGBA", (cw + 40, ch + 40), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cimg)
        x0, y0, x1, y1 = 20, 20, 20 + cw, 20 + ch
        cd.rounded_rectangle([x0 - 5, y0 - 5, x1 + 5, y1 + 5], 46,
                             outline=GREEN + (int(60 + 40 * pulse),), width=4)
        cd.rounded_rectangle([x0, y0, x1, y1], 42, fill=(24, 44, 34, 255),
                             outline=GREEN + (220,), width=3)
        gcx, gcy = x0 + 44, (y0 + y1) / 2
        cd.ellipse([gcx - 17, gcy - 17, gcx + 17, gcy + 17], fill=GREEN + (255,))
        cd.line([gcx - 8, gcy + 1, gcx - 2, gcy + 8], fill=(10, 20, 14, 255), width=5)
        cd.line([gcx - 2, gcy + 8, gcx + 10, gcy - 8], fill=(10, 20, 14, 255), width=5)
        cd.text((x0 + 78, ccy), chip_txt, font=fc, fill=TXT + (255,), anchor="lm")
        shine_sweep(cimg, (x0, y0, x1, y1), 42, ((lt - CHIP_T) * 0.8) % 1.0,
                    width=80, max_alpha=70)
        cw2, ch2 = int(cimg.width * cs), int(cimg.height * cs)
        cimg = cimg.resize((cw2, ch2), Image.LANCZOS)
        img.alpha_composite(with_alpha(cimg, ease_out(cu)),
                            (int(ccx - cw2 / 2), int(ccy - ch2 / 2)))
        # искры вокруг чипа
        su = min((lt - CHIP_T) / 0.9, 1.0)
        if su < 1:
            for k in range(7):
                ang = k * 2 * math.pi / 7 + 0.4
                dist = ease_out(su) * (60 + 12 * (k % 3))
                sxp = ccx + dist * math.cos(ang)
                syp = ccy + dist * math.sin(ang) * 0.5
                sparkle(d, (sxp, syp), 7 + 3 * (k % 2), GREEN,
                        int(200 * (1 - su)))
        # короткая зелёная вспышка
        fl = max(0.0, 1.0 - (lt - CHIP_T) / 0.35)
        if fl > 0:
            d.rectangle([0, 0, W, H], fill=GREEN + (int(60 * fl ** 2),))

    # ---- таб-бар (как в реальном приложении)
    d.line([ix0, 1414, ix1, 1414], fill=(48, 51, 66, 255), width=2)
    tabs = [("Поиск", "search"), ("Загрузки", "dl"), ("Ещё", "more")]
    tw3 = (ix1 - ix0) / 3
    for i, (label, icon) in enumerate(tabs):
        tcx = ix0 + tw3 * (i + 0.5)
        active = i == 0
        col = ACC1 if active else (110, 114, 132)
        if icon == "search":
            d.ellipse([tcx - 11, 1434, tcx + 7, 1452], outline=col + (255,), width=3)
            d.line([tcx + 6, 1451, tcx + 13, 1458], fill=col + (255,), width=3)
        elif icon == "dl":
            d.line([tcx, 1436, tcx, 1452], fill=col + (255,), width=3)
            d.line([tcx - 7, 1445, tcx, 1452], fill=col + (255,), width=3)
            d.line([tcx + 7, 1445, tcx, 1452], fill=col + (255,), width=3)
            d.line([tcx - 10, 1458, tcx + 10, 1458], fill=col + (255,), width=3)
        else:
            for k in range(3):
                d.ellipse([tcx - 12 + k * 9, 1442, tcx - 6 + k * 9, 1448],
                          fill=col + (255,))
        d.text((tcx, 1478), label, font=F(True if active else False, 22),
               fill=(col if active else MUT) + (255,), anchor="mm")
        if active:
            d.ellipse([tcx - 3, 1494, tcx + 3, 1500], fill=ACC1 + (255,))

    return with_alpha(img, alpha)

# ================================================================ СЦЕНА 3
CARDS = [
    ("FLAC и 320 кбит/с", ACC1, "flac"),
    ("Плейлисты одним сообщением", ACC2, "list"),
    ("18+ без блюра", ACC1, "age"),
]
CARD_STARTS = [0.0, 1.7, 3.4]

def render_card(i, t, pulse):
    start = CARD_STARTS[i]
    if t < start:
        return None
    u = min((t - start) / 0.45, 1.0)
    xoff = (1 - ease_out_back(u)) * 280
    alpha = ease_out(min(u * 1.6, 1.0))
    if i < 2 and t > CARD_STARTS[i + 1]:
        alpha *= 1 - 0.35 * min((t - CARD_STARTS[i + 1]) / 0.4, 1.0)
    _, col, icon = CARDS[i]
    cimg = Image.new("RGBA", (960, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(cimg)
    x0, y0, x1, y1 = 30, 20, 930, 180
    d.rounded_rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], 30,
                        outline=col + (int(50 + 40 * pulse),), width=6)
    d.rounded_rectangle([x0, y0, x1, y1], 26, fill=CARD + (255,),
                        outline=col + (130,), width=2)
    paste_round_grad(cimg, grad_h(8, 100, col, col), (x0, y0 + 30, x0 + 8, y1 - 30), 4)
    icx, icy = x0 + 80, (y0 + y1) / 2
    d.ellipse([icx - 42, icy - 42, icx + 42, icy + 42], fill=col + (36,))
    d.ellipse([icx - 42, icy - 42, icx + 42, icy + 42], outline=col + (160,), width=3)
    if icon == "flac":
        for k in range(5):
            hbar = (0.45 + 0.55 * abs(math.sin(t * 8 + k * 1.1))) * 38
            bx = icx - 30 + k * 14
            d.rounded_rectangle([bx, icy - hbar / 2, bx + 8, icy + hbar / 2], 4,
                                fill=col + (255,))
    elif icon == "list":
        for k in range(3):
            ly = icy - 18 + k * 18
            lu = ease_out(min((t - start) * 3 - k * 0.35, 1.0))
            if lu <= 0:
                continue
            d.ellipse([icx - 26, ly - 4, icx - 18, ly + 4], fill=col + (255,))
            d.line([icx - 8, ly, icx - 8 + int(36 * lu), ly], fill=col + (220,), width=5)
    else:
        rr = 42 + 6 * pulse
        d.ellipse([icx - rr, icy - rr, icx + rr, icy + rr], outline=col + (70,), width=3)
        d.text((icx, icy), "18+", font=F(True, 34), fill=col + (255,), anchor="mm")
    text = CARDS[i][0]
    fcard = fit_font(text, True, 50, x1 - (x0 + 150) - 20)
    d.text((x0 + 150, (y0 + y1) / 2), text, font=fcard, fill=TXT + (255,), anchor="lm")
    if u >= 1.0:  # блик после приземления карточки
        shine_sweep(cimg, (x0, y0, x1, y1), 26, ((t - start - 0.5) / 0.9), width=130)
    return with_alpha(cimg, alpha), xoff

def render_cards(t, pulse):
    a, b = S_CARDS
    if not (a <= t < b):
        return None
    lt = t - a
    alpha = 1.0
    if lt < 0.3:
        alpha = lt / 0.3
    out_u = (lt - 5.2) / 0.3
    if out_u >= 1:
        return None
    if out_u > 0:
        alpha *= 1 - out_u
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for i in range(3):
        r = render_card(i, lt, pulse)
        if r is None:
            continue
        cimg, xoff = r
        top = [705, 885, 1065][i] - 20
        canvas.alpha_composite(cimg, (int(60 + xoff), top))
    return with_alpha(canvas, alpha)

# ================================================================ СЦЕНА 4
def build_cta():
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, R = 540, 690, 130
    for rr, al in ((R + 8, 60), (R + 3, 110), (R, 200)):
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr], 180, 360,
              fill=ACC1 + (al,), width=24)
    for sgn in (-1, 1):
        ex = cx + sgn * R
        for off, al in ((9, 55), (4, 110)):
            d.rounded_rectangle([ex - 26 - off, cy - 12 - off,
                                 ex + 26 + off, cy + 122 + off], 26,
                                outline=ACC2 + (al,), width=4)
        d.rounded_rectangle([ex - 26, cy - 12, ex + 26, cy + 122], 24,
                            fill=ACC2 + (255,))
        d.rounded_rectangle([ex - 14, cy, ex + 14, cy + 98], 14,
                            fill=(255, 160, 190, 255))
    glow_text(d, (540, 950), "Vibe Music", fit_font("Vibe Music", True, 140, 940),
              TXT, ACC1, rings=((18, 40), (9, 80), (4, 120)))
    handle = "t.me/tG_dols_music_bot"
    fh = fit_font(handle, True, 50, 660)
    tb = fh.getbbox(handle)
    pw = (tb[2] - tb[0]) + 90
    ph = 94
    px0, py0 = 540 - pw // 2, 1080
    for off, al in ((8, 50), (4, 90)):
        d.rounded_rectangle([px0 - off, py0 - off, px0 + pw + off, py0 + ph + off],
                            (ph + 2 * off) // 2, outline=ACC1 + (al,), width=4)
    paste_round_grad(img, grad_h(pw, ph, ACC1, ACC2),
                     (px0, py0, px0 + pw, py0 + ph), ph // 2)
    d.text((540, py0 + ph // 2), handle, font=fh, fill=(255, 255, 255, 255), anchor="mm")
    glow_text(d, (540, 1260), "Ссылка в описании", F(False, 42), MUT, None)
    return img

CTA_IMG = None

def render_cta(t, pulse):
    global CTA_IMG
    a, b = S_CTA
    if not (a <= t < b):
        return None
    lt = t - a
    if CTA_IMG is None:
        CTA_IMG = build_cta()
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    cx, cy, R = 540, 690, 130
    # вращающиеся дуги-«орбиты» вокруг иконки
    for k, (col, rot, span) in enumerate(((ACC1, lt * 130, 100), (ACC2, -lt * 100 + 180, 80))):
        st = rot % 360
        d.arc([cx - R - 34, cy - R - 34, cx + R + 34, cy + R + 34], st, st + span,
              fill=col + (int(60 + 40 * pulse),), width=6)
    # искры вокруг иконки
    for k in range(5):
        ang = k * 2 * math.pi / 5 + lt * 0.8
        rr = R + 46 + 8 * math.sin(lt * 3 + k)
        soft_circle(d, (cx + rr * math.cos(ang), cy + rr * math.sin(ang)),
                    3, ACC2 if k % 2 else ACC1, int(120 + 80 * pulse))
    pulse_s = 1.0 + 0.03 * math.sin(2 * math.pi * lt / 1.1) + 0.012 * pulse
    alpha = ease_out(min(lt / 0.4, 1.0))
    nw, nh = int(W * pulse_s), int(H * pulse_s)
    r = CTA_IMG.resize((nw, nh), Image.LANCZOS)
    canvas.alpha_composite(with_alpha(r, alpha), (int(540 - nw / 2), int(960 - nh / 2)))
    # мини-эквалайзер под подписью
    eq_bars(d, 540, 1330, lt, ACC2, n=5, maxh=30, alpha=int(200 * alpha), speed=7)
    return canvas

# ---------------------------------------------------------------- музыка
def make_beat():
    sr = 44100
    n = int(sr * DUR)
    out = np.zeros(n, dtype=np.float64)
    beat = 60.0 / 120.0  # 0.5 c

    # кик: синус 50 Гц с экспоненциальным затуханием, на каждую долю
    kd = int(0.45 * sr)
    kt = np.arange(kd) / sr
    kick = np.sin(2 * np.pi * 50.0 * kt) * np.exp(-kt * 7.0)
    kick[:64] *= np.linspace(0.0, 1.0, 64)  # анти-клик
    kick_times = np.arange(int(DUR / beat)) * beat
    duck = np.ones(n)
    for tk in kick_times:
        s = int(tk * sr)
        e = min(s + kd, n)
        out[s:e] += kick[: e - s]
        ds = s
        de = min(s + int(0.30 * sr), n)
        if de > ds:
            dt = np.arange(de - ds) / sr
            duck[ds:de] = np.minimum(duck[ds:de], 1.0 - 0.6 * np.exp(-dt / 0.08))

    # хэт: шум с быстрым затуханием, на восьмые
    hd = int(0.07 * sr)
    ht = np.arange(hd) / sr
    henv = np.exp(-ht * 90.0)
    rng = np.random.default_rng(7)
    for k in range(int(DUR / 0.25)):
        s = int(k * 0.25 * sr)
        e = min(s + hd, n)
        noise = np.diff(rng.standard_normal(e - s + 1), prepend=0.0)[: e - s]
        gain = 0.09 if k % 2 == 0 else 0.15
        out[s:e] += noise * henv[: e - s] * gain * 3.0

    # бас-арпеджио Am (110 / 130.81 / 164.81 Гц), мягкие квадраты, восьмые
    notes = [110.0, 130.81, 164.81, 110.0, 130.81, 164.81, 110.0, 130.81]
    bd = int(0.23 * sr)
    bt = np.arange(bd) / sr
    attack = np.minimum(bt / 0.005, 1.0)
    bass = np.zeros(n)
    for k in range(int(DUR / 0.25)):
        s = int(k * 0.25 * sr)
        e = min(s + bd, n)
        f = notes[k % len(notes)]
        sq = np.tanh(2.2 * np.sin(2 * np.pi * f * bt))  # мягкий квадрат
        seg = sq * np.exp(-bt * 4.5) * attack
        bass[s:e] += seg[: e - s]
    out += bass * duck * 0.55

    out *= 0.7 / max(np.abs(out).max(), 1e-9)
    fi = int(0.02 * sr)
    out[:fi] *= np.linspace(0, 1, fi)
    fo = int(0.3 * sr)
    out[-fo:] *= np.linspace(1, 0, fo)
    pcm = (out * 32767).astype(np.int16)
    with wave.open(WAV, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

# ---------------------------------------------------------------- сборка
def main():
    os.makedirs(FRAMES, exist_ok=True)
    print("1/4 музыка...")
    make_beat()
    print("   beat.wav готов")

    global PROG_GRAD, AVATAR_GRAD, PULSE_V, PULSE_P, BG_BIG
    PROG_GRAD = grad_h(600, 16, ACC1, ACC2)
    AVATAR_GRAD = grad_h(64, 64, ACC1, ACC2)
    PULSE_V = radial_alpha_img(ACC1, 0, 0, 0.5)
    PULSE_P = radial_alpha_img(ACC2, W, H, 0.45)
    print("2/4 фон...")
    BG_BIG = build_background()

    print("3/4 рендер 720 кадров...")
    for i in range(NF):
        t = i / FPS
        pulse = beat_pulse(t)
        canvas = bg_frame(BG_BIG, t).convert("RGBA")
        # неон-пульс фона в такт биту
        canvas.alpha_composite(with_alpha(PULSE_V, 0.30 * pulse))
        canvas.alpha_composite(with_alpha(PULSE_P, 0.26 * pulse))
        dp = ImageDraw.Draw(canvas)
        draw_particles(dp, t)
        for rnd in (render_hook, render_demo, render_cards, render_cta):
            img = rnd(t, pulse)
            if img is not None:
                canvas.alpha_composite(img)
        if t > 23.65:  # финальное затухание в чёрный
            fa = int(255 * min((t - 23.65) / 0.35, 1.0))
            ImageDraw.Draw(canvas).rectangle([0, 0, W, H], fill=(0, 0, 0, fa))
        canvas.convert("RGB").save(os.path.join(FRAMES, "%05d.png" % i))
        if i % 120 == 0:
            print("   кадр %d/720" % i)

    print("4/4 кодирование ffmpeg...")
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "30", "-i", os.path.join(FRAMES, "%05d.png"),
         "-i", WAV, "-c:v", "libx264", "-preset", "medium", "-crf", "17",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", OUT],
        check=True, capture_output=True)

    # проверка
    import json
    pr = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
         "-show_format", OUT], capture_output=True, text=True, check=True)
    info = json.loads(pr.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    au = next(s for s in info["streams"] if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    size = os.path.getsize(OUT)
    print("ffprobe: %sx%s, %s/%s, %.2f c, %.2f МБ"
          % (v["width"], v["height"], v["codec_name"], au["codec_name"],
             dur, size / 1e6))
    assert v["width"] == 1080 and v["height"] == 1920
    assert v["codec_name"] == "h264" and au["codec_name"] == "aac"
    assert dur > 23.0 and size > 3_000_000

    for sec, name in ((1, "preview_1s"), (8, "preview_8s"), (22, "preview_22s")):
        pv = os.path.join(PROMO, name + ".png")
        subprocess.run(["ffmpeg", "-y", "-ss", str(sec), "-i", OUT,
                        "-frames:v", "1", pv], check=True, capture_output=True)
        psz = os.path.getsize(pv)
        assert psz > 50_000, "%s слишком мал: %d Б" % (name, psz)
        print("   %s.png: %.1f КБ" % (name, psz / 1024))

    shutil.rmtree(FRAMES)
    print("Готово: %s" % OUT)

if __name__ == "__main__":
    main()
