#!/usr/bin/env node
//
// Measures how many requests per minute your Gemini key actually allows for a
// given model, by sending minimal requests until one is refused.
//
//   node scripts/probe-models.mjs
//   node scripts/probe-models.mjs models/gemini-3.5-flash models/gemini-2.5-pro
//
// Why this exists: Google no longer publishes per-model free-tier rate limits,
// and the limits differ enough to decide the architecture. Jerry spends 4-6
// model calls on a single message — master agent, sub-agent, then another round
// each once the tools return — all inside about twenty seconds. A model capped
// at 5 requests/minute fails halfway through a sentence; one that allows 15 is
// comfortable. Guessing that number wrong looks like a broken assistant.
//
// The key is read from n8n's encrypted credential store, never printed.

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const MODELS = process.argv.slice(2).length
  ? process.argv.slice(2)
  : ['models/gemini-3.1-flash-lite', 'models/gemini-3.5-flash'];

const CEILING = 16; // stop here; enough to prove a model is comfortable

function readApiKey() {
  const dir = mkdtempSync(join(tmpdir(), 'jerry-probe-'), { mode: 0o700 });
  try {
    const out = join(dir, 'c.json');
    execFileSync(join(ROOT, 'runtime', 'node_modules', '.bin', 'n8n'),
      ['export:credentials', '--all', '--decrypted', `--output=${out}`], {
        stdio: 'ignore',
        env: {
          ...process.env,
          PATH: `/usr/local/opt/node@22/bin:${process.env.PATH}`,
          N8N_USER_FOLDER: join(ROOT, 'runtime'),
        },
      });
    const creds = JSON.parse(readFileSync(out, 'utf8'));
    return creds.find((c) => c.id === 'jerry-gemini')?.data?.apiKey ?? '';
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const key = readApiKey();
if (!key) {
  console.error('No Gemini key in the credential store. Run ./scripts/setup.sh first.');
  process.exit(1);
}

const body = JSON.stringify({
  contents: [{ parts: [{ text: 'hi' }] }],
  generationConfig: { maxOutputTokens: 1 },
});

async function probe(model) {
  let ok = 0;
  for (let i = 0; i < CEILING; i++) {
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/${model}:generateContent?key=${key}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body },
    );
    if (res.status === 429) {
      const quota = (await res.text()).match(/"quotaValue":"(\d+)"/);
      return { ok, limit: quota ? Number(quota[1]) : ok };
    }
    if (!res.ok) return { ok, error: `HTTP ${res.status}` };
    ok++;
  }
  return { ok, limit: `${CEILING}+` };
}

for (const [i, model] of MODELS.entries()) {
  if (i > 0) {
    process.stdout.write('  waiting 65s for the rate window to reset...\r');
    await new Promise((r) => setTimeout(r, 65_000));
  }
  const { ok, limit, error } = await probe(model);
  if (error) {
    console.log(`${model.padEnd(34)} unusable (${error})`);
  } else {
    const verdict = typeof limit === 'number' && limit < 10 ? '  <- too low for Jerry' : '';
    console.log(`${model.padEnd(34)} ${ok} calls accepted, limit ${limit}/min${verdict}`);
  }
}
