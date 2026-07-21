#!/usr/bin/env node
//
// Evaluates every expression in the workflow with n8n's real expression engine.
//
//   node scripts/test-expressions.mjs
//
// This exists because of one specific hazard. The Fireflies tools send a raw
// GraphQL query, and GraphQL is made of braces. n8n's expression tokenizer
// treats `}}` as "end of expression", so a query written `{transcript{id}}`
// would be cut in half and produce a syntactically valid but wrong request that
// only fails when someone asks about a meeting. Spacing the braces avoids it —
// this test proves the spacing is right rather than assuming it.

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(ROOT, 'runtime', 'package.json'));
const { Tournament } = require('@n8n/tournament');

const tournament = new Tournament();
const wf = JSON.parse(readFileSync(join(ROOT, 'workflows', 'jerry.workflow.json'), 'utf8'));

// Stand-ins for the values n8n injects at run time.
const DATA = {
  $now: {
    setZone: () => ({ toFormat: () => 'Sunday 19 July 2026, 11:45' }),
  },
  $json: { chatInput: 'hello', sessionId: 'session-1' },
  $fromAI: (key, _description, type = 'string', dflt) => {
    if (dflt !== undefined) return dflt;
    return { string: `<${key}>`, number: 42, boolean: true, json: {} }[type];
  },
  $: () => ({ first: () => ({ json: { sessionId: 'session-1' } }) }),
  JSON,
};

let checked = 0;
const failures = [];

function* strings(value, path) {
  if (typeof value === 'string') yield [path, value];
  else if (Array.isArray(value)) {
    for (const [i, v] of value.entries()) yield* strings(v, `${path}[${i}]`);
  } else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) yield* strings(v, `${path}.${k}`);
  }
}

for (const node of wf.nodes) {
  for (const [path, raw] of strings(node.parameters ?? {}, node.name)) {
    if (!raw.startsWith('=')) continue;
    checked++;
    let result;
    try {
      result = tournament.execute(raw.slice(1), DATA);
    } catch (err) {
      failures.push(`${path}: ${err.message}`);
      continue;
    }
    // A surviving `{{` means a fragment was never treated as code. A surviving
    // `}}` proves nothing — JSON.stringify emits it whenever objects nest.
    if (typeof result === 'string' && result.includes('{{')) {
      failures.push(`${path}: expression left an unevaluated "{{" — ${result.slice(0, 120)}`);
    }
    // The Fireflies bodies must come out as parseable JSON holding a GraphQL doc.
    if (path.endsWith('.jsonBody')) {
      try {
        const body = JSON.parse(result);
        if (typeof body.query !== 'string') throw new Error('no query field');
        const opens = (body.query.match(/\{/g) ?? []).length;
        const closes = (body.query.match(/\}/g) ?? []).length;
        if (opens !== closes) throw new Error(`GraphQL braces unbalanced: ${opens} vs ${closes}`);
        if (!body.query.trimEnd().endsWith('}')) throw new Error('GraphQL query was truncated');
        console.log(`  ${node.name}: GraphQL intact (${opens} brace pairs), variables ${JSON.stringify(body.variables)}`);
      } catch (err) {
        failures.push(`${path}: ${err.message}`);
      }
    }
  }
}

if (failures.length) {
  console.error(`\n✗ ${failures.length} of ${checked} expressions failed\n`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}

console.log(`\n✓ all ${checked} expressions evaluate with n8n's engine`);
