#!/bin/bash
# 打「统一底座」离线安装包，并上传七牛
#
# 为什么要有这个脚本：
#   上一次的包是手工 tar 出来的，结果漏了 lib/sr-cdn.js 和 data/sr-cdn.json，
#   装到服务器上会让星河所有 AI 图/视频 404 —— 而且没人会发现，直到打开资产库。
#   打包口径必须固定下来、可复现，不能再靠记性。
#
# 用法：
#   bash scripts/build_install_pkg.sh            # 打包 + 上传
#   bash scripts/build_install_pkg.sh --no-upload # 只打包
#
# 产物：dist/cb-<时间戳>.tgz，并上传到七牛 install/ 下（含 cb-latest.tgz 便于固定地址）
set -euo pipefail

CB="$(cd "$(dirname "$0")/.." && pwd)"          # CosmoBounty
# 星河在另一个仓库里，目录名可能是 starbox-clone（历史）或 starriver
if [ -n "${STARRIVER_PATH:-}" ]; then SB="$STARRIVER_PATH"
elif [ -d "$CB/../starbox-clone" ]; then SB="$(cd "$CB/../starbox-clone" && pwd)"
elif [ -d "$CB/../starriver" ]; then SB="$(cd "$CB/../starriver" && pwd)"
else echo "找不到星河代码目录（starbox-clone/ 或 starriver/），可用 STARRIVER_PATH 指定"; exit 1; fi
TS=$(date +%Y%m%d_%H%M)
NAME="cb-$TS"
STAGE="$CB/dist/$NAME"
OUT="$CB/dist/$NAME.tgz"
PY="${PY:-/Users/v/.workbuddy/binaries/python/versions/3.13.12/bin/python3}"

UPLOAD=1
[ "${1:-}" = "--no-upload" ] && UPLOAD=0

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "0. 前置检查（宁可不打，也不打半个坏包）"
need=(
  "$CB/deploy/install.sh"
  "$CB/service/app.py" "$CB/service/wbdata.py"
  "$CB/db/cosmobounty.db"
  "$CB/data/media_cdn.json"
  "$SB/server.js" "$SB/lib/sr-cdn.js" "$SB/data/sr-cdn.json"
  "$SB/public/wb/index.html"
)
for f in "${need[@]}"; do
  [ -e "$f" ] || { echo "  ✗ 缺少必需文件：$f"; exit 1; }
  echo "  ✓ ${f#$CB/}"
done
# 私有签名配置必须是 https，否则部署后站点上仍会混合内容告警
HOST=$($PY -c "import json;print(json.load(open('$SB/data/sr-cdn.json'))['host'])")
case "$HOST" in
  https://*) echo "  ✓ 私有 CDN host = $HOST" ;;
  *) echo "  ✗ sr-cdn.json 的 host 不是 https（当前 $HOST），先修再打包"; exit 1 ;;
esac

say "1. 建 staging"
rm -rf "$STAGE"; mkdir -p "$STAGE/cosmobounty" "$STAGE/starbox"
cp "$CB/deploy/install.sh" "$STAGE/install.sh"

say "2. 底座代码（排除 knowledge/media/大文件库）"
cd "$CB"
rsync -a --exclude '__pycache__/' --exclude '*.pyc' \
      --exclude 'knowledge/' --exclude 'data/cosmobounty.db*' --exclude 'data/media_cdn.json' \
      service/ "$STAGE/cosmobounty/service/"
mkdir -p "$STAGE/cosmobounty/data" "$STAGE/cosmobounty/db" "$STAGE/cosmobounty/config"
cp db/cosmobounty.db            "$STAGE/cosmobounty/db/cosmobounty.db"
cp data/media_cdn.json          "$STAGE/cosmobounty/data/media_cdn.json"
cp service/data/token.txt       "$STAGE/cosmobounty/data/token.txt" 2>/dev/null || true
# 配置只带运行必需的两个；阿里云 AK 绝不进包
cp config/brands.json config/qiniu.json "$STAGE/cosmobounty/config/"
cp deploy/niling.service        "$STAGE/cosmobounty/niling.service"

say "3. 知识库（只带文档，媒体留七牛，省 16GB）"
mkdir -p "$STAGE/cosmobounty/knowledge"
rsync -a --include '*/' \
      --include '*.md' --include '*.docx' --include '*.xlsx' --include '*.json' --include '*.txt' \
      --exclude '.DS_Store' --exclude '*' \
      knowledge/ "$STAGE/cosmobounty/knowledge/"
[ -f "$STAGE/cosmobounty/knowledge/README.md" ] || cp knowledge/README.md "$STAGE/cosmobounty/knowledge/"

say "4. 星河网关（代码 + lib 全量 + 私有签名配置）"
cp "$SB/server.js" "$STAGE/starbox/server.js"
rsync -a "$SB/lib/" "$STAGE/starbox/lib/"
mkdir -p "$STAGE/starbox/public/wb" "$STAGE/starbox/data"
rsync -a --exclude '*.bak' "$SB/public/wb/" "$STAGE/starbox/public/wb/"
cp "$SB/data/sr-cdn.json" "$STAGE/starbox/data/sr-cdn.json"
chmod 600 "$STAGE/starbox/data/sr-cdn.json"

say "5. 打包前自检（漏文件直接拦下，不靠事后发现）"
fail=0
chk() { if [ -e "$1" ]; then echo "  ✓ $2"; else echo "  ✗ $2  ($1)"; fail=1; fi; }
chk "$STAGE/starbox/lib/sr-cdn.js"          "星河 lib/sr-cdn.js"
chk "$STAGE/starbox/lib/compliance.js"      "星河 lib/compliance.js"
chk "$STAGE/starbox/data/sr-cdn.json"       "私有素材签名配置"
chk "$STAGE/cosmobounty/service/wbdata.py"  "底座 wbdata.py"
chk "$STAGE/cosmobounty/db/cosmobounty.db"  "数据库"
chk "$STAGE/cosmobounty/data/media_cdn.json" "六品牌媒体映射"
chk "$STAGE/install.sh"                     "install.sh"
[ -e "$STAGE/cosmobounty/config/aliyun.json" ] && { echo "  ✗ 阿里云密钥被打进包了！"; fail=1; } || echo "  ✓ 未包含阿里云密钥"
# server.js 必须认得 sr-cdn，否则私有素材会悄悄回落本地
grep -q "sr-cdn" "$STAGE/starbox/server.js" && echo "  ✓ server.js 含私有 CDN 分支" || { echo "  ✗ server.js 不含 sr-cdn"; fail=1; }
# install.sh 必须带 4.5 步
grep -q "sr-cdn.json" "$STAGE/install.sh" && echo "  ✓ install.sh 会安装 sr-cdn.json" || { echo "  ✗ install.sh 缺 sr-cdn 步骤"; fail=1; }
[ "$fail" = "0" ] || { echo; echo "自检未通过，终止。"; exit 1; }

say "6. 压缩"
mkdir -p "$CB/dist"
tar -czf "$OUT" -C "$CB/dist" "$NAME"
echo "  $(ls -lh "$OUT" | awk '{print $5}')  $OUT"

if [ "$UPLOAD" = "1" ]; then
  say "7. 上传七牛 install/"
  $PY - "$OUT" "$NAME" "$CB" <<'PY'
import json,sys,os,hmac,hashlib,base64,time,urllib.request,uuid,subprocess

path,name,cb=sys.argv[1],sys.argv[2],sys.argv[3]
cfg=json.load(open(os.path.join(cb,'config/qiniu.json')))
AK,SK=cfg['AK'],cfg['SK']
BUCKET='chunke'

def b64u(b): return base64.urlsafe_b64encode(b).decode().replace('+','-').replace('/','_')

def up(local,key):
    policy={"scope":f"{BUCKET}:{key}","deadline":int(time.time())+3600}
    enc=b64u(json.dumps(policy,separators=(',',':')).encode())
    # 注意：sign 要签【base64 后的 policy】，签原文一律 401 BadToken
    sign=b64u(hmac.new(SK.encode(),enc.encode(),hashlib.sha1).digest())
    token=f"{AK}:{sign}:{enc}"
    boundary='----cb'+uuid.uuid4().hex
    body=[]
    def field(k,v):
        body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    field('token',token); field('key',key)
    body.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{os.path.basename(local)}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    body.append(open(local,'rb').read()); body.append(f'\r\n--{boundary}--\r\n'.encode())
    data=b''.join(body)
    # chunke 在华南 z2，必须用 up-z2；用通用 upload.qiniup.com 会 400 incorrect region
    req=urllib.request.Request('https://up-z2.qiniup.com/',data=data,
        headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req,timeout=300) as r: return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:200]

for key in (f'install/{name}.tgz','install/cb-latest.tgz'):
    st,b=up(path,key)
    print(f'  install/{key.split("/")[-1]:<22} → {st} {b[:80]}')
PY
  echo
  echo "  服务器上用："
  echo "    curl -o cb.tgz http://tni044vir.hn-bkt.clouddn.com/install/cb-latest.tgz && tar xzf cb.tgz && sudo bash $NAME/install.sh"
fi

say "完成"
echo "  本地：$OUT"
