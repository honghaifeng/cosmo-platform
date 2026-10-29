#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断单条笔记页的 state 结构（排查"取不到视频地址"时用）。

用法：
  python3 crawler/debug_note_state.py <note_url_or_note_id>
打印：页面标题 / state 里有哪些 note / note 顶层 key / video 子树 / 图片数。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "tools", "MediaCrawler", "browser_data", "xhs_user_data_dir")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")

DUMP_JS = r"""(nid) => {
  const out = {href: location.href, title: document.title, nid};
  const s = window.__INITIAL_STATE__;
  if (!s) { out.err = 'no __INITIAL_STATE__'; return out; }
  const nd = s.note || {};
  out.noteKeys = Object.keys(nd);
  const map = nd.noteDetailMap || {};
  out.mapKeys = Object.keys(map);
  const c = map[nid];
  out.hasCurrent = !!c;
  if (!c) { out.mapSample = JSON.stringify(Object.keys(map).slice(0, 3)); return out; }
  out.commentKeys = Object.keys(c);
  const n = c.note || {};
  out.noteTopKeys = Object.keys(n);
  out.noteType = n.type;
  out.nImages = (n.imageList || []).length;
  const im0 = (n.imageList || [])[0] || {};
  out.image0Keys = Object.keys(im0);
  out.image0Sample = {urlDefault: (im0.urlDefault || '').slice(0, 90),
                      urlPre: (im0.urlPre || '').slice(0, 90)};
  const v = n.video;
  out.videoRaw = v === undefined ? 'undefined' : (v === null ? 'null' : typeof v);
  if (v && typeof v === 'object') {
    out.videoKeys = Object.keys(v);
    out.consumer = v.consumer ? Object.keys(v.consumer) : null;
    out.originVideoKey = (v.consumer && v.consumer.originVideoKey) || '';
    out.videoId = v.media ? v.media.videoId : '';
    const st = (v.media && v.media.stream) || {};
    out.streamCodecs = Object.keys(st);
    out.h264 = (st.h264 || []).map(x => ({masterUrl: (x.masterUrl || '').slice(0, 80),
                                          backupUrls: (x.backupUrls || []).map(u => u.slice(0, 80))}));
  }
  // 兜底：全文搜索 state 里像视频地址的字符串
  try {
    const blob = JSON.stringify(s);
    const hits = (blob.match(/https?:[^"']*(?:\.mp4|\.m4s)[^"']*/g) || []);
    out.mp4Urls = Array.from(new Set(hits)).slice(0, 6).map(u => u.slice(0, 130));
  } catch (e) { out.mp4Urls = ['scan_failed']; }
  return out;
}"""


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    m = re.search(r"([0-9a-fA-F]{24})", arg)
    if not m:
        print("用法：python3 crawler/debug_note_state.py <note_url_or_note_id>")
        return
    nid = m.group(1)
    tok = re.search(r"[?&]xsec_token=([^&]+)", arg)
    src = re.search(r"[?&]xsec_source=([^&]+)", arg)
    url = f"https://www.xiaohongshu.com/explore/{nid}"
    if tok:
        url += f"?xsec_token={tok.group(1)}&xsec_source={src.group(1) if src else 'pc_search'}"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = dict(user_data_dir=PROFILE, headless=True, locale="zh-CN",
                  viewport={"width": 1400, "height": 900}, user_agent=UA,
                  args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**kw)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3000)
        d = pg.evaluate(DUMP_JS, nid)
        print(json.dumps(d, ensure_ascii=False, indent=1))
        ctx.close()


if __name__ == "__main__":
    main()
