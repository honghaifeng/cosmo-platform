#!/usr/bin/env bash
# 修复「逆龄观察」详情页媒体全被判「未落地」的问题。
#
# 症状：打开任一条视频内容，页面写着
#   「该条原帖是视频，视频文件未落地本站（原帖签名链接已过期）」
# 而视频其实好好躺在七牛上（2183 个，/cdn/ 直取 200）。
#
# 根因：service/app.py 的 media_cdn_map() 只找 service/data/media_cdn.json，
# 但媒体映射表一直在数据根目录 data/media_cdn.json。开发机上 upload_qiniu.py
# 会同时写两份，所以本地永远是对的；服务器上只放了根目录那一份，
# 于是读到空表 → 索引退化成「只扫本地磁盘」→ 服务器不落地 16.9GB 媒体
# → 5996 条内容的封面/视频全部判成「未落地」。
# 更冤的是 niling.service 里早就设了 COSMOBOUNTY_CDN_MAP 指向正确路径，
# 只是 app.py 从来没读这个变量（wbdata.py 读了，所以工作台一直正常）。
#
# 幂等：已经修过就直接跳过。
#
# 用法（在服务器上）：
#   bash fix-niling-media.sh              # 修复并重启
#   bash fix-niling-media.sh --dry-run    # 只看会改什么
set -uo pipefail

APP=${COSMOBOUNTY_APP:-/data/apps/cosmobounty}
PY=${COSMOBOUNTY_APP}/service/app.py
SVC=niling
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

die() { printf '\033[31m失败：%s\033[0m\n' "$*"; exit 1; }
say() { printf '\n\033[36m%s\033[0m\n' "$*"; }

[ -f "$PY" ] || die "找不到 $PY（用 COSMOBOUNTY_APP 指定应用目录）"
command -v python3 >/dev/null || die "需要 python3"

if grep -q "COSMOBOUNTY_CDN_MAP" "$PY"; then
  echo "app.py 已经读过 COSMOBOUNTY_CDN_MAP，无需修改。"
else
  say "1. 修改 service/app.py 的映射表查找逻辑"
  [ "$DRY" = "1" ] && echo "  （dry-run，不写入）"
  python3 - "$PY" "$DRY" <<'PY'
import re, sys
p, dry = sys.argv[1], sys.argv[2] == '1'
s = open(p, encoding='utf-8').read()

new = '''def media_cdn_map():
    """本地媒体 → 七牛对象 key 的映射（上传脚本产出）。

    ⚠️ 这份表在「项目根/data/media_cdn.json」，不在 service/data/ 下。
    以前这里写的是 os.path.join(HERE, "data", ...)，即 service/data/ ——
    开发机上 upload_qiniu.py 会同时写两份，所以本地一直是对的；
    而服务器上只有根目录那一份，读不到表 → _media_index() 退化成
    「只扫本地磁盘」→ 服务器不落地 16.9GB 媒体 → 所有封面/视频判成未落地。
    现在优先读 niling.service 里已经设好的 COSMOBOUNTY_CDN_MAP。
    """
    global _MEDIA_CDN
    if _MEDIA_CDN is None:
        root = os.environ.get("COSMOBOUNTY_ROOT") or os.path.dirname(HERE)
        cands = [
            os.environ.get("COSMOBOUNTY_CDN_MAP"),
            os.path.join(root, "data", "media_cdn.json"),
            os.path.join(HERE, "data", "media_cdn.json"),
        ]
        _MEDIA_CDN = {}
        for f in cands:
            if not f or not os.path.exists(f):
                continue
            try:
                m = json.load(open(f, encoding="utf-8"))
            except Exception as e:
                print("[warn] 媒体映射表读取失败 %s: %s" % (f, e), file=sys.stderr)
                continue
            if m:
                _MEDIA_CDN = m
                break
        if not _MEDIA_CDN:
            print("[warn] 媒体映射表为空/未找到，封面与视频会全部显示未落地", file=sys.stderr)
    return _MEDIA_CDN'''

pat = re.compile(r'def media_cdn_map\(\):.*?\n    return _MEDIA_CDN\n', re.S)
if not pat.search(s):
    print("  没能定位 media_cdn_map()，app.py 版本可能不同，请人工检查")
    sys.exit(2)
out = pat.sub(new + '\n', s, count=1)
print("  函数体 %d 字符 → %d 字符" % (len(pat.search(s).group(0)), len(new) + 1))
if not dry:
    open(p, 'w', encoding='utf-8').write(out)
    print("  已写入")
PY
  [ $? -eq 2 ] && die "app.py 结构与预期不符"
fi

if [ "$DRY" = "1" ]; then say "dry-run 结束，未重启服务"; exit 0; fi

say "2. 重启 $SVC"
if command -v systemctl >/dev/null; then
  sudo systemctl restart "$SVC" || die "重启失败（看 journalctl -u $SVC -n 30）"
  sleep 2
  sudo systemctl is-active "$SVC" | sed 's/^/  /'
else
  echo "  没有 systemctl，请手工重启服务"
fi

say "3. 验证（应能看到 /cdn/media/... 的视频地址）"
TOKEN=""
[ -f "$APP/data/token.txt" ] && TOKEN=$(cat "$APP/data/token.txt")
PORT=${PORT:-8098}
ID=$(curl -s -m 10 "http://127.0.0.1:$PORT/api/list?token=$TOKEN&limit=20" \
     | python3 -c "import sys,json;d=json.load(sys.stdin);print(next((x['id'] for x in d.get('items',[]) if x.get('platform')=='douyin'),''))" 2>/dev/null)
if [ -z "$ID" ]; then echo "  取样本失败，请手工打开 /niling/ 看一条视频内容"; exit 0; fi
curl -s -m 10 "http://127.0.0.1:$PORT/api/item?id=$ID&token=$TOKEN" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
ml = d.get('media_local') or {}
v = ml.get('video') or ''
print('  样本 id=%s  %s' % (d.get('id'), d.get('platform')))
print('  video      :', v or '(空)')
print('  cover      :', ml.get('cover') or '(空)')
print('  images     :', len(ml.get('images') or []))
print()
if v.startswith('/cdn/'):
    print('  ✅ 修好了：视频已识别，页面会显示「播放/下载」而不是「未落地」')
else:
    print('  ❌ 仍是空。检查下面两件事：')
    print('     1) ls -l $APP/data/media_cdn.json  （文件在不在、是不是 800KB+）')
    print('     2) systemctl show niling -p Environment | tr \' \' \'\\n\' | grep CDN_MAP')
"
