#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
品牌资产采集：logo / 产品主图 / 产品档案（规格·成分·服用方式·价格·来源）

为什么要单独一个脚本：
  工作台「产品」那一层此前只有内容侧词频反推，规格/成分/价格/官方图四格全空。
  这些字段不能猜——必须落到具体来源页。所以这里按「来源适配器」抓：
    · mitoq   官网 mitoqcn.com 产品列表 + 逐个产品详情页（规格、成分、「X 的成分？」问答）
    · granver 官网 granverus.com/?id=N 产品页（页面自带「品名/规格/保质期/成分/服用方式」表）
    · shopify 品牌独立站 products.json（LAIFE 美国站：图 + 描述 + 美元价）
  抓不到的结构化来源（塞乐瑞、W+端粒塔、自有品牌）走 config/products_manual.json 人工登记，
  每条必须写 source 与 evidence（official=官方页 / media=公开报道 / content=内容库实拍 / doc=品牌资料）。

输出：
  config/products.json                     全量产品档案（工作台唯一读取源）
  knowledge/media/<brand>/logo/logo.<ext>
  knowledge/media/<brand>/product/<key>.<ext>
  workbench/v4/products.js                 PRODUCTS 常量，供前端直接渲染

用法：
  python crawler/fetch_product_catalog.py                # 全量（含下载）
  python crawler/fetch_product_catalog.py --no-download  # 只抓字段，不动图
  python crawler/fetch_product_catalog.py --only MitoQ
"""
import argparse
import base64
import copy
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote as _urlquote

CB = "/Users/v/Desktop/2026/秋峰/Demo/CosmoBounty"
MEDIA = f"{CB}/knowledge/media"
OUT_JSON = f"{CB}/config/products.json"
MANUAL = f"{CB}/config/products_manual.json"
JS_OUT = f"{CB}/workbench/v4/products.js"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BRANDS = ["MitoQ", "LAIFE", "GRANVER", "塞乐瑞", "W+端粒塔", "COSMOBOUNTY"]

# 官方 logo 地址（都是各站页面里实际引用的资源，不是猜的）
LOGO_URL = {
    "MitoQ": "https://mitoq-website.oss-cn-beijing.aliyuncs.com/mitoq/mitoq_logo_new2.png",
    "GRANVER": "http://www.granverus.com/zb_users/upload/2024/11/202411131731461518422955.png",
    "LAIFE": "https://cdn.shopify.com/s/files/1/0590/2571/9369/files/LOGO_slogan-06.png?v=1698832364&width=900",
}


def log(*a):
    print(*a, flush=True)


def http_get(url, timeout=30, retries=3, referer=None):
    # Shopify 等 CDN 的图片地址带 ™/中文，必须按路径百分号编码，否则 latin-1 直接炸
    url = _urlquote(url, safe=":/?#[]@!$&'()*+,;=%")
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/json,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        # referer 也可能是带 ™/中文的商品页，同样要编码
        headers["Referer"] = _urlquote(referer, safe=":/?&=%")
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def text_of(raw):
    return raw.decode("utf-8", "ignore")


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def norm_key(brand, name):
    """产品目录里的文件名：品牌内唯一即可，保留可读性"""
    k = re.sub(r"[^\w\u4e00-\u9fa5+-]+", "-", name).strip("-").lower()
    return k or "item"


# ---------------------------------------------------------------- 适配器

def fetch_mitoq():
    """官网产品列表页给出「图 + 名 + 一句卖点 + 详情链接」，详情页给出规格与成分"""
    url = "https://www.mitoqcn.com/product/"
    page = text_of(http_get(url))
    card_re = re.compile(
        r'<img src="(?P<img>[^"]+)" class="img"[^>]*>\s*'
        r'<div class="infos"[^>]*>\s*<div class="txt1"[^>]*>(?P<t1>.*?)</div>\s*'
        r'<div class="txt2"[^>]*>(?P<t2>.*?)</div>\s*'
        r'<a href="(?P<href>/product-detail/[^"]+)"',
        re.S)
    out, seen = [], set()
    for m in card_re.finditer(page):
        name = strip_tags(m.group("t1"))
        name = re.sub(r"^MitoQ\s*", "", name)
        name = re.sub(r"^[\s+·.\-]+", "", name).strip()   # 「MitoQ + 骨赋能胶囊」只剩产品名
        href = m.group("href")
        if not name or href in seen:
            continue
        seen.add(href)
        img = m.group("img")
        if img.startswith("/"):
            img = "https://www.mitoqcn.com" + img
        out.append({
            "name": name,
            "desc": strip_tags(m.group("t2")),
            "img_url": img,
            "url": "https://www.mitoqcn.com" + href,
            "source": "official",
        })
    log(f"  MitoQ 列表命中 {len(out)} 个产品，逐个取规格/成分…")
    for p in out:
        try:
            d = text_of(http_get(p["url"], referer=url))
            body = re.sub(r"<(script|style)[\s\S]*?</\1>", "", d)
            flat = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", body)))
            # 规格：老款写「30天 (60粒)」，新款只写粒数或干脆不写
            m = re.search(r"\d{1,3}\s*天\s*[（(]?\s*\d{1,3}\s*粒\s*[)）]?", flat) or \
                re.search(r"\d{1,3}\s*粒\s*/\s*瓶", flat) or \
                re.search(r"\d{1,3}\s*粒\s*[（(]\s*\d{1,3}\s*天", flat)
            if m:
                p["spec"] = m.group(0).strip()
            # 服用建议：「每日清晨空腹两粒」「每天清晨定时服用」
            md = re.search(r"(每日[^。！？]{2,30}[。！？])", flat) or \
                re.search(r"(每[天日][^。！？]{2,30}[。！？])", flat)
            if md:
                p["dosage"] = md.group(1).strip()
            # 成分：页面的「关于 X 的成分？」问答；新款无此问答时退回卖点句里的「有效成分…」
            mi = re.search(r"的成分？\s*([^。]{5,300}。)", flat)
            if not mi:
                mi = re.search(r"有效成分(.{4,90}?)，", flat)
                if mi:
                    p["ingredients"] = mi.group(1).strip()
            else:
                p["ingredients"] = mi.group(1).replace("同时每粒胶囊含有", "；每粒另含").strip()
            mm = re.search(r"注意事项\s*([^。]{4,120}。)", flat)
            if mm:
                p["notice"] = mm.group(1).strip()
            p["title"] = (re.findall(r"<title>(.*?)</title>", d) or [""])[0].strip()
        except Exception as e:  # noqa: BLE001
            log(f"    ! {p['name']} 详情失败 {e}")
        time.sleep(0.6)
    return out


def fetch_granver():
    """官网产品页正文是一张「品名/规格/保质期/成分/服用方式」表，正则直接摘"""
    ids = [5, 6, 7, 8, 12]
    out = []
    for i in ids:
        url = f"http://www.granverus.com/?id={i}"
        try:
            d = text_of(http_get(url, referer="http://www.granverus.com/"))
        except Exception as e:  # noqa: BLE001
            log(f"    ! granver id={i} 失败 {e}")
            continue
        title = (re.findall(r"<title>(.*?)</title>", d) or [""])[0]
        body = re.sub(r"<(script|style)[\s\S]*?</\1>", "", d)
        # 页面正文被模板拆成一行行，个别页还会把「保质期」和「成分」挤在同一行，
        # 所以统一拍平成一段再按标签切。
        flat = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", body)))
        flat = flat[flat.find("正文"):] if "正文" in flat else flat

        def between(labels, stops):
            for lb in labels:
                m = re.search(lb + r"\s*[:：]\s*(.+?)(?=" + "|".join(stops) + r"|$)", flat)
                if m:
                    v = re.sub(r"\s+", " ", m.group(1)).strip(" ，,。")
                    if v:
                        return v
            return ""

        STOP = ["品\\s*名", "规\\s*格", "保\\s*质\\s*期", "成\\s*分", "服用方式", "存储方式", "温馨提示", "上一篇"]
        name_full = between(["品\\s*名"], STOP)
        # 吉返盾页把「2 年」和「成分:」黏在一行，用独立正则兜一次
        ing = between(["成\\s*分"], STOP)
        if not ing:
            m = re.search(r"成分\s*[:：]\s*(.+?)(?:服用方式|存储方式|温馨提示|上一篇|$)", flat)
            ing = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

        imgs = [u for u in re.findall(r'<img[^>]+src="([^"]+)"', d)]
        imgs = [u if u.startswith("http") else "http://www.granverus.com" + u for u in imgs]
        # 产品图：upload 目录里、排除主题背景
        pick_imgs = [u for u in imgs if "/zb_users/upload/" in u and not u.endswith("list_bj.jpg")]
        name = re.sub(r"^GRANVER\s*", "", title.split("_")[0]).strip()
        out.append({
            "name": name or name_full,
            "full_name": name_full,
            "spec": between(["规\\s*格"], STOP),
            "shelf_life": (re.search(r"保\s*质\s*期\s*[:：]?\s*([0-9]{1,2}\s*年)", flat) or [None, ""])[1]
            if re.search(r"保\s*质\s*期", flat) else "",
            "ingredients": ing,
            "dosage": between(["服用方式"], STOP),
            "notice": between(["温馨提示"], STOP),
            "img_url": pick_imgs[-1] if pick_imgs else "",
            "img_all": pick_imgs,
            "url": url,
            "source": "official",
        })
        log(f"    GRANVER {out[-1]['name']} | {out[-1]['spec']} | {(out[-1]['ingredients'] or '')[:30]}")
        time.sleep(0.5)
    return out


def fetch_laife():
    """Shopify 独立站 products.json：图、描述、美元价一次拿全"""
    url = "https://www.laife.us/products.json?limit=250"
    data = json.loads(text_of(http_get(url)))
    out = []
    SKIP = ("Test-LTS", "Screening", "Ultrasound", "Check-up Package", "Routine + TCT")
    for p in data.get("products", []):
        title = p["title"].strip()
        # 独立站里混了体检/筛查类服务（-LTS 结尾），不是产品，别进产品目录
        if title.endswith("-LTS") or any(k in title for k in SKIP):
            continue
        desc = strip_tags(p.get("body_html") or "")
        imgs = [i["src"] for i in p.get("images", [])]
        v = (p.get("variants") or [{}])[0]
        out.append({
            "name": title,
            "handle": p.get("handle"),
            "spec": "",
            "ingredients": "",
            "desc": desc[:400],
            "price_usd": float(v["price"]) if v.get("price") else None,
            "img_url": imgs[0] if imgs else "",
            "img_all": imgs[:4],
            "url": f"https://www.laife.us/products/{p.get('handle')}",
            "source": "official",
        })
    log(f"  LAIFE 独立站 {len(out)} 个 SKU")
    return out


ADAPTERS = {"MitoQ": fetch_mitoq, "GRANVER": fetch_granver, "LAIFE": fetch_laife}

# ---------------------------------------------------------------- 下载

def ext_of(url, raw=b""):
    u = url.split("?")[0].lower()
    for e in (".png", ".jpg", ".jpeg", ".webp", ".avif"):
        if u.endswith(e):
            return ".jpg" if e == ".jpeg" else e
    if raw[:4] == b"\x89PNG":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def save_img(url, path_noext, referer=None):
    raw = http_get(url, referer=referer)
    if len(raw) < 2000:
        raise ValueError(f"too small {len(raw)}")
    ext = ext_of(url, raw)
    path = path_noext + ext
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)
    return path


def rel(path):
    return os.path.relpath(path, CB)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    manual = {}
    if os.path.exists(MANUAL):
        manual = json.load(open(MANUAL, encoding="utf-8"))

    log("① 抓官网结构化产品数据")
    crawled = {}
    for brand, fn in ADAPTERS.items():
        if only and brand not in only:
            continue
        try:
            crawled[brand] = fn()
        except Exception as e:  # noqa: BLE001
            log(f"  ! {brand} 适配器失败：{e}")
            crawled[brand] = []

    # ② 合并：官网抓的 + 人工登记的（人工条目补价格、中文名、缺失品牌）
    log("② 合并人工登记")
    catalog = {}
    for brand in BRANDS:
        items = []
        for it in crawled.get(brand, []):
            it["key"] = norm_key(brand, it.get("name") or it.get("full_name") or "item")
            items.append(it)
        for it in (manual.get(brand) or {}).get("products", []):
            it = dict(it)
            it["key"] = it.get("key") or norm_key(brand, it["name"])
            # 人工条目怎么和官网条目对上：先按 key/同名，再按手动写的 match 串（英文名片段）
            same = next((x for x in items if x["key"] == it["key"]), None)
            if not same:
                same = next((x for x in items if x.get("name") == it["name"]), None)
            if not same and it.get("match"):
                mt = it["match"].lower()
                same = next((x for x in items
                             if mt in (x.get("name") or "").lower() or mt in (x.get("handle") or "").lower()), None)
            if same:
                for k, v in it.items():
                    if k == "match":
                        continue
                    if v not in (None, "", []) or k not in same:
                        same[k] = v
            else:
                items.append(it)
        if items:
            catalog[brand] = items

    # ③ logo（防盗链的站带上自家 referer）
    log("③ 下载 logo")
    LOGO_REFERER = {"MitoQ": "https://www.mitoqcn.com/", "LAIFE": "https://www.laife.us/",
                    "GRANVER": "http://www.granverus.com/"}
    if not args.no_download:
        for brand, url in LOGO_URL.items():
            if only and brand not in only:
                continue
            try:
                p = save_img(url, f"{MEDIA}/{brand}/logo/logo", referer=LOGO_REFERER.get(brand))
                log(f"  ✓ {brand} {rel(p)}")
                catalog.setdefault(brand, [])
            except Exception as e:  # noqa: BLE001
                log(f"  ! {brand} logo 失败 {e}")

    # ④ 产品主图
    log("④ 产品主图")
    if True:
        for brand, items in catalog.items():
            for it in items:
                if it.get("img") and os.path.exists(f"{CB}/{it['img']}"):
                    continue
                if it.get("local_img"):
                    p = f"{CB}/{it['local_img']}"
                    if os.path.exists(p):
                        it["img"] = it["local_img"]
                        continue
                if args.no_download:
                    # 只做本地映射，不真正下载：已下载过的按文件名对回去
                    for e in (".png", ".jpg", ".webp"):
                        pth = f"{MEDIA}/{brand}/product/{it['key']}{e}"
                        if os.path.exists(pth):
                            it["img"] = rel(pth)
                            break
                    continue
                url = it.get("img_url")
                if not url:
                    continue
                try:
                    p = save_img(url, f"{MEDIA}/{brand}/product/{it['key']}", referer=it.get("url"))
                    it["img"] = rel(p)
                    log(f"  ✓ {brand}/{it['key']} {os.path.getsize(p)//1024}KB")
                except Exception as e:  # noqa: BLE001
                    log(f"  ! {brand}/{it['key']} {e}")
                time.sleep(0.4)

    # ⑤ 落盘
    logos = {}
    for b in BRANDS:
        for e in (".png", ".jpg", ".webp"):
            pth = f"{MEDIA}/{b}/logo/logo{e}"
            if os.path.exists(pth):
                logos[b] = rel(pth)
                break

    # 输出契约：workbench/v4 的 shared.js mediaPath() 会给 rel 统一加 "media/" 前缀
    # （workbench/v4/media -> knowledge/media 软链），所以对外输出的图路径必须是
    # "<brand>/<sub>/<file>" 短格式；"knowledge/media/..." 全路径只允许留在本脚本内部。
    def short_media(p):
        m = re.match(r"knowledge/media/(.+)", p or "")
        return m.group(1) if m else (p or "")

    catalog_out = copy.deepcopy(catalog)
    for items in catalog_out.values():
        for it in items:
            if it.get("img"):
                it["img"] = short_media(it["img"])
            if it.get("local_img"):
                it["local_img"] = short_media(it["local_img"])
    logos_out = {}
    def sniff_mime(raw):
        if raw[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
        if raw[:2] == b"\xff\xd8": return "image/jpeg"
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP": return "image/webp"
        return "image/png"
    def embed_logo(src):
        """内嵌前先按内容判型；>80KB 的先缩尺寸、仍大再转 JPEG，避免 products.js 膨胀"""
        raw = open(src, "rb").read()
        if len(raw) > 80 * 1024:
            import subprocess, tempfile
            tmp = tempfile.mktemp(suffix=".img")
            open(tmp, "wb").write(raw)
            subprocess.run(["sips", "-Z", "480", tmp], capture_output=True)
            small = open(tmp, "rb").read()
            if 0 < len(small) < len(raw):
                raw = small
            if len(raw) > 150 * 1024:
                jpg = tempfile.mktemp(suffix=".jpg")
                subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                                tmp, "--out", jpg], capture_output=True)
                small = open(jpg, "rb").read()
                os.unlink(jpg)
                if 0 < len(small) < len(raw):
                    raw = small
            os.unlink(tmp)
        return f"data:{sniff_mime(raw)};base64," + base64.b64encode(raw).decode()
    for b, p in logos.items():
        # logo 直接内嵌 data URI：彻底避开缓存/软链/路径问题
        src = f"{CB}/{p}" if not os.path.isabs(p) else p
        if os.path.exists(src):
            logos_out[b] = embed_logo(src)
        else:
            logos_out[b] = short_media(p)

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "logos": logos_out,
        "brands": catalog_out,
        "note": "evidence: official=官网产品页 / media=公开报道或权威百科 / doc=品牌方资料 / content=内容库实拍。price 为人民币，price_note 里写清是日常价还是活动价。img/local_img 为 workbench/v4/media 相对路径。",
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(payload, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = sum(len(v) for v in catalog_out.values())
    log(f"⑤ 写入 {rel(OUT_JSON)}：{len(catalog_out)} 个品牌 / {n} 个产品")

    with_img = sum(1 for v in catalog_out.values() for x in v if x.get("img"))
    with_price = sum(1 for v in catalog_out.values() for x in v if x.get("price"))
    with_ing = sum(1 for v in catalog_out.values() for x in v if x.get("ingredients"))
    log(f"   有图 {with_img} / 有价 {with_price} / 有成分 {with_ing}")

    # ⑥ 前端数据
    js = ("/* 由 crawler/fetch_product_catalog.py 生成，勿手改 */\nvar PRODUCTS = "
          + json.dumps(catalog_out, ensure_ascii=False, separators=(",", ":"))
          + ";\nvar BRAND_LOGOS = "
          + json.dumps(logos_out, ensure_ascii=False, separators=(",", ":")) + ";\n")
    open(JS_OUT, "w", encoding="utf-8").write(js)
    log(f"⑥ 写入 {rel(JS_OUT)}")


if __name__ == "__main__":
    sys.exit(main())
