# -*- coding: utf-8 -*-
"""小红书图文配图 v2 · 实拍图 + 排版卡混合 · 修重叠 bug（v1 备份见 make_xhs_note_images_v1.py.bak）"""
from PIL import Image, ImageDraw, ImageFont
import os, math

W, H = 1242, 1656
BG = (250, 246, 240)
INK = (43, 43, 43)
INK2 = (110, 105, 98)
DAY = (232, 134, 46)
NIGHT = (62, 76, 122)
WHITE = (255, 255, 255)
LINE = (228, 220, 210)
CREAM_CARD = (255, 251, 246)

FB = "/System/Library/Fonts/Hiragino Sans GB.ttc"
def F(size, bold=False):
    return ImageFont.truetype(FB, size, index=1 if bold else 0)

MEDIA = "knowledge/media/COSMOBOUNTY/post/"
OUT = "exports/xhs_note_2026-10-24/"
os.makedirs(OUT, exist_ok=True)

def canvas():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=DAY)
    d.rectangle([W*2//3, 0, W, 14], fill=NIGHT)
    return img, d

def crop_fill(path, box, w, h):
    im = Image.open(path).convert("RGB").crop(box)
    sw, sh = im.size
    scale = max(w/sw, h/sh)
    nw, nh = int(sw*scale+0.5), int(sh*scale+0.5)
    im = im.resize((nw, nh), Image.LANCZOS)
    x, y = (nw-w)//2, (nh-h)//2
    return im.crop((x, y, x+w, y+h))

def rounded_photo(img, d, path, box, x, y, w, h, radius=28, outline=LINE):
    ph = crop_fill(path, box, w, h)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    img.paste(ph, (x, y), mask)
    d.rounded_rectangle([x, y, x+w, y+h], radius=radius, outline=outline, width=2)

def sun(d, cx, cy, r, color=DAY):
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color)
    for i in range(8):
        a = i*math.pi/4
        d.line([cx+(r+14)*math.cos(a), cy+(r+14)*math.sin(a),
                cx+(r+34)*math.cos(a), cy+(r+34)*math.sin(a)], fill=color, width=8)

def moon(d, cx, cy, r, color=NIGHT, bg=BG):
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color)
    d.ellipse([cx-r*1.05-r*0.5, cy-r-r*0.35, cx-r*0.05-r*0.5, cy+r-r*0.35], fill=bg)

def tag(d, x, y, text, size=34, pad=22):
    f = F(size)
    w = d.textlength(text, font=f)
    d.rounded_rectangle([x, y, x+w+pad*2, y+size+pad+8], radius=16, fill=WHITE, outline=LINE, width=2)
    d.text((x+pad, y+pad//2+2), text, font=f, fill=INK)
    return x + w + pad*2 + 24

def footer(d, page):
    d.line([80, H-110, W-80, H-110], fill=LINE, width=2)
    d.text((80, H-84), "分时抗衰 · 观察笔记", font=F(30), fill=INK2)
    tw = d.textlength(page, font=F(30))
    d.text((W-80-tw, H-84), page, font=F(30), fill=INK2)

P_GIFT = MEDIA + "xhs:6acda430000000001c01d360.jpg"   # 礼盒开箱（右上象限，无人脸）
P_FLAT = MEDIA + "xhs:6ad88a230000000032015c49.jpg"   # 瓶+盒摆拍（右上象限，无人脸）

# ---------- 图1 封面：实拍在上，大字在下 ----------
img, d = canvas()
rounded_photo(img, d, P_GIFT, (540, 0, 1080, 720), 80, 90, W-160, 780, radius=32)
d.text((80, 930), "白天充能", font=F(150, bold=True), fill=INK)
sun(d, 950, 1010, 52)
d.text((80, 1120), "晚上修护", font=F(150, bold=True), fill=INK)
moon(d, 1010, 1215, 46)
d.rounded_rectangle([80, 1330, W-80, 1450], radius=24, fill=CREAM_CARD, outline=LINE, width=2)
d.text((120, 1358), "口服抗衰界的「早C晚A」· 连吃 60 天交作业", font=F(44, bold=True), fill=DAY)
x = 80
for t in ["#成分党", "#熬夜自救", "#分时抗衰"]:
    x = tag(d, x, 1480, t)
footer(d, "1 / 4")
img.save(OUT + "01_cover.png")

# ---------- 图2 为什么要分时 ----------
img, d = canvas()
d.text((80, 96), "为什么抗衰要「分时」？", font=F(76, bold=True), fill=INK)
d.text((80, 208), "先从 2017 年诺奖研究说起", font=F(40), fill=INK2)
d.rounded_rectangle([80, 300, W-80, 428], radius=24, fill=WHITE, outline=LINE, width=2)
d.text((120, 330), "2017 诺贝尔生理学或医学奖：", font=F(40, bold=True), fill=INK)
d.text((120, 382), "人体细胞自带昼夜节律，白天和晚上干的活不一样", font=F(34), fill=INK2)
for x0, accent, icon, head, rows in [
    (80, DAY, "sun", "白天", ["细胞在「上班」", "充能 · 制造能量", "防御 · 对抗氧化", "清除 · 处理废料"]),
    (636, NIGHT, "moon", "晚上", ["细胞在「检修」", "修护 · 修复损伤", "自噬 · 清理旧件", "整理 · 重塑结构"]),
]:
    d.rounded_rectangle([x0, 480, x0+526, 1090], radius=30, fill=WHITE, outline=LINE, width=2)
    cx = x0 + 263
    if icon == "sun":
        sun(d, cx, 590, 50, accent)
    else:
        moon(d, cx, 590, 46, accent, bg=WHITE)
    tw = d.textlength(head, font=F(54, bold=True))
    d.text((cx - tw/2, 680), head, font=F(54, bold=True), fill=INK)
    for i, t in enumerate(rows):
        col = INK if i == 0 else INK2
        f = F(40, bold=(i == 0))
        tw2 = d.textlength(t, font=f)
        d.text((cx - tw2/2, 790 + i*66), t, font=f, fill=col)
d.rounded_rectangle([80, 1150, W-80, 1340], radius=28, fill=(255, 243, 230))
d.text((120, 1188), "同一批成分，白天吃和晚上吃，做的事完全不同", font=F(44, bold=True), fill=INK)
d.text((120, 1256), "——这就是「分时」的底层逻辑", font=F(40), fill=INK)
d.text((80, 1390), "细胞实验佐证：NR 分次给药（日300mg+夜100mg）NAD 总量 +101.7%，", font=F(29), fill=INK2)
d.text((80, 1432), "单次给药仅 +44.2%（编号 RSH-S20260022-001 · NIH/3T3 模型）", font=F(29), fill=INK2)
footer(d, "2 / 4")
img.save(OUT + "02_why.png")

# ---------- 图3 早晚两瓶：实拍 + 成分卡 ----------
img, d = canvas()
d.text((80, 96), "我的早晚两瓶怎么吃", font=F(76, bold=True), fill=INK)
d.text((80, 208), "白天瓶管充能，晚上瓶管修护", font=F(40), fill=INK2)
rounded_photo(img, d, P_FLAT, (540, 0, 1080, 720), 80, 290, W-160, 560, radius=30)
d.rounded_rectangle([80, 900, 606, 1400], radius=28, fill=WHITE, outline=LINE, width=2)
sun(d, 150, 968, 28)
d.text((205, 938), "日活瓶 · 早2粒随餐", font=F(43, bold=True), fill=INK)
yy = 1040
for name, desc in [("NR", "NAD 通路原料"), ("PQQ", "线粒体新生"),
                   ("麦角硫因", "抗氧化护卫"), ("非瑟酮+槲皮素", "清除衰老细胞")]:
    d.text((130, yy), name, font=F(38, bold=True), fill=INK)
    d.text((130, yy+42), desc, font=F(29), fill=INK2)
    yy += 96
d.rounded_rectangle([636, 900, 1162, 1400], radius=28, fill=WHITE, outline=LINE, width=2)
moon(d, 706, 968, 25, bg=BG)
d.text((760, 938), "夜修瓶 · 睡前2小时1粒", font=F(42, bold=True), fill=INK)
yy = 1040
for name, desc in [("环黄芪醇", "激活端粒酶 TERT"), ("南非醉茄", "调节皮质醇助眠"),
                   ("亚精胺+尿石素A", "激活细胞自噬"), ("双甘氨酸镁", "放松神经睡得深")]:
    d.text((686, yy), name, font=F(38, bold=True), fill=INK)
    d.text((686, yy+42), desc, font=F(29), fill=INK2)
    yy += 96
d.text((80, 1448), "细胞联用实验：日+夜联用，抗衰效果为单用的 1.2~1.6 倍", font=F(30), fill=INK2)
footer(d, "3 / 4")
img.save(OUT + "03_products.png")

# ---------- 图4 体感时间线：描述 x 随标题宽度动态定位，杜绝重叠 ----------
img, d = canvas()
d.text((80, 96), "60 天体感时间线", font=F(76, bold=True), fill=INK)
d.text((80, 208), "以及几句实话", font=F(40), fill=INK2)
steps = [("第 1 周", "入睡变快，晨起更清醒"),
         ("第 2-4 周", "皮肤光泽改善，没那么干"),
         ("第 4-8 周", "弹性与轮廓感"),
         ("第 8-12 周", "整体状态年轻化")]
f_label = F(46, bold=True)
label_w = max(d.textlength(t, font=f_label) for t, _ in steps)
desc_x = int(190 + label_w + 36)
y = 320
for i, (t, desc) in enumerate(steps):
    d.ellipse([100, y+18, 148, y+66], fill=DAY if i % 2 == 0 else NIGHT)
    d.text((190, y), t, font=f_label, fill=INK)
    d.text((desc_x, y+12), desc, font=F(38), fill=INK2)
    if i < 3:
        d.line([124, y+72, 124, y+134], fill=LINE, width=4)
    y += 142
d.rounded_rectangle([80, 920, W-80, 1064], radius=24, fill=WHITE, outline=LINE, width=2)
d.text((120, 950), "数据出处：中南大学湘雅医院 30 人 28 天检测", font=F(38, bold=True), fill=INK)
d.text((120, 1004), "睡眠问题指数 -50.8% ｜ 皮肤水润度 +39.3% ｜ 弹性 +36.1%", font=F(32), fill=INK2)
d.text((80, 1110), "几句实话（重要）", font=F(52, bold=True), fill=INK)
notes = ["它是膳食补充剂，不能代替药品，不以治疗疾病为目的",
         "孕期哺乳期、有基础疾病请先遵医嘱",
         "别指望仙丹——作息烂了，什么补剂都白搭",
         "它只是把熬夜的损失兜住一部分，早睡才是yyds"]
y = 1200
for n in notes:
    d.rounded_rectangle([80, y, W-80, y+82], radius=20, fill=WHITE)
    d.text((120, y+20), "· " + n, font=F(36), fill=INK)
    y += 92
footer(d, "4 / 4")
img.save(OUT + "04_timeline.png")
print("v2 done")
