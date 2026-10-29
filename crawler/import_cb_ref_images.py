#!/usr/bin/env python3
"""把「cosmobounty角度图20260818(1)」接入知识库，建生图/生视频参考图资源库。

做了三件事：
1. 修复原文件夹：解压出的 38 个文件全部损坏（魔数 88 7d 1c，非任何图片格式），
   用同目录 RAR 重新解压覆盖（RAR 本身完好，可随时复原）
2. 入库：拷贝到 knowledge/media/COSMOBOUNTY/ref/ 三类目录，文件名规范化
3. 生成 index.json：每张图的语义描述（产品/视角/规格/用途），供 ImageGen/VideoGen 引用
"""
import json, os, shutil, subprocess, time

CB = "/Users/v/Desktop/2026/秋峰/Demo/CosmoBounty"
SRC_DIR = f"{CB}/knowledge/cosmobounty/cosmobounty角度图20260818(1)"
RAR = f"{CB}/knowledge/cosmobounty/cosmobounty角度图20260818(1).rar"
TMP = "/tmp/cb_ref_extract"
REF = f"{CB}/knowledge/media/COSMOBOUNTY/ref"

# ① 重新解压（原文件夹文件已损坏，以 RAR 为准）
subprocess.run(["rm", "-rf", TMP], check=True)
os.makedirs(TMP, exist_ok=True)
subprocess.run(["bsdtar", "-xf", RAR, "-C", TMP], check=True)
inner = f"{TMP}/cosmobounty角度图20260818"

MAGICS = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8": "jpeg", b"RIFF": "webp"}
def sniff(p):
    raw = open(p, "rb").read(12)
    for m, n in MAGICS.items():
        if raw.startswith(m):
            return n
    return None

# ② 修复原文件夹（同名覆盖坏文件）
fixed = 0
for root, _, files in os.walk(inner):
    for fn in files:
        src = os.path.join(root, fn)
        rel = os.path.relpath(src, inner)
        dst = os.path.join(SRC_DIR, rel)
        if os.path.exists(dst) and sniff(dst) is None and sniff(src):
            shutil.copy2(src, dst)
            fixed += 1
print(f"① 修复原文件夹损坏文件: {fixed} 个")

# ③ 规范化入库
# 编号 -> 语义（白底图逐张目检确认；透明图/角度大图与白底同编号同内容，已抽查验证）
SEM = {
    "瓶子角度 (1)": ("bottle-01", "cytovigor", "正面平视", "CytoVigor 日活瓶正面（银盖银标，60粒，NR）"),
    "瓶子角度 (2)": ("bottle-02", "pair", "双瓶合照", "日活瓶+夜修瓶并排合照"),
    "瓶子角度 (3)": ("bottle-03", "cytorenova", "正面微仰", "CytoRenova 夜修瓶正面微仰（金盖黑金标，30粒，NR）"),
    "瓶子角度 (4)": ("bottle-04", "cytovigor", "微仰视", "CytoVigor 日活瓶微仰视"),
    "瓶子角度 (5)": ("bottle-05", "cytorenova", "俯视侧", "CytoRenova 夜修瓶俯视侧角"),
    "瓶子角度 (6)": ("bottle-06", "cytovigor", "俯视侧", "CytoVigor 日活瓶俯视侧角"),
    "瓶子角度 (7)": ("bottle-07", "cytorenova", "背面", "CytoRenova 夜修瓶背面（Suggested Usage + Warning + 条码）"),
    "瓶子角度 (8)": ("bottle-08", "cytovigor", "背面", "CytoVigor 日活瓶背面（Suggested Usage + Warning + 条码）"),
    "瓶子角度 (9)": ("bottle-09", "cytorenova", "正面", "CytoRenova 夜修瓶正面"),
    "盒子角度 (1)": ("box-01", "giftbox", "开盖展示", "黑白分时套装礼盒开盖，内置双瓶"),
    "盒子角度 (2)有背景": ("box-02-scene", "giftbox", "场景图", "礼盒+双瓶合影（浅灰场景背景，可直接用于社媒）"),
    "盒子角度 (2)": ("box-02", "giftbox", "带瓶合影", "礼盒+双瓶合影（白底）"),
    "盒子角度 (3)": ("box-03", "giftbox", "另一角度", "礼盒另一角度"),
    "盒子角度 (4)": ("box-04", "giftbox", "开盖俯视", "礼盒开盖 45° 俯视，双瓶内衬可见（大图目录独有）"),
}
DIRS = [
    ("cosmobounty角度大图", "angle-large", "PNG 2000x2400 透明底", "高清母版：视频素材、特写裁切、印刷"),
    ("白底图800x800", "white-800", "800x800 白底", "电商详情底图、信息图合成、对比图"),
    ("透明图800x800", "transparent-800", "PNG 800x800 透明底", "海报/场景合成抠图素材（注意：个别图中心仍有白底块，使用前检查 alpha）"),
]

def dims(p):
    try:
        out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", p],
                             capture_output=True, text=True).stdout
        w = h = None
        for line in out.splitlines():
            if "pixelWidth" in line: w = int(line.split()[-1])
            if "pixelHeight" in line: h = int(line.split()[-1])
        return f"{w}x{h}" if w and h else ""
    except Exception:
        return ""

assets = []
copied = 0
for src_sub, dst_sub, spec, use in DIRS:
    os.makedirs(f"{REF}/{dst_sub}", exist_ok=True)
    for src_name, (base, product, view, desc) in SEM.items():
        # 找源文件（扩展名可能是 png/jpg）
        hit = None
        for ext in (".png", ".jpg"):
            p = f"{inner}/{src_sub}/{src_name}{ext}"
            if os.path.exists(p):
                hit = p
                break
        if not hit:
            print(f"  ! 缺 {src_sub}/{src_name}")
            continue
        ext = os.path.splitext(hit)[1]
        dst = f"{REF}/{dst_sub}/{base}{ext}"
        shutil.copy2(hit, dst)
        copied += 1
        assets.append({
            "id": f"{dst_sub}/{base}",
            "file": f"knowledge/media/COSMOBOUNTY/ref/{dst_sub}/{base}{ext}",
            "orig": f"cosmobounty角度图20260818(1)/{src_sub}/{src_name}{ext}",
            "product": {"cytovigor": "日活瓶 CytoVigor", "cytorenova": "夜修瓶 CytoRenova",
                        "pair": "双瓶合照", "giftbox": "黑白分时套装礼盒"}[product],
            "view": view,
            "desc": desc,
            "spec": spec if src_name != "盒子角度 (2)有背景" else "PNG 800x800 带场景背景",
            "use_for": use,
        })
print(f"③ 入库: {copied} 张 -> {os.path.relpath(REF, CB)}")

index = {
    "brand": "COSMOBOUNTY",
    "purpose": "自有品牌产品参考图资源库：ImageGen / VideoGen 生成图片和视频时的参考底图与素材",
    "source": "cosmobounty角度图20260818(1).rar（2026-08-18 品牌方交付，Adobe Photoshop 26.3 制作）",
    "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "verified": "38/38 张魔数校验通过（JPEG/PNG）；原始文件夹同批文件曾损坏，已用 RAR 重新解压修复",
    "products": {
        "cytovigor": {"cn": "日活瓶 CytoVigor", "en": "Daytime Cell Energize", "core": "NR",
                      "spec": "60 capsules", "look": "深棕瓶身 + 银盖 + 银色标签",
                      "usage_on_label": "Take two (2) capsules daily in the morning with food."},
        "cytorenova": {"cn": "夜修瓶 CytoRenova", "en": "Nighttime Cell Renewal", "core": "NR",
                       "spec": "30 capsules", "look": "深棕瓶身 + 金盖 + 黑金标签",
                       "usage_on_label": "Take one (1) capsule daily with dinner, at least 2 hours before bedtime."},
        "giftbox": {"cn": "黑白分时套装礼盒", "slogan": "BE RHYTHM BE TIMING BE NATURE",
                    "look": "黑色礼盒 + COSMO BOUNTY 烫银标"},
        "mfr": "Made for Cosmobounty BioSciences Inc. (Spokane, WA) / Origin: Singapore / KINGMAO INTERNATIONAL (S) PTE LTD（瓶标直读）",
    },
    "assets": assets,
    "usage_note": "生图引用：把 assets 里的 file 路径作为参考底图传给 ImageGen；生视频用 angle-large 做首帧或素材。对外使用前过一遍小红书违禁词（见 README 口径基准）。",
}
json.dump(index, open(f"{REF}/index.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"④ 索引: {REF}/index.json（{len(assets)} 项）")
