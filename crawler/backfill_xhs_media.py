#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书媒体补齐（D + E 共用一条流水线）。

为什么不能直接用库里的 media_json 下载：
  小红书图片/视频都是签名 CDN 链，TTL 约 24h，事后直接请求必定 403
  （实测「库里链接直接下」成功率接近 0）。要补，只能**重新打开笔记页**，
  从页面 __INITIAL_STATE__ 里取当次签发的地址，现采现下。

做法（每条笔记一次页面访问）：
  笔记页 → __INITIAL_STATE__.note.noteDetailMap[0].note
        → imageList[].urlDefault（图片）
        → video.media.stream.h264[0].masterUrl（视频）
        → 落盘 knowledge/media/<品牌>/post|video|cover/xhs:<note_id>[_i].<ext>

不做的事：
  · 不改数据库。media_json 存的是「当时的原文链接」，是溯源证据；
    页面渲染走的是文件名索引 + 七牛映射，落盘即生效。
  · 不硬冲。每条之间 3~6s，每 25 条长停 30~60s，连续 5 条失败立即熔断。

断点续跑：目标文件已存在且 >1KB 就跳过（不会重复下载）。

用法：
  python3 crawler/backfill_xhs_media.py --dry-run                 # 看要补什么
  python3 crawler/backfill_xhs_media.py --kind video --limit 40   # 先补 40 条视频
  python3 crawler/backfill_xhs_media.py --brand 品类场景词          # 只补场景词（E）
  python3 crawler/backfill_xhs_media.py --kind both               # 图片+视频全补
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "crawler"))
sys.path.insert(0, os.path.join(ROOT, "service"))

MEDIA = os.path.join(ROOT, "knowledge", "media")
PROFILE = os.path.join(ROOT, "tools", "MediaCrawler", "browser_data", "xhs_user_data_dir")
MANIFEST = os.path.join(ROOT, "data", "xhs_media_backfill.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
MAX_IMAGE_MB = 25
MAX_VIDEO_MB = 150

PAGE_JS = r"""(nid) => {
  try {
    const s = window.__INITIAL_STATE__;
    if (!s) return {err: 'no_state', url: location.href, title: document.title};
    const nd = s.note || {};
    const map = nd.noteDetailMap || {};
    // 只认本条笔记，绝不能 fallback 到 map[keys[0]]：
    // SPA 会把上一条笔记留在 map 里，fallback 会读到上一条的图片/视频，
    // 存成本条的文件名（实测导致两条不相干笔记存下同一个视频）。
    const c = map[nid];
    const note = (c && c.note) || {};
    if (!note.noteId) return {err: 'no_note', url: location.href, title: document.title};
    if (note.noteId !== nid) return {err: 'wrong_note', got: note.noteId};
    const images = (note.imageList || []).map(i => i.urlDefault || i.urlPre ||
                    (((i.infoList || [])[0] || {}).url) || '').filter(Boolean);
    const v = note.video || null;
    const st = (v && v.media && v.media.stream) || {};
    let vurl = '', vbak = '';
    for (const codec of ['h264', 'h265', 'av1', 'h266']) {
      const a = st[codec];
      if (!a || !a[0]) continue;
      if (!vurl && a[0].masterUrl) vurl = a[0].masterUrl;
      if (!vbak && (a[0].backupUrls || []).length) vbak = a[0].backupUrls[0];
      if (vurl) break;
    }
    // 很多视频笔记的 stream 只在播放器真正起播后才写回 state，
    // 但 consumer.originVideoKey 一开始就在。它是 CDN 路径，可拼出真实地址，
    // 比"等播放器就绪"稳定得多（实测能救回大部分"无地址"的笔记）。
    const vkey = (v && v.consumer && v.consumer.originVideoKey) || '';
    return {ok: 1, note_id: note.noteId, type: note.type,
            title: (note.title || '').slice(0, 40),
            images: images, video: vurl || vbak || '', video_key: vkey};
  } catch (e) { return {err: String(e).slice(0, 120)}; }
}"""


def note_parts(url):
    """从笔记链接取 (note_id, xsec_token, xsec_source)"""
    m = re.search(r"/(?:explore|discovery/item|search_result)/([0-9a-fA-F]{16,32})", url or "")
    nid = m.group(1) if m else ""
    tok = re.search(r"[?&]xsec_token=([^&]+)", url or "")
    src = re.search(r"[?&]xsec_source=([^&]+)", url or "")
    return nid, (tok.group(1) if tok else ""), (src.group(1) if src else "pc_search")


def exists(path):
    return os.path.isfile(path) and os.path.getsize(path) > 1024


def ext_of(ctype, default):
    ctype = (ctype or "").lower()
    if "webp" in ctype:
        return ".webp"
    if "png" in ctype:
        return ".png"
    if "gif" in ctype:
        return ".gif"
    if "jpeg" in ctype or "jpg" in ctype:
        return ".jpg"
    if "mp4" in ctype or "video" in ctype:
        return ".mp4"
    return default


def fetch_to_file(url, dst, max_mb, referer="https://www.xiaohongshu.com/"):
    """流式落盘；返回 (ok, 字节数, 说明)。先试 urllib，失败再让浏览器会话兜底。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    part = dst + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            ctype = r.headers.get("Content-Type", "")
            total = 0
            with open(part, "wb") as f:
                while True:
                    b = r.read(262144)
                    if not b:
                        break
                    total += len(b)
                    if total > max_mb * 1024 * 1024:
                        raise IOError("超过大小上限")
                    f.write(b)
        if total < 1024:
            os.remove(part)
            return False, total, "文件过小"
        os.replace(part, dst)
        return True, total, ctype
    except Exception as e:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
        return False, 0, f"{type(e).__name__} {str(e)[:60]}"


def load_manifest():
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            pass
    return {"done": [], "failed": {}}


def save_manifest(m):
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    json.dump(m, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pick_jobs(kind, brand, limit, only_scene):
    import media_gap_list
    items = media_gap_list.build()
    items = [i for i in items if i["platform"] == "xhs"]
    if brand:
        items = [i for i in items if i["brand"] == brand]
    if only_scene:
        items = [i for i in items if i["brand"] == "品类场景词"]
    if kind == "video":
        items = [i for i in items if i["need_video"]]
    elif kind == "image":
        items = [i for i in items if i["miss_images"]]
    else:
        items = [i for i in items if i["need_video"] or i["miss_images"]]
    items.sort(key=lambda x: (x["brand"] != "品类场景词", -x["need_video"], -x["miss_images"]))
    return items[:limit] if limit else items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["video", "image", "both"], default="both")
    ap.add_argument("--brand")
    ap.add_argument("--scene", action="store_true", help="只补品类场景词（E）")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多处理多少条笔记")
    ap.add_argument("--sleep", type=float, default=3.0, help="每条之间的基础间隔秒")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    man = load_manifest()
    jobs = pick_jobs(a.kind, a.brand, a.limit, a.scene)
    # 已确认源笔记下架的，不再重复访问（省时间也少一次请求）
    gone_ids = set(man.get("gone") or [])
    if gone_ids:
        before = len(jobs)
        jobs = [j for j in jobs if j["id"] not in gone_ids]
        if before != len(jobs):
            print(f"跳过已确认源下架 {before - len(jobs)} 条")
    print(f"待处理 {len(jobs)} 条笔记（kind={a.kind}）")
    if a.dry_run:
        for j in jobs[:12]:
            print(f"  {j['brand']:10s} id={j['id']:<6d} 缺视频 {j['need_video']} 缺图 {j['miss_images']}")
        return
    if not jobs:
        return

    from playwright.sync_api import sync_playwright

    ok_notes = fail_notes = 0
    files_ok = files_fail = 0
    bytes_total = 0
    streak = 0
    t_start = time.time()

    with sync_playwright() as p:
        kw = dict(user_data_dir=PROFILE, headless=True, locale="zh-CN",
                  viewport={"width": 1400, "height": 900}, user_agent=UA,
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                        "--disable-gpu", "--disable-dev-shm-usage"])
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**kw)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 注意：**不能**用「拦截 media 请求」的方式取视频地址。
        # 上一条笔记的视频可能还在拉分片，拦截到的第一个请求往往是上一条的，
        # 会把 A 的视频存成 B 的文件（实测两条不相干笔记存下同一个 34MB 视频）。
        # 只认本条笔记 state 里的 masterUrl，没有就重试，宁可缺也不写错。
        for idx, j in enumerate(jobs, 1):
            nid, tok, src = note_parts(j["url"])
            if not nid:
                man["failed"][str(j["id"])] = {"reason": "url 里没有 note_id", "url": j["url"][:120]}
                fail_notes += 1
                continue
            url = f"https://www.xiaohongshu.com/explore/{nid}"
            if tok:
                url += f"?xsec_token={tok}&xsec_source={src}"

            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(2400)
                d = pg.evaluate(PAGE_JS, nid)
                # 本条笔记没进 state（首次渲染失败 / 被判定为客户端跳转）时，
                # 整页重载一次再看，绝不拿别的笔记顶替。
                if not d.get("ok") and d.get("err") in ("no_note", "wrong_note"):
                    pg.reload(wait_until="domcontentloaded", timeout=45000)
                    pg.wait_for_timeout(2600)
                    d = pg.evaluate(PAGE_JS, nid)
                # 视频地址常常不在首次 SSR 的 state 里。点一下播放试试；
                # 实测只对少数笔记有效（多数是详情接口被 300031 挡住，
                # 点多少次都拿不到），所以只试 1 轮，不在无效等待上耗时间。
                if d.get("ok") and j["need_video"] and not d.get("video") and not d.get("video_key"):
                    try:
                        pg.click("video", timeout=1800)
                    except Exception:
                        pass
                    pg.wait_for_timeout(1400)
                    d2 = pg.evaluate(PAGE_JS, nid)
                    if d2.get("ok"):
                        if d2.get("video") or d2.get("video_key"):
                            d = d2
                        elif not (d.get("images") or []):
                            d = d2
            except Exception as e:
                d = {"err": f"{type(e).__name__} {str(e)[:60]}"}

            if not d.get("ok"):
                reason = d.get("err", "unknown")
                href = d.get("url") or ""
                title = d.get("title") or ""
                blocked = "website-login" in href or "安全限制" in title
                # 300031「当前笔记暂时无法浏览」= 源笔记已下架/受限。
                # 实测对照笔记同刻可正常打开，所以这不是限流也不是解析失败，
                # 重试没有意义；单独记一类，报告里如实说"源已消失"。
                gone = ("300031" in href) or ("300030" in href) or ("页面不见了" in title)
                man["failed"][str(j["id"])] = {"reason": reason, "blocked": blocked,
                                               "gone": gone, "title": title, "url": url[:140]}
                if gone:
                    man.setdefault("gone", [])
                    if j["id"] not in man["gone"]:
                        man["gone"].append(j["id"])
                    print(f"  – {idx}/{len(jobs)} id={j['id']} 源笔记已下架（300031），跳过", flush=True)
                    time.sleep(random.uniform(1.5, 3))
                    continue
                fail_notes += 1
                streak += 1
                print(f"  ✗ {idx}/{len(jobs)} id={j['id']} {j['brand']} 打开失败 {reason}"
                      + ("（风控拦截）" if blocked else ""), flush=True)
                if blocked:
                    print("  命中风控，立即停止本轮（不硬冲）", flush=True)
                    break
                if streak >= 5:
                    print("  连续失败 5 条，停止本轮", flush=True)
                    break
                time.sleep(random.uniform(6, 10))
                continue

            streak = 0
            brand = j["brand"] or "UNKNOWN"
            got = []

            # 图片
            if a.kind in ("image", "both") and j["miss_images"] > 0:
                imgs = d.get("images") or []
                single = len(imgs) == 1
                for i, u in enumerate(imgs[:12]):
                    # 注意：ext 自带点（.jpg），模板里不能再补一个点，
                    # 否则会存成 xhs:xxx..webp，页面按文件名索引不到 = 白下。
                    name = f"xhs:{nid}{{ext}}" if single else f"xhs:{nid}_{i}{{ext}}"
                    base = os.path.join(MEDIA, brand, "post")
                    hit = None
                    for e in (".jpg", ".webp", ".png", ".gif"):
                        cand = os.path.join(base, name.format(ext=e))
                        if exists(cand):
                            hit = cand
                            break
                    if hit:
                        continue
                    tmp = os.path.join(base, name.format(ext=".dl"))
                    okdl, n, info = fetch_to_file(u, tmp, MAX_IMAGE_MB)
                    if okdl:
                        ext = ext_of(info, ".jpg")
                        final = os.path.join(base, name.format(ext=ext))
                        os.replace(tmp, final)
                        files_ok += 1
                        bytes_total += n
                        got.append(f"图{ext}")
                    else:
                        files_fail += 1
                        if os.path.exists(tmp):
                            os.remove(tmp)
                        got.append(f"图失败({info})")

            # 视频
            if a.kind in ("video", "both") and j["need_video"] > 0:
                vurl = d.get("video") or ""
                cands = [vurl] if vurl else []
                vkey = (d.get("video_key") or "").strip()
                if vkey:
                    cands += [f"https://sns-video-{h}.xhscdn.com/{vkey}"
                              for h in ("bd", "al", "qc", "hw")]
                base = os.path.join(MEDIA, brand, "video")
                dst = os.path.join(base, f"xhs:{nid}.mp4")
                if not exists(dst):
                    okdl, n, info = False, 0, "无可用地址"
                    for cu in cands:
                        okdl, n, info = fetch_to_file(cu, dst, MAX_VIDEO_MB)
                        if okdl:
                            break
                        # 分片地址有时要求带会话 cookie，交给浏览器会话再试一次
                        try:
                            rr = pg.request.get(cu, headers={"Referer": "https://www.xiaohongshu.com/"},
                                                timeout=120000)
                            if rr.ok and len(rr.body()) > 1024:
                                with open(dst, "wb") as f:
                                    f.write(rr.body())
                                okdl, n, info = True, len(rr.body()), "browser"
                                break
                        except Exception:
                            pass
                    if okdl:
                        files_ok += 1
                        bytes_total += n
                        got.append(f"视频{n//1024}KB")
                    else:
                        files_fail += 1
                        got.append(f"视频失败({info})" if cands else "视频无地址")

            # 封面（视频笔记的首图就是封面）
            if d.get("type") == "video" and (d.get("images") or []):
                base = os.path.join(MEDIA, brand, "cover")
                hit = any(exists(os.path.join(base, f"xhs:{nid}{e}")) for e in (".jpg", ".webp", ".png"))
                if not hit:
                    okdl, n, info = fetch_to_file(d["images"][0], os.path.join(base, f"xhs:{nid}.tmp"),
                                                  MAX_IMAGE_MB)
                    if okdl:
                        os.replace(os.path.join(base, f"xhs:{nid}.tmp"),
                                   os.path.join(base, f"xhs:{nid}{ext_of(info, '.jpg')}"))
                        files_ok += 1
                        bytes_total += n

            man["done"].append(j["id"])
            man["failed"].pop(str(j["id"]), None)
            ok_notes += 1
            print(f"  ✓ {idx}/{len(jobs)} id={j['id']} {brand:8s} {d.get('type'):5s} "
                  f"{' '.join(got) if got else '文件已在盘'}", flush=True)

            if idx % 15 == 0:
                save_manifest(man)
            time.sleep(random.uniform(a.sleep * 0.7, a.sleep * 1.6))
            if idx % 25 == 0:
                pause = random.uniform(30, 60)
                print(f"    …长停 {pause:.0f}s（{idx}/{len(jobs)}）", flush=True)
                time.sleep(pause)

        ctx.close()

    save_manifest(man)
    dur = time.time() - t_start
    print(f"\n完成：笔记成功 {ok_notes} 失败 {fail_notes}；文件成功 {files_ok} 失败 {files_fail}；"
          f"共 {bytes_total/1024/1024:.0f}MB；耗时 {dur/60:.1f} 分钟")
    print("下一步：python3 crawler/upload_qiniu.py   # 把新文件推到七牛并更新映射")


if __name__ == "__main__":
    main()
