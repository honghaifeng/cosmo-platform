#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探路：能否在页面内用 /api/sns/web/v1/feed 直接拿笔记详情（含视频流地址）。

动机：逐条开笔记页 = 一次完整页面渲染，量大必招"安全验证"（实测 35 条就停）。
接口方式一条笔记只有 1 个 XHR，不渲染、不加载播放器，风控特征小得多，
而且详情接口返回的是**当次新签发**的 CDN 地址，不存在签名过期问题。

签名靠页面自带的 window._webmsxyw（小红书自己的 XHR 签名函数）。
本脚本只做可行性验证 + 打印结果，不改任何数据。

用法：python3 crawler/probe_feed_api.py <note_id> [xsec_token] [xsec_source]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "tools", "MediaCrawler", "browser_data", "xhs_user_data_dir")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")

JS = r"""async ([nid, tok, src]) => {
  const out = {hasSigner: typeof window._webmsxyw};
  const path = '/api/sns/web/v1/feed';
  const payload = {source_note_id: nid, image_formats: ['jpg', 'webp', 'avif'],
                   extra: {need_body_topic: '1'},
                   xsec_source: src || 'pc_search', xsec_token: tok || ''};
  let hdrs = {'Content-Type': 'application/json'};
  try {
    if (typeof window._webmsxyw === 'function') {
      const s = window._webmsxyw(path, JSON.stringify(payload));
      if (s && s['X-s']) { hdrs['X-s'] = s['X-s']; hdrs['X-t'] = String(s['X-t']); }
      out.signKeys = s ? Object.keys(s) : null;
    }
  } catch (e) { out.signErr = String(e).slice(0, 100); }
  try {
    const r = await fetch(path, {method: 'POST', headers: hdrs,
                                body: JSON.stringify(payload), credentials: 'include'});
    out.status = r.status;
    const j = await r.json();
    out.code = j.code;
    out.msg = j.msg;
    const items = (j.data && j.data.items) || [];
    out.nItems = items.length;
    if (!items.length) return out;
    const n = items[0].note_card || {};
    const nc = items[0];
    out.noteKeys = Object.keys(n).slice(0, 30);
    out.type = n.type;
    out.nImages = (n.image_list || []).length;
    const v = n.video || null;
    out.hasVideo = !!v;
    if (v) {
      out.videoKeys = Object.keys(v);
      const st = (v.media && v.media.stream) || {};
      out.codecs = Object.keys(st);
      out.masterUrl = ((st.h264 || [])[0] || {}).master_url || '';
    }
    out.topKeys = Object.keys(nc).slice(0, 12);
    return out;
  } catch (e) { out.fetchErr = String(e).slice(0, 160); return out; }
}"""


def main():
    nid = sys.argv[1]
    tok = sys.argv[2] if len(sys.argv) > 2 else ""
    src = sys.argv[3] if len(sys.argv) > 3 else "pc_search"
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
        pg.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(2500)
        d = pg.evaluate(JS, [nid, tok, src])
        print(json.dumps(d, ensure_ascii=False, indent=1))
        ctx.close()


if __name__ == "__main__":
    main()
