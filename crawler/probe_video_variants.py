#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在**独立临时 profile**里测同一条笔记，看看视频流地址到底受什么影响。

为什么用临时 profile：主 profile 正被补齐任务占用（SingletonLock），
且我们要测「匿名/无 token」这类变体，必须在干净环境里跑才准。

用法：python3 crawler/probe_video_variants.py <note_id> [老token]
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")

PROBE = r"""(nid) => {
  const o = {url: location.href, title: document.title};
  const s = window.__INITIAL_STATE__;
  if (!s || !s.note) { o.err = 'no_state'; return o; }
  const map = s.note.noteDetailMap || {};
  o.mapKeys = Object.keys(map).slice(0, 4);
  const c = map[nid];
  if (!c || !c.note) { o.err = 'no_note_for_id'; return o; }
  const n = c.note;
  o.type = n.type;
  o.nImages = (n.imageList || []).length;
  o.hasVideoObj = !!n.video;
  const st = (n.video && n.video.media && n.video.media.stream) || {};
  o.codecs = Object.keys(st);
  o.masterUrl = ((st.h264 || [])[0] || {}).masterUrl || '';
  o.videoId = (n.video && n.video.media && n.video.media.videoId) || '';
  o.originKey = (n.video && n.video.consumer && n.video.consumer.originVideoKey) || '';
  return o;
}"""


def main():
    nid = sys.argv[1]
    tok = sys.argv[2] if len(sys.argv) > 2 else ""
    variants = [("无 token", f"https://www.xiaohongshu.com/explore/{nid}")]
    if tok:
        variants.append(("带老 token",
                         f"https://www.xiaohongshu.com/explore/{nid}?xsec_token={tok}&xsec_source=pc_search"))
    variants.append(("搜索页取新链接", f"https://www.xiaohongshu.com/search_result?keyword={nid}"))

    from playwright.sync_api import sync_playwright
    tmp = tempfile.mkdtemp(prefix="xhs_probe_")
    with sync_playwright() as p:
        kw = dict(user_data_dir=tmp, headless=True, locale="zh-CN",
                  viewport={"width": 1400, "height": 900}, user_agent=UA,
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**kw)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        for name, u in variants:
            try:
                pg.goto(u, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(2600)
                d = pg.evaluate(PROBE, nid)
            except Exception as e:
                d = {"err": f"{type(e).__name__} {str(e)[:80]}"}
            print(f"--- {name} ---")
            print(json.dumps(d, ensure_ascii=False, indent=1)[:900])
        # 搜索页里能否拿到带新 token 的笔记卡片链接
        try:
            pg.goto(f"https://www.xiaohongshu.com/search_result?keyword={nid}",
                    wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(3000)
            links = pg.evaluate("""(nid) => Array.from(document.querySelectorAll('a[href*="xsec_token"]'))
                     .map(a => a.getAttribute('href')).filter(h => h.includes(nid)).slice(0,3)""", nid)
            print("--- 搜索页匹配卡片 ---")
            print(json.dumps(links, ensure_ascii=False, indent=1))
        except Exception as e:
            print("搜索页失败:", e)
        ctx.close()


if __name__ == "__main__":
    main()
