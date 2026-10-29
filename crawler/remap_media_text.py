#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""remap_media_text.py — 修复 media_text 孤儿数据。

背景：
  2026-10-23 对抖音/小红书做过 OCR+ASR 媒体解读，结果写入 media_text 表，
  content_id 指向当时的 content.id（1~3205）。
  2026-10-24 content 表被重建，id 空间变成 45563~64800，
  但 media_text.content_id 没跟着迁移，导致 147 条 asr + 945 条 ocr 全部失联。

修复：
  用 text_hash（跨库稳定的幂等键）把旧 media_text 重新挂到新库 content.id。
  旧库: db/backup/cosmobounty_20261023_203633_pre_dyfix.db
  新库: db/cosmobounty.db

用法（只报告，不改库）:
    python3 crawler/remap_media_text.py --report
用法（执行）:
    python3 crawler/remap_media_text.py
"""
import argparse
import os
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_DB = os.path.join(BASE, "db", "cosmobounty.db")
OLD_DB = os.path.join(BASE, "db", "backup", "cosmobounty_20261023_203633_pre_dyfix.db")


def load_old_mapping():
    """旧库: (text_hash, kind, source, text) 列表。"""
    con = sqlite3.connect(OLD_DB)
    rows = con.execute(
        """SELECT c.text_hash, m.kind, m.source, m.text, m.created_at
           FROM media_text m JOIN content c ON m.content_id = c.id
           WHERE c.text_hash IS NOT NULL"""
    ).fetchall()
    con.close()
    return rows


def build_new_idmap():
    """新库: {text_hash: id}。"""
    con = sqlite3.connect(NEW_DB)
    rows = con.execute("SELECT id, text_hash FROM content WHERE text_hash IS NOT NULL").fetchall()
    con.close()
    return {th: cid for cid, th in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只统计映射率，不写库")
    a = ap.parse_args()

    old_rows = load_old_mapping()
    idmap = build_new_idmap()

    # 按 text_hash 聚合旧 media_text
    from collections import defaultdict
    by_hash = defaultdict(list)
    for th, kind, source, text, created_at in old_rows:
        by_hash[th].append((kind, source, text, created_at))

    matched = sum(1 for th in by_hash if th in idmap)
    print(f"旧库 media_text 涉及 {len(by_hash)} 个 text_hash，"
          f"新库能匹配 {matched} 个（{matched/len(by_hash)*100:.1f}%）")

    if a.report:
        # 列出无法匹配的（这些 content 在新库已被删除）
        missing = [th for th in by_hash if th not in idmap]
        if missing:
            print(f"\n无法匹配 {len(missing)} 个 text_hash（新库已删除该 content）:")
            for th in missing[:20]:
                print(f"  - {th}")
        return

    # 写库：先清掉新库现有孤儿 media_text，再按 text_hash 重新挂
    con = sqlite3.connect(NEW_DB)
    # 删除所有 content_id 对不上 content 的孤儿记录
    orphan_ids = con.execute(
        "SELECT m.id FROM media_text m WHERE m.content_id NOT IN (SELECT id FROM content)"
    ).fetchall()
    n_del = len(orphan_ids)
    con.execute("DELETE FROM media_text WHERE content_id NOT IN (SELECT id FROM content)")
    print(f"清理孤儿记录 {n_del} 条")

    inserted = skipped = 0
    for th, items in by_hash.items():
        cid = idmap.get(th)
        if cid is None:
            continue
        for kind, source, text, created_at in items:
            # UNIQUE(content_id, source) 冲突时忽略
            try:
                con.execute(
                    "INSERT OR IGNORE INTO media_text (content_id, kind, source, text, created_at) "
                    "VALUES (?,?,?,?,?)", (cid, kind, source, text, created_at or ""))
                inserted += 1
            except Exception as e:
                skipped += 1
    con.commit()

    # 验证
    orphan = con.execute(
        "SELECT COUNT(*) FROM media_text m WHERE m.content_id NOT IN (SELECT id FROM content)"
    ).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM media_text").fetchone()[0]
    asr_n = con.execute("SELECT COUNT(*) FROM media_text WHERE kind='asr'").fetchone()[0]
    ocr_n = con.execute("SELECT COUNT(*) FROM media_text WHERE kind='ocr'").fetchone()[0]
    con.close()

    print(f"重挂 {inserted} 条（跳过 {skipped}）")
    print(f"修复后 media_text 共 {total} 条（asr {asr_n} / ocr {ocr_n}），孤儿 {orphan} 条")


if __name__ == "__main__":
    main()
