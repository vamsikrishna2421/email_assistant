#!/usr/bin/env node
//
// Validates workflows/jerry.workflow.json against the n8n actually installed in
// runtime/, before it ever reaches the editor.
//
//   node scripts/validate-workflow.mjs
//
// `n8n import:workflow` only checks that the JSON fits the database schema — it
// will happily store a node whose type does not exist or whose typeVersion the
// installed package cannot run. Those failures surface later as a broken canvas
// or a runtime error mid-conversation, so we check them here instead.

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(ROOT, 'runtime', 'package.json'));

const { safeParseWorkflowStructure } = require('n8n-workflow');

const problems = [];
const fail = (msg) => problems.push(msg);

// --- build the node-type registry from the installed packages ---------------

/** name -> { versions:Set, credentials:Set, usableAsTool:boolean } */
const registry = new Map();

for (const pkg of ['n8n-nodes-base', '@n8n/n8n-nodes-langchain']) {
  const pkgJsonPath = require.resolve(`${pkg}/package.json`);
  const pkgDir = dirname(pkgJsonPath);
  const { n8n } = JSON.parse(readFileSync(pkgJsonPath, 'utf8'));
  const prefix = pkg === 'n8n-nodes-base' ? 'n8n-nodes-base' : '@n8n/n8n-nodes-langchain';

  for (const rel of n8n?.nodes ?? []) {
    let mod;
    try {
      mod = require(join(pkgDir, rel));
    } catch {
      continue; // a handful of nodes fail to load standalone; not our concern
    }
    for (const Exported of Object.values(mod)) {
      if (typeof Exported !== 'function') continue;
      let inst;
      try {
        inst = new Exported();
      } catch {
        continue;
      }
      const desc = inst.description;
      if (!desc?.name) continue;

      const versions = new Set();
      if (inst.nodeVersions) {
        for (const v of Object.keys(inst.nodeVersions)) versions.add(Number(v));
      }
      for (const v of [desc.version].flat()) if (v !== undefined) versions.add(Number(v));

      const credentials = new Set((desc.credentials ?? []).map((c) => c.name));
      // Versioned nodes keep credentials on each version, not the shell.
      for (const sub of Object.values(inst.nodeVersions ?? {})) {
        for (const c of sub?.description?.credentials ?? []) credentials.add(c.name);
      }

      const usableAsTool = Boolean(
        desc.usableAsTool ||
        Object.values(inst.nodeVersions ?? {}).some((s) => s?.description?.usableAsTool),
      );

      registry.set(`${prefix}.${desc.name}`, { versions, credentials, usableAsTool });
    }
  }
}

// --- load and structurally parse the workflow -------------------------------

const wfPath = process.argv[2]
  ? (process.argv[2].startsWith('/') ? process.argv[2] : join(ROOT, process.argv[2]))
  : join(ROOT, 'workflows', 'jerry.workflow.json');
const wf = JSON.parse(readFileSync(wfPath, 'utf8'));

const parsed = safeParseWorkflowStructure(wf);
if (!parsed.success) {
  for (const issue of parsed.error?.issues ?? [parsed.error]) {
    fail(`structure: ${issue.message ?? JSON.stringify(issue)}`);
  }
}

const byName = new Map(wf.nodes.map((n) => [n.name, n]));

// --- per-node checks --------------------------------------------------------

const FROM_AI_KEY = /^[a-zA-Z0-9_-]{1,64}$/;

for (const node of wf.nodes) {
  // A node used as an AI tool has no file of its own: n8n appends "Tool" to the
  // base node's name at load time, and only if the base declares usableAsTool.
  let entry = registry.get(node.type);
  let toolOf = null;
  if (!entry && node.type.endsWith('Tool')) {
    toolOf = node.type.slice(0, -'Tool'.length);
    entry = registry.get(toolOf);
    if (entry && !entry.usableAsTool) {
      fail(`${node.name}: ${toolOf} exists but is not usable as a tool`);
    }
  }

  if (!entry) {
    fail(`${node.name}: unknown node type "${node.type}"`);
    continue;
  }

  if (!entry.versions.has(node.typeVersion)) {
    const have = [...entry.versions].sort((a, b) => a - b).join(', ');
    fail(`${node.name}: typeVersion ${node.typeVersion} not supported by ${node.type} (has ${have})`);
  }

  for (const key of Object.keys(node.credentials ?? {})) {
    if (!entry.credentials.has(key)) {
      const have = [...entry.credentials].join(', ') || 'none';
      fail(`${node.name}: credential "${key}" not accepted by ${node.type} (accepts ${have})`);
    }
  }

  // Expression sanity: `{{` and `}}` must pair up, and every $fromAI key must
  // match the key regex n8n enforces at run time.
  for (const [param, value] of Object.entries(node.parameters ?? {})) {
    for (const str of collectStrings(value)) {
      if (!str.startsWith('=')) continue;
      const open = (str.match(/\{\{/g) ?? []).length;
      const close = (str.match(/\}\}/g) ?? []).length;
      if (open !== close) {
        fail(`${node.name}.${param}: unbalanced expression braces (${open} "{{" vs ${close} "}}")`);
      }
      for (const [, key] of str.matchAll(/\$fromAI\(\s*'([^']*)'/g)) {
        if (!FROM_AI_KEY.test(key)) fail(`${node.name}.${param}: invalid $fromAI key "${key}"`);
      }
    }
  }
}

function* collectStrings(value) {
  if (typeof value === 'string') yield value;
  else if (Array.isArray(value)) for (const v of value) yield* collectStrings(v);
  else if (value && typeof value === 'object') for (const v of Object.values(value)) yield* collectStrings(v);
}

// --- connection checks ------------------------------------------------------

const incoming = new Map(); // target node -> { type -> count }

for (const [source, byType] of Object.entries(wf.connections)) {
  if (!byName.has(source)) fail(`connection source "${source}" is not a node`);
  for (const [type, groups] of Object.entries(byType)) {
    for (const group of groups) {
      for (const link of group) {
        if (!byName.has(link.node)) fail(`connection ${source} -> "${link.node}" targets a missing node`);
        if (link.type !== type) fail(`connection ${source} -> ${link.node}: type "${link.type}" != key "${type}"`);
        const counts = incoming.get(link.node) ?? {};
        counts[type] = (counts[type] ?? 0) + 1;
        incoming.set(link.node, counts);
      }
    }
  }
}

// Every agent needs exactly one chat model and at most one memory, or n8n
// refuses to execute it.
const AGENTS = ['@n8n/n8n-nodes-langchain.agent', '@n8n/n8n-nodes-langchain.agentTool'];
for (const node of wf.nodes.filter((n) => AGENTS.includes(n.type))) {
  const counts = incoming.get(node.name) ?? {};
  if (counts.ai_languageModel !== 1) {
    fail(`${node.name}: needs exactly 1 chat model, has ${counts.ai_languageModel ?? 0}`);
  }
  if ((counts.ai_memory ?? 0) > 1) fail(`${node.name}: has ${counts.ai_memory} memories, max is 1`);
  if (!counts.ai_tool) fail(`${node.name}: has no tools connected`);
}

// Every agent's system prompt lists the tools it can call. n8n derives the name
// the model actually sees from the canvas node name, so a prompt that names a
// tool slightly differently is a prompt that lies to the model — it will call
// something that does not exist and get an error it cannot interpret.
const toolName = (canvasName) => canvasName.replace(/[^a-zA-Z0-9_-]+/g, '_');

for (const agent of wf.nodes.filter((n) => AGENTS.includes(n.type))) {
  const offered = new Set();
  for (const [source, byType] of Object.entries(wf.connections)) {
    for (const link of byType.ai_tool?.flat() ?? []) {
      if (link.node === agent.name) offered.add(toolName(source));
    }
  }

  // Every prompt declares its tools as "- Tool_Name — what it does". Anchoring
  // on that shape keeps the check away from prose that merely contains an
  // underscore, like America/New_York or Gmail's newer_than: operator.
  const prompt = agent.parameters?.options?.systemMessage ?? '';
  const declared = new Set(
    [...prompt.matchAll(/^- ([A-Za-z][A-Za-z0-9_]*) —/gm)].map((m) => m[1]),
  );

  for (const name of declared) {
    if (!offered.has(name)) {
      fail(`${agent.name}: prompt describes tool "${name}", which is not connected to it`);
    }
  }
  for (const name of offered) {
    if (!declared.has(name)) {
      fail(`${agent.name}: tool "${name}" is connected but its prompt never mentions it`);
    }
  }
}

// Nothing should be stranded on the canvas. A trigger with no inbound
// connection is normal, and an IF's unused false branch leaves no trace.
for (const node of wf.nodes) {
  const isSource = node.name in wf.connections;
  const isTarget = incoming.has(node.name);
  if (!isSource && !isTarget) fail(`${node.name}: not connected to anything`);
}

// --- report -----------------------------------------------------------------

const agents = wf.nodes.filter((n) => AGENTS.includes(n.type)).length;
const tools = [...incoming.values()].reduce((n, c) => n + (c.ai_tool ?? 0), 0);

if (problems.length) {
  console.error(`✗ ${problems.length} problem(s) in ${wfPath}\n`);
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}

console.log(`✓ ${wfPath}`);
console.log(`  ${wf.nodes.length} nodes, ${agents} agents, ${tools} tool connections`);
console.log(`  all node types and versions resolve against the installed n8n`);
