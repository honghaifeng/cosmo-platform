'use strict';
/**
 * 归档分析：统计口径 + 抽取内嵌生成文件（<generated_file>）
 * 用法：node crawler/starbox/analyze_archive.mjs [归档日期目录]
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '../..');
const DAY = process.argv[2] || new Date().toISOString().slice(0, 10);
const DIR = path.join(ROOT, 'data/starbox_archive', DAY);
const OUT = path.join(DIR, 'extracted');
fs.mkdirSync(OUT, { recursive: true });

const idx = JSON.parse(fs.readFileSync(path.join(DIR, 'index.json'), 'utf8'));
const recs = idx.conversations.map(c =>
  JSON.parse(fs.readFileSync(path.join(DIR, 'conversations', c.convId + '.json'), 'utf8')));

/* ---------- 1. 总量 ---------- */
const all = recs.flatMap(r => r.messages);
const byRole = all.reduce((a, m) => (a[m.role] = (a[m.role] || 0) + 1, a), {});
const tokIn = all.reduce((s, m) => s + (m.promptTokens || 0), 0);
const tokOut = all.reduce((s, m) => s + (m.completionTokens || 0), 0);
const charOut = all.filter(m => m.role === 'assistant')
  .reduce((s, m) => s + String(m.content || '').length, 0);

/* ---------- 2. 按 Agent ---------- */
const byAgent = {};
for (const r of recs) {
  const k = r.agentName || '未标注';
  const a = byAgent[k] = byAgent[k] || { agent: k, box: r.boxName, convs: 0, msgs: 0, in: 0, out: 0, chars: 0 };
  a.convs++;
  a.msgs += r.messages.length;
  a.in += r.tokens.in; a.out += r.tokens.out;
  a.chars += r.messages.filter(m => m.role === 'assistant')
    .reduce((s, m) => s + String(m.content || '').length, 0);
}

/* ---------- 3. 重复开场 / 模板消耗 ---------- */
const opener = '功能介绍';
const openerMsgs = all.filter(m => m.role === 'user' && String(m.content).trim() === opener).length;
const openerCost = recs.reduce((s, r) => {
  // 该对话里第一次「功能介绍」触发的 assistant 回复所耗 token
  const i = r.messages.findIndex(m => m.role === 'user' && String(m.content).trim() === opener);
  if (i < 0) return s;
  const a = r.messages[i + 1];
  return s + (a ? (a.promptTokens || 0) + (a.completionTokens || 0) : 0);
}, 0);

// 同一句需求重复提交的次数
const reqCount = {};
for (const m of all.filter(x => x.role === 'user')) {
  const k = String(m.content).replace(/\s+/g, '').slice(0, 60);
  if (k.length > 12) reqCount[k] = (reqCount[k] || 0) + 1;
}
const repeated = Object.entries(reqCount).filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]);

/* ---------- 4. 工具痕迹 ---------- */
const tools = {};
for (const m of all) for (const t of (m.tools || [])) tools[t.tool] = (tools[t.tool] || 0) + 1;

/* ---------- 5. 抽取内嵌生成文件 ---------- */
const files = [];
for (const r of recs) {
  for (const m of r.messages) {
    const s = String(m.content || '');
    for (const g of s.matchAll(/<generated_file>\s*<filename>([\s\S]*?)<\/filename>\s*<content>([\s\S]*?)<\/content>\s*<\/generated_file>/g)) {
      const name = g[1].trim();
      const body = g[2];
      const safe = name.replace(/[\/\\:*?"<>|]/g, '_');
      const isHtml = /^\s*<style|^\s*<div|^\s*<!DOCTYPE/i.test(body);
      const base = name.replace(/\.(pdf|md|html?|txt|docx?)$/i, '');
      const out = path.join(OUT, base + (isHtml ? '.html' : '.md'));
      fs.writeFileSync(out, body);
      files.push({ conv: r.convId, agent: r.agentName, name, chars: body.length, kind: isHtml ? 'html' : 'md', file: path.basename(out) });
    }
  }
}

/* ---------- 6. 输出 ---------- */
console.log('════ 归档总量 ════');
console.log(`对话 ${recs.length} · 消息 ${all.length}（用户 ${byRole.user || 0} / 助手 ${byRole.assistant || 0}）`);
console.log(`token 输入 ${tokIn.toLocaleString()} / 输出 ${tokOut.toLocaleString()} / 合计 ${(tokIn + tokOut).toLocaleString()}`);
console.log(`助手正文 ${charOut.toLocaleString()} 字（平均 ${Math.round(charOut / (byRole.assistant || 1))} 字/条）`);
console.log(`输入:输出 = ${(tokIn / Math.max(1, tokOut)).toFixed(1)} : 1`);

console.log('\n════ 按 Agent ════');
Object.values(byAgent).sort((a, b) => (b.in + b.out) - (a.in + a.out)).forEach(a => {
  console.log(`${a.agent.padEnd(22)} ${String(a.convs).padStart(2)}对话 ${String(a.msgs).padStart(3)}msg ` +
    `${String(a.in + a.out).padStart(8)}tok  正文 ${String(a.chars).padStart(6)}字  [${a.box || '-'}]`);
});

console.log('\n════ 重复提交（同一句话问了多次）════');
repeated.slice(0, 8).forEach(([k, n]) => console.log(`  ${n}×  ${k.slice(0, 56)}`));

console.log('\n════ 模板开场 ════');
console.log(`「${opener}」被当作开场白 ${openerMsgs} 次，光这部分回复就消耗 ${openerCost.toLocaleString()} token`);

console.log('\n════ 工具痕迹 ════');
console.log(Object.entries(tools).map(([k, v]) => `${k}×${v}`).join('  ') || '无');

console.log('\n════ 内嵌生成的文档 ════');
files.forEach(f => console.log(`  ${f.name}  (${f.chars} 字, ${f.kind}) → extracted/${f.file}`));

const report = {
  day: DAY, totals: { convs: recs.length, msgs: all.length, byRole, tokIn, tokOut, charOut },
  byAgent: Object.values(byAgent).sort((a, b) => (b.in + b.out) - (a.in + a.out)),
  repeated: repeated.slice(0, 20), openerMsgs, openerCost, tools, files
};
fs.writeFileSync(path.join(DIR, 'analysis.json'), JSON.stringify(report, null, 2));
console.log('\n→ analysis.json / extracted/');
