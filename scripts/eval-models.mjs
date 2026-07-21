#!/usr/bin/env node
//
// Evaluates candidate models on YOUR REAL INBOX, using the exact production
// prompt and the exact production batching.
//
//   node scripts/eval-models.mjs
//   node scripts/eval-models.mjs qwen2.5:7b qwen3.5:9b
//
// This exists because the synthetic benchmark in bench-models.mjs lied twice.
// It scored qwen3.5:4b as the clear winner; given the real inbox that model
// abandoned the output format and left 33 of 34 emails unclassified. Synthetic
// previews were ~70 characters, real ones ~300, and the prompt that resulted was
// half the size. A benchmark that does not use the real input is a guess.
//
// Emails come from the last successful digest run stored in n8n's own database,
// so nothing is re-fetched from Gmail and no content leaves the machine.
//
// SCORING — no invented ground truth. Only things that are objectively checkable:
//
//   coverage     every email must receive exactly one valid category. This is
//                the failure that actually happened, and it is binary.
//   stable       same batch, same prompt, temperature 0, twice. A model whose
//                answer moves between runs cannot be trusted to triage.
//   needles      of the emails the reference marks as mattering, how many did
//                the model also surface? THIS IS THE METRIC THAT COUNTS. The
//                reference is 32 NOISE to 2 non-NOISE, so plain agreement is
//                nearly useless — a model answering NOISE to everything scores
//                94% and tells you nothing. Missing the one security alert is
//                the failure that matters.
//   noise-purity how much of what it surfaced was actually worth surfacing. A
//                model that flags everything never misses anything, and is also
//                worthless. Read this alongside needles.
//   agree        agreement with config/reference-labels.json, a hand-made pass
//                over these same 34 emails written before any model was run.
//                This is the closest thing to ground truth available, and it is
//                editable — it is your inbox, so your call overrules it.
//   consensus    agreement with the majority vote of all models tested. Not
//                truth, but a model disagreeing with every peer is a signal.
//   sanity       three rules no reasonable triage would break:
//                  a bulk discount blast is not ACTION
//                  an alert about your own money is not NOISE
//                  something you paid for is not NOISE
//   seconds      wall clock for the whole inbox.

import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(ROOT, 'runtime', 'package.json'));
const { parse } = require('flatted');
const { Agent } = require('undici');

const OLLAMA = process.env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434';
const BATCH_SIZE = 12;
// Reasoning models emit minutes of discarded chain-of-thought on CPU and blow
// past undici's 300s default. Both are handled: think off, timeouts long.
const dispatcher = new Agent({ headersTimeout: 3_600_000, bodyTimeout: 3_600_000 });

// --- the real emails, from n8n's own execution history ----------------------

const db = join(ROOT, 'runtime', '.n8n', 'database.sqlite');
const sql = (q) => execFileSync('sqlite3', [db, q], { encoding: 'utf8', maxBuffer: 1 << 28 }).trim();

const execId = sql(
  "SELECT MAX(id) FROM execution_entity WHERE workflowId='JerryEmailDigest' AND status='success';",
);
if (!execId) {
  console.error('No successful digest run found. Run the digest once first.');
  process.exit(1);
}

const runData = parse(sql(`SELECT data FROM execution_data WHERE executionId=${execId};`)).resultData.runData;
const gmail = runData['Get recent mail'][0].data.main[0];

const labelsOf = (m) => (m.labels ?? m.labelIds ?? [])
  .map((l) => (typeof l === 'string' ? l : (l.name ?? l.id ?? '')));

const emails = gmail.filter((i) => i.json?.id).map((item) => {
  const m = item.json;
  const labels = labelsOf(m);
  return {
    from: String(m.From ?? 'unknown').replace(/\s*<.*/, '').replace(/"/g, '').trim(),
    subject: String(m.Subject ?? '(no subject)').trim(),
    preview: String(m.snippet ?? '').replace(/\s+/g, ' ').slice(0, 260),
    filedAs: labels.includes('SPAM') ? 'Gmail put this in SPAM'
      : labels.includes('CATEGORY_PROMOTIONS') ? 'Gmail tab: Promotions'
      : labels.includes('CATEGORY_SOCIAL') ? 'Gmail tab: Social'
      : labels.includes('CATEGORY_UPDATES') ? 'Gmail tab: Updates'
      : 'Gmail tab: Primary',
  };
});

// --- the real prompt, lifted from the built workflow ------------------------

const workflow = JSON.parse(readFileSync(join(ROOT, 'workflows', 'digest.workflow.json'), 'utf8'));
const PROMPT_TEMPLATE = workflow.nodes
  .find((n) => n.name === 'Write the digest').parameters.text
  .replace(/^=/, '')                       // n8n expression marker
  .replace('{{ $json.emails }}', '__EMAILS__');

const batches = [];
for (let i = 0; i < emails.length; i += BATCH_SIZE) batches.push(emails.slice(i, i + BATCH_SIZE));

const renderBatch = (batch) => batch.map((e, n) =>
  `[${n + 1}] From: ${e.from}\n    Subject: ${e.subject}\n    Filed by Gmail as: ${e.filedAs}\n    Preview: ${e.preview}`,
).join('\n\n');

// --- run ---------------------------------------------------------------------

const VALID = ['ACTION', 'FYI', 'NOISE'];

async function ask(model, prompt) {
  const res = await fetch(`${OLLAMA}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    dispatcher,
    body: JSON.stringify({
      model, stream: false, think: false,
      options: { temperature: 0 },
      messages: [{ role: 'user', content: prompt }],
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()).message?.content ?? '';
}

/** Parse "<n>. CATEGORY" lines into a batch-local index -> category map. */
function parseVerdicts(text, size) {
  const out = new Map();
  for (const line of text.split('\n')) {
    const m = line.match(/^\s*[-*]?\s*(\d+)\s*[.)]\s*\**\s*([A-Za-z]+)/);
    if (!m) continue;
    const cat = m[2].toUpperCase();
    const n = Number(m[1]);
    if (!VALID.includes(cat) || n < 1 || n > size) continue;
    out.set(n, cat);
  }
  return out;
}

async function classify(model) {
  const labels = new Array(emails.length).fill(null);
  const t0 = Date.now();
  for (const [b, batch] of batches.entries()) {
    const text = await ask(model, PROMPT_TEMPLATE.replace('__EMAILS__', renderBatch(batch)));
    const verdicts = parseVerdicts(text, batch.length);
    for (const [local, cat] of verdicts) labels[b * BATCH_SIZE + local - 1] = cat;
  }
  return { labels, seconds: (Date.now() - t0) / 1000 };
}

// --- sanity rules: violations no reasonable triage would produce -------------

const BULK_OFFER = /\b(\d+% off|sale|save \$|bonus points|coupon|flash|limited time|deal of)\b/i;
const OWN_MONEY = /\b(payment|invoice|statement|fico|credit score|balance|transaction|zelle|transfer|refund|overdue|declined|sign ?in from)\b/i;
const PAID_FOR = /\b(tickets? for|order confirmation|receipt|booking|reservation)\b/i;

function sanityViolations(labels) {
  const bad = [];
  emails.forEach((e, i) => {
    const c = labels[i];
    if (!c) return;
    if (c === 'ACTION' && BULK_OFFER.test(e.subject) && !PAID_FOR.test(e.subject)) {
      bad.push(`marketing blast as ACTION: ${e.subject.slice(0, 44)}`);
    }
    if (c === 'NOISE' && OWN_MONEY.test(e.subject)) {
      bad.push(`own-money alert as NOISE: ${e.subject.slice(0, 44)}`);
    }
    if (c === 'NOISE' && PAID_FOR.test(e.subject)) {
      bad.push(`something you paid for as NOISE: ${e.subject.slice(0, 44)}`);
    }
  });
  return bad;
}

// --- main --------------------------------------------------------------------

// Hand-made reference pass over the same emails.
let REFERENCE = null;
try {
  const ref = JSON.parse(readFileSync(join(ROOT, 'config', 'reference-labels.json'), 'utf8'));
  if (String(ref.execution) === String(execId)) {
    REFERENCE = emails.map((_, i) => ref.labels[String(i + 1)]?.[0] ?? null);
  } else {
    console.log(`note: reference labels are for execution #${ref.execution}, this is #${execId} — skipping the agree column\n`);
  }
} catch { /* no reference available */ }

const installed = (await (await fetch(`${OLLAMA}/api/tags`)).json()).models.map((m) => m.name);
const models = process.argv.slice(2).length ? process.argv.slice(2) : installed;

console.log(`Evaluating ${models.length} models on ${emails.length} real emails `
  + `(${batches.length} batches of ${BATCH_SIZE}), execution #${execId}\n`);

const results = [];
for (const model of models) {
  process.stdout.write(`  ${model.padEnd(16)} `);
  try {
    const first = await classify(model);
    // Stability: repeat only the first batch — enough to catch a wobbly model
    // without tripling the runtime of the whole evaluation.
    const repeatText = await ask(model, PROMPT_TEMPLATE.replace('__EMAILS__', renderBatch(batches[0])));
    const repeat = parseVerdicts(repeatText, batches[0].length);
    let same = 0, compared = 0;
    for (const [local, cat] of repeat) {
      const original = first.labels[local - 1];
      if (original) { compared++; if (original === cat) same++; }
    }
    results.push({
      model,
      labels: first.labels,
      seconds: first.seconds,
      coverage: first.labels.filter(Boolean).length,
      stable: compared ? same / compared : 0,
      violations: sanityViolations(first.labels),
    });
    console.log(`done in ${first.seconds.toFixed(0)}s`);
  } catch (err) {
    console.log(`FAILED — ${err.message}`);
    results.push({ model, labels: [], seconds: 0, coverage: 0, stable: 0, violations: [], failed: true });
  }
}

// Consensus: majority vote per email across models that classified it.
const consensus = emails.map((_, i) => {
  const votes = {};
  for (const r of results) if (r.labels[i]) votes[r.labels[i]] = (votes[r.labels[i]] ?? 0) + 1;
  const best = Object.entries(votes).sort((a, b) => b[1] - a[1])[0];
  return best?.[0] ?? null;
});

console.log(`\n${'model'.padEnd(16)} ${'cover'.padStart(7)} ${'needles'.padStart(8)} ${'purity'.padStart(7)} ${'agree'.padStart(6)} ${'stable'.padStart(7)} ${'sanity'.padStart(7)} ${'secs'.padStart(6)}`);
console.log('-'.repeat(74));

for (const r of results) {
  if (r.failed) { console.log(`${r.model.padEnd(16)} ${'FAILED'.padStart(7)}`); continue; }
  let agree = 0, n = 0;
  r.labels.forEach((c, i) => { if (c && consensus[i]) { n++; if (c === consensus[i]) agree++; } });
  let refHit = 0, refN = 0;
  if (REFERENCE) r.labels.forEach((c, i) => { if (c && REFERENCE[i]) { refN++; if (c === REFERENCE[i]) refHit++; } });
  // The needles: emails the reference says matter. Did the model surface them?
  let needleTotal = 0, needleFound = 0, surfaced = 0, surfacedRight = 0;
  if (REFERENCE) {
    REFERENCE.forEach((ref, i) => {
      const got = r.labels[i];
      if (ref && ref !== 'NOISE') {
        needleTotal++;
        if (got && got !== 'NOISE') needleFound++;
      }
      if (got && got !== 'NOISE') {
        surfaced++;
        if (ref && ref !== 'NOISE') surfacedRight++;
      }
    });
  }
  const cover = `${r.coverage}/${emails.length}`;
  const agreeRef = REFERENCE && refN ? `${((refHit / refN) * 100).toFixed(0)}%` : '-';
  const needles = needleTotal ? `${needleFound}/${needleTotal}` : '-';
  const purity = surfaced ? `${surfacedRight}/${surfaced}` : '0/0';
  console.log(
    `${r.model.padEnd(16)} ${cover.padStart(7)} ${needles.padStart(8)} ${purity.padStart(7)} `
    + `${agreeRef.padStart(6)} ${(r.stable * 100).toFixed(0).padStart(6)}% `
    + `${String(r.violations.length).padStart(7)} ${r.seconds.toFixed(0).padStart(6)}`,
  );
}

// Where the models disagree with the reference, show what and how.
if (REFERENCE) {
  const disagreements = new Map();
  for (const r of results.filter((x) => !x.failed)) {
    r.labels.forEach((c, i) => {
      if (!c || !REFERENCE[i] || c === REFERENCE[i]) return;
      const key = `${i + 1}|${REFERENCE[i]}|${c}`;
      disagreements.set(key, (disagreements.get(key) ?? 0) + 1);
    });
  }
  const common = [...disagreements.entries()]
    .filter(([, n]) => n >= Math.max(2, Math.ceil(results.length / 2)))
    .sort((a, b) => b[1] - a[1]).slice(0, 8);
  if (common.length) {
    console.log('\nWhere most models disagree with the reference (worth a human eye):');
    for (const [key, n] of common) {
      const [idx, want, got] = key.split('|');
      console.log(`   #${idx} ${n} models said ${got}, reference says ${want}`
        + `  — ${emails[Number(idx) - 1].from}: ${emails[Number(idx) - 1].subject.slice(0, 42)}`);
    }
  }
}

console.log('\ncover   = emails given a valid category (anything below full is a broken digest)');
console.log('needles = of the emails that MATTER, how many the model surfaced (the metric that counts)');
console.log('purity  = of what it surfaced, how much actually mattered (flag-everything scores badly here)');
console.log('agree   = raw agreement with the reference; near-useless at 32:2 class imbalance');
console.log('stable  = same verdicts when the first batch is re-run at temperature 0');
console.log('consens = agreement with the majority vote of the models tested');
console.log('sanity  = rule violations; 0 is the only acceptable score');

for (const r of results.filter((x) => x.violations.length)) {
  console.log(`\n${r.model} violations:`);
  for (const v of [...new Set(r.violations)].slice(0, 6)) console.log(`   ${v}`);
}
