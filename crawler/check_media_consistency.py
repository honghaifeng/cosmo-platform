#!/usr/bin/env python3
"""check_media_consistency.py — 本地 / 七牛 / 数据库三方一致性核对。

回答四个问题：
  ① 七牛对象能否对应到库里的一条内容（孤儿对象 = 传上去了但库里有没这条）
  ② 本地文件是否都上了七牛（漏传）
  ③ 七牛对象是否都有本地副本（纯云端、本地丢了）
  ④ 库里需要媒体但两边都没有（真缺口）

用法:
    python3 crawler/check_media_consistency.py
    python3 crawler/check_media_consistency.py --json out.json
    python3 crawler/check_media_consistency.py --list orphan 30
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
ARCHIVE = os.path.join(PROJ, "data", "xhs_archive", "media")
CDN_MAP = os.path.join(PROJ, "service", "data", "media_cdn.json")

FN_RX = re.compile(r"^([a-z]+):([0-9a-zA-Z]+?)(?:_(\d+))?\.(mp4|jpg|jpeg|webp|png|gif)$", re.I)
ARC_RX = re.compile(r"^([0-9a-f]{16,32})(?:_(\d+))?\.(mp4|jpg|jpeg|webp|png)$", re.I)


def obj_id(fn):
    """文件名 -> 'platform:note_id'，取不到返回 None"""
    m = FN_RX.match(fn)
    return f"{m.group(1).lower()}:{m.group(2)}" if m else None


def scan_local():
    """{(brand, kind, 'plat:id[_n].ext')} 与 {(kind, 'plat:id')}"""
    exact = set()
    byid = collections.defaultdict(set)
    for brand in os.listdir(MEDIA):
        bp = os.path.join(MEDIA, brand)
        if not os.path.isdir(bp):
            continue
        for kind in os.listdir(bp):
            kp = os.path.join(bp, kind)
            if not os.path.isdir(kp):
                continue
            for fn in os.listdir(kp):
                if fn.startswith("."):
                    continue
                exact.add((brand, kind, fn))
                t = obj_id(fn)
                if t:
                    byid[kind].add(t)
    return exact, byid


def scan_archive():
    v, i = set(), set()
    if not os.path.isdir(ARCHIVE):
        return v, i
    for fn in os.listdir(ARCHIVE):
        m = ARC_RX.match(fn)
        if not m:
            continue
        (v if m.group(3).lower() == "mp4" else i).add(m.group(1))
    return v, i


def main():
    args = sys.argv[1:]
    local_exact, local_byid = scan_local()
    cdn = json.load(open(CDN_MAP, encoding="utf-8"))
    arc_v, arc_i = scan_archive()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db = {}
    for cid, brand, plat, th, vf, mj in conn.execute(
            "SELECT id, brand, platform, text_hash, verified, media_json FROM content"):
        db[th] = {"id": cid, "brand": brand, "plat": plat, "verified": vf,
                  "media_json": mj}

    # 七牛 side
    cdn_exact = set()          # (brand, kind, fn)
    cdn_byid = collections.defaultdict(set)
    orphan_objs = []
    for key in cdn:
        parts = key.split("/")
        if len(parts) < 3:
            orphan_objs.append(("bad_key", key))
            continue
        brand, kind, fn = parts[0], parts[1], "/".join(parts[2:])
        cdn_exact.add((brand, kind, fn))
        t = obj_id(fn)
        if not t:
            orphan_objs.append(("unparsed", key))
            continue
        cdn_byid[kind].add(t)
        if t not in db:
            orphan_objs.append(("not_in_db", key))

    # ① 七牛 → DB
    cdn_no_db = collections.Counter(r for r, _ in orphan_objs)

    # ② 本地 → 七牛（漏传）
    not_uploaded = sorted(local_exact - cdn_exact)
    nu_by_kind = collections.Counter(k for _, k, _ in not_uploaded)

    # ③ 七牛 → 本地（纯云端）
    cdn_only = sorted(cdn_exact - local_exact)
    co_by_kind = collections.Counter(k for _, k, _ in cdn_only)

    # ④ DB 需要但两边都没有
    gap = collections.Counter()
    gap_detail = collections.defaultdict(list)
    for th, meta in db.items():
        mj = meta["media_json"]
        if not mj or not str(mj).strip():
            continue
        try:
            d = json.loads(mj)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        nid = th.split(":", 1)[1] if ":" in th else th
        for kind, want in (("post", bool(d.get("images") or [])),
                           ("video", bool(d.get("video"))),
                           ("cover", bool(d.get("cover")))):
            if not want:
                continue
            in_cdn = th in cdn_byid.get(kind, set())
            in_loc = th in local_byid.get(kind, set())
            in_arc = (nid in arc_v) if kind == "video" else (nid in arc_i) if kind == "post" else False
            if in_cdn or in_loc or in_arc:
                continue
            gap[kind] += 1
            if meta["verified"] == 1:
                gap[kind + "_verified"] += 1
            if len(gap_detail[kind]) < 200:
                gap_detail[kind].append({"id": meta["id"], "brand": meta["brand"],
                                         "platform": meta["plat"], "verified": meta["verified"]})

    report = {
        "cdn_objects": len(cdn),
        "cdn_matched_db": len(cdn) - cdn_no_db["not_in_db"] - cdn_no_db["unparsed"] - cdn_no_db["bad_key"],
        "cdn_orphans": {"total": len(orphan_objs), "by_reason": dict(cdn_no_db)},
        "orphan_list": [{"reason": r, "key": k} for r, k in orphan_objs],
        "local_files": len(local_exact),
        "local_not_uploaded": {"total": len(not_uploaded), "by_kind": dict(nu_by_kind),
                               "sample": [f"{b}/{k}/{f}" for b, k, f in not_uploaded[:15]]},
        "cdn_only": {"total": len(cdn_only), "by_kind": dict(co_by_kind),
                     "sample": [f"{b}/{k}/{f}" for b, k, f in cdn_only[:10]]},
        "db_gap": {k: v for k, v in gap.items()},
        "db_gap_detail": {k: v[:20] for k, v in gap_detail.items()},
        "archive": {"videos": len(arc_v), "image_notes": len(arc_i)},
    }

    if "--json" in args:
        out = args[args.index("--json") + 1]
        json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写出", out)

    if "--list" in args:
        what = args[args.index("--list") + 1]
        n = int(args[args.index("--list") + 2]) if args.index("--list") + 2 < len(args) else 20
        if what == "orphan":
            for o in report["orphan_list"][:n]:
                print(f"  {o['reason']:<11} {o['key']}")
        elif what == "not_uploaded":
            for s in report["local_not_uploaded"]["sample"][:n]:
                print("  ", s)
        elif what == "cdn_only":
            for s in report["cdn_only"]["sample"][:n]:
                print("  ", s)
        elif what == "gap":
            for kind in ("post", "video"):
                print(f"--- 缺 {kind} ---")
                for g in report["db_gap_detail"].get(kind, [])[:n]:
                    print(f"  #{g['id']:<6} {g['brand']:<12} {g['platform']:<8} v={g['verified']}")
        return

    print("=" * 64)
    print("本地 / 七牛 / 数据库 三方一致性")
    print("=" * 64)
    print(f"七牛对象              {report['cdn_objects']:,}")
    print(f"  能对应到库内内容    {report['cdn_matched_db']:,}")
    print(f"  孤儿（库里没这条）  {report['cdn_orphans']['total']:,}  "
          f"{report['cdn_orphans']['by_reason']}")
    print()
    print(f"本地文件              {report['local_files']:,}")
    print(f"  未上传七牛          {report['local_not_uploaded']['total']:,}  "
          f"{report['local_not_uploaded']['by_kind']}")
    print(f"  仅七牛有、本地无    {report['cdn_only']['total']:,}  {report['cdn_only']['by_kind']}")
    print()
    print("库里需要但三方都没有")
    for kind in ("post", "video", "cover"):
        if report["db_gap"].get(kind):
            print(f"  {kind:<6} {report['db_gap'][kind]:>5}  "
                  f"（其中已校验 {report['db_gap'].get(kind+'_verified', 0)}）")


if __name__ == "__main__":
    main()
