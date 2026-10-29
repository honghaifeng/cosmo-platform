#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
COSMOBOUNTY 小红书封面 · 品牌海报版
对标官号视觉：深空蓝底 + 细线轨道 + 中文衬线大标题 + 英文微排版 + 品牌字标
流程：AI 生成无字底图（1080x1536）→ 3:4 裁切去水印 → 双层文字排版 → 1080x1440 输出
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

W, H = 1080, 1440
S = 2  # 超采样倍率
CW, CH = W * S, H * S

SRC = "/Users/v/Desktop/2026/秋峰/Demo/CosmoBounty/generated-images"
OUT = "/Users/v/Desktop/2026/秋峰/Demo/CosmoBounty/exports/brand_covers_2026-10-24"
os.makedirs(OUT, exist_ok=True)

SONG_BLACK = "/System/Library/Fonts/Songti.ttc"
SONG_BOLD = "/System/Library/Fonts/Songti.ttc"
HIRA = "/System/Library/Fonts/Hiragino Sans GB.ttc"
HN = "/System/Library/Fonts/HelveticaNeue.ttc"

WHITE = (246, 247, 250)
AMBER = (233, 195, 141)
STEEL = (156, 184, 216)


def F(path, size, index=0):
    return ImageFont.truetype(path, int(size * S), index=index)


def song(size, weight="black"):
    return F(SONG_BLACK, size, 0 if weight == "black" else 1)


def hira(size, bold=False):
    return F(HIRA, size, 2 if bold else 0)


def hn(size, style="ultralight"):
    idx = {"ultralight": 5, "light": 7, "regular": 0, "medium": 10, "cond": 4}[style]
    return F(HN, size, idx)


def load_bg(name, crop_top=0):
    """读取 1024x1536 底图，裁成 3:4 并去掉右下角 AI 水印，放大到超采样画布"""
    im = Image.open(os.path.join(SRC, name)).convert("RGB")
    w, h = im.size
    target_h = int(w * H / W)
    if target_h > h:
        target_h = h
    im = im.crop((0, crop_top, w, crop_top + target_h))
    im = im.resize((CW, CH), Image.LANCZOS)
    return im


def scrim(img, top=0.0, bottom=0.0, mid=0.0):
    """上下渐变遮罩，为文字让出对比度"""
    if not (top or bottom or mid):
        return img
    ov = Image.new("L", (1, CH), 0)
    px = ov.load()
    for y in range(CH):
        t = y / (CH - 1)
        v = 0
        if top:
            v = max(v, top * 255 * max(0.0, 1 - t / 0.38) ** 1.5)
        if mid:
            v = max(v, mid * 255 * max(0.0, 1 - abs(t - 0.45) / 0.35) ** 2)
        if bottom:
            v = max(v, bottom * 255 * max(0.0, (t - 0.6) / 0.4) ** 1.6)
        px[0, y] = int(v)
    mask = ov.resize((CW, CH))
    return Image.composite(Image.new("RGB", (CW, CH), (3, 7, 16)), img, mask)


def vignette(img, strength=0.55):
    mask = Image.new("L", (CW // 4, CH // 4), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((-CW // 12, -CH // 12, CW // 4 + CW // 12, CH // 4 + CH // 12), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(40)).resize((CW, CH), Image.LANCZOS)
    dark = ImageEnhance.Brightness(img).enhance(1 - strength * 0.45)
    return Image.composite(img, dark, mask)


def grain(img, amount=6):
    import random
    random.seed(7)
    noise = Image.effect_noise((CW, CH), amount).convert("L")
    return Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.035)


def tracked_len(text, font, tracking):
    return sum(font.getlength(ch) for ch in text) + tracking * S * max(0, len(text) - 1)


def tracked(draw, x, y, text, font, tracking=0.0, fill=WHITE, anchor="mm", glow=0.0,
            glow_color=(120, 170, 230), glow_alpha=70, img=None):
    total = tracked_len(text, font, tracking)
    cx = x if anchor[0] == "l" else (x - total / 2 if anchor[0] == "m" else x - total)
    for ch in text:
        draw.text((cx, y), ch, font=font, fill=fill, anchor="l" + anchor[1])
        cx += font.getlength(ch) + tracking * S


def text_layer(size, drawer):
    lay = Image.new("RGBA", size, (0, 0, 0, 0))
    drawer(ImageDraw.Draw(lay))
    return lay


def with_glow(base, lay, radius=14, alpha=90):
    glow = lay.filter(ImageFilter.GaussianBlur(radius * S))
    a = glow.split()[3].point(lambda v: int(v * alpha / 255))
    glow.putalpha(a)
    base.alpha_composite(glow)
    base.alpha_composite(lay)


def hairline(draw, x1, y, x2, color=(255, 255, 255), alpha=70, width=1):
    draw.line([(x1, y), (x2, y)], fill=color + (alpha,), width=width * S)


def brand_block(img, sub="BE RHYTHM · BE TIMING · BE NATURE", y=1258, color=WHITE):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    hairline(d, 392 * S, (y - 62) * S, 688 * S, color, 55)
    tracked(d, CW // 2, y * S, "COSMO BOUNTY", hn(31, "light"), tracking=9.2,
            fill=color + (232,), anchor="mm")
    tracked(d, CW // 2, (y + 50) * S, sub, hn(15.5, "light"), tracking=4.4,
            fill=color + (120,), anchor="mm")
    img.alpha_composite(lay)


def top_label(img, left, right, color=WHITE):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    tracked(d, 84 * S, 96 * S, left, hn(17, "regular"), tracking=4.6,
            fill=color + (150,), anchor="lm")
    tracked(d, CW - 84 * S, 96 * S, right, hn(17, "regular"), tracking=4.0,
            fill=color + (110,), anchor="rm")
    hairline(d, 84 * S, 128 * S, CW - 84 * S, color, 34)
    img.alpha_composite(lay)


def save(img, name):
    out = img.convert("RGB").resize((W, H), Image.LANCZOS)
    p = os.path.join(OUT, name)
    out.save(p, quality=94, subsampling=1)
    print("saved", p)


# ─────────────────────────────────────────────────────────────
# 01 玻璃球双瓶 · 白天一瓶，夜里一瓶
# ─────────────────────────────────────────────────────────────
def cover_01():
    bg = load_bg("Vertical_3_4_ultra_premium_bio_2026-10-24T09-45-19.png", 0)
    img = scrim(bg, top=0.86, bottom=0.8).convert("RGBA")
    img = vignette(img.convert("RGB"), 0.5).convert("RGBA")
    top_label(img, "COSMOBOUNTY  /  昼夜分护", "N° 01")

    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    tracked(d, CW // 2, 250 * S, "白天一瓶", song(112), tracking=12, fill=WHITE + (247,))
    tracked(d, CW // 2, 386 * S, "夜里一瓶", song(112), tracking=12, fill=AMBER + (250,))
    tracked(d, CW // 2, 500 * S, "分 时 抗 衰 · 口 服 抗 衰 5 . 0", hira(27), tracking=1.6,
            fill=STEEL + (215,))
    with_glow(img, lay, radius=18, alpha=70)

    brand_block(img, "DAYTIME · NIGHTTIME · 24H RHYTHM")
    save(img, "01_白天一瓶夜里一瓶.jpg")


# ─────────────────────────────────────────────────────────────
# 02 剪影 · 24h
# ─────────────────────────────────────────────────────────────
def cover_02():
    bg = load_bg("Vertical_3_4_cinematic_poster__2026-10-24T09-45-17.png", 0)
    img = scrim(bg, top=0.8, bottom=0.75).convert("RGBA")
    img = vignette(img.convert("RGB"), 0.55).convert("RGBA")
    top_label(img, "COSMOBOUNTY  /  节律研究", "N° 02")

    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    # 大字 24h
    f_num = hn(268, "ultralight")
    f_h = hn(96, "ultralight")
    n_w = f_num.getlength("24")
    total = n_w + f_h.getlength("h") + 6 * S
    x0 = CW // 2 - total / 2
    d.text((x0, 330 * S), "24", font=f_num, fill=WHITE + (238,), anchor="lm")
    d.text((x0 + n_w + 6 * S, 330 * S + 46 * S), "h", font=f_h, fill=WHITE + (200,), anchor="lm")
    hairline(d, CW // 2 - 210 * S, 236 * S, CW // 2 + 210 * S, WHITE, 60)
    hairline(d, CW // 2 - 210 * S, 424 * S, CW // 2 + 210 * S, WHITE, 60)

    tracked(d, CW // 2, 512 * S, "身 体 有 自 己 的 时 刻 表", song(66), tracking=6,
            fill=WHITE + (245,))
    tracked(d, CW // 2, 590 * S, "补觉补不回来的，是节律", hira(25), fill=STEEL + (200,))
    with_glow(img, lay, radius=16, alpha=60)

    brand_block(img, "CIRCADIAN RHYTHM · 24 HOURS")
    save(img, "02_24h身体有自己的时刻表.jpg")


# ─────────────────────────────────────────────────────────────
# 03 人物侧影 · 明星不老的秘密
# ─────────────────────────────────────────────────────────────
def cover_03():
    bg = load_bg("Vertical_3_4_cinematic_beauty__2026-10-24T09-46-45.png", 0)
    img = scrim(bg, top=0.62, bottom=0.92).convert("RGBA")
    img = vignette(img.convert("RGB"), 0.5).convert("RGBA")
    top_label(img, "COSMOBOUNTY  /  逆龄观察", "N° 03")

    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    tracked(d, CW // 2, 902 * S, "他们的作息", song(84), tracking=10, fill=WHITE + (246,))
    tracked(d, CW // 2, 1014 * S, "拆开就四个人人都学得起", song(84), tracking=10, fill=WHITE + (246,))
    tracked(d, CW // 2, 1104 * S, "能 学 的 只 有 一 层 · 还 最 省 钱", hira(25), fill=AMBER + (232,))
    with_glow(img, lay, radius=18, alpha=80)

    brand_block(img, "AGEING WELL · A FIELD NOTE")
    save(img, "03_明星作息拆解.jpg")


# ─────────────────────────────────────────────────────────────
# 04 月相 · 46 亿年
# ─────────────────────────────────────────────────────────────
def cover_04():
    bg = load_bg("Vertical_3_4_abstract_science__2026-10-24T09-46-15.png", 0)
    img = scrim(bg, top=0.82, bottom=0.8).convert("RGBA")
    img = vignette(img.convert("RGB"), 0.5).convert("RGBA")
    img = grain(img.convert("RGB"), 5).convert("RGBA")
    top_label(img, "COSMOBOUNTY  /  时间营养学", "N° 04")

    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    tracked(d, CW // 2, 232 * S, "46 亿年", song(126), tracking=14, fill=WHITE + (248,))
    tracked(d, CW // 2, 352 * S, "身 体 只 学 会 了 一 件 事", song(56), tracking=7,
            fill=WHITE + (238,))
    tracked(d, CW // 2, 424 * S, "按 时 间 活 着", song(56), tracking=7, fill=AMBER + (250,))
    with_glow(img, lay, radius=20, alpha=70)

    brand_block(img, "BE RHYTHM · BE TIMING · BE NATURE")
    save(img, "04_46亿年.jpg")


if __name__ == "__main__":
    cover_01()
    cover_02()
    cover_03()
    cover_04()
