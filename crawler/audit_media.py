#!/usr/bin/env python3
"""audit_media.py — 媒体资产落地审计：库里该有多少图/视频，实际落地多少，缺在哪。

判定口径：
- 需求侧取 content.media_json：images 非空=该有条目配图，video 非空=该有视频，cover 非空=该有封面
- 供给侧两个来源，任一命中即算"已落地"：
    local  knowledge/media/<brand>/<kind>/<platform>:<id>[_n].<ext>
    cdn    service/data/media_cdn.json（七牛对象，key 同构）
- media_json 为空的内容单独计一类（连媒体地址都没有，属于采集缺失，不是下载缺失）

用法:
    python3 crawler/audit_media.py               # 汇总
    python3 crawler/audit_media.py --by-brand    # 按品牌
    python3 crawler/audit_media.py --by-platform # 按平台
    python3 crawler/audit_media.py --missing 20  # 列出缺口样例
    python3 crawler/audit_media.py --json out.json
"""
import os
import re
import json
import sqlite3
import sys
import collections

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJ, "db", "cosmobounty.db")
MEDIA = os.path.join(PROJ, "knowledge", "media")
CDN_MAP = os.path.join(PROJ, "service", "data", "media_cdn.json")
ARCHIVE = os.path.join(PROJ, "data", "xhs_archive", "media")   # 小红书归档媒体（无品牌/类型分层）

FN_RX = re.compile(r"^([a-z]+):([0-9a-zA-Z]+?)(?:_(\d+))?\.(mp4|jpg|jpeg|webp|png|gif)$", re.I)
ARC_RX = re.compile(r"^([0-9a-f]{16,32})(?:_(\d+))?\.(mp4|jpg|jpeg|webp|png)$", re.I)
KINDS = ("post", "video", "cover")


def scan_archive():
    """data/xhs_archive/media 存的是 <note_id>.mp4 / <note_id>_<i>.jpg，没有平台前缀。

    这批是小红书早期归档留下的，同样算「已落地」——否则会把已有文件误报成缺失。
    """
    vids, imgs = set(), collections.defaultdict(set)
    if not os.path.isdir(ARCHIVE):
        return vids, imgs
    for fn in os.listdir(ARCHIVE):
        m = ARC_RX.match(fn)
        if not m:
            continue
        if m.group(3).lower() == "mp4":
            vids.add(m.group(1))
        else:
            imgs[m.group(1)].add(int(m.group(2) or 0))
    return vids, imgs


def scan_local():
    """返回 {kind: {(brand, 'plat:id'): {idx...}}}"""
    out = collections.defaultdict(lambda: collections.defaultdict(set))
    unparsed = []
    for brand in sorted(os.listdir(MEDIA)):
        bdir = os.path.join(MEDIA, brand)
        if not os.path.isdir(bdir):
            continue
        for kind in sorted(os.listdir(bdir)):
            kdir = os.path.join(bdir, kind)
            if not os.path.isdir(kdir):
                continue
            for fn in sorted(os.listdir(kdir)):
                m = FN_RX.match(fn)
                if not m:
                    if not fn.startswith("."):
                        unparsed.append(f"{brand}/{kind}/{fn}")
                    continue
                key = (brand, f"{m.group(1).lower()}:{m.group(2)}")
                out[kind][key].add(int(m.group(3) or 1))
    return out, unparsed


def scan_cdn():
    """七牛映射，按 kind 建索引；同时返回原始条目数"""
    try:
        data = json.load(open(CDN_MAP, encoding="utf-8"))
    except Exception:
        return collections.defaultdict(set), 0
    out = collections.defaultdict(set)
    for k in data:
        parts = k.split("/")
        if len(parts) < 3:
            continue
        brand, kind, fn = parts[0], parts[1], "/".join(parts[2:])
        m = FN_RX.match(fn)
        if m:
            out[kind].add((brand, f"{m.group(1).lower()}:{m.group(2)}"))
    return out, len(data)


def audit():
    local, unparsed = scan_local()
    cdn, cdn_raw = scan_cdn()
    arc_v, arc_i = scan_archive()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # text_hash = "<platform>:<note_id>"，正是媒体文件的命名键
    rows = conn.execute(
        "SELECT id, brand, platform, verified, media_json, url, text_hash FROM content"
    ).fetchall()
    cdn_flat = {k: {t for _, t in v} for k, v in cdn.items()}   # 跨品牌兜底
    local_flat = {k: {t for _, t in v} for k, v in local.items()}
    post_idx = {}
    for (_b, t), idxs in local.get("post", {}).items():
        post_idx[t] = max(post_idx.get(t, 0), max(idxs))

    need = collections.Counter()      # (kind) -> 条数需求
    hit_local = collections.Counter()
    hit_cdn = collections.Counter()
    hit_arc = collections.Counter()
    hit_any = collections.Counter()
    no_media_json = collections.Counter()
    per_brand = collections.defaultdict(lambda: collections.Counter())
    per_plat = collections.defaultdict(lambda: collections.Counter())
    missing_samples = collections.defaultdict(list)
    img_want = img_have = img_have_arc = 0   # 图片按张数算（多图）

    for cid, brand, plat, verified, mj, url, th in rows:
        if not mj or not str(mj).strip():
            no_media_json["all"] += 1
            no_media_json[plat] += 1
            per_brand[brand]["no_media_json"] += 1
            continue
        try:
            d = json.loads(mj)
        except Exception:
            no_media_json["all"] += 1
            continue
        if not isinstance(d, dict):
            continue
        tid = (th or "").strip()
        nid = tid.split(":", 1)[1] if ":" in tid else tid   # 归档目录只用 note_id
        imgs = [u for u in (d.get("images") or []) if isinstance(u, str) and u.strip()]
        vid = d.get("video") if isinstance(d.get("video"), str) else ""
        cov = d.get("cover") if isinstance(d.get("cover"), str) else ""

        for kind, want in (("post", bool(imgs)), ("video", bool(vid)), ("cover", bool(cov))):
            if not want:
                continue
            need[kind] += 1
            per_brand[brand][f"need_{kind}"] += 1
            per_plat[plat][f"need_{kind}"] += 1
            hi = bool(tid) and (tid in local_flat.get(kind, set()))
            hc = bool(tid) and (tid in cdn_flat.get(kind, set()))
            ha = False
            if nid and kind in ("video", "post"):
                ha = nid in (arc_v if kind == "video" else arc_i)
            if hi:
                hit_local[kind] += 1
                per_brand[brand][f"local_{kind}"] += 1
                per_plat[plat][f"local_{kind}"] += 1
            if hc:
                hit_cdn[kind] += 1
                per_brand[brand][f"cdn_{kind}"] += 1
                per_plat[plat][f"cdn_{kind}"] += 1
            if ha:
                hit_arc[kind] += 1
                per_brand[brand][f"arc_{kind}"] += 1
                per_plat[plat][f"arc_{kind}"] += 1
            if hi or hc or ha:
                hit_any[kind] += 1
            else:
                per_brand[brand][f"miss_{kind}"] += 1
                per_plat[plat][f"miss_{kind}"] += 1
                if len(missing_samples[kind]) < 500:
                    missing_samples[kind].append(
                        {"id": cid, "brand": brand, "platform": plat,
                         "verified": verified, "url": url,
                         "media": (imgs[0] if imgs else (vid or cov))[:110]}
                    )

        # 图片按张数
        if imgs:
            img_want += len(imgs)
            img_have += post_idx.get(tid, 0)
            img_have_arc += len(arc_i.get(nid, ()))

    result = {
        "content_total": len(rows),
        "with_media_json": len(rows) - no_media_json["all"],
        "no_media_json": {"total": no_media_json["all"],
                          "by_platform": {k: v for k, v in no_media_json.items() if k != "all"}},
        "kinds": {},
        "images": {"want": img_want, "local": img_have, "archive": img_have_arc},
        "local_files": {k: sum(len(v) for v in local[k].values()) for k in local},
        "local_keys": {k: len(local[k]) for k in KINDS},
        "cdn_raw_entries": cdn_raw,
        "cdn_keys": {k: len(cdn[k]) for k in KINDS},
        "archive": {"videos": len(arc_v), "image_notes": len(arc_i)},
        # 本地已有的键，有多少已在七牛上——回答「是不是都传了七牛」
        "cdn_coverage": {
            k: {"local_keys": len(local.get(k, {})),
                "on_cdn": len({t for _, t in local.get(k, {})} & {t for _, t in cdn.get(k, set())})}
            for k in KINDS
        },
        "unparsed_local": unparsed,
        "by_brand": {b: dict(c) for b, c in per_brand.items()},
        "by_platform": {p: dict(c) for p, c in per_plat.items()},
        "missing_samples": {k: v[:20] for k, v in missing_samples.items()},
    }
    for kind in KINDS:
        w = need[kind]
        a = hit_any[kind]
        result["kinds"][kind] = {
            "need": w, "local": hit_local[kind], "cdn": hit_cdn[kind],
            "archive": hit_arc[kind], "any": a, "missing": w - a,
            "rate": round(a / w * 100, 1) if w else None,
        }
    return result


def main():
    args = sys.argv[1:]
    r = audit()

    if "--json" in args:
        out = args[args.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        print("已写出", out)

    if "--by-brand" in args:
        order = sorted(r["by_brand"].items(), key=lambda kv: -kv[1].get("need_post", 0))
        print(f"{'品牌':<14}{'内容':>5}{'需图':>6}{'有图':>6}{'缺图':>6}"
              f"{'需视频':>7}{'有视频':>7}{'缺视频':>7}")
        for b, c in order:
            print(f"{b:<14}{c.get('need_post',0)+c.get('no_media_json',0):>5}"
                  f"{c.get('need_post',0):>6}{c.get('local_post',0):>6}"
                  f"{c.get('miss_post',0):>6}{c.get('need_video',0):>7}"
                  f"{c.get('local_video',0):>7}{c.get('miss_video',0):>7}")
        return

    if "--by-platform" in args:
        print(f"{'平台':<12}{'需图':>6}{'有图':>6}{'缺图':>6}{'需视频':>7}{'有视频':>7}{'缺视频':>7}")
        for p, c in sorted(r["by_platform"].items(), key=lambda kv: -kv[1].get("need_post", 0)):
            print(f"{p:<12}{c.get('need_post',0):>6}{c.get('local_post',0):>6}"
                  f"{c.get('miss_post',0):>6}{c.get('need_video',0):>7}"
                  f"{c.get('local_video',0):>7}{c.get('miss_video',0):>7}")
        return

    if "--missing" in args:
        n = int(args[args.index("--missing") + 1]) if args.index("--missing") + 1 < len(args) else 10
        for kind, items in r["missing_samples"].items():
            print(f"\n=== 缺 {kind} 样例（前 {min(n, len(items))} 条）===")
            for it in items[:n]:
                print(f"  #{it['id']:<6} {it['brand']:<12} {it['platform']:<8} v={it['verified']} {it['url']}")
                print(f"         {it['media']}")
        return

    print("=" * 62)
    print("媒体资产落地审计")
    print("=" * 62)
    print(f"内容总条数            {r['content_total']:,}")
    print(f"  有 media_json       {r['with_media_json']:,}")
    print(f"  无 media_json       {r['no_media_json']['total']:,}  ← 连媒体地址都没有")
    print()
    print(f"{'类型':<8}{'应有':>8}{'知识库':>8}{'归档':>8}{'七牛':>8}{'合计有':>8}{'缺':>8}{'落地率':>9}")
    for kind in KINDS:
        k = r["kinds"][kind]
        print(f"{kind:<8}{k['need']:>8,}{k['local']:>8,}{k['archive']:>8,}{k['cdn']:>8,}"
              f"{k['any']:>8,}{k['missing']:>8,}{(str(k['rate'])+'%'):>9}")
    print()
    print(f"图片张数  应有 {r['images']['want']:,}  知识库 {r['images']['local']:,}  "
          f"归档 {r['images']['archive']:,}")
    print(f"本地文件  视频 {r['local_files'].get('video',0):,}  配图 {r['local_files'].get('post',0):,}"
          f"  封面 {r['local_files'].get('cover',0):,}")
    print(f"归档目录  video {r['archive']['videos']} 个笔记 / 配图 {r['archive']['image_notes']} 个笔记"
          f"（data/xhs_archive/media）")
    print(f"七牛对象  {r['cdn_raw_entries']:,} 条映射（video {r['cdn_keys']['video']}, "
          f"post {r['cdn_keys']['post']}, cover {r['cdn_keys']['cover']}）")
    print()
    print("七牛覆盖率（本地已有的东西传上去多少）")
    for kind in KINDS:
        c = r["cdn_coverage"][kind]
        rate = c["on_cdn"] / c["local_keys"] * 100 if c["local_keys"] else 0
        print(f"  {kind:<6} 本地 {c['local_keys']:>5,} 键 / 七牛 {c['on_cdn']:>5,} → {rate:>5.1f}%"
              f"   未上传 {c['local_keys']-c['on_cdn']:,}")


if __name__ == "__main__":
    main()
