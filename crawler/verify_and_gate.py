#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据校验闸门：只有通过校验的记录才允许对外展示。

用户规则（2026-10-24）：
  「数据库里必须是经过校验过的、准确的数据。对外只展示没问题的数据。
    所有没有校验过的，都不要对外展示。不管是小红书还是其他平台。」

设计：
  · content 表加 verified（0/1）+ verify_note（不合格原因）
  · xhs_note 表同样加
  · 所有对外出口（MCP / HTTP API / 导出 CSV / 页面）必须带 verified=1 过滤
  · 校验规则分「硬性」（不合格直接隐藏）与「提示性」（记录但不隐藏）

校验规则（硬性 = 不合格即隐藏）：
  H1 归属可信   —— 品牌归属有据（正文含品牌名 / 强证据 / 搜索词+正文双证）
  H2 时间可信   —— 有发布时间且格式合法（YYYY-MM-DD 或 YYYY-MM-DD HH:MM），不晚于今天
  H3 正文非空   —— body 有实质内容（长度 ≥ 阈值），SERP 摘要级不合格
  H4 数值合理   —— 赞/藏/评非负
  H5 链接有效   —— 有原文链接

用法：
  python crawler/verify_and_gate.py            # 跑校验并打标
  python crawler/verify_and_gate.py --report   # 只看统计，不改库
"""
import argparse
import datetime
import json
import os
import re
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
VALID_BRANDS = {"COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞"}
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")
SERP_MARK = "（SERP 收录级）"

# 各平台正文长度阈值：低于此视为「无实质内容」
# 口径必须与 service/app.py 的 level_of() 一致，否则同一份数据两套标准：
#   level_of: >=220 full(全文) / 30-219 partial(摘要级) / 其余 missing
# 百度、头条、新闻源是「文章」形态，30 字太宽松会放进垃圾，但 220 会把
# 实测有实质内容的 150-199 字资讯整段误杀（含品牌评测、商品页参数与价格）。
# 取 150：低于此仍是摘要碎片，不放行；达到即视为「摘要级全文」，通过但标注 partial。
MIN_BODY = {
    "xhs": 30, "douyin": 30, "shipinhao": 30,
    "baidu": 150, "toutiao": 150, "web": 150,
}
FULL_BODY = 220  # 达到此长度才算「全文」，与 app.py level_of 对齐

# 品牌提及正则（与归档脚本同口径）
BRAND_RX = {
    "COSMOBOUNTY": re.compile(r"(cosmo\s*bounty|cosmobounty|科诗博缇|黑白分时胶囊|日活瓶|夜修瓶)", re.I),
    "MitoQ": re.compile(r"(mito\s*q|mitoq)", re.I),
    "GRANVER": re.compile(r"(granver|granve|吉利肝|吉清肺|吉力威|吉瑞维)", re.I),
    "LAIFE": re.compile(r"(laife|乐梵|乐悦泰|乐轻肝|乐维源|乐净肺|乐返盾|乐腺舒|巢汐)", re.I),
    "W+端粒塔": re.compile(r"(端粒塔|duanlta|nmn\s*25000)", re.I),
    "塞乐瑞": re.compile(r"(塞乐瑞|赛乐瑞|cellerator|stemax|莱复隆|精倍莱|赛特茵|愈龄菌|勉益莱)", re.I),
}
# 歧义产品词：单独出现不足以归属
AMBIG_RX = {
    "COSMOBOUNTY": re.compile(r"(分时胶囊|分时抗衰|节律营养)", re.I),
    "MitoQ": re.compile(r"(经典胶囊\s*pure|衡糖胶囊|姜黄素\s*pro|舒心\s*pro|奶蓟草胶囊|明眸胶囊|锐智胶囊|骨赋能胶囊|解压胶囊|净肝胶囊|高纯鱼油)", re.I),
    "GRANVER": re.compile(r"(护肝片|护肺片)", re.I),
    "LAIFE": re.compile(r"(红宝瓶|紫宝瓶|橙宝瓶|肌龄锁|3s\s*女人味胶囊|红气瓶)", re.I),
    # 「端粒」是生物学概念，任何抗衰科普都会提，绝不能当品牌归属证据
    # （曾因此把 10 条泛科普「跑步如何让端粒体逆生长」误归到 W+端粒塔）。
    # 只保留成分名 NMN，且仍需配品类上下文，算弱证据。
    "W+端粒塔": re.compile(r"nmn", re.I),
    "塞乐瑞": re.compile(r"(super\s*-?\s*k|巢悦莱|精华倍莱)", re.I),
}
# 自有品牌（科诗博缇）自有号内容：讲自家理念/场景，正文未必提品牌名，
# 但归属由「官方号作者」+「产品线关键词」双重保证 → 视为归属可信。
OWN_CONTENT_RX = re.compile(
    r"(分时|节律|昼夜|白天.{0,4}晚|夜修|日活|黑白分时|社会时差|"
    r"细胞.{0,6}白天|充电.{0,4}修复|早睡|熬夜)", re.I)
# 自有品牌的官方号（已核实）
OWN_OFFICIAL = {"cosmobounty", "cosmo bounty", "科诗博缇"}

# 六品牌官号 creator_id（2026-10-26 主页抓取时逐一核实，
# 来源：creator 表 + 官号主页滚动采集 crawl_creator_scroll.py）
OFFICIAL_CREATOR = {
    "5c4a73a000000000110201d1": "MitoQ",          # MitoQ
    "6607cfa3000000000d025cfa": "GRANVER",        # Granver
    "62b4027c000000001b027eff": "塞乐瑞",          # Cellerator赛乐瑞
    "6274d323000000001501c4ae": "LAIFE",          # LAIFE乐梵
    "5dfcb5e70000000001001beb": "W+端粒塔",        # 端粒塔 nmn
    "694126b60000000032029ebb": "COSMOBOUNTY",    # COSMOBOUNTY
}

CATEGORY_CTX = re.compile(
    r"(抗衰|逆龄|保健|营养|补充剂|胶囊|片剂|补剂|成分|剂量|功效|服用|口碑|测评|种草|"
    r"Healthy|health|Capsule|supplement|姜黄|镁|奶蓟|鱼油|NMN|NAD|槲皮素|"
    r"精华|抗氧|细胞|线粒体|护肝|护肺|睡眠|精力|内服|口服|品牌|旗舰)", re.I)

# 他牌排除：以下词一旦出现，说明文本属于别家品牌或无关语境，
# 即使命中了本品牌的产品词（撞名 / 撞概念）也不得归属。
# 来源：2026-10-26 归属核查发现的真实误判。
NEG_RX = {
    # 安热沙也有「夜修瓶」；Gaibu/凌创 也做「黑白胶囊/分时」；「日活」撞「日活瓶」
    "COSMOBOUNTY": re.compile(r"(安热沙|anessa|gaibu|凌创|玛丽安保莱|魅宠|日活\s*瓶颈|日活\s*用户)", re.I),
    # 「serelys」是法国更年期品牌，与塞乐瑞同音异牌
    "塞乐瑞": re.compile(r"(serelys)", re.I),
    # 「红宝瓶」是通用产品名：大宝红宝瓶（护肤）、英氏红宝瓶（婴童洗护）、
    # 沐熙红宝瓶（卵巢胶囊）、少女泉/巢天骄（肌醇）都在用，不能因命中
    # 「红宝瓶」就归到 LAIFE 乐梵（2026-10-26 归属核查发现）
    "LAIFE": re.compile(r"(大宝|英氏|沐熙|少女泉|巢天骄|润本|红色小象)", re.I),
    # 「高纯鱼油 / 奶蓟草胶囊」是通用品类：汤普森(Thompson's)、Swisse、
    # 澳佳宝都有同形态产品，命中产品词不等于命中 MitoQ
    "MitoQ": re.compile(r"(汤普森|thompson|swisse|澳佳宝|blackmores|纽崔莱)", re.I),
    # 「护肝片/护肺片」通用名：Swisse、汤普森、澳佳宝都在做
    "GRANVER": re.compile(r"(汤普森|thompson|swisse|澳佳宝|blackmores)", re.I),
}

# 人工核实的误判黑名单（2026-10-26 归属核查）。
# 产品词撞名这类错误靠规则无法可靠区分，逐个核实后按 id 永久排除。
# verified 在任何一次重跑后都会被强制压回 0；新增误判请追加并注明原因。
MANUAL_REJECT = {
    46022: "H1-他牌排除(安热沙夜修瓶)",
    46023: "H1-他牌排除(安热沙夜修瓶)",
    65622: "H1-他牌排除(安热沙夜修瓶)",
    65624: "H1-他牌排除(安热沙夜修瓶)",
    65585: "H1-他牌排除(抖音日活行业文)",
    48829: "H1-他牌排除(Gaibu黑白胶囊)",
    48834: "H1-他牌排除(凌创早晚套组)",
    48835: "H1-他牌排除(玛丽安保莱护肤)",
    65467: "H1-他牌排除(海南干细胞行业文)",
}


def ensure_cols(con):
    for tbl, cols in (
        ("content", [("verified", "INTEGER DEFAULT 0"), ("verify_note", "TEXT")]),
        ("xhs_note", [("verified", "INTEGER DEFAULT 0"), ("verify_note", "TEXT")]),
    ):
        have = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
        for name, decl in cols:
            if name not in have:
                con.execute(f"ALTER TABLE {tbl} ADD COLUMN {name} {decl}")
                print(f"  已加列 {tbl}.{name}")


def _dominant(brand, text):
    """本品牌词在全文里是否占主导（≥2 次且多于竞品词命中次数）。

    用途：正文补全成全文后，「对比评测」类文章会同时出现本品牌和竞品词。
    若本品牌是文章主体，出现竞品只是横向对比，不能据此排除归属。
    """
    rx = BRAND_RX.get(brand)
    if not rx or not text:
        return False
    own = len(rx.findall(text))
    neg = NEG_RX.get(brand)
    other = len(neg.findall(text)) if neg else 0
    return own >= 2 and own > other


def judge(brand, title, body, publish_time, url, platform, evidence=None, media_len=0,
          has_video=False, scene_tagged=False, author_id=""):
    """返回 (verified, reasons[]) —— reasons 非空即不合格

    media_len：media_text 表里该内容的 ASR/OCR 解读文本长度。
      视频平台（douyin）的 body 往往是标题/简介（很短），真正的「内容」
      是口播转写（ASR）或画面文字（OCR），存在 media_text 表。H3 判断时
      要把这个也当「实质内容」看，否则视频内容会因 body 短被误杀。
    has_video：该条是否真有视频素材（本地已落盘或已有稳定 CDN 地址）。
      抖音有一批是纯 BGM 无口播的视频（ASR 只出歌词/水印），正文也是空的，
      但视频画面本身就是内容。用户口径：没有正文、只有图片视频的也算数。
      所以「有视频」可替代 H3 的正文要求；但 H1 品牌归属仍必须通过。
    """
    reasons = []
    # 场景词轨道：非品牌内容（品类/场景/痛点类笔记），不按品牌归属判定。
    # 它的价值是场景与用户语言分析，走独立的 H2/H3/H4/H5 质检 + 场景标签要求。
    scene_track = brand == "品类场景词"
    text = f"{title or ''} {body or ''}"

    # H1 归属可信
    if scene_track:
        # 场景词轨道：不归属品牌，但必须先过 scene_tag.py 的详细分析
        # （场景分类 + 痛点提取），没分析过的不允许对外。
        if not scene_tagged:
            reasons.append("H1-场景未分析")
    else:
        # 注意：标题里明确写了品牌名也算证据 —— 采集时正文可能没抓到，
        # 但「标题含品牌 + 有链接 + 有时间」已足够说明这是该品牌的内容。
        title_rx = BRAND_RX.get(brand)
        title_hit = bool(title_rx and title_rx.search(title or ""))
        if brand not in VALID_BRANDS:
            reasons.append("H1-品牌不明")
        elif (NEG_RX.get(brand) and NEG_RX[brand].search(text)
              and not (author_id and OFFICIAL_CREATOR.get(author_id) == brand)
              and not title_hit
              and not _dominant(brand, text)):
            # 他牌排除：撞名产品（安热沙「夜修瓶」等）不能因命中自家产品词就归到本品牌。
            # 官方号发布的内容不受此限。
            # 两个例外（正文补全全文后出现的误杀，2026-10-26）：
            #   ① 标题明确写了本品牌 —— 正文提到竞品是对比评测，不是归属错
            #   ② 本品牌词在全文里占主导（出现≥2 次且多于竞品词）—— 主体在讲本品牌
            # 没有这两个例外时，一篇 MitoQ 通稿里顺带提一句 Swisse 就会被整条踢掉，
            # 属于把「对比」误当「他牌」。
            reasons.append("H1-他牌排除")
        elif brand == "COSMOBOUNTY" and platform == "xhs":
            # 小红书有证据分级：只有 strong/weak 通过，tag（蹭词位）不通过
            if evidence == "tag":
                reasons.append("H1-蹭词位")
            elif evidence not in ("strong", "weak"):
                # 没有正文可核 → 三种情况仍算归属可信：
                #   ① 标题含品牌名
                #   ② 作者是自有品牌官方号
                #   ③ 正文含产品线/理念关键词（自有号讲自家理念，正文常不带品牌名）
                own_hit = bool(OWN_CONTENT_RX.search(text))
                if not (title_hit or own_hit):
                    reasons.append("H1-归属未验证")
        else:
            # 官号作者即归属铁证：内容发布在品牌官方账号主页，归属由账号身份保证，
            # 不依赖标题/正文是否写出品牌名（官号笔记常只写「新品上线」这类话）。
            if author_id and OFFICIAL_CREATOR.get(author_id) == brand:
                pass
            else:
                rx = BRAND_RX.get(brand)
                amb = AMBIG_RX.get(brand)
                if rx and rx.search(text):
                    pass
                elif title_hit:
                    pass
                elif amb and amb.search(text) and CATEGORY_CTX.search(text):
                    pass
                else:
                    reasons.append("H1-正文未见品牌")

    # H2 时间可信
    pt = (publish_time or "").strip()
    if not pt:
        reasons.append("H2-无发布时间")
    elif not TIME_RE.match(pt):
        reasons.append("H2-时间格式异常")
    else:
        try:
            d = datetime.datetime.strptime(pt[:10], "%Y-%m-%d").date()
            if d > datetime.date.today():
                reasons.append("H2-未来时间")
        except ValueError:
            reasons.append("H2-时间解析失败")

    # H3 正文非空
    b = (body or "").strip()
    minlen = MIN_BODY.get(platform, 30)
    if not b:
        if platform == "douyin":
            # 视频笔记正文常为空，实质内容在口播 ASR 里；ASR 够长即算有内容。
            # 纯 BGM 无口播的视频（ASR 只有歌词/水印）也算数 —— 视频画面本身是内容。
            if media_len < minlen and not has_video:
                reasons.append("H3-无正文")
        else:
            # 图文/文章类平台：正文就是内容本身。只有标题 = 数据不全，不对外展示。
            # 例外：图片文字（OCR）/媒体解读够多，说明内容确实取到了。
            if media_len < minlen and not has_video:
                reasons.append("H3-无正文(仅标题)")
    elif b.startswith(SERP_MARK):
        reasons.append("H3-SERP摘要级")
    elif len(b) < minlen:
        if media_len < minlen and not has_video:
            reasons.append(f"H3-正文过短({len(b)})")

    # H4 数值合理
    return reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只看统计，不改库")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    print("=" * 62)
    print("数据校验闸门")
    print("=" * 62)
    ensure_cols(con)

    # ── content 表 ───────────────────────────────────────────
    print("\n【content 表 · 品牌维度】")
    stats = {}
    upd = []
    # 预加载每条内容的媒体解读（ASR/OCR）文本长度，供 H3 判断视频内容是否有实质
    media_lens = {}
    for cid, ln in con.execute(
            "SELECT content_id, MAX(LENGTH(text)) FROM media_text GROUP BY content_id"):
        media_lens[cid] = ln or 0
    for r in con.execute("""SELECT id, brand, platform, title, body, publish_time, url,
                                    metrics_json, media_json, analysis_json, author_id FROM content"""):
        m = {}
        try:
            m = json.loads(r["metrics_json"] or "{}")
        except Exception:
            pass
        # 该条是否真有视频素材：media_json 的 video 非空即视为有。
        # 视频画面本身就是内容 —— 用户口径（2026-10-26）：没正文、只有图片
        # 视频的也算数，媒体解读（OCR/ASR）参与综合判断。
        has_video = False
        try:
            _md = json.loads(r["media_json"] or "{}")
            if isinstance(_md, dict) and ((_md.get("video") or "").strip()
                                          or len(_md.get("images") or []) >= 2):
                has_video = r["platform"] in ("douyin", "xhs", "shipinhao")
        except Exception:
            pass
        # 场景词轨道：是否已完成详细分析（scene_tag.py 打了场景标签）
        _scene_tagged = False
        if r["brand"] == "品类场景词":
            try:
                _aj = json.loads(r["analysis_json"] or "{}")
                _scene_tagged = isinstance(_aj, dict) and bool(_aj.get("scene"))
            except Exception:
                pass
        neg = any((m.get(k) or 0) < 0 for k in ("likes", "collects", "comments", "shares"))
        reasons = judge(r["brand"], r["title"], r["body"], r["publish_time"], r["url"],
                        r["platform"], media_len=media_lens.get(r["id"], 0),
                        has_video=has_video, scene_tagged=_scene_tagged,
                        author_id=r["author_id"] or "")
        if neg:
            reasons.append("H4-负数互动")
        if not (r["url"] or "").strip():
            reasons.append("H5-无链接")
        ok = not reasons
        upd.append((1 if ok else 0, ";".join(reasons), r["id"]))
        key = "verified" if ok else (reasons[0].split("-")[0])
        stats[key] = stats.get(key, 0) + 1

    total = len(upd)
    passed = sum(1 for u in upd if u[0] == 1)
    print(f"  总 {total} → 通过 {passed}（{100*passed/max(total,1):.1f}%）/ 拦截 {total-passed}")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"    {k:22s}{v:5d}")

    # ── xhs_note 表 ──────────────────────────────────────────
    print("\n【xhs_note 表 · 小红书归档】")
    stats2 = {}
    upd2 = []
    for r in con.execute("""SELECT note_id, brand, title, body, publish_time, note_url,
                                    evidence, brand_mention, video_url, images_json, metrics_dummy FROM
                             (SELECT note_id, brand, title, body, publish_time, note_url,
                                     evidence, brand_mention, video_url, images_json,
                                     0 AS metrics_dummy FROM xhs_note)"""):
        # 小红书笔记同理：有视频或有多张图，内容载体就是齐的，不该因正文空被剔。
        # 只认「视频」和「图片>=2」——单封面图（占位图）不算。
        _n_img = 0
        try:
            _imgs = json.loads(r["images_json"] or "[]")
            _n_img = len(_imgs) if isinstance(_imgs, list) else 0
        except Exception:
            pass
        has_v = bool((r["video_url"] or "").strip()) or _n_img >= 2
        reasons = judge(r["brand"], r["title"], r["body"], r["publish_time"],
                        r["note_url"], "xhs", r["evidence"], has_video=has_v)
        # 未归属的内容不是「错误」，但也不该对外当品牌内容展示
        if not r["brand_mention"]:
            reasons.append("H0-未归属品牌")
        # 归档层额外要求：有原文链接
        if not (r["note_url"] or "").strip():
            reasons.append("H5-无链接")
        ok = not reasons
        upd2.append((1 if ok else 0, ";".join(reasons), r["note_id"]))
        key = "verified" if ok else (reasons[0].split("-")[0])
        stats2[key] = stats2.get(key, 0) + 1

    total2 = len(upd2)
    passed2 = sum(1 for u in upd2 if u[0] == 1)
    print(f"  总 {total2} → 通过 {passed2}（{100*passed2/max(total2,1):.1f}%）/ 拦截 {total2-passed2}")
    for k, v in sorted(stats2.items(), key=lambda x: -x[1]):
        print(f"    {k:22s}{v:5d}")

    if args.report:
        con.close()
        return

    # ── 写库 ────────────────────────────────────────────────
    con.executemany("UPDATE content SET verified=?, verify_note=? WHERE id=?", upd)
    con.executemany("UPDATE xhs_note SET verified=?, verify_note=? WHERE note_id=?", upd2)
    con.commit()

    # 人工核实黑名单强制压回 0：产品词撞名靠规则区分不可靠，
    # 这几条在任何一次重跑后都必须保持隔离，否则又会混进对外口径。
    if MANUAL_REJECT:
        con.executemany("UPDATE content SET verified=0, verify_note=? WHERE id=?",
                        [(note, cid) for cid, note in MANUAL_REJECT.items()])
        con.commit()
        print(f"\n  已强制隔离人工核实的误判 {len(MANUAL_REJECT)} 条")

    # ── 过滤评论：只保留挂在已通过内容下的 ────────────────────
    n = con.execute("""DELETE FROM comment WHERE content_id IN
                     (SELECT id FROM content WHERE verified=0)""").rowcount
    con.commit()
    print(f"\n  已清理挂在未通过内容下的评论 {n} 条")

    print(f"\n【写库完成】")
    for t in ("content", "xhs_note"):
        p = con.execute(f"SELECT COUNT(*) FROM {t} WHERE verified=1").fetchone()[0]
        a = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:12s} 已校验可展示 {p:5d} / 总 {a:5d}")

    print("\n【六品牌 · 可对外展示的数据量】")
    for b in ["COSMOBOUNTY", "MitoQ", "GRANVER", "LAIFE", "W+端粒塔", "塞乐瑞"]:
        row = con.execute("""SELECT COUNT(*) n,
            SUM(CASE WHEN platform='xhs' THEN 1 ELSE 0 END) xh
            FROM content WHERE verified=1 AND brand=?""", (b,)).fetchone()
        print(f"  {b:14s}{row['n']:4d} 条（其中小红书 {row['xh'] or 0}）")
    con.close()


if __name__ == "__main__":
    main()
