#!/usr/bin/env python3
"""一次性脚本：把 2026-10-27 电商价格观测结果合并进 products_manual.json。

口径：
- price = 代表性零售价（人民币），套装/多瓶装折算写进 price_note
- evidence = media（京东/天猫公开页面、公开报道，非品牌官网直查）
- 每条带观测日期，方便事后复核
"""
import json, os

CB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL = f"{CB}/config/products_manual.json"

D = "2026-10-27 观测"

UPDATES = {
    "MitoQ": [
        {"name": "三重镁复配", "match": "三重镁", "price": 289,
         "price_note": f"{D}：京东 MitoQ海外官方旗舰店 3瓶装 ¥865–897，折合约 ¥289–299/瓶（60粒）", "evidence": "media"},
        {"name": "岩藻黄素胶囊", "match": "岩藻黄素", "price": 289,
         "price_note": f"{D}：京东官方旗舰店 ¥289–299（60粒）；天猫 MITOQ海外旗舰店 ¥489–509", "evidence": "media"},
        {"name": "奶蓟草Pro 随享装", "match": "奶蓟草Pro", "price": 164,
         "price_note": f"{D}：天猫 MITOQ海外旗舰店 ¥164（14粒随享装）", "evidence": "media"},
        {"name": "锐智胶囊", "match": "锐智", "price": 515,
         "price_note": f"{D}：天猫 MITOQ海外旗舰店 3瓶装 ¥1545（常卖 ¥1749），折合约 ¥515–583/瓶", "evidence": "media"},
        {"name": "骨赋能胶囊", "match": "骨赋能", "price": 619,
         "price_note": f"{D}：电商参照价 ¥619–699（60粒）", "evidence": "media"},
        {"name": "净肝胶囊", "match": "净肝", "price": 486,
         "price_note": f"{D}：京东渠道 ¥486–488（60粒）", "evidence": "media"},
        {"name": "麦角硫因胶囊", "match": "麦角硫因", "price": 420,
         "price_note": f"{D}：京东官方旗舰店 3瓶装 ¥1195–1259，折合约 ¥398–420/瓶", "evidence": "media"},
    ],
    "GRANVER": [
        {"name": "吉利肝", "match": "吉利肝", "price": 1050,
         "price_note": f"{D}：官方统一定价 ¥1050/瓶（60粒，月服周期）；京东 6瓶巩固装 ¥4678 折合 ¥780/瓶", "evidence": "media"},
        {"name": "吉清肺", "match": "吉清肺", "price": 1050,
         "price_note": f"{D}：官方统一定价 ¥1050/瓶；京东 3瓶改善装 ¥2548 折合约 ¥849/瓶（60粒）", "evidence": "media"},
        {"name": "吉悦巢", "match": "吉悦巢", "price": 1003,
         "price_note": f"{D}：京东 3瓶改善装 ¥3010（常卖 ¥3110），折合约 ¥1003–1037/瓶（60粒）", "evidence": "media"},
        {"name": "吉瑞维", "match": "吉瑞维", "price": 1380,
         "price_note": f"{D}：官方定价 ¥1380/瓶（60粒）；京东 3瓶改善装 ¥3480 折合 ¥1160/瓶", "evidence": "media"},
        {"name": "吉返盾", "match": "吉返盾", "price": 720,
         "price_note": f"{D}：官方参考价 ¥720/瓶（60粒，30天量）", "evidence": "media"},
    ],
    "塞乐瑞": [
        {"name": "莱复隆（赛乐瑞 Stemax）", "match": "莱复隆", "price": 32000,
         "price_note": f"{D}：京东/天猫跨境电商现价 ¥32000/瓶（60粒；2022 上市价 ¥26000）", "evidence": "media"},
        {"name": "精倍莱", "match": "精倍莱", "price": 3399,
         "price_note": f"{D}：天猫赛乐瑞海外旗舰店在售价 ¥3399（90粒），促销价 ¥2399", "evidence": "media"},
        {"name": "赛特茵", "match": "赛特茵", "price": 5400,
         "price_note": f"{D}：京东国际活动价 ¥5400，满减后约 ¥3000/瓶；公开报道中'赛特茵(SUPER-K)'同挂一个链接，SUPER-K 疑为同品线（待核）", "evidence": "media"},
        {"name": "赛乐瑞 SUPER-K", "match": "SUPER-K", "price_note":
            f"{D}：未取得独立报价；公开渠道 'PRO Super-K 纳豆' 京东 ¥6000、'赛特茵(SUPER-K)' ¥5400，SKU 对应关系待核", "evidence": "media"},
    ],
}

m = json.load(open(MANUAL, encoding="utf-8"))
changed = 0
for brand, items in UPDATES.items():
    blk = m.setdefault(brand, {"products": []})
    prods = blk.setdefault("products", [])
    for up in items:
        hit = None
        for p in prods:
            if p.get("name") == up["name"] or up["match"].lower() in (p.get("name") or "").lower():
                hit = p
                break
        if hit is None:
            prods.append(dict(up))
            changed += 1
            print(f"+ {brand}/{up['name']} 新增")
        else:
            for k, v in up.items():
                if k != "match":
                    hit[k] = v
            changed += 1
            print(f"~ {brand}/{up['name']} 更新")

m["_说明"] = ("人工登记的确定性信息：官网抓不到价格/中文名的在这里补；没有官网产品的品牌（塞乐瑞、W+端粒塔、自有品牌）在这里整条登记。"
              "每条必须带 source + evidence。price 单位人民币；活动价写进 price_note，不要混成日常价。"
              "价格来源为京东/天猫公开页面与公开报道（evidence=media），观测日期写在 price_note 开头。")
json.dump(m, open(MANUAL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"done, {changed} 条变更 -> {MANUAL}")
