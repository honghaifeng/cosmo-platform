#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里云云解析 DNS 的最小客户端（零依赖，RPC 风格签名 v1.0）

为什么自己写：官方 SDK 要装一堆依赖，而我们只需要三个动作
（看记录 / 加记录 / 删记录）。签名规则固定，自己算更透明、更好排查。

凭据来源（按优先级）：
  1. 环境变量 ALIYUN_AK / ALIYUN_SK
  2. config/aliyun.json  {"AK": "...", "SK": "..."}
  3. ~/.aliyun/config.json（阿里云 CLI 的格式，取 profiles[0]）

用法：
  python3 scripts/aliyun_dns.py list  pianai.love
  python3 scripts/aliyun_dns.py list  pianai.love --rr sr
  python3 scripts/aliyun_dns.py add   pianai.love sr CNAME sr-pianai-love-idvs7gb.qiniudns.com
  python3 scripts/aliyun_dns.py add   pianai.love @ TXT "some-token" --ttl 600
  python3 scripts/aliyun_dns.py del   pianai.love <RecordId>
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = "https://alidns.aliyuncs.com/"
API_VERSION = "2015-01-09"


def creds():
    ak = os.environ.get("ALIYUN_AK", "").strip()
    sk = os.environ.get("ALIYUN_SK", "").strip()
    if ak and sk:
        return ak, sk
    p = os.path.join(ROOT, "config", "aliyun.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        ak = (d.get("AK") or d.get("accessKeyId") or "").strip()
        sk = (d.get("SK") or d.get("accessKeySecret") or "").strip()
        if ak and sk:
            return ak, sk
    p = os.path.expanduser("~/.aliyun/config.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        prof = (d.get("profiles") or [{}])[0]
        ak = (prof.get("access_key_id") or "").strip()
        sk = (prof.get("access_key_secret") or "").strip()
        if ak and sk:
            return ak, sk
    raise SystemExit("没有阿里云凭据：设 ALIYUN_AK/ALIYUN_SK 或写 config/aliyun.json")


def pct(v):
    """阿里云要求 RFC3986 编码：encodeURIComponent 那套，空格变 %20"""
    return urllib.parse.quote(str(v), safe="~")


def sign(params, sk):
    """
    RPC 签名：把参数按 key 排序 → k=v 用 & 拼 → 再整体 percent-encode
    → StringToSign = "GET&%2F&" + 上一步 → HMAC-SHA1(sk + "&") → base64
    注意三点（错一个就 SignatureDoesNotMatch）：
      · 编码前后各来一次，不能只编一次
      · 用 GET，HTTPMethod 固定 "GET"
      · 密钥是 sk + "&"，不是 sk
    """
    items = sorted(params.items())
    canonical = "&".join(f"{pct(k)}={pct(v)}" for k, v in items)
    string_to_sign = "GET&%2F&" + pct(canonical)
    digest = hmac.new((sk + "&").encode(), string_to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def call(action, **kwargs):
    ak, sk = creds()
    params = {
        "Action": action,
        "Version": API_VERSION,
        "Format": "JSON",
        "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    for k, v in kwargs.items():
        if v is not None:
            params[k] = v
    params["Signature"] = sign(params, sk)
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    # 沙箱有代理，这里显式绕过，否则会拿到假的 502
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url), timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except Exception:
            raise SystemExit(f"HTTP {e.code}: {body[:500]}")


def err(d):
    """阿里云成功响应不带 Code；带 Code 就是错"""
    if isinstance(d, dict) and d.get("Code"):
        raise SystemExit(f"阿里云报错 {d.get('Code')}: {d.get('Message')}")
    return d


def cmd_list(domain, rr=None):
    d = err(call("DescribeDomainRecords", DomainName=domain, RRKeyWord=rr,
                 PageSize=500, Line="default"))
    recs = d.get("DomainRecords", {}).get("Record", [])
    if not recs:
        print(f"{domain} 没有匹配记录")
        return
    print(f"{'RR':<22}{'TYPE':<10}{'VALUE':<58}{'TTL':<8}{'ID'}")
    for r in recs:
        print(f"{r.get('RR',''):<22}{r.get('Type',''):<10}{r.get('Value','')[:56]:<58}"
              f"{r.get('TTL',''):<8}{r.get('RecordId','')}")
    return recs


def cmd_add(domain, rr, rtype, value, ttl="600"):
    d = err(call("AddDomainRecord", DomainName=domain, RR=rr, Type=rtype,
                 Value=value, TTL=ttl))
    print(f"已添加：{rr}.{domain} {rtype} → {value}  (RecordId={d.get('RecordId')})")
    return d.get("RecordId")


def cmd_del(domain, record_id):
    err(call("DeleteDomainRecord", RecordId=record_id))
    print(f"已删除记录 {record_id}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    op = a[0]
    if op == "list":
        domain = a[1]
        rr = a[a.index("--rr") + 1] if "--rr" in a else None
        cmd_list(domain, rr)
    elif op == "add":
        ttl = a[a.index("--ttl") + 1] if "--ttl" in a else "600"
        cmd_add(a[1], a[2], a[3], a[4], ttl)
    elif op == "del":
        cmd_del(a[1], a[2])
    else:
        raise SystemExit(__doc__)
