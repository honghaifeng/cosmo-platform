#!/usr/bin/env node
// STARBOX RSC 解析器：解码 flight 流，还原 $xx 引用（含 T 长文本行），输出结构化 JSON
const fs = require('fs');

function decodeFlight(html) {
  const re = /self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)/g;
  let m, out = [];
  while ((m = re.exec(html))) {
    try { out.push(JSON.parse(m[1])); } catch (e) {}
  }
  return out.join('');
}

// 解析 flight 文本为行表：支持 T<hexlen> 长文本行
function parseRows(flight) {
  const rows = new Map();
  const buf = Buffer.from(flight, 'utf8');
  let pos = 0;
  while (pos < buf.length) {
    // 找到行首 id 后跟冒号
    const nl = buf.indexOf(0x0a, pos);
    const lineEnd = nl === -1 ? buf.length : nl;
    const line = buf.slice(pos, lineEnd).toString('utf8');
    const m = line.match(/^([0-9a-z]*):([\s\S]*)$/);
    if (!m) { pos = lineEnd + 1; continue; }
    const id = m[1];
    const rest = m[2];
    const tm = rest.match(/^T([0-9a-f]+),/);
    if (tm) {
      const byteLen = parseInt(tm[1], 16);
      const textStart = buf.indexOf(0x2c, pos) + 1; // 跳过逗号
      const textEnd = textStart + byteLen;
      rows.set(id, { kind: 'T', text: buf.slice(textStart, textEnd).toString('utf8') });
      pos = textEnd;
      // 跳过紧随的换行
      if (buf[pos] === 0x0a) pos += 1;
      continue;
    }
    // 普通行：尝试 JSON
    let value;
    try { value = JSON.parse(rest); } catch (e) { value = rest; }
    rows.set(id, { kind: 'J', value });
    pos = lineEnd + 1;
  }
  return rows;
}

// 把 "$18" 引用替换为 T 行文本，递归处理对象
function resolve(node, rows, depth = 0) {
  if (depth > 40) return node;
  if (typeof node === 'string') {
    const m = node.match(/^\$([0-9a-f]+)$/);
    if (m && rows.has(m[1])) {
      const r = rows.get(m[1]);
      return r.kind === 'T' ? r.text : resolve(r.value, rows, depth + 1);
    }
    return node;
  }
  if (Array.isArray(node)) return node.map((x) => resolve(x, rows, depth + 1));
  if (node && typeof node === 'object') {
    const o = {};
    for (const k of Object.keys(node)) o[k] = resolve(node[k], rows, depth + 1);
    return o;
  }
  return node;
}

function walkCollect(node, pred, out = [], depth = 0) {
  if (!node || typeof node !== 'object' || depth > 40) return out;
  if (pred(node)) out.push(node);
  for (const k of Object.keys(node)) {
    const v = node[k];
    if (v && typeof v === 'object') walkCollect(v, pred, out, depth + 1);
  }
  return out;
}

module.exports = { decodeFlight, parseRows, resolve, walkCollect };

if (require.main === module) {
  const file = process.argv[2];
  const html = fs.readFileSync(file, 'utf8');
  const flight = decodeFlight(html);
  const rows = parseRows(flight);
  console.log('rows:', rows.size, 'flight chars:', flight.length);
  const root = rows.get('0');
  if (!root) { console.log('no root'); process.exit(1); }
  const tree = resolve(root.value, rows);
  const agents = walkCollect(tree, (o) => typeof o.prompt === 'string' && o.prompt.length > 50 && (o.name || o.id));
  console.log('agents with prompt:', agents.length);
  for (const a of agents.slice(0, 2)) {
    console.log('---- keys:', Object.keys(a).join(','));
    console.log('name:', a.name, '| model:', a.model, '| temp:', a.temperature, '| prompt chars:', a.prompt.length);
  }
  fs.writeFileSync('/tmp/agent_tree.json', JSON.stringify(tree, null, 2));
}
