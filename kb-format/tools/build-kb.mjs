#!/usr/bin/env node
// build-kb.mjs: turn a Lattice knowledge-base export (.zip with page_index.json) into the
// sem-ai-kb folder layout (see ../STRUCTURE.md). Zero dependencies, Node 18+.
//
//   node tools/build-kb.mjs <export.zip> <out-dir> [--name "Product Name"]
//
// Regenerated on every build (edits there are overwritten):
//   index.md, kb.json, modules/, images/, needs-review.generated.md, mcp-server/server.mjs
// Created once, then left alone (hand-maintained):
//   AGENTS.md, README.md, CLAUDE.md, needs-review.md, interaction/decision-tree.json

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// ---------- zip reading (stored and deflated entries only) ----------

function readZip(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('not a zip file');
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  const files = new Map();
  for (let n = 0; n < count; n++) {
    if (buf.readUInt32LE(p) !== 0x02014b50) throw new Error('bad zip central directory');
    const method = buf.readUInt16LE(p + 10);
    const csize = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28), extraLen = buf.readUInt16LE(p + 30), commentLen = buf.readUInt16LE(p + 32);
    const local = buf.readUInt32LE(p + 42);
    const name = buf.toString('utf8', p + 46, p + 46 + nameLen);
    p += 46 + nameLen + extraLen + commentLen;
    if (name.endsWith('/')) continue;
    const lNameLen = buf.readUInt16LE(local + 26), lExtraLen = buf.readUInt16LE(local + 28);
    const start = local + 30 + lNameLen + lExtraLen;
    const raw = buf.subarray(start, start + csize);
    files.set(name, () => method === 0 ? Buffer.from(raw) : method === 8 ? zlib.inflateRawSync(raw)
      : (() => { throw new Error(`unsupported zip method ${method} for ${name}`); })());
  }
  return files;
}

// ---------- text helpers ----------

const slug = s => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')
  .replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60).replace(/-+$/, '') || 'section';
const withoutNumber = t => t.replace(/^\s*(?:chapter\s+)?\d+(?:\.\d+)*\.?\s+/i, '').trim() || t.trim();
const norm = t => withoutNumber(t).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
const pad = (n, w) => String(n).padStart(w, '0');
const pagesLabel = pg => !pg ? 'pages not recorded' : pg[0] === pg[1] ? `p. ${pg[0]}` : `pp. ${pg[0]}–${pg[1]}`;
const yamlStr = s => JSON.stringify(s);

function plainText(md) {
  return md
    .replace(/^!\[[^\]]*\]\([^)]*\)\s*$/gm, '')
    .replace(/^Image path: .*$/gm, '')
    .replace(/^Image description: /gm, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*\n]+)\*/g, '$1').replace(/_([^_\n]+)_/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s*\|?\s*-{3,}[-|\s:]*$/gm, '')
    .replace(/\|/g, ' ')
    .replace(/^\s*[-*]\s+/gm, '')
    .replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, '\n').replace(/\n{2,}/g, '\n').trim()
    .replace(/\n/g, ' ');
}

// ---------- reading one document of the export ----------

function documentMarkdown(exportFiles, entry) {
  // older exports: files/<name>/<name>.md next to the nested bundle; newer: only files/<x>.bundle.zip
  const loose = (entry.files && entry.files.markdown) || null;
  const bundleName = entry.bundle_zip
    || [...exportFiles.keys()].find(k => k.endsWith('.bundle.zip') && k.includes(entry.filename));
  let md = null, images = new Map();
  if (bundleName && exportFiles.has(bundleName)) {
    const inner = readZip(exportFiles.get(bundleName)());
    for (const [name, get] of inner) {
      if (name.endsWith('.md') && md === null) md = get().toString('utf8');
      else if (/(^|\/)images\/[^/]+\.(png|jpe?g|webp|gif)$/i.test(name)) images.set(path.basename(name), get);
    }
  }
  if (md === null) {
    const key = loose && exportFiles.has(loose) ? loose : [...exportFiles.keys()].find(k => k.endsWith(`${entry.filename}.md`));
    if (key) md = exportFiles.get(key)().toString('utf8');
  }
  if (md === null) throw new Error(`${entry.filename}: no Markdown found in the export`);
  md = md.replace(/^﻿/, '').replace(/^---\n[\s\S]*?\n---\n/, '');
  return { md, images };
}

// split "## Image Descriptions" (one "### figure_000.png" per picture) off the document
function takeImageDescriptions(md) {
  const m = md.match(/^#{1,3}\s+Image Descriptions\s*$/im);
  if (!m) return { md, described: new Map() };
  const tail = md.slice(m.index + m[0].length);
  const described = new Map();
  for (const part of tail.split(/^#{2,4}\s+/m).slice(1)) {
    const nl = part.indexOf('\n');
    const name = part.slice(0, nl).trim();
    const text = part.slice(nl + 1).replace(/\s+/g, ' ').trim();
    if (/\.(png|jpe?g|webp|gif)$/i.test(name) && text) described.set(name, text);
  }
  return { md: md.slice(0, m.index).trimEnd() + '\n', described };
}

function headings(md) {
  const out = [];
  const lines = md.split('\n');
  let fence = false;
  lines.forEach((line, i) => {
    if (/^\s*```/.test(line)) fence = !fence;
    const h = !fence && line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (h) out.push({ level: h[1].length, title: h[2].trim(), line: i });
  });
  return { lines, list: out };
}

// modules and sections of one document
function splitDocument(entry, md) {
  const { lines, list } = headings(md);
  let hs = list;
  // a first heading that is only the file name ("# InspectAI_Studio_4_User_Guide.pdf") is not content
  if (hs.length && (hs[0].title === entry.filename || /\.(pdf|docx?|xlsx?|csv|txt|json|xml|md)$/i.test(hs[0].title))) hs = hs.slice(1);
  // one heading above all others ("# TaskFlow Pro 4.2 - User Manual") is the document's title: split
  // on the level below it, and keep any text under the title as an overview
  let preamble = null;
  while (hs.length > 1 && hs.filter(h => h.level === hs[0].level).length === 1 && hs.every(h => h.level >= hs[0].level)) {
    const body = lines.slice(hs[0].line + 1, hs[1].line).join('\n').trim();
    if (body) preamble = { title: hs[0].title, body };
    hs = hs.slice(1);
  }
  const top = hs.length ? Math.min(...hs.map(h => h.level)) : 1;
  const second = hs.filter(h => h.level > top).length ? Math.min(...hs.filter(h => h.level > top).map(h => h.level)) : null;
  const bodyOf = (from, to) => lines.slice(from + 1, to).join('\n').trim();
  const nextAtOrAbove = (i, level) => { for (let j = i + 1; j < hs.length; j++) if (hs[j].level <= level) return hs[j].line; return lines.length; };
  const firstChild = i => (i + 1 < hs.length && hs[i + 1].level > hs[i].level) ? hs[i + 1].line : nextAtOrAbove(i, hs[i].level);

  const modules = [];
  // chapters become modules when most of them have sub-sections of their own (a user manual:
  // "2 Image Classification" with 2.1-2.6); otherwise (a short guide, a report, a policy) the
  // document is one module and each chapter one section
  const tops = hs.map((h, i) => [h, i]).filter(([h]) => h.level === top);
  const subCount = i => { let n = 0; for (let j = i + 1; j < hs.length && hs[j].level > top; j++) if (hs[j].level === second) n++; return n; };
  const byChapter = second !== null && tops.length > 1 && tops.filter(([, i]) => subCount(i) >= 2).length * 2 >= tops.length;
  if (!byChapter) {
    const name = (entry.doc_title || entry.filename).replace(/\.[a-z0-9]+$/i, '').replace(/[_]+/g, ' ').trim();
    const title = name.charAt(0).toUpperCase() + name.slice(1);
    // a chapter with sub-sections ("4 Common Issues": 4.1-4.3) becomes its intro plus one section
    // per sub-section, so a search for one issue finds that issue's section, not the whole chapter
    const demote = (text, base) => text.replace(/^(#{1,6})\s+/gm, (_, hashes) => '#'.repeat(Math.max(2, hashes.length - base + 1)) + ' ').trim();
    const secs = [];
    for (const [h, i] of tops) {
      if (second === null || subCount(i) === 0) {
        secs.push({ title: h.title, body: demote(lines.slice(h.line + 1, nextAtOrAbove(i, top)).join('\n'), top) });
        continue;
      }
      const intro = bodyOf(h.line, firstChild(i));
      if (intro) secs.push({ title: h.title, body: intro });
      for (let j = i + 1; j < hs.length && hs[j].level > top; j++) {
        if (hs[j].level === second) secs.push({ title: hs[j].title, body: demote(lines.slice(hs[j].line + 1, nextAtOrAbove(j, second)).join('\n'), second) });
      }
    }
    const lead = bodyOf(-1, hs.length ? hs[0].line : lines.length);
    const pre = preamble || (lead ? { title, body: lead } : null);
    if (pre && pre.body) secs.unshift(pre);
    modules.push({ title, sections: secs.filter(s => s.body), wholeDocument: true });
    return modules;
  }
  hs.forEach((h, i) => {
    if (h.level !== top) return;
    const sections = [];
    const intro = bodyOf(h.line, firstChild(i));
    if (intro) sections.push({ title: h.title, body: intro });
    for (let j = i + 1; j < hs.length && hs[j].level > top; j++) {
      if (hs[j].level !== second) continue;
      // deeper headings ("2.2.1 Option A") stay in the section's body, one level below its title
      const end = nextAtOrAbove(j, second);
      const body = lines.slice(hs[j].line + 1, end).join('\n')
        .replace(/^(#{1,6})\s+/gm, (_, hashes) => '#'.repeat(Math.max(2, hashes.length - second + 1)) + ' ').trim();
      sections.push({ title: hs[j].title, body });
    }
    if (sections.length) modules.push({ title: h.title, sections });
  });
  if (preamble) modules.unshift({ title: 'Overview', sections: [preamble] });
  // an unnumbered first chapter ahead of numbered ones ("InspectAI Studio 4 - User Guide" then
  // "1 Getting Started") is the manual's own introduction
  const numbered = t => /^\s*\d+(\.\d+)*\.?\s/.test(t);
  if (modules.length > 1 && !numbered(modules[0].title) && modules.slice(1).some(m => numbered(m.title))) {
    modules[0].title = 'Overview';
  }
  return modules;
}

// ---------- build ----------

function build(exportZip, outDir, opts = {}) {
  const exportFiles = readZip(fs.readFileSync(exportZip));
  if (!exportFiles.has('page_index.json')) throw new Error('no page_index.json: not a Lattice knowledge-base export');
  const index = JSON.parse(exportFiles.get('page_index.json')().toString('utf8'));
  const product = opts.name || index.kb_name || 'Knowledge base';
  const description = index.kb_description || '';

  fs.rmSync(path.join(outDir, 'modules'), { recursive: true, force: true });
  fs.rmSync(path.join(outDir, 'images'), { recursive: true, force: true });
  for (const d of ['modules', 'images', 'pages', 'interaction', 'mcp-server']) fs.mkdirSync(path.join(outDir, d), { recursive: true });

  const kb = { name: product, description, source: { file: path.basename(exportZip), generated_at: index.generated_at || null }, modules: [], sections: [] };
  const gaps = [];
  const usedSlugs = new Set();
  const imgPerPage = new Map();
  const multiDoc = index.documents.length > 1;

  for (const entry of index.documents) {
    const { md: raw, images } = documentMarkdown(exportFiles, entry);
    const { md, described } = takeImageDescriptions(raw);
    // the export's records for each heading, matched in document order: "2.2 Importing Images" and
    // "3.2 Importing Images" have the same title, so a lookup by title alone mixes them up
    const records = new Map();
    for (const s of entry.sections || []) {
      const k = norm(s.title);
      if (!records.has(k)) records.set(k, []);
      records.get(k).push(s);
    }
    const recordFor = title => {
      const list = records.get(norm(title));
      if (!list || !list.length) return null;
      const num = (title.match(/^\s*(\d+(?:\.\d+)*)/) || [])[1];
      const at = num ? list.findIndex(r => r.section_number === num || r.title.startsWith(num + ' ')) : -1;
      return list.splice(at >= 0 ? at : 0, 1)[0];
    };
    const docSlug = slug(entry.filename.replace(/\.[a-z0-9]+$/i, ''));

    const docName0 = entry.filename.replace(/\.[a-z0-9]+$/i, '').replace(/_+/g, ' ').trim();
    const docName = docName0.charAt(0).toUpperCase() + docName0.slice(1);
    for (const mod of splitDocument(entry, md)) {
      // several manuals in one KB: a chapter module says which manual it is from ("Maintenance"
      // is in both the HX-500 and the HX-900 manual)
      if (multiDoc && !mod.wholeDocument) mod.title = `${docName}: ${withoutNumber(mod.title)}`;
      const base = slug(withoutNumber(mod.title));
      let id = base;
      if (usedSlugs.has(id) && !multiDoc) id = slug(`${docSlug}-${base}`);
      for (let k = 2; usedSlugs.has(id); k++) id = `${base}-${k}`;
      usedSlugs.add(id);
      const modDir = path.join(outDir, 'modules', id);
      fs.mkdirSync(modDir, { recursive: true });

      const sectionRows = [];
      mod.sections.forEach((sec, n) => {
        const nn = pad(n + 1, 2);
        const sslug = n === 0 && sec.title === mod.title ? id : slug(withoutNumber(sec.title));
        const file = `${nn}-${sslug}.md`;
        const sid = `${id}/${nn}-${sslug}`;
        const rec = recordFor(sec.title);
        const pages = rec && rec.start_page != null ? [rec.start_page, rec.end_page ?? rec.start_page] : null;
        const summary = rec && rec.summary && norm(rec.summary) !== norm(sec.title) ? rec.summary : null;
        const sectionImages = [];
        // pictures: renamed page_NNN_imgK.png, followed by their path and, when the export has one, a description
        let body = sec.body.replace(/!\[([^\]]*)\]\((?:\.\/)?images\/([^)\s]+)\)/g, (all, alt, name) => {
          const get = images.get(name);
          if (!get) { gaps.push(`**${id}/${file}**: picture \`${name}\` is referenced but missing from the export`); return all; }
          const page = pages ? pages[0] : 0;
          const k = (imgPerPage.get(page) || 0) + 1; imgPerPage.set(page, k);
          const ext = path.extname(name).toLowerCase() || '.png';
          const target = `page_${pad(page, 3)}_img${k}${ext}`;
          fs.writeFileSync(path.join(outDir, 'images', target), get());
          sectionImages.push(`images/${target}`);
          const what = described.get(name);
          if (!what) gaps.push(`**${id}/${file}** (${pagesLabel(pages)}): picture ${target} has no description; questions about what it shows cannot be answered`);
          if (!pages) gaps.push(`**${id}/${file}**: picture ${target} named page 000 because the export gives no page number`);
          return `![screenshot ${pages ? 'p.' + page : alt || 'figure'}](../../images/${target})\n\nImage path: images/${target}` +
            (what ? `\n\nImage description: ${what}` : '');
        });
        if (/\b(TBD|TODO|XXX|\?\?\?|lorem ipsum)\b/i.test(body)) {
          const hit = body.match(/[^\n]*\b(TBD|TODO|XXX|\?\?\?)\b[^\n]*/i);
          gaps.push(`**${id}/${file}** (${pagesLabel(pages)}): value not documented (TBD) — \`${(hit ? hit[0] : '').trim().slice(0, 120)}\``);
        }
        if (!plainText(body)) gaps.push(`**${id}/${file}** (${pagesLabel(pages)}): section has no text`);
        const frontmatter = ['---', `id: ${sid}`, `module: ${id}`, `title: ${yamlStr(sec.title)}`, `pages: ${pages ? `[${pages[0]}, ${pages[1]}]` : 'null'}`, '---'].join('\n');
        fs.writeFileSync(path.join(modDir, file), `${frontmatter}\n\n# ${sec.title}\n\n_Manual ${pagesLabel(pages)}_\n\n${body}\n`);
        sectionRows.push({ file, title: sec.title, pages });
        // summary: kept from the Lattice export (not in the sem-ai-kb spec; used for search previews)
        kb.sections.push({ id: sid, module: id, title: sec.title, pages, images: sectionImages, file: `modules/${id}/${file}`,
          text: plainText(body), ...(summary ? { summary } : {}) });
      });

      const known = sectionRows.filter(r => r.pages).map(r => r.pages);
      const modPages = known.length ? [Math.min(...known.map(p => p[0])), Math.max(...known.map(p => p[1]))] : null;
      const modTitle = withoutNumber(mod.title);
      fs.writeFileSync(path.join(modDir, 'README.md'),
        `# ${modTitle}\n\n_Manual ${pagesLabel(modPages)}_\n\n${multiDoc ? `Source: ${entry.filename}\n\n` : ''}Sections:\n\n` +
        sectionRows.map(r => `- [${r.title}](${r.file}) — ${pagesLabel(r.pages)}`).join('\n') + '\n');
      kb.modules.push({ id, title: modTitle, pages: modPages, source: entry.filename, ...(mod.wholeDocument && entry.doc_summary ? { summary: entry.doc_summary } : {}) });
      if (!modPages) gaps.push(`**${id}/** : the export gives no page numbers for this module, so citations cannot name a page`);
    }
  }

  fs.writeFileSync(path.join(outDir, 'kb.json'), JSON.stringify(kb, null, 2) + '\n');
  fs.writeFileSync(path.join(outDir, 'index.md'),
    `# ${product} knowledge base\n\n${description ? description + '\n\n' : ''}` +
    `Start with [AGENTS.md](AGENTS.md). Interview flow: [interaction/decision-tree.json](interaction/decision-tree.json).\n\n## Modules\n\n` +
    kb.modules.map(m => `- [${m.title}](modules/${m.id}/README.md) — ${pagesLabel(m.pages)}`).join('\n') + '\n');
  const gapText = `# Needs review (generated)\n\nAuto-detected gaps from the last build (${new Date().toISOString().slice(0, 10)}). ` +
    `Copy what is still open into needs-review.md; this file is overwritten on every build.\n\n` +
    (gaps.length ? [...new Set(gaps)].map(g => `- ${g}`).join('\n') : '- none found') + '\n';
  fs.writeFileSync(path.join(outDir, 'needs-review.generated.md'), gapText);
  fs.copyFileSync(path.join(HERE, '..', 'mcp-server', 'server.mjs'), path.join(outDir, 'mcp-server', 'server.mjs'));
  if (!fs.readdirSync(path.join(outDir, 'pages')).length) fs.writeFileSync(path.join(outDir, 'pages', '.gitkeep'), '');

  // hand-maintained files: written only when missing
  const once = (rel, text) => { const f = path.join(outDir, rel); if (!fs.existsSync(f)) fs.writeFileSync(f, text); };
  once('needs-review.md', gapText.replace('# Needs review (generated)', '# Needs review')
    .replace(/Auto-detected gaps from the last build[^\n]*\n/, 'Known gaps in the manual (missing page numbers, TBD values, undescribed pictures). Edit this file as gaps are resolved; the agent reads it before asserting values. Never overwritten by the build.\n'));
  once('interaction/decision-tree.json', JSON.stringify(skeletonTree(kb), null, 2) + '\n');
  once('AGENTS.md', agentsTemplate(product, description));
  once('CLAUDE.md', '@AGENTS.md\n');
  once('README.md', readmeTemplate(path.basename(path.resolve(outDir)), product));
  return { modules: kb.modules.length, sections: kb.sections.length, images: kb.sections.reduce((n, s) => n + s.images.length, 0), gaps: new Set(gaps).size };
}

function skeletonTree(kb) {
  // one question: which module. Replace with a real interview (goal -> task -> variant).
  const opts = kb.modules.slice(0, 5).map(m => ({ label: m.title, next: `result:${m.id}` }));
  if (kb.modules.length > 5) opts.push({ label: 'Something else', next: 'result:browse' });
  const results = Object.fromEntries(kb.modules.map(m => [m.id, { modules: [m.id], summary: m.title, ask_next: [] }]));
  results.browse = { modules: kb.modules.map(m => m.id), summary: 'All modules', ask_next: ['What are you trying to do?'] };
  return { start: 'topic', nodes: { topic: { ask: 'What do you need help with?', options: opts } }, resolve: {}, results };
}

function agentsTemplate(product, description) {
  return `# ${product} assistant

You are an interactive guide for **${product}**${description ? `, ${description.replace(/\.$/, '')}` : ''}.

Your knowledge base is this folder (the manual, restructured). You are a guide,
not a search box: **interview first, then walk the user through, step by step.**

## How to use the KB

- If the MCP server is connected → use its tools (kb_start, kb_answer, kb_search, kb_get_section, ...)
- Otherwise read files directly: index.md, interaction/decision-tree.json,
  modules/<module>/README.md, modules/<module>/<NN>-<section>.md, needs-review.md

## Core behaviour: clarify before answering

1. When a request is vague → ask 1-2 clarifying questions with 2-5 options + "something else"
2. Facts that change the answer → establish before giving steps
3. Reuse what the user already told you. Never re-ask.
4. After the interview → say what you understood in one line → ask follow-ups → proceed

## Core behaviour: guided walkthrough

- Present one step at a time → confirm before continuing
- Offer the next logical step when a step finishes
- If stuck → ask what they see on screen, which step, what they clicked

## Images

- Include the screenshot path as \`Screenshot: <path>\` when relevant
- Don't dump every image; pick the one matching the current step

## Grounding rules

- Cite the source: *(Module Name, p. N)*; when the section has no page number, *(Module Name › Section)*
- If the manual doesn't say → say so. Never invent UI labels, values, defaults.
- Check needs-review.md before asserting values
- OCR slips can be read charitably but say when you do

## Maintenance

- modules/, kb.json and index.md are generated by tools/build-kb.mjs (rebuilding overwrites edits)
- needs-review.md is hand-maintained and never overwritten
- Edit interaction/decision-tree.json to change clarifying questions
`;
}

function readmeTemplate(repo, product) {
  return `# ${repo}

Interactive knowledge base for **${product}**, built from the user manual.
Works with any agent harness, with or without MCP.

## Option A: files only (any harness)

Point the harness at this folder and make \`AGENTS.md\` its instructions.
(For Claude Code: \`CLAUDE.md\` contains \`@AGENTS.md\`.)

## Option B: MCP server (recommended)

Zero dependencies, needs Node 18+. Use the absolute path to \`mcp-server/server.mjs\`.

Claude Code (\`.mcp.json\`):

\`\`\`json
{ "mcpServers": { "kb": { "command": "node", "args": ["/abs/path/${repo}/mcp-server/server.mjs"] } } }
\`\`\`

opencode (\`opencode.json\`):

\`\`\`json
{ "mcp": { "kb": { "type": "local", "command": ["node", "/abs/path/${repo}/mcp-server/server.mjs"] } } }
\`\`\`

Hermes (\`config.yaml\`):

\`\`\`yaml
mcp_servers:
  kb:
    command: node
    args: ["/abs/path/${repo}/mcp-server/server.mjs"]
\`\`\`

## Layout

\`\`\`
AGENTS.md                      agent instructions (hand-maintained)
index.md                       module list (generated)
needs-review.md                known gaps (hand-maintained)
needs-review.generated.md      gaps found by the last build (generated)
interaction/decision-tree.json clarifying interview (hand-maintained)
modules/<module>/README.md     module overview (generated)
modules/<module>/NN-<slug>.md  one section of the manual (generated)
kb.json                        machine-readable index for search (generated)
images/                        screenshots, page_NNN_imgK.png (generated)
pages/                         original page scans, page_NNN.jpg (not in Lattice exports)
mcp-server/server.mjs          MCP server: search + interview
\`\`\`
`;
}

// ---------- CLI ----------

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const nameAt = args.indexOf('--name');
  const name = nameAt >= 0 ? args.splice(nameAt, 2)[1] : undefined;
  if (args.length !== 2) {
    console.error('usage: node tools/build-kb.mjs <export.zip> <out-dir> [--name "Product Name"]');
    process.exit(2);
  }
  const r = build(args[0], args[1], { name });
  console.log(`built ${args[1]}: ${r.modules} modules, ${r.sections} sections, ${r.images} images, ${r.gaps} gaps`);
}

export { build, readZip, plainText, slug };
