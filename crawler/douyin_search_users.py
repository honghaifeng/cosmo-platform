#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""douyin_search_users.py — 抖音用户搜索：找品牌矩阵号。

在已登录 dy_user_data_dir 的浏览器里打开搜索页（type=user），
抓出候选账号（昵称 + sec_uid + 粉丝数 + 认证），输出 JSON 供人工核实后爬取。

用法：
  python3 crawler/douyin_search_users.py --kw "LAIFE"
  python3 crawler/douyin_search_users.py --kw "乐梵" --kw "红宝瓶"
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MC = os.path.join(ROOT, "tools", "MediaCrawler")
PROFILE = os.path.join(MC, "browser_data", "dy_user_data_dir")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def clear_profile_locks():
    import glob
    subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], capture_output=True)
    time.sleep(1)
    for p in glob.glob(os.path.join(PROFILE, "Singleton*")):
        try:
            os.remove(p)
        except OSError:
            pass


async def search(kws):
    sys.path.insert(0, MC)
    from playwright.async_api import async_playwright
    clear_profile_locks()
    out = {}
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE, headless=False, user_agent=UA,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            for kw in kws:
                url = f"https://www.douyin.com/search/{kw}?type=user"
                try:
                    await page.goto(url, timeout=45000, wait_until="domcontentloaded")
                except Exception as e:
                    print(f"[warn] {kw} 搜索页加载失败：{e}")
                    continue
                await page.wait_for_timeout(6000)
                # 用户卡片：链接 /user/MS4w... ，昵称在卡片文本里
                cards = await page.evaluate(
                    """() => {
                      const out = [];
                      document.querySelectorAll('a[href*="/user/MS4w"]').forEach(a => {
                        const card = a.closest('li') || a.closest('div[class*="card"]') || a.parentElement;
                        out.push({href: a.getAttribute('href'), text: (card ? card.innerText : a.innerText) || ''});
                      });
                      return out;
                    }""")
                n = 0
                for c in cards:
                    m = re.search(r"/user/(MS4w[A-Za-z0-9_-]+)", c["href"] or "")
                    if not m:
                        continue
                    uid = m.group(1)
                    if uid in out:
                        continue
                    txt = (c["text"] or "").strip().replace("\n", " ｜ ")[:160]
                    out[uid] = {"kw": kw, "text": txt}
                    n += 1
                print(f"[{kw}] 候选 {n} 个（累计 {len(out)}）", flush=True)
                await page.wait_for_timeout(4000)
        finally:
            await ctx.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", action="append", required=True)
    ap.add_argument("--out", default="/tmp/dy_users.json")
    a = ap.parse_args()
    res = asyncio.run(search(a.kw))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"已写入 {a.out}（{len(res)} 个候选）")


if __name__ == "__main__":
    main()
