#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书数据正确性校验（不是完整度体检 —— 体检看「缺多少」，这个看「对不对」）。

背景：2026-10-24 的一次审计发现了三类真实错误：
  ① 发布时间 57% 有 1~13 天偏差 —— 月日型 time 硬套 note_id 年份造成
  ② 话题标签被切成了单字符 —— tag_list 是逗号分隔字符串，被当 list 迭代
  ③ 归档重建覆盖了新采数据 —— 只从日志重建，没读数据库

本脚本把这三类检查固化为可复跑的守卫，每次改动采集/归档逻辑后跑一次。

用法：
  python crawler/verify_xhs_data.py
  python crawler/verify_xhs_data.py --strict   # 有 FAIL 就退出码 1
"""
import argparse
import collections
import datetime
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "data", "xhs_archive")
NOTES = os.path.join(ARCHIVE, "notes.jsonl")
DB = os.path.join(ROOT, "db", "cosmobounty.db")
MEDIA = os.path.join(ARCHIVE, "media")
AUTH_FILE = os.path.join(ROOT, "data", "xhs_auth_pubtime.json")

BRANDS = ["COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞"]
RX = {
    "COSMOBOUNTY": re.compile(r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时胶囊|日活瓶|夜修瓶)", re.I),
    "MitoQ": re.compile(r"(mito\s*q|mitoq)", re.I),
    "GRANVER": re.compile(r"(granver|granve|吉利肝|吉清肺|吉力威|吉瑞维)", re.I),
    "LAIFE": re.compile(r"(laife|乐梵|乐悦泰|乐轻肝|乐维源|乐净肺)", re.I),
    "W+端粒塔": re.compile(r"(端粒塔|duanlta|nmn\s*25000)", re.I),
    "塞乐瑞": re.compile(r"(塞乐瑞|赛乐瑞|cellerator|stemax|莱复隆|精倍莱)", re.I),
}

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results = []


def check(name, ok, detail, level=None):
    lv = level or (PASS if ok else FAIL)
    results.append((lv, name, detail))
    mark = {PASS: "✅", WARN: "⚠️ ", FAIL: "❌"}[lv]
    print(f"  {mark} {name}")
    if detail:
        print(f"       {detail}")
    return lv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(NOTES):
        print("找不到 notes.jsonl，请先跑 build_xhs_archive.py --build")
        return 2
    rows = [json.loads(l) for l in open(NOTES, encoding="utf-8") if l.strip()]
    total = len(rows)
    byid = {r["note_id"]: r for r in rows}
    print("=" * 66)
    print(f"小红书数据正确性校验 · {total} 条归档")
    print("=" * 66)

    # ── ① 发布时间 ─────────────────────────────────────────────
    print("\n【① 发布时间正确性】")
    if os.path.exists(AUTH_FILE):
        auth = json.load(open(AUTH_FILE, encoding="utf-8"))
        ts_map = {}
        for nid, v in auth.items():
            s = str(v).strip()
            if re.fullmatch(r"\d{13}", s):
                ts_map[nid] = datetime.datetime.fromtimestamp(int(s) / 1000)
            elif re.fullmatch(r"\d{10}", s):
                ts_map[nid] = datetime.datetime.fromtimestamp(int(s))
        exact = day = near = far = 0
        worst = []
        for nid, t in ts_map.items():
            r = byid.get(nid)
            if not r or not r.get("publish_time"):
                continue
            try:
                sv = datetime.datetime.strptime(r["publish_time"][:16], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            dd = abs((sv - t).total_seconds()) / 86400
            if dd < 1 / 24:
                exact += 1
            elif dd < 0.5:
                day += 1
            elif dd <= 3.5:
                near += 1
            else:
                far += 1
                worst.append((nid, t, r["publish_time"], dd))
        n = exact + day + near + far
        acc = 100 * (exact + day + near) / max(n, 1)
        check("对平台精确发布时间的准确率(±3天) ≥ 95%", acc >= 95,
              f"实测 {acc:.1f}%（精确±1h {exact} / 同日 {day} / ±3天 {near} / 偏差>3天 {far}，样本 {n}）")
        if worst:
            print(f"       偏差样本（前3）:")
            for nid, t, s, dd in worst[:3]:
                print(f"         {nid[:12]} 平台={t:%Y-%m-%d %H:%M} 存储={s[:16]} 差{dd:.1f}天")
    else:
        check("权威时间交叉验证", False, f"缺少 {AUTH_FILE}，无法验证时间准确率", WARN)

    pubs = [r["publish_time"] for r in rows if r.get("publish_time")]
    check("发布时间 100% 覆盖", len(pubs) == total, f"{len(pubs)}/{total}")
    fut = [p for p in pubs if p[:10] > datetime.date.today().isoformat()]
    check("无未来时间", not fut, f"未来时间 {len(fut)} 条" if fut else "0 条")
    notime = [p for p in pubs if len(p) <= 10]
    check("时间精确到时分（非仅日期）", len(notime) <= 5,
          f"仅日期 {len(notime)} 条" if notime else "全部含时分",
          PASS if len(notime) <= 5 else WARN)

    # ── ② 话题标签 ─────────────────────────────────────────────
    print("\n【② 话题标签正确性】")
    tg = [r for r in rows if r.get("tags")]
    single = [r for r in tg if any(len(t) == 1 for t in r["tags"])]
    comma = [r for r in tg if any("," in t for t in r["tags"])]
    check("标签未被字符化", len(single) <= 12,
          f"含单字符标签的 {len(single)} 条（阈值12：少量单字标签本身合法）",
          PASS if len(single) <= 12 else FAIL)
    check("标签内无逗号残留", not comma, f"{len(comma)} 条含逗号")
    avg = sum(len(r["tags"]) for r in tg) / max(len(tg), 1)
    check("平均标签数合理（2~15）", 2 <= avg <= 15, f"平均 {avg:.1f} 个，共 {len(tg)} 条有标签")

    # ── ③ 品牌归属 ─────────────────────────────────────────────
    print("\n【③ 品牌归属正确性】")
    brand = [r for r in rows if r.get("brand_mention")]
    check("归属品牌都在六家之内",
          all(r["brand"] in BRANDS for r in brand),
          f"越界品牌 {sorted(set(r['brand'] for r in brand) - set(BRANDS)) or '无'}")
    strong = [r for r in brand if r.get("evidence") == "strong" and (r.get("desc") or "").strip()]
    false_strong = [r for r in strong
                    if RX.get(r["brand"]) and not RX[r["brand"]].search(f"{r.get('title') or ''} {r.get('desc') or ''}")]
    check("强证据条目正文确实含品牌名", not false_strong,
          f"强证据 {len(strong)} 条中有正文可核，误判 {len(false_strong)} 条")
    tag_ev = [r for r in brand if r.get("evidence") == "tag"]
    mis_tag = []
    for r in tag_ev:
        if not (r.get("desc") or "").strip():
            continue
        text = f"{r.get('title') or ''} {r.get('desc') or ''}"
        bp = re.split(r"#\s*\S*?\[话题\]", text)[0]
        bp = re.sub(r"(?:^|\s)#[\w一-鿿+]+", " ", bp)
        if RX.get(r["brand"]) and RX[r["brand"]].search(bp):
            mis_tag.append(r)
    check("蹭词位标记准确（品牌确实只在标签里）", not mis_tag,
          f"蹭词位 {len(tag_ev)} 条，误判 {len(mis_tag)} 条")

    # ── ④ 一致性 ───────────────────────────────────────────────
    print("\n【④ 四层一致性】")
    if os.path.exists(DB):
        con = sqlite3.connect(DB)
        db_ids = {r[0] for r in con.execute("SELECT note_id FROM xhs_note")}
        svc_path = os.path.join(ROOT, "service", "data", "cosmobounty.db")
        svc_ids = set()
        if os.path.exists(svc_path):
            sv = sqlite3.connect(svc_path)
            svc_ids = {r[0] for r in sv.execute("SELECT note_id FROM xhs_note")}
            sv.close()
        arc_ids = set(byid)
        check("归档 ↔ 主库 一致", arc_ids == db_ids,
              f"归档 {len(arc_ids)} | 主库 {len(db_ids)} | 差异 {len(arc_ids ^ db_ids)}")
        check("主库 ↔ service副本 一致", (not svc_ids) or svc_ids == db_ids,
              f"service {len(svc_ids)} | 主库 {len(db_ids)} | 差异 {len(svc_ids ^ db_ids)}")
        # 字段抽样一致
        dbmap = {r[0]: r for r in con.execute(
            "SELECT note_id, body, author FROM xhs_note")}
        diff = 0
        for r in rows[:800]:
            d = dbmap.get(r["note_id"])
            if d and ((r.get("desc") or "") != (d[1] or "") or (r.get("author") or "") != (d[2] or "")):
                diff += 1
        check("抽样 800 条正文/作者与库一致", diff == 0, f"不一致 {diff} 条")
        con.close()

    dup = [k for k, v in collections.Counter(r["note_id"] for r in rows).items() if v > 1]
    check("无重复 note_id", not dup, f"重复 {len(dup)} 个")

    # ── ⑤ 数值合理性 ───────────────────────────────────────────
    print("\n【⑤ 数值合理性】")
    neg = [r for r in rows if (r.get("likes") or 0) < 0 or (r.get("collects") or 0) < 0]
    check("互动数无负值", not neg, f"负值 {len(neg)} 条")
    zero = sum(1 for r in rows if not (r.get("likes") or 0))
    check("点赞=0 比例 < 10%", zero < total * 0.1, f"{zero}/{total}（{100*zero/total:.1f}%）")
    noid = sum(1 for r in rows if not r.get("author_id"))
    check("账号ID 100% 覆盖", noid == 0, f"缺 {noid} 条")
    masked = sum(1 for r in rows if (r.get("author") or "").find("*") >= 0)
    check("昵称脱敏比例 < 15%", masked < total * 0.15,
          f"脱敏 {masked}/{total}（{100*masked/total:.1f}%）")

    # ── ⑥ 媒体落盘 ─────────────────────────────────────────────
    print("\n【⑥ 媒体落盘】")
    if os.path.isdir(MEDIA):
        mp4 = [f for f in os.listdir(MEDIA) if f.endswith(".mp4")]
        jpg = [f for f in os.listdir(MEDIA) if f.endswith(".jpg")]
        need_v = [r for r in rows if r.get("video_url")]
        have_v = [r for r in need_v if os.path.exists(os.path.join(MEDIA, f"{r['note_id']}.mp4"))]
        check("视频落盘率 ≥ 90%", len(have_v) >= 0.9 * max(len(need_v), 1),
              f"{len(have_v)}/{len(need_v)}（{100*len(have_v)/max(len(need_v),1):.0f}%）")
        print(f"       mp4 {len(mp4)} 个 | jpg {len(jpg)} 张")

    # ── 汇总 ───────────────────────────────────────────────────
    print("\n" + "=" * 66)
    c = collections.Counter(lv for lv, _, _ in results)
    print(f"校验结果：PASS {c[PASS]} | WARN {c[WARN]} | FAIL {c[FAIL]}")
    if c[FAIL]:
        print("失败项：")
        for lv, name, detail in results:
            if lv == FAIL:
                print(f"  ❌ {name} — {detail}")
    print("=" * 66)
    return 1 if (args.strict and c[FAIL]) else 0


if __name__ == "__main__":
    sys.exit(main())
