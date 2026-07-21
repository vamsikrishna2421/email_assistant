#!/usr/bin/env node
//
// Seeds n8n's credential store from config/.env.
//
//   ./scripts/setup.sh          (runs this for you)
//   node scripts/import-credentials.mjs
//
// The four credentials Jerry needs are created with fixed ids that match the
// ids referenced in workflows/jerry.workflow.json, so the nodes are wired up
// the moment they are imported — no clicking through the credential UI.
//
// Google OAuth is the exception: the client id and secret can be seeded, but
// the token itself can only be obtained by a browser round-trip, so you still
// press "Connect my account" once per Google credential. See docs/SETUP.md.
//
// The generated file contains live secrets. It is written to a 0600 temp file
// and deleted in a finally block, whatever happens.

import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFileSync, existsSync } from 'node:fs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const ENV_FILE = join(ROOT, 'config', '.env');

if (!existsSync(ENV_FILE)) {
  console.error('config/.env not found.\n  cp config/.env.example config/.env   then fill it in');
  process.exit(1);
}

const env = {};
for (const line of readFileSync(ENV_FILE, 'utf8').split('\n')) {
  const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/);
  if (m) env[m[1]] = m[2].trim().replace(/^["']|["']$/g, '');
}

const has = (k) => Boolean(env[k]);

// ids and names here must match CRED in scripts/lib/nodes.mjs and PROVIDERS in
// scripts/lib/providers.mjs. Every provider Jerry can run on gets a credential
// if its key is present, so switching JERRY_PROVIDER needs no extra setup.
const candidates = [
  {
    id: 'jerry-gemini',
    name: 'Jerry — Google Gemini',
    type: 'googlePalmApi',
    needs: ['GEMINI_API_KEY'],
    data: () => ({ host: 'https://generativelanguage.googleapis.com', apiKey: env.GEMINI_API_KEY }),
  },
  {
    id: 'jerry-ollama',
    name: 'Jerry — Ollama',
    type: 'ollamaApi',
    // No API key: a local Ollama needs only its address, so this one is always
    // created and JERRY_PROVIDER=ollama works with no further setup.
    needs: [],
    // 127.0.0.1, not localhost. Ollama binds IPv4 only, and n8n's AI nodes go
    // through a custom undici dispatcher that does not fall back from ::1 to
    // 127.0.0.1 the way the default fetch agent does — so "localhost" resolves
    // to IPv6, gets refused, and surfaces as a bare "fetch failed".
    data: () => ({ baseUrl: env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434', apiKey: '' }),
  },
  {
    id: 'jerry-openrouter',
    name: 'Jerry — OpenRouter',
    type: 'openRouterApi',
    needs: ['OPENROUTER_API_KEY'],
    data: () => ({ apiKey: env.OPENROUTER_API_KEY, url: 'https://openrouter.ai/api/v1' }),
  },
  {
    id: 'jerry-groq',
    name: 'Jerry — Groq',
    type: 'groqApi',
    needs: ['GROQ_API_KEY'],
    data: () => ({ apiKey: env.GROQ_API_KEY }),
  },
  {
    id: 'jerry-openai',
    name: 'Jerry — OpenAI-compatible',
    type: 'openAiApi',
    // `url` is what makes this useful: point it at a LiteLLM proxy and one
    // endpoint can fail over across several providers behind Jerry's back.
    needs: ['OPENAI_API_KEY'],
    data: () => ({
      apiKey: env.OPENAI_API_KEY,
      url: env.OPENAI_BASE_URL || 'https://api.openai.com/v1',
    }),
  },
  {
    id: 'jerry-gmail',
    name: 'Jerry — Gmail',
    type: 'gmailOAuth2',
    needs: ['GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET'],
    oauth: true,
    data: () => ({
      clientId: env.GOOGLE_OAUTH_CLIENT_ID,
      clientSecret: env.GOOGLE_OAUTH_CLIENT_SECRET,
    }),
  },
  {
    id: 'jerry-gcal',
    name: 'Jerry — Google Calendar',
    type: 'googleCalendarOAuth2Api',
    needs: ['GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET'],
    oauth: true,
    data: () => ({
      clientId: env.GOOGLE_OAUTH_CLIENT_ID,
      clientSecret: env.GOOGLE_OAUTH_CLIENT_SECRET,
    }),
  },
  {
    id: 'jerry-fireflies',
    name: 'Jerry — Fireflies',
    type: 'httpHeaderAuth',
    needs: ['FIREFLIES_API_KEY'],
    data: () => ({ name: 'Authorization', value: `Bearer ${env.FIREFLIES_API_KEY}` }),
  },
];

const ready = candidates.filter((c) => c.needs.every(has));
const skipped = candidates.filter((c) => !c.needs.every(has));

if (!ready.length) {
  console.error('No credentials to import — every value in config/.env is still blank.');
  process.exit(1);
}

const dir = mkdtempSync(join(tmpdir(), 'jerry-cred-'), { mode: 0o700 });
const file = join(dir, 'credentials.json');

const n8nBin = join(ROOT, 'runtime', 'node_modules', '.bin', 'n8n');
const n8nEnv = {
  ...process.env,
  PATH: `/usr/local/opt/node@22/bin:${process.env.PATH}`,
  N8N_USER_FOLDER: join(ROOT, 'runtime'),
};

// Importing a credential replaces its whole `data` blob — including the OAuth
// tokens n8n stores there once you have pressed "Connect my account". Re-running
// setup after connecting Google would therefore silently disconnect it, and the
// only symptom is an auth error the next time an agent touches Gmail. So carry
// any existing token across instead of clobbering it.
const preserved = new Map();
try {
  const dump = join(dir, 'existing.json');
  execFileSync(n8nBin, ['export:credentials', '--all', '--decrypted', `--output=${dump}`],
    { stdio: 'ignore', env: n8nEnv });
  for (const c of JSON.parse(readFileSync(dump, 'utf8'))) {
    if (c?.data?.oauthTokenData) preserved.set(c.id, c.data.oauthTokenData);
  }
  rmSync(dump, { force: true });
} catch {
  // No credentials exported yet (fresh install) — nothing to preserve.
}

try {
  writeFileSync(file, JSON.stringify(ready.map((c) => {
    const data = c.data();
    const token = preserved.get(c.id);
    if (token) data.oauthTokenData = token;
    return { id: c.id, name: c.name, type: c.type, data };
  }), null, 2), { mode: 0o600 });

  execFileSync(n8nBin, ['import:credentials', `--input=${file}`], { stdio: 'inherit', env: n8nEnv });
} finally {
  rmSync(dir, { recursive: true, force: true });
}

console.log();
for (const c of ready) {
  const kept = preserved.has(c.id) ? '  (existing Google connection kept)' : '';
  console.log(`  imported  ${c.name}${kept}`);
}
for (const c of skipped) console.log(`  skipped   ${c.name}  (set ${c.needs.filter((n) => !has(n)).join(', ')} in config/.env)`);

const toConnect = ready.filter((c) => c.oauth && !preserved.has(c.id));
if (toConnect.length) {
  console.log('\nStill to do by hand — OAuth needs a browser round-trip:');
  for (const c of toConnect) {
    console.log(`  open http://localhost:5678 -> Credentials -> "${c.name}" -> Connect my account`);
  }
}
