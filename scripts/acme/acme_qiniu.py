#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
certbot manual hook：把 Let's Encrypt 的 HTTP-01 验证文件放进七牛空间。

为什么这么做：cdn.pianai.love 已经 CNAME 到七牛 CDN，源站是 pianai-love 空间。
LE 来验证时请求 /  .well-known/acme-challenge/<token> ，七牛回源就能读到我们放进去的对象，
于是验证通过 —— 全程不需要动阿里云 DNS，也不需要人工点控制台。

用法（由 certbot 调用，不要手动跑）：
  auth    : CERTBOT_DOMAIN / CERTBOT_TOKEN / CERTBOT_VALIDATION 三个环境变量已注入
  cleanup : 删掉刚放上去的对象，别把空间弄脏
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CFG = os.path.join(ROOT, "config", "qiniu.json")
BUCKET = "pianai-love"          # cdn.pianai.love 的源站空间
UPLOAD_HOST = "https://upload.qiniup.com"   # 华东 z0
UC_HOST = "https://uc.qbox.me"


def creds():
    ak = os.environ.get("QINIU_AK", "").strip()
    sk = os.environ.get("QINIU_SK", "").strip()
    if ak and sk:
        return ak, sk
    with open(CFG, encoding="utf-8") as f:
        d = json.load(f)
    return d["AK"].strip(), d["SK"].strip()


def upload_token(ak, sk, key, ttl=900):
    policy = json.dumps({"scope": f"{BUCKET}:{key}", "deadline": int(time.time()) + ttl,
                         "insertOnly": 0}, separators=(",", ":"))
    enc = base64.urlsafe_b64encode(policy.encode()).decode()
    sign = hmac.new(sk.encode(), enc.encode(), hashlib.sha1).digest()
    return f"{ak}:{base64.urlsafe_b64encode(sign).decode()}:{enc}"


def mgmt_token(ak, sk, path_query, body=b""):
    """管理凭证：path?query + '\\n' + body 的 HMAC-SHA1"""
    signing = path_query + "\n" + body.decode("utf-8", "replace") if body else path_query + "\n"
    sign = hmac.new(sk.encode(), signing.encode(), hashlib.sha1).digest()
    return f"QBox {ak}:{base64.urlsafe_b64encode(sign).decode()}"


def put_object(key, data):
    ak, sk = creds()
    tok = upload_token(ak, sk, key)
    boundary = "----acme" + hashlib.md5(key.encode()).hexdigest()[:12]
    parts = []
    for name, val in (("token", tok), ("key", key)):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"v\"\r\n"
                 f"Content-Type: text/plain\r\n\r\n".encode())
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(UPLOAD_HOST + "/", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=30) as r:
        return r.status, r.read().decode()


def delete_object(key):
    ak, sk = creds()
    # 七牛的 entry 用 urlsafe_base64，不是 URL 百分号编码 —— 用错了会 400
    enc = base64.urlsafe_b64encode(f"{BUCKET}:{key}".encode()).decode()
    path = "/delete/" + enc
    req = urllib.request.Request("https://rs.qbox.me" + path, method="POST",
                                 headers={"Authorization": mgmt_token(ak, sk, path), "Content-Length": "0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auth"
    token = os.environ.get("CERTBOT_TOKEN", "").strip()
    domain = os.environ.get("CERTBOT_DOMAIN", "").strip()
    validation = os.environ.get("CERTBOT_VALIDATION", "")
    if not token:
        print("缺少 CERTBOT_TOKEN，说明不是由 certbot 调用", file=sys.stderr)
        return 2

    key = f".well-known/acme-challenge/{token}"
    if mode == "cleanup":
        st, body = delete_object(key)
        print(f"[cleanup] 删除 {key} → {st} {body[:120]}")
        return 0

    # 关键：内容必须和 LE 给的校验串完全一致，末尾不能多换行
    st, body = put_object(key, validation.encode())
    print(f"[auth] 上传 {key} → {st} {body[:120]}")
    if st != 200:
        print("上传失败，验证必然失败", file=sys.stderr)
        return 1
    # 给 CDN 一点回源时间，避免 LE 抢先请求拿到 404
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
