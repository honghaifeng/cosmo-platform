#!/bin/zsh
# 证书续期 + 同步到七牛 CDN（多个域名）
# certbot 只在到期前 30 天才真正续，平时跑是空转，所以可以放心高频调度。
#
# 两个域名走两条完全不同的验证路，别混：
#   cdn.pianai.love → HTTP-01，源站是公开空间 pianai-love，把挑战文件塞进 bucket 让 LE 回源读
#   sr.pianai.love  → DNS-01，源站是 **私有**空间 sr-private，匿名读不到文件，只能写 TXT 记录
# sr 这条还必须是 RSA：七牛不认 ECDSA 证书（certbot 现默认签 ECDSA，会卡在上传那步）
set -u
cd "$(dirname "$0")/../.." || exit 1

PY=/Users/v/.workbuddy/binaries/python/envs/default/bin/python
CERTBOT=/Users/v/.workbuddy/binaries/python/envs/default/bin/certbot
COMMON=(--config-dir .acme/config --work-dir .acme/work --logs-dir .acme/logs --quiet)

fail=0

# ---- cdn.pianai.love：HTTP-01 文件验证 ----
DOMAIN=cdn.pianai.love
echo "=== $(date '+%F %T') 检查 $DOMAIN 续期 ==="
"$CERTBOT" renew --cert-name "$DOMAIN" \
  --manual-auth-hook "$PY scripts/acme/acme_qiniu.py auth" \
  --manual-cleanup-hook "$PY scripts/acme/acme_qiniu.py cleanup" \
  "${COMMON[@]}"
rc=$?
if [ $rc -ne 0 ]; then
  echo "certbot renew $DOMAIN 失败（退出码 $rc），请查 .acme/logs/letsencrypt.log"
  fail=1
fi
# 无论是否刚续期，都把当前证书重新同步一次 —— 幂等，且能修「本地新证书、CDN 还挂旧证书」
"$PY" scripts/qiniu_cert_sync.py --domain "$DOMAIN" || fail=1

# ---- sr.pianai.love：DNS-01（阿里云云解析） ----
DOMAIN=sr.pianai.love
echo "=== $(date '+%F %T') 检查 $DOMAIN 续期 ==="
"$CERTBOT" renew --cert-name "$DOMAIN" --key-type rsa --rsa-key-size 2048 \
  --manual-auth-hook "$PY scripts/acme/acme_aliyun.py auth" \
  --manual-cleanup-hook "$PY scripts/acme/acme_aliyun.py cleanup" \
  "${COMMON[@]}"
rc=$?
if [ $rc -ne 0 ]; then
  echo "certbot renew $DOMAIN 失败（退出码 $rc），请查 .acme/logs/letsencrypt.log"
  fail=1
fi
"$PY" scripts/qiniu_cert_sync.py --domain "$DOMAIN" || fail=1

exit $fail
