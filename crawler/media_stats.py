#!/usr/bin/env python3
"""媒体素材库现状统计 —— 扫描 knowledge/media 实际落盘文件，产出可重跑的统计。

背景：manifest.json 是 10-23 的「下载台账」（69 条，36 成功 / 33 直链过期），
之后抖音官号素材批量下载（1,200+ 文件）没有进台账，导致各处仍显示「36 个」。
本脚本不改台账语义，只补一份「现状快照」，并可回写文档里的数字。

用法：
  python3 crawler/media_stats.py                # 只统计，打印
  python3 crawler/media_stats.py --write         # 统计 + 写 media_stats.json + 回写文档数字
"""
import json, os, sys, time
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(BASE, 'knowledge', 'media')
BRANDS = ['COSMOBOUNTY', 'GRANVER', 'LAIFE', 'MitoQ', 'W+端粒塔', '塞乐瑞']
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.webm'}
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def scan_service():
    """线上可访问的素材子集（service/knowledge/media，只放图片，视频走七牛 CDN）。"""
    root = os.path.join(BASE, 'service', 'knowledge', 'media')
    if not os.path.isdir(root):
        return None
    n = img = vid = 0
    size = 0
    for r, _, names in os.walk(root):
        for f in names:
            ext = os.path.splitext(f)[1].lower()
            if ext not in VIDEO_EXT and ext not in IMAGE_EXT:
                continue
            try:
                s = os.path.getsize(os.path.join(r, f))
            except OSError:
                continue
            n += 1; size += s
            if ext in VIDEO_EXT:
                vid += 1
            else:
                img += 1
    return {'files': n, 'image': img, 'video': vid, 'bytes': size}


def scan():
    stats = {}
    files = []
    for brand in BRANDS:
        bdir = os.path.join(MEDIA, brand)
        if not os.path.isdir(bdir):
            continue
        v = i = 0
        vb = ib = 0
        for root, _, names in os.walk(bdir):
            for n in names:
                ext = os.path.splitext(n)[1].lower()
                if ext not in VIDEO_EXT and ext not in IMAGE_EXT:
                    continue
                p = os.path.join(root, n)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                rel = os.path.relpath(p, BASE)
                if ext in VIDEO_EXT:
                    v += 1; vb += sz
                else:
                    i += 1; ib += sz
                files.append({'path': rel, 'brand': brand, 'kind': 'video' if ext in VIDEO_EXT else 'post', 'bytes': sz})
        stats[brand] = {'video': v, 'image': i, 'files': v + i,
                        'video_bytes': vb, 'image_bytes': ib, 'bytes': vb + ib}
    total = {k: sum(s[k] for s in stats.values()) for k in ('video', 'image', 'files', 'bytes')}
    return stats, total, files


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f}{unit}' if unit in ('B', 'KB') else f'{n:.1f}{unit}'
        n /= 1024


def text_stats():
    """OCR / ASR 文本量，取库内 media_text。"""
    import sqlite3
    p = os.path.join(BASE, 'db', 'cosmobounty.db')
    if not os.path.exists(p):
        return None
    c = sqlite3.connect(p)
    out = {}
    for kind, n, chars in c.execute('select kind, count(*), coalesce(sum(length(text)),0) from media_text group by kind'):
        out[kind] = {'rows': n, 'chars': chars}
    return out


def patch_docs(stats, total, tstats, svc=None):
    """回写三处文档数字。带标记的块整段替换，未带标记的按行正则替换。"""
    import re
    tag = time.strftime('%Y-%m-%d %H:%M')
    v, i = total['video'], total['image']
    files, size = total['files'], human(total['bytes'])
    dist = ' / '.join(f'{b} {stats[b]["files"]}' for b in BRANDS if b in stats)

    # 1) 工作台素材页（标记块）
    wp = os.path.join(BASE, 'workbench', 'v2', 'create-assets.html')
    if os.path.exists(wp):
        s = open(wp).read()
        ocr = tstats.get('ocr', {}) if tstats else {}
        asr = tstats.get('asr', {}) if tstats else {}
        ocr_w = f"{ocr.get('chars',0)/10000:.1f}万" if ocr else '—'
        asr_w = f"{asr.get('chars',0)/10000:.1f}万" if asr else '—'
        tot_w = f"{((ocr.get('chars',0)+asr.get('chars',0))/10000):.1f}万" if ocr else '—'
        if svc and svc['files']:
            online_note = f"（其中线上可访问 {svc['image']:,} 张图片 / {human(svc['bytes'])}；{v} 个视频走七牛 CDN，不进发布包）"
        else:
            online_note = ''
        block = f'''<!--MEDIA_STATS_START 由 crawler/media_stats.py --write 自动生成，勿手改-->
  <div class="grid g4" style="margin-bottom:14px">
    <div class="card"><div class="k">内容配图（post）</div><div class="v">{i:,} <small>张</small></div><div class="s">六品牌全部本地落盘 · 无过期直链</div></div>
    <div class="card"><div class="k">视频（video）</div><div class="v">{v:,} <small>个</small></div><div class="s">含抖音官号矩阵全量 · {size}</div></div>
    <div class="card"><div class="k">OCR / ASR 文本</div><div class="v" style="font-size:18px">{tot_w} 字</div><div class="s">OCR {ocr.get('rows',0):,} 条（{ocr_w}）+ ASR {asr.get('rows',0):,} 条（{asr_w}）</div></div>
    <div class="card"><div class="k">logo / 产品白底图</div><div class="v" style="color:var(--red)">0</div><div class="s">待人工下载补齐 · 创作参考图源</div></div>
  </div>
  <p class="sub" style="font-size:12.5px;color:#8a909c;margin:-4px 0 14px">统计时点 {tag} · 本地素材库合计 {files:,} 个文件 / {size}{online_note}（抖音官号素材仍在下载，数字会继续增长）</p>
  <!--MEDIA_STATS_END-->'''
        new, n = re.subn(r'<!--MEDIA_STATS_START.*?<!--MEDIA_STATS_END-->', block, s, flags=re.S)
        if n:
            open(wp, 'w').write(new)
            print(f'  已回写 {os.path.relpath(wp, BASE)}')
        # tabs 里的品牌括号数字
        new = new
        for b in BRANDS:
            if b in stats:
                new = re.sub(rf'(筛选：knowledge/media/{re.escape(b)}/\'\)">){re.escape(b)}(（[\d,]+）)?',
                             rf"\g<1>{b}（{stats[b]['files']}）", new)
        if new != open(wp).read():
            open(wp, 'w').write(new)
            print('  已更新品牌筛选按钮计数')

    # 2) 知识库总纲（两处：表格行 + 说明段）
    for rp in [os.path.join(BASE, 'knowledge', 'README.md'),
               os.path.join(BASE, 'service', 'knowledge', 'README.md')]:
        if not os.path.exists(rp):
            continue
        s = open(rp).read()
        s = re.sub(r'\| 7 \| 媒体素材库 \|[^\n]*\n',
                   f'| 7 | 媒体素材库 | `media/<品牌>/{{logo,product,post,video}}/` | ✅ 已落地 **{files:,} 个文件**（{v} 视频 + {i} 图片，{size}），官方 logo 与产品白底图待人工补 | 拆解对比、投放反制、AI 分析的视觉输入 |\n',
                   s)
        s = re.sub(r'- \*\*当前规模（[^）]*）\*\*：[^\n]*\n',
                   f'- **当前规模（{tag} 扫描）**：{dist}，合计 **{v} 视频 + {i} 图片 = {files:,} 文件 / {size}**\n',
                   s)
        open(rp, 'w').write(s)
        print(f'  已回写 {os.path.relpath(rp, BASE)}')


def main():
    write = '--write' in sys.argv
    stats, total, files = scan()
    svc = scan_service()
    snap = {
        'scanned_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'root': 'knowledge/media',
        'total': total,
        'by_brand': stats,
        'service_subset': svc,
        'note': '由 crawler/media_stats.py 扫描实际落盘文件生成；manifest.json 为下载台账（含失败记录），两者语义不同。',
    }

    print(f"扫描时点 {snap['scanned_at']}")
    print(f"{'品牌':<14}{'视频':>7}{'图片':>7}{'合计':>8}{'体积':>10}")
    for b in BRANDS:
        s = stats.get(b)
        if not s:
            continue
        print(f"{b:<14}{s['video']:>7}{s['image']:>7}{s['files']:>8}{human(s['bytes']):>10}")
    print(f"{'合计':<14}{total['video']:>7}{total['image']:>7}{total['files']:>8}{human(total['bytes']):>10}")

    if not write:
        print('\n（未写盘，加 --write 生效）')
        return

    out = os.path.join(MEDIA, 'media_stats.json')
    with open(out, 'w') as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(f'\n已写 {os.path.relpath(out, BASE)}')

    # 同步到 manifest 的 meta 段（保留原有 items 台账）
    mpath = os.path.join(MEDIA, 'manifest.json')
    if os.path.exists(mpath):
        m = json.load(open(mpath))
        m['meta'] = {
            'ledger_items': len(m.get('items', [])),
            'ledger_ok': sum(1 for i in m.get('items', []) if i.get('status') == 'ok'),
            'ledger_expired': sum(1 for i in m.get('items', []) if i.get('status') != 'ok'),
            'scanned_at': snap['scanned_at'],
            'total_files': total['files'],
            'total_bytes': total['bytes'],
            'by_brand': {b: stats[b]['files'] for b in stats},
            'note': 'items = 2026-10-23 首次批次的下载台账（含 33 条直链过期失败记录）；'
                    'total_files 为当前实际落盘数，详见 media_stats.json。',
        }
        json.dump(m, open(mpath, 'w'), ensure_ascii=False, indent=1)
        print('已更新 manifest.json meta 段')

    # 服务副本
    svc_dir = os.path.join(BASE, 'service', 'knowledge', 'media')
    if os.path.isdir(svc_dir):
        with open(os.path.join(svc_dir, 'media_stats.json'), 'w') as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        print('已同步 service/knowledge/media/media_stats.json')

    print('\n回写文档：')
    tstats = text_stats()
    if tstats:
        for k, s in tstats.items():
            print(f"  {k}: {s['rows']} 条 / {s['chars']/10000:.1f} 万字")
    patch_docs(stats, total, tstats, svc)


if __name__ == '__main__':
    main()
