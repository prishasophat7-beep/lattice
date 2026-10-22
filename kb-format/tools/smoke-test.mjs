#!/usr/bin/env node
// smoke-test.mjs: start a KB's MCP server and drive it over JSON-RPC like an agent harness would.
//   node tools/smoke-test.mjs knowledge-bases/inspectai-studio
import { spawn } from 'node:child_process';
import path from 'node:path';
import readline from 'node:readline';

const dir = path.resolve(process.argv[2] || 'knowledge-bases/inspectai-studio');
const srv = spawn(process.execPath, [path.join(dir, 'mcp-server/server.mjs')], { stdio: ['pipe', 'pipe', 'inherit'] });
const waiting = new Map();
readline.createInterface({ input: srv.stdout }).on('line', l => { const m = JSON.parse(l); waiting.get(m.id)?.(m); waiting.delete(m.id); });
let next = 1;
const rpc = (method, params) => new Promise(res => { const id = next++; waiting.set(id, res); srv.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n'); });
const call = async (name, args = {}) => { const r = await rpc('tools/call', { name, arguments: args }); if (r.error) throw new Error(r.error.message); return JSON.parse(r.result.content[0].text); };

let failed = 0;
const check = (ok, what) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${what}`); if (!ok) failed++; };

const init = await rpc('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'smoke', version: '1' } });
check(init.result?.serverInfo?.name === 'sem-ai-kb', 'initialize');
srv.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
check((await rpc('ping', {})).result !== undefined, 'ping');
const tools = (await rpc('tools/list', {})).result.tools.map(t => t.name);
check(['kb_start', 'kb_answer', 'kb_search', 'kb_list_modules', 'kb_get_module', 'kb_get_section', 'kb_needs_review'].every(t => tools.includes(t)), `tools/list: ${tools.join(', ')}`);

// interview: build a model -> classification -> folder
let s = await call('kb_start');
check(s.status === 'question' && s.options.length >= 2, `kb_start: "${s.question}" (${s.options.length} options)`);
const pick = (view, re) => view.options.find(o => re.test(o.label))?.index;
s = await call('kb_answer', { state: s.state, option_index: pick(s, /build|train/i) });
check(s.status === 'question', `-> "${s.question}"`);
s = await call('kb_answer', { state: s.state, option_index: pick(s, /classes/i) });
check(s.status === 'question', `-> "${s.question}"`);
const r = await call('kb_answer', { state: s.state, option_index: pick(s, /folder/i) });
check(r.status === 'result' && r.modules[0].id === 'image-classification' && /Option A/.test(r.start_hint || ''),
  `-> result ${r.result}: ${r.summary} (start: ${r.start_hint}; ${r.sections.length} sections; ${r.follow_up_questions.length} follow-ups)`);

// interview: text reading -> assisted
s = await call('kb_start');
s = await call('kb_answer', { state: s.state, option_index: pick(s, /build|train/i) });
s = await call('kb_answer', { state: s.state, option_index: pick(s, /text/i) });
const o = await call('kb_answer', { state: s.state, option_index: pick(s, /assisted/i) });
check(o.result === 'ocr-assisted', `text reading + assisted -> ${o.result}`);

const open = await call('kb_answer', { state: { node: 'goal' }, option_index: (await call('kb_start')).options.length - 1 });
check(open.status === 'open', '"Something else" -> open question');
const bad = await call('kb_answer', { state: { node: 'goal' }, option_index: 99 });
check(Boolean(bad.error), 'bad option index -> error');

// search
for (const [q, want] of [['how do I train a text model', 'text-reading-ocr/04-training'],
                          ['threshold slider defective parts', 'anomaly-detection/05-validation'],
                          ['export TensorRT edge box', 'image-classification/07-deployment'],
                          ['licence key offline request code', 'getting-started/03-activating-the-licence']]) {
  const hits = (await call('kb_search', { query: q, limit: 3 })).results;
  check(hits[0]?.id === want, `kb_search "${q}" -> ${hits.map(h => h.id).join(', ')}`);
}

const mods = await call('kb_list_modules');
check(mods.modules.length > 0, `kb_list_modules: ${mods.modules.map(m => m.id).join(', ')}`);
const mod = await call('kb_get_module', { module: 'text-reading-ocr' });
check(mod.sections?.length > 0, `kb_get_module text-reading-ocr: ${mod.sections.map(x => x.title).join(' | ')}`);
const sec = await call('kb_get_section', { id: 'text-reading-ocr/03-annotating-text' });
check(/Method 2: Assisted annotation/.test(sec.markdown) && sec.cite_as, `kb_get_section: ${sec.markdown.length} chars, cite as ${sec.cite_as}`);
check(Boolean((await call('kb_get_section', { id: 'nope' })).error), 'unknown section -> error');
check(/Needs review/.test((await call('kb_needs_review')).content), 'kb_needs_review');

srv.kill();
console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
process.exit(failed ? 1 : 0);
