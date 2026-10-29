# 06 · 媒体：七牛双空间

5988 个对象 / 16.9GB · **一个都不在服务器硬盘上**

## 为什么不用服务器硬盘

服务器 `/data` 194G，装得下。但不放的原因：

| 理由 | 说明 |
|---|---|
| 备份 | 服务器挂了媒体跟着没，七牛有冗余 |
| 带宽 | 走七牛 CDN 比走公司那条出口快 |
| 扩容 | 采集只会越来越多，本地磁盘有限 |
| 迁移 | 换服务器不用搬 17G |

代价：**媒体地址会变、会过期、要签名**。这是后面所有坑的来源。

## 两个空间，按「能不能公开」切

| 空间 | 区域 | 域名 | 存什么 | 访问 |
|---|---|---|---|---|
| `chunke` | 华南 z2 | `tni044vir.hn-bkt.clouddn.com` | 六品牌公开素材：封面 / 正文图 / 视频 / 产品图 | 直接 URL |
| `sr-private` | 华东 z0 | `sr.pianai.love` | 星河客户私有素材：AI 生成图 / 上传件 | **必须签名** |

**为什么不按项目切**：星河也读公开素材（产品图鉴），底座也产私有素材（报告图）。
按项目切会导致跨项目引用时权限打架。

**为什么不干脆全私有**：chunke 有 5988 个对象，改私有意味着工作台和逆龄观察
每一个图片请求都要签名，改动面覆盖两个产品所有页面。风险远大于收益。

所以分界线是「**这份素材能不能公开**」：
六品牌的公开内容可以，客户的 AI 生成图和上传件不行。

## 映射表是唯一权威

`data/media_cdn.json` · 5988 条 · 863KB

```json
{
  "media/MitoQ/video/douyin_7514966830948879675.mp4": {
    "key": "media/MitoQ/video/douyin_7514966830948879675.mp4",
    "size": 5876224,
    "type": "video"
  }
}
```

**新增媒体只跑脚本，不手工拼 key**：

```bash
python3 crawler/upload_qiniu.py        # 公开素材，全目录遍历，增量幂等
python3 crawler/upload_sr_private.py   # 私有素材，上传 + 自动写签名配置
```

两边都会改写映射表。手工拼 key 的结果是映射表里有一条永远对不上的记录，
排查半天才发现 `:` 没换成 `_`。

## 五条硬规则（每条都是踩出来的）

### 1. 签名要签完整 URL，不是 path

```python
token = AK + ":" + urlsafe_b64(hmac_sha1(SK, "http://host/key?e=<deadline>"))
```

**结尾没有 `\n`**。老格式签的是 `"/key?e=xxx\n"`，稳定返回 401
`download token auth failed`。

### 2. 设私有用 `uc.qbox.me`，不是 `api.qiniu.com`

```
POST https://uc.qbox.me/private?bucket=<b>&private=1
```

`api.qiniu.com/private` 是 404。域名级 `/domain/<name>/private` 永远 400408，
别再试 —— 空间私有就够了。

### 3. CDN 按 path 缓存、忽略 query

后果：某个 path 被缓存过 401 之后，**带任何 token 都是 401**。

排查时必须用**没碰过的新 key** 做对照，否则会被「同一个 key 时好时坏」
骗到，误判成签名算法错了（这个坑花了很久）。

### 4. 对象 key 里的 `:` 换成 `_`

小红书/抖音的文件名带 `:`（`2026-10-29 10:30:00`）。
不换直接上传，七牛存进去是对的，但取的时候 404 —— 因为 `:` 在 URL 里是端口分隔符。

### 5. 测试域名只支持 HTTP

`tni044vir.hn-bkt.clouddn.com` 用 `https://` **一律连不上**。

代码里 `wbdata.cdn_base()` 会强制降级成 http。要 HTTPS 必须给空间绑自有域名
（`sr.pianai.love` 就是这么来的）。

## 私有空间签名

`lib/sr-cdn.js`（星河）+ `service/app.py`（底座）各有一份实现。

行为：

```
GET /img/<id>  →  星河鉴权（这个图是不是你的）
               →  302 到七牛签名地址
               →  没配 data/sr-cdn.json 就回落本地文件（不会白屏）
```

回落是刻意的：本地开发没有七牛凭据，不能让所有图片变成红叉。

## CDN 域名与证书

| 域名 | 验证方式 | 为什么 |
|---|---|---|
| `cdn.pianai.love` | HTTP-01 | 源站 `pianai-love` 公开可读，能放挑战文件 |
| `sr.pianai.love` | **DNS-01** | 源站 `sr-private` 私有，匿名读不到挑战文件 |

第二个必须用 DNS-01 的原因很直接：ACME 的 HTTP-01 要在源站上放一个文件让
Let's Encrypt 访问，但私有空间匿名读不到任何东西，所以只能用 DNS TXT 记录验证。

两个域名统一在 `base/scripts/acme/renew.sh` 里续期，每月 1/15 自动跑。

### 七牛不认 ECDSA

certbot 现在默认签 ECDSA 证书，七牛**不接受**，也不接受 PKCS#8 私钥。

```
签发必须加：--key-type rsa --rsa-key-size 2048
上传前必须：openssl rsa -in key.pem -out key.pem   # 转 PKCS#1
上传字段：ca（完整证书链，含叶证书）+ pri
```

单独塞 `cert` 字段会报 400328「密钥不匹配」，看着像密钥错，其实是字段名错。

## 首次开 HTTPS 必须走 sslize

```
PUT https://api.qiniu.com/domain/<d>/sslize   body {}
```

不先 sslize 直接 `PUT /domain/<d>/httpsconf`，永远返回 400302「更改证书失败」。

**400302 不是证书问题**：拿另一个域名的现成合法证书来绑，同样报 400302。
`base/scripts/qiniu_cert_sync.py` 已内置自愈 —— 撞 400302 自动 sslize 再重试。

## 已知缺口

| 缺口 | 影响 |
|---|---|
| 星河自己的 AI 生成图（146 张）仍在本地 | chunke 是公开空间，客户私有素材不能直接传；要另建私有空间才动 |
| `chunke` 用测试域名 | 30 天回收一次，长期要绑自有域名 |
| 映射表不记录删除 | 七牛删了对象，映射表还有记录，读的时候才 404 |
