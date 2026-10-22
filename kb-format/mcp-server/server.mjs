#!/usr/bin/env node
// sem-ai-kb MCP server: BM25 search and the clarifying interview over one knowledge-base folder.
// Zero dependencies, Node 18+. JSON-RPC 2.0 over stdio, one message per line.
// Reads ../kb.json, ../interaction/decision-tree.json and ../needs-review.md (relative to this file),
// or the folder given in KB_DIR.

import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(process.env.KB_DIR || path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const readJson = rel => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
const kb = readJson('kb.json');
const tree = fs.existsSync(path.join(ROOT, 'interaction/decision-tree.json'))
  ? readJson('interaction/decision-tree.json') : { start: null, nodes: {}, resolve: {}, results: {} };
const NAME = kb.name || 'knowledge base';

// ---------- BM25 ----------

const STOP = new Set(('a an the of to in is are was were be been and or for on with how do does did i can what ' +
  'which who when where why it its this that these those my me you your we our at by from as into about ' +
  'if then than so not no yes should would could will there their they them have has had').split(' '));
const tok = s => (String(s).toLowerCase().match(/[a-z0-9]+/g) || []).filter(w => !STOP.has(w));
const docs = kb.sections.map(s => ({ s, t: tok(`${s.title} ${s.title} ${s.module.replace(/-/g, ' ')} ${s.text}`) }));
const N = docs.length;
const avgLen = docs.reduce((n, d) => n + d.t.length, 0) / Math.max(1, N);
const df = new Map();
for (const d of docs) for (const w of new Set(d.t)) df.set(w, (df.get(w) || 0) + 1);
const K1 = 1.2, B = 0.75;
const idf = w => Math.log(1 + (N - (df.get(w) || 0) + 0.5) / ((df.get(w) || 0) + 0.5));

function search(query, limit = 5) {
  const q = [...new Set(tok(query))];
  if (!q.length) return [];
  return docs.map(d => {
    const tf = new Map();
    for (const w of d.t) tf.set(w, (tf.get(w) || 0) + 1);
    let score = 0;
    for (const w of q) {
      const f = tf.get(w) || 0;
      if (f) score += idf(w) * f * (K1 + 1) / (f + K1 * (1 - B + B * d.t.length / avgLen));
    }
    return { d, score };
  }).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, limit)
    .map(({ d, score }) => ({ ...brief(d.s), score: +score.toFixed(3), excerpt: excerpt(d.s.text, q), images: d.s.images }));
}

function excerpt(text, words) {
  const lower = text.toLowerCase();
  const at = words.map(w => lower.search(new RegExp(`\\b${w}`))).filter(i => i >= 0).sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, at - 80);
  return (start ? '…' : '') + text.slice(start, start + 300) + (start + 300 < text.length ? '…' : '');
}

// ---------- KB access ----------

const moduleById = id => kb.modules.find(m => m.id === id);
const brief = s => ({ id: s.id, module: s.module, title: s.title, pages: s.pages, file: s.file });
const sectionsOf = id => kb.sections.filter(s => s.module === id);

function getSection(id) {
  const s = kb.sections.find(x => x.id === id || x.file === id || x.id === id.replace(/\.md$/, ''));
  if (!s) return { error: `no section "${id}"`, hint: 'use kb_search or kb_get_module to find section ids' };
  const markdown = fs.readFileSync(path.join(ROOT, s.file), 'utf8').replace(/^---\n[\s\S]*?\n---\n+/, '');
  const scans = [];
  if (s.pages) for (let p = s.pages[0]; p <= s.pages[1]; p++) {
    const f = `pages/page_${String(p).padStart(3, '0')}.jpg`;
    if (fs.existsSync(path.join(ROOT, f))) scans.push({ path: f, absolute: path.join(ROOT, f) });
  }
  const m = moduleById(s.module);
  return {
    ...brief(s), module_title: m ? m.title : s.module, markdown,
    images: s.images.map(p => ({ path: p, absolute: path.join(ROOT, p) })),
    original_page_scans: scans,
    cite_as: s.pages ? `(${m ? m.title : s.module}, ${s.pages[0] === s.pages[1] ? 'p. ' + s.pages[0] : `pp. ${s.pages[0]}–${s.pages[1]}`})`
      : `(${m ? m.title : s.module} › ${s.title})`,
  };
}

// ---------- interview ----------

function nodeView(id, state) {
  const node = tree.nodes[id];
  if (!node) return { status: 'error', error: `decision tree has no node "${id}"` };
  return {
    status: 'question', question: node.ask, why: node.why,
    options: node.options.map((o, i) => ({ index: i, label: o.label })),
    state: { ...state, node: id },
    note: 'Ask the user this question with these options (plus "something else"), then call kb_answer with the chosen option_index and this state.',
  };
}

function resultView(rid, state) {
  const r = tree.results[rid];
  if (!r) return { status: 'error', error: `decision tree has no result "${rid}"` };
  return {
    status: 'result', result: rid, summary: r.summary, start_hint: r.start_hint,
    modules: r.modules.map(id => moduleById(id) || { id, missing: true }),
    follow_up_questions: r.ask_next || [], vars: state.vars,
    sections: r.modules.flatMap(id => sectionsOf(id).map(brief)),
    note: 'Tell the user in one line what you understood, ask the follow-up questions conversationally, then read sections with kb_get_section.',
  };
}

function resolveKey(vars) {
  const keys = tree.resolve_keys || ['task', 'framework'];
  const parts = keys.map(k => vars[k] ?? 'unknown');
  const tries = [parts.join(':'), [...parts.slice(0, -1), 'unknown'].join(':'), parts[0]];
  return tries.map(k => tree.resolve[k]).find(Boolean);
}

function answer(state, index) {
  if (!state || !state.node || !tree.nodes[state.node]) return { status: 'error', error: 'state.node missing or unknown; start again with kb_start' };
  const node = tree.nodes[state.node];
  const opt = node.options[index];
  if (!opt) return { status: 'error', error: `option_index must be 0-${node.options.length - 1}` };
  const next = { vars: { ...(state.vars || {}), ...(opt.set || {}) }, history: [...(state.history || []), { node: state.node, picked: opt.label }] };
  if (!opt.next || opt.next === 'open') {
    return { status: 'open', vars: next.vars, note: 'The user picked an open option: ask them to describe what they need, then use kb_search.' };
  }
  if (opt.next === 'resolve') {
    const rid = resolveKey(next.vars);
    return rid ? resultView(rid, next) : { status: 'error', error: `no resolve entry for ${JSON.stringify(next.vars)}` };
  }
  if (opt.next.startsWith('result:')) return resultView(opt.next.slice(7), next);
  return nodeView(opt.next, next);
}

// ---------- tools ----------

const TOOLS = [
  { name: 'kb_start', description: `Start the clarifying interview for ${NAME}: returns the first question, its options and a state object.`,
    inputSchema: { type: 'object', properties: {} }, run: () => tree.start ? nodeView(tree.start, { vars: {}, history: [] }) : { status: 'error', error: 'no decision tree' } },
  { name: 'kb_answer', description: 'Give the option the user picked; returns the next question, or the result with the modules and sections to read.',
    inputSchema: { type: 'object', properties: { state: { type: 'object', description: 'state from the previous kb_start/kb_answer' }, option_index: { type: 'integer' } }, required: ['state', 'option_index'] },
    run: a => answer(a.state, a.option_index) },
  { name: 'kb_search', description: `Keyword (BM25) search over every section of ${NAME}. Returns ranked sections with a 300-character excerpt and image paths.`,
    inputSchema: { type: 'object', properties: { query: { type: 'string' }, limit: { type: 'integer', default: 5 } }, required: ['query'] },
    run: a => ({ results: search(a.query, a.limit || 5) }) },
  { name: 'kb_list_modules', description: 'List all modules with their page ranges.',
    inputSchema: { type: 'object', properties: {} }, run: () => ({ modules: kb.modules }) },
  { name: 'kb_get_module', description: 'One module and its sections (id, title, pages, image_count).',
    inputSchema: { type: 'object', properties: { module: { type: 'string' } }, required: ['module'] },
    run: a => { const m = moduleById(a.module); return m ? { ...m, sections: sectionsOf(m.id).map(s => ({ id: s.id, title: s.title, pages: s.pages, image_count: s.images.length })) } : { error: `no module "${a.module}"`, modules: kb.modules.map(x => x.id) }; } },
  { name: 'kb_get_section', description: 'Full Markdown of one section, its image paths (relative and absolute), page scans, and how to cite it.',
    inputSchema: { type: 'object', properties: { id: { type: 'string', description: 'section id, e.g. image-classification/03-importing-images' } }, required: ['id'] },
    run: a => getSection(a.id) },
  { name: 'kb_needs_review', description: 'Known gaps in the manual (TBD values, missing pages, undescribed pictures). Read before asserting values.',
    inputSchema: { type: 'object', properties: {} },
    run: () => ({ content: fs.existsSync(path.join(ROOT, 'needs-review.md')) ? fs.readFileSync(path.join(ROOT, 'needs-review.md'), 'utf8') : '' }) },
];

// ---------- JSON-RPC over stdio ----------

const send = msg => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...msg }) + '\n');

function handle(msg) {
  const { id, method, params } = msg;
  if (id === undefined) return; // notifications (notifications/initialized, ...)
  try {
    if (method === 'initialize') {
      return send({ id, result: { protocolVersion: params?.protocolVersion || '2024-11-05', capabilities: { tools: {} },
        serverInfo: { name: 'sem-ai-kb', version: '1.0.0' }, instructions: `Knowledge base for ${NAME}. Interview first (kb_start), then read sections and walk the user through step by step.` } });
    }
    if (method === 'ping') return send({ id, result: {} });
    if (method === 'tools/list') return send({ id, result: { tools: TOOLS.map(({ run, ...t }) => t) } });
    if (method === 'tools/call') {
      const tool = TOOLS.find(t => t.name === params?.name);
      if (!tool) return send({ id, error: { code: -32602, message: `unknown tool ${params?.name}` } });
      const out = tool.run(params.arguments || {});
      return send({ id, result: { content: [{ type: 'text', text: JSON.stringify(out, null, 2) }], isError: Boolean(out && out.error) } });
    }
    send({ id, error: { code: -32601, message: `method not found: ${method}` } });
  } catch (e) {
    send({ id, error: { code: -32603, message: e.message } });
  }
}

readline.createInterface({ input: process.stdin }).on('line', line => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return send({ id: null, error: { code: -32700, message: 'parse error' } }); }
  Array.isArray(msg) ? msg.forEach(handle) : handle(msg);
});
