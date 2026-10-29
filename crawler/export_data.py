#!/usr/bin/env python3
"""对外数据导出（唯一入口）——只导 verified=1 的内容与其涉及账号。

产物（exports/data/）：
  content_full_<date>.csv   全量品牌内容宽表
  creators_<date>.csv       内容涉及账号表

为什么要有这个脚本：这两个 CSV 之前是临时手搓的，数据一变就与库脱节，
`check_data_boundary.py` 会立刻报「越界 N 行」。固定成脚本后，
每次数据变更只要跑一次，导出口就不会漏未校验内容。
"""
import csv, datetime, json, os, sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'db', 'cosmobounty.db')
OUT = os.path.join(BASE, 'exports', 'data')

PLAT_CN = {'xhs': '小红书', 'douyin': '抖音', 'baidu': '百度',
           'toutiao': '今日头条', 'web': '新闻源', 'shipinhao': '视频号'}


def likes_of(mj):
    if not mj:
        return 0, 0, 0
    try:
        d = json.loads(mj)
    except Exception:
        return 0, 0, 0
    return (d.get('likes', 0) or 0, d.get('collects', 0) or 0, d.get('comments', 0) or 0)


def main():
    os.makedirs(OUT, exist_ok=True)
    today = datetime.date.today().isoformat()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # ── content_full ────────────────────────────────────────
    rows = con.execute("""SELECT brand, platform, content_type, publish_time, collect_time,
                                 author, title, metrics_json, body, url
                          FROM content WHERE verified=1
                          ORDER BY brand, publish_time DESC""").fetchall()
    p1 = os.path.join(OUT, f'content_full_{today}.csv')
    with open(p1, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['品牌', '平台', '类型', '发布时间', '采集时间', '作者', '标题',
                    '点赞', '收藏', '评论', '正文字数', '链接'])
        for r in rows:
            lk, co, cm = likes_of(r['metrics_json'])
            w.writerow([r['brand'], PLAT_CN.get(r['platform'], r['platform']),
                        r['content_type'] or '', (r['publish_time'] or '')[:16],
                        (r['collect_time'] or '')[:19], r['author'] or '',
                        r['title'] or '', lk, co, cm, len(r['body'] or ''), r['url']])
    print(f'content_full: {len(rows)} 行 → {os.path.relpath(p1, BASE)}')

    # ── creators（只含 verified 内容涉及的账号）────────────
    crow = con.execute("""SELECT platform, creator_id, nickname, profile_url, note_count,
                                 total_likes, total_collects, total_comments,
                                 first_publish, last_publish, brands
                          FROM creator
                          WHERE creator_id IN (
                            SELECT DISTINCT author_id FROM content
                            WHERE verified=1 AND author_id IS NOT NULL AND author_id != '')
                          ORDER BY platform, total_likes DESC""").fetchall()
    p2 = os.path.join(OUT, f'creators_{today}.csv')
    with open(p2, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['平台', '账号ID', '昵称', '主页链接', '发帖数', '点赞', '收藏', '评论',
                    '最早发布', '最新发布', '涉及品牌'])
        for r in crow:
            w.writerow([PLAT_CN.get(r['platform'], r['platform']), r['creator_id'],
                        r['nickname'] or '', r['profile_url'] or '', r['note_count'] or 0,
                        r['total_likes'] or 0, r['total_collects'] or 0, r['total_comments'] or 0,
                        (r['first_publish'] or '')[:16], (r['last_publish'] or '')[:16],
                        r['brands'] or ''])
    print(f'creators:     {len(crow)} 行 → {os.path.relpath(p2, BASE)}')

    # 清掉旧日期的同名产物，避免边界检查扫到过期文件
    for f in os.listdir(OUT):
        if not f.endswith('.csv'):
            continue
        keep = (f.startswith('content_full_') and f == os.path.basename(p1)) or \
               (f.startswith('creators_') and f == os.path.basename(p2))
        if not keep:
            os.remove(os.path.join(OUT, f))
            print(f'  已清理过期导出 {f}')
    con.close()


if __name__ == '__main__':
    main()
