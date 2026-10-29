#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本地 Let's Encrypt 证书同步到七牛 CDN 域名（上传 + 绑定），一步到位。

为什么要有这个脚本：七牛控制台的免费证书 90 天到期不会自动续，续期还得回来点鼠标。
这里把「上传证书 → 绑定到域名」固化成命令，续期后跑一次就行，也可以挂定时任务。

用法：
  python3 scripts/qiniu_cert_sync.py                 # 用默认域名 cdn.pianai.love
  python3 scripts/qiniu_cert_sync.py --domain cdn.xxx.com
  python3 scripts/qiniu_cert_sync.py --dry-run       # 只打印将要做什么

踩过的坑（别改回去）：
  1. 上传证书的字段是 ca（放完整证书链 PEM）和 pri（私钥），不是 certificate/private_key；
     写错会得到 400324「https证书解码失败」。
  2. 私钥必须是 PKCS#1（BEGIN RSA PRIVATE KEY）。certbot 默认给的 PKCS#8 七牛不认。
  3. 七牛不认 ECDSA 证书，签发时要 --key-type rsa --rsa-key-size 2048。
  4. 绑定证书的接口是 PUT /domain/<name>/httpsconf，参数是 certId / forceHttps / http2Enable
     （不是 certid / http2）。
  5. 这个接口用「旧版签名」：signingStr = path + "\\n"，千万别把 body 拼进签名，
     拼了必定 401 BadToken。
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "qiniu.json")
ACME_LIVE = os.path.join(ROOT, ".acme", "config", "live")
DEFAULT_DOMAIN = "cdn.pianai.love"


def creds():
    with open(CFG, encoding="utf-8") as f:
        d = json.load(f)
    return d["AK"].strip(), d["SK"].strip()


def api(url, method="GET", body=None, sign_body=False):
    """七牛管理 API。sign_body=False 走旧版签名（仅 path），CDN 配置类接口必须这样。"""
    ak, sk = creds()
    import urllib.parse
    path_q = urllib.parse.urlsplit(url).path
    q = urllib.parse.urlsplit(url).query
    if q:
        path_q += "?" + q
    signing = path_q + "\n"
    if sign_body and body is not None:
        signing += body.decode()
    sign = base64.urlsafe_b64encode(hmac.new(sk.encode(), signing.encode(), hashlib.sha1).digest()).decode()
    headers = {"Authorization": f"QBox {ak}:{sign}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def to_pkcs1(privkey_path):
    """PKCS#8 → PKCS#1，七牛只认后者"""
    with open(privkey_path, encoding="utf-8") as f:
        head = f.readline()
    if "BEGIN RSA PRIVATE KEY" in head:
        with open(privkey_path, encoding="utf-8") as f:
            return f.read().strip()
    fd, out = tempfile.mkstemp(suffix=".key")
    os.close(fd)
    try:
        subprocess.run(["openssl", "rsa", "-in", privkey_path, "-out", out], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out, encoding="utf-8") as f:
            return f.read().strip()
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=DEFAULT_DOMAIN)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    live = os.path.join(ACME_LIVE, args.domain)
    fullchain = os.path.join(live, "fullchain.pem")
    privkey = os.path.join(live, "privkey.pem")
    if not (os.path.exists(fullchain) and os.path.exists(privkey)):
        print(f"找不到证书：{fullchain}\n先跑 certbot 签发（见 scripts/acme/acme_qiniu.py 顶部说明）", file=sys.stderr)
        return 1

    with open(fullchain, encoding="utf-8") as f:
        chain = f.read().strip()
    key = to_pkcs1(privkey)
    name = f"{args.domain}-le-{__import__('time').strftime('%Y%m%d')}"

    if args.dry_run:
        print(f"[dry-run] 将上传证书 name={name} 链长={len(chain)} 私钥长度={len(key)}")
        print(f"[dry-run] 将绑定到 {args.domain}")
        return 0

    payload = {"name": name, "common_name": args.domain, "ca": chain, "pri": key}
    st, body = api("https://api.qiniu.com/sslcert", "POST", json.dumps(payload).encode())
    print(f"上传证书 → {st} {body[:160]}")
    if st != 200:
        return 1
    cert_id = json.loads(body).get("certID") or json.loads(body).get("certid")
    if not cert_id:
        print("没拿到 certID", file=sys.stderr)
        return 1

    conf = {"certId": cert_id, "forceHttps": False, "http2Enable": True}
    st, body = api(f"https://api.qiniu.com/domain/{args.domain}/httpsconf", "PUT",
                   json.dumps(conf, separators=(",", ":")).encode(), sign_body=False)
    print(f"绑定证书 certId={cert_id} → {st} {body[:160]}")

    # 400302「更改证书失败」的真实含义：这个域名从来没开过 HTTPS。
    # 七牛要求先 sslize 把协议升上去，之后 httpsconf 才会接受证书 ——
    # 光看错误码会以为是证书有问题，其实证书没错（拿别的域名现成证书来试也是这个错）。
    if st != 200 and b"400302" in body:
        print("域名首次开 HTTPS，先 sslize 再重试绑定")
        st2, body2 = api(f"https://api.qiniu.com/domain/{args.domain}/sslize", "PUT", b"{}",
                         sign_body=False)
        print(f"  sslize → {st2} {body2[:120]}")
        st, body = api(f"https://api.qiniu.com/domain/{args.domain}/httpsconf", "PUT",
                       json.dumps(conf, separators=(",", ":")).encode(), sign_body=False)
        print(f"  重试绑定 → {st} {body[:160]}")

    if st != 200:
        return 1

    # 强制 HTTPS 跳转单独下发一次：和证书一起传 protocol 的话，部分账号会忽略
    st3, body3 = api(f"https://api.qiniu.com/domain/{args.domain}/httpsconf", "PUT",
                     json.dumps({**conf, "forceHttps": True}, separators=(",", ":")).encode(),
                     sign_body=False)
    print(f"开启强制 HTTPS → {st3} {body3[:120]}")

    st, body = api(f"https://api.qiniu.com/domain/{args.domain}")
    if st == 200:
        d = json.loads(body)
        print(f"域名当前协议：{d.get('protocol')}  证书到期：{d.get('https', {}).get('notAfter') or d.get('https')}")
    print("完成。CDN 生效通常 1–10 分钟。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
