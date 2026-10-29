# -*- coding: utf-8 -*-
"""小红书图文笔记配图生成 · 分时抗衰主题 · 4 张 3:4 (1242x1656)"""
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1242, 1656
BG = (250, 246, 240)        # 米白
INK = (43, 43, 43)
INK2 = (110, 105, 98)
DAY = (232, 134, 46)        # 柿橙（日）
NIGHT = (62, 76, 122)       # 靛蓝（夜）
WHITE = (255, 255, 255)
LINE = (228, 220, 210)

FB = "/System/Library/Fonts/Hiragino Sans GB.ttc"
def F(size, idx=0):
    return ImageFont.truetype(FB, size, index=idx)

def canvas():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # 顶部细色带
    d.rectangle([0, 0, W, 14], fill=DAY)
    d.rectangle([W*2//3, 0, W, 14], fill=NIGHT)
    return img, d

def sun(d, cx, cy, r, color=DAY):
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color)
    import math
    for i in range(8):
        a = i * math.pi / 4
        x1, y1 = cx + (r+14)*math.cos(a), cy + (r+14)*math.sin(a)
        x2, y2 = cx + (r+34)*math.cos(a), cy + (r+34)*math.sin(a)
        d.line([x1, y1, x2, y2], fill=color, width=8)

def moon(d, cx, cy, r, color=NIGHT):
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color)
    d.ellipse([cx-r*0.5-r*0.55, cy-r-r*0.35, cx+r*0.5-r*0.55, cy+r-r*0.35], fill=BG)

def tag(d, x, y, text, fg, bgc, size=34, pad=22):
    f = F(size)
    w = d.textlength(text, font=f)
    d.rounded_rectangle([x, y, x+w+pad*2, y+size+pad+8], radius=16, fill=bgc)
    d.text((x+pad, y+pad//2+2), text, font=f, fill=fg)
    return y + size + pad + 8

def footer(d, page):
    d.line([80, H-120, W-80, H-120], fill=LINE, width=2)
    d.text((80, H-96), "分时抗衰 · 观察笔记", font=F(30), fill=INK2)
    tw = d.textlength(page, font=F(30))
    d.text((W-80-tw, H-96), page, font=F(30), fill=INK2)

os.makedirs("exports/xhs_note_2026-10-24", exist_ok=True)

# ---------- 图1 封面 ----------
img, d = canvas()
d.text((80, 110), "口服抗衰观察 VOL.01", font=F(38), fill=INK2)
d.text((80, 170), "熬夜党自救实录", font=F(56), fill=DAY)
# 大标题
d.text((80, 420), "白天充能", font=F(190), fill=INK)
d.text((80, 660), "晚上修护", font=F(190), fill=INK)
# 中间日月
sun(d, 940, 540, 66)
moon(d, 1080, 880, 60)
# 副标
d.rounded_rectangle([80, 1010, 1162, 1170], radius=28, fill=WHITE, outline=LINE, width=2)
d.text((120, 1050), "口服抗衰界的「早C晚A」", font=F(62), fill=INK)
d.text((120, 1130), "同一批成分，吃对时间，效果差一半", font=F(38), fill=INK2)
# 底部三标签
x = 80
for t, fg, bgc in [("成分党", INK, WHITE), ("熬夜党", INK, WHITE), ("分时抗衰", INK, WHITE)]:
    x = tag(d, x, 1260, "#"+t, fg, bgc) + 24
d.text((80, 1400), "连吃 60 天的交作业帖，评论区问爆的都在这", font=F(40), fill=INK)
footer(d, "1 / 4")
img.save("exports/xhs_note_2026-10-24/01_cover.png")

# ---------- 图2 为什么要分时 ----------
img, d = canvas()
d.text((80, 100), "为什么抗衰要「分时」？", font=F(76), fill=INK)
d.text((80, 205), "先从 2017 年诺奖研究说起", font=F(40), fill=INK2)
d.rounded_rectangle([80, 300, 1162, 420], radius=24, fill=WHITE, outline=LINE, width=2)
d.text((120, 330), "2017 诺贝尔生理学或医学奖：", font=F(40), fill=INK)
d.text((120, 380), "人体细胞自带昼夜节律，白天和晚上干的活不一样", font=F(36), fill=INK2)
# 左卡：白天
d.rounded_rectangle([80, 480, 606, 1080], radius=30, fill=WHITE, outline=LINE, width=2)
sun(d, 343, 590, 52)
d.text((343-72, 680), "白天", font=F(56), fill=INK)
for i, t in enumerate(["细胞在「上班」", "充能 · 制造能量", "防御 · 对抗氧化", "清除 · 处理废料"]):
    d.text((160, 790+i*62), t, font=F(40), fill=INK if i==0 else INK2)
# 右卡：晚上
d.rounded_rectangle([636, 480, 1162, 1080], radius=30, fill=WHITE, outline=LINE, width=2)
moon(d, 899, 590, 48)
d.text((899-72, 680), "晚上", font=F(56), fill=INK)
for i, t in enumerate(["细胞在「检修」", "修护 · 修复损伤", "自噬 · 清理旧件", "整理 · 重塑结构"]):
    d.text((716, 790+i*62), t, font=F(40), fill=INK if i==0 else INK2)
# 底部结论
d.rounded_rectangle([80, 1140, 1162, 1330], radius=28, fill=(255, 243, 230))
d.text((120, 1180), "同一批成分，白天吃和晚上吃，", font=F(46), fill=INK)
d.text((120, 1245), "做的事完全不同——这就是「分时」的底层逻辑", font=F(46), fill=INK)
d.text((80, 1370), "细胞实验佐证：NR 分次给药（日 300mg+夜 100mg）NAD 总量提升 +101.7%，", font=F(30), fill=INK2)
d.text((80, 1415), "同等剂量单次给药仅 +44.2%（实验编号 RSH-S20260022-001，NIH/3T3 模型）", font=F(30), fill=INK2)
footer(d, "2 / 4")
img.save("exports/xhs_note_2026-10-24/02_why.png")

# ---------- 图3 早晚两瓶 ----------
img, d = canvas()
d.text((80, 100), "我的早晚两瓶怎么吃", font=F(76), fill=INK)
d.text((80, 205), "白天瓶管充能，晚上瓶管修护", font=F(40), fill=INK2)
# 日活瓶
d.rounded_rectangle([80, 320, 1162, 880], radius=30, fill=WHITE, outline=LINE, width=2)
sun(d, 170, 400, 34)
d.text((240, 360), "日活瓶 CytoVigor", font=F(56), fill=INK)
d.text((240, 440), "早上随餐 2 粒", font=F(40), fill=DAY)
yy = 530
for name, desc in [("NR 烟酰胺核糖", "NAD 通路原料，日间充能"),
                   ("PQQ", "线粒体新生，拉高 ATP"),
                   ("麦角硫因 99.9%", "细胞抗氧化护卫"),
                   ("非瑟酮+脂质体槲皮素", "清除衰老细胞（Senolytics）")]:
    d.text((140, yy), name, font=F(40), fill=INK)
    d.text((660, yy+6), desc, font=F(32), fill=INK2)
    yy += 78
# 夜修瓶
d.rounded_rectangle([80, 930, 1162, 1490], radius=30, fill=WHITE, outline=LINE, width=2)
moon(d, 170, 1010, 30)
d.text((240, 970), "夜修瓶 CytoRenova", font=F(56), fill=INK)
d.text((240, 1050), "晚饭后 1 粒（睡前 2 小时）", font=F(40), fill=NIGHT)
yy = 1140
for name, desc in [("环黄芪醇", "激活端粒酶 TERT，上调长寿蛋白"),
                   ("南非醉茄 KSM-66", "调节皮质醇，助眠"),
                   ("亚精胺 + 尿石素 A", "激活细胞自噬，清废料"),
                   ("双甘氨酸镁", "放松神经，睡得更深")]:
    d.text((140, yy), name, font=F(40), fill=INK)
    d.text((660, yy+6), desc, font=F(32), fill=INK2)
    yy += 78
d.text((80, 1516), "细胞联用实验：日+夜联用抗衰效果为单用的 1.2~1.6 倍", font=F(30), fill=INK2)
footer(d, "3 / 4")
img.save("exports/xhs_note_2026-10-24/03_products.png")

# ---------- 图4 体感时间线+说实话 ----------
img, d = canvas()
d.text((80, 100), "60 天体感时间线", font=F(76), fill=INK)
d.text((80, 205), "以及几句实话", font=F(40), fill=INK2)
# 时间线
steps = [("第 1 周", "入睡变快，晨起更清醒"),
         ("第 2-4 周", "皮肤光泽改善，没那么干"),
         ("第 4-8 周", "弹性与轮廓感"),
         ("第 8-12 周", "整体状态年轻化")]
y = 330
for i, (t, desc) in enumerate(steps):
    d.ellipse([100, y+18, 148, y+66], fill=DAY if i % 2 == 0 else NIGHT)
    d.text((190, y), t, font=F(48), fill=INK)
    d.text((420, y+12), desc, font=F(38), fill=INK2)
    if i < 3:
        d.line([124, y+70, 124, y+130], fill=LINE, width=4)
    y += 140
d.rounded_rectangle([80, 900, 1162, 1040], radius=24, fill=WHITE, outline=LINE, width=2)
d.text((120, 930), "数据出处：中南大学湘雅医院 30 人 28 天检测", font=F(38), fill=INK)
d.text((120, 982), "睡眠问题指数 -50.8% ｜ 皮肤水润度 +39.3% ｜ 弹性 +36.1%", font=F(32), fill=INK2)
# 说实话
d.text((80, 1090), "几句实话（重要）", font=F(52), fill=INK)
notes = ["它是膳食补充剂，不能代替药品，不以治疗疾病为目的",
         "孕期哺乳期、有基础疾病请先遵医嘱",
         "别指望仙丹——作息烂了，什么补剂都白搭",
         "它只是把熬夜的损失兜住一部分，早睡才是yyds"]
y = 1180
for n in notes:
    d.rounded_rectangle([80, y, 1162, y+82], radius=20, fill=WHITE)
    d.text((120, y+20), "· " + n, font=F(36), fill=INK)
    y += 92
footer(d, "4 / 4")
img.save("exports/xhs_note_2026-10-24/04_timeline.png")
print("done")
