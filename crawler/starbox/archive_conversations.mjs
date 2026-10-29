'use strict';
/**
 * 星盒历史对话归档（只读，不产生任何生成请求 → 不消耗平台积分）
 *
 * 原理：对话页 /chat/<boxAgentId>?conv=<convId> 是服务端渲染，
 * 消息全在 RSC flight payload 的 initialMessages 里。这里只发 GET，
 * 不调用 /api/chat/* 的任何生成接口。
 *
 * 产出：
 *   data/starbox_archive/<date>/conversations/<convId>.json   结构化（含 token / model / 时间）
 *   data/starbox_archive/<date>/conversations/<convId>.md     可读版
 *   data/starbox_archive/<date>/raw/<convId>.html             原始页面（证据留档）
 *   data/starbox_archive/<date>/index.json                    清单 + 统计
 *
 * 用法：node crawler/starbox/archive_conversations.mjs [--limit N] [--no-raw]
 */
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '../..');

const { decodeFlight, parseRows, resolve, walkCollect } = require(path.join(__dirname, 'rsc.js'));

const PROFILE = path.join(ROOT, 'data/starbox_profile');
const BASE = 'https://www.starbox.ing';
const DAY = new Date().toISOString().slice(0, 10);
const OUT = path.join(ROOT, 'data/starbox_archive', DAY);
const NO_RAW = process.argv.includes('--no-raw');
const limitArg = process.argv.indexOf('--limit');
const LIMIT = limitArg > 0 ? +process.argv[limitArg + 1] : 0;

const { chromium } = require('/Users/v/.workbuddy/binaries/node/workspace/node_modules/playwright');

fs.mkdirSync(path.join(OUT, 'conversations'), { recursive: true });
if (!NO_RAW) fs.mkdirSync(path.join(OUT, 'raw'), { recursive: true });

const sleep = ms => new Promise(r => setTimeout(r, ms));

function parseConversation(html) {
  const flight = decodeFlight(html);
  const rows = parseRows(flight);
  for (const [, r] of rows) {
    if (r.kind !== 'J') continue;
    const tree = resolve(r.value, rows);
    const hits = walkCollect(tree, o => o && Array.isArray(o.initialMessages));
    if (hits.length) return { hit: hits[0], rows: rows.size };
  }
  return null;
}

/** RSC 的 $D2026-... 是日期标记，去掉前缀 */
const cleanDate = v => (typeof v === 'string' ? v.replace(/^\$D/, '') : v);

/** 从消息内容里抽出工具痕迹，便于分析 */
function extractTools(content) {
  if (typeof content !== 'string') return [];
  const out = [];
  for (const m of content.matchAll(/<(\w+)\s+title="([^"]*)"[^>]*>/g)) out.push({ tool: m[1], title: m[2] });
  return out;
}

function toMarkdown(rec) {
  const c = rec.conversation || {};
  const L = [];
  L.push(`# ${c.title || '(无标题)'}`);
  L.push('');
  L.push(`- 会话 ID：\`${c.id}\``);
  L.push(`- Agent：${rec.agentName || '-'}${rec.boxName ? ' · ' + rec.boxName : ''}`);
  L.push(`- 消息数：${rec.messages.length}`);
  L.push(`- 时间：${rec.firstAt || '-'} → ${rec.lastAt || '-'}`);
  if (rec.models.length) L.push(`- 模型：${rec.models.join(' / ')}`);
  L.push(`- token：输入 ${rec.tokens.in} / 输出 ${rec.tokens.out}`);
  L.push('');
  L.push('---');
  L.push('');
  for (const m of rec.messages) {
    const who = m.role === 'user' ? '👤 用户' : m.role === 'assistant' ? '🤖 助手' : m.role;
    const meta = [];
    if (m.model) meta.push(m.model);
    if (m.promptTokens || m.completionTokens) meta.push(`${m.promptTokens}/${m.completionTokens} tok`);
    if (m.createdAt) meta.push(String(m.createdAt).slice(0, 19).replace('T', ' '));
    L.push(`### ${who}${meta.length ? '  <sub>' + meta.join(' · ') + '</sub>' : ''}`);
    L.push('');
    L.push(String(m.content ?? ''));
    L.push('');
  }
  return L.join('\n');
}

(async () => {
  console.log('星盒历史对话归档（只读）→', OUT);
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: 'chrome', headless: true, viewport: { width: 1440, height: 900 },
    args: ['--no-first-run', '--no-default-browser-check']
  });
  const page = ctx.pages()[0] || await ctx.newPage();

  await page.goto(BASE + '/boxes', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(2000);

  const side = await page.evaluate(async () => (await fetch('/api/conversations/sidebar', { credentials: 'include' })).json());
  let convs = side.conversations || [];
  if (LIMIT) convs = convs.slice(0, LIMIT);
  console.log('待归档对话:', convs.length, '条\n');

  // agent / box 名称映射（从页面上下文拿）
  const dir = await page.evaluate(async () => {
    const [b, a] = await Promise.all([
      fetch('/api/boxes', { credentials: 'include' }).then(r => r.json()).catch(() => ({})),
      fetch('/api/user-guide', { credentials: 'include' }).then(r => r.json()).catch(() => ({}))
    ]);
    return { boxes: b, guide: !!a };
  }).catch(() => ({}));

  const records = [];
  for (let i = 0; i < convs.length; i++) {
    const c = convs[i];
    const url = `${BASE}/chat/${c.boxAgentId}?conv=${c.id}`;
    process.stdout.write(`[${i + 1}/${convs.length}] ${c.title.slice(0, 34)} ... `);
    try {
      const html = await page.evaluate(async u => {
        const r = await fetch(u, { credentials: 'include' });
        return await r.text();
      }, `/chat/${c.boxAgentId}?conv=${c.id}`);

      if (!NO_RAW) fs.writeFileSync(path.join(OUT, 'raw', c.id + '.html'), html);

      const parsed = parseConversation(html);
      if (!parsed) { console.log('无 initialMessages'); continue; }

      const h = parsed.hit;
      const ba = h.boxAgent || {};
      const msgs = h.initialMessages || [];
      const models = [...new Set(msgs.map(m => m.model).filter(Boolean))];
      const tokens = msgs.reduce((a, m) => ({
        in: a.in + (m.promptTokens || 0), out: a.out + (m.completionTokens || 0)
      }), { in: 0, out: 0 });
      const times = msgs.map(m => cleanDate(m.createdAt)).filter(Boolean).sort();

      const rec = {
        convId: c.id,
        title: c.title,
        boxAgentId: c.boxAgentId,
        isPinned: !!c.isPinned,
        url: `https://www.starbox.ing/chat/${c.boxAgentId}?conv=${c.id}`,
        agentName: ba.name || null,
        agentDesc: ba.description || null,
        agentQuickQuestions: (() => { try { return JSON.parse(ba.quickQuestions || '[]'); } catch { return []; } })(),
        boxName: h.boxName || null,
        defaultModel: h.defaultModel || null,
        isReadOnly: !!h.isReadOnly,
        models, tokens,
        firstAt: times[0], lastAt: times[times.length - 1],
        messages: msgs.map(m => ({
          id: m.id, role: m.role, type: m.type,
          content: m.content,
          model: m.model,
          promptTokens: m.promptTokens, completionTokens: m.completionTokens,
          cacheReadTokens: m.cacheReadTokens, cacheCreationTokens: m.cacheCreationTokens,
          createdAt: cleanDate(m.createdAt),
          tools: extractTools(m.content),
          toolResults: m.toolResults || null,
          context: m.context ? {
            providerSelection: m.context.providerSelection || null
          } : null
        }))
      };

      // 第一次遇到时把「新用户必读」和全量 Agent 清单也存下来（这是运营资产）
      if (h.userGuideMarkdown && !fs.existsSync(path.join(OUT, '_user-guide.md'))) {
        fs.writeFileSync(path.join(OUT, '_user-guide.md'), h.userGuideMarkdown);
      }
      if (Array.isArray(h.sidebarAgents) && !fs.existsSync(path.join(OUT, '_agents-full.json'))) {
        fs.writeFileSync(path.join(OUT, '_agents-full.json'), JSON.stringify(h.sidebarAgents, null, 2));
      }

      fs.writeFileSync(path.join(OUT, 'conversations', c.id + '.json'), JSON.stringify(rec, null, 2));
      fs.writeFileSync(path.join(OUT, 'conversations', c.id + '.md'), toMarkdown(rec));
      records.push(rec);
      console.log(`${msgs.length} 条消息 · ${tokens.in}/${tokens.out} tok`);
    } catch (e) {
      console.log('失败:', String(e.message || e).slice(0, 80));
    }
    await sleep(1200);
  }

  const index = {
    capturedAt: new Date().toISOString(),
    account: 'cosom_test001',
    source: BASE,
    method: 'RSC flight payload of /chat/<agent>?conv=<id>（纯 GET，未调用任何生成接口）',
    total: records.length,
    totals: records.reduce((a, r) => ({
      messages: a.messages + r.messages.length,
      tokensIn: a.tokensIn + r.tokens.in,
      tokensOut: a.tokensOut + r.tokens.out
    }), { messages: 0, tokensIn: 0, tokensOut: 0 }),
    conversations: records.map(r => ({
      convId: r.convId, title: r.title, agentName: r.agentName, boxName: r.boxName,
      models: r.models, messages: r.messages.length, tokens: r.tokens,
      firstAt: r.firstAt, lastAt: r.lastAt, url: r.url
    }))
  };
  fs.writeFileSync(path.join(OUT, 'index.json'), JSON.stringify(index, null, 2));
  console.log(`\n完成：${records.length} 个对话，${index.totals.messages} 条消息，` +
    `${index.totals.tokensIn}/${index.totals.tokensOut} tokens`);
  console.log('清单 →', path.join(OUT, 'index.json'));

  await ctx.close();
})();
