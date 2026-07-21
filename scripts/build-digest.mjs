#!/usr/bin/env node
//
// Generates workflows/digest.workflow.json — the local email digest.
//
//   node scripts/build-digest.mjs
//   JERRY_DIGEST_PROVIDER=gemini node scripts/build-digest.mjs
//
// Deliberately NOT part of the Jerry chat workflow. It is one scheduled pass:
//
//   every 6h -> read the last 6h of mail -> format -> summarise -> email you
//
// It runs on a local Ollama model by default, and that is the point. Google's
// Gemini API terms for the free tier say Google trains on submitted content,
// that human reviewers may read it, and "Do not submit sensitive, confidential,
// or personal information to the Unpaid Services." This workflow handles other
// people's email. On Ollama the bodies never leave the machine.
//
// There is no agent and no tool calling here — one model call, summarising text
// it was handed. That matters: on this hardware local models summarise well and
// call tools badly (see scripts/bench-models.mjs), so this shape is exactly the
// part that is safe to run locally.

import { writeFileSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { CRED, id } from './lib/nodes.mjs';
import { selectProvider } from './lib/providers.mjs';
import { TIMEZONE } from './lib/prompts.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

// What matters to this person, kept in a file they can edit rather than in
// prompt text buried in a build script. See config/preferences.json.
let prefs = { about_me: [], always_important: 'regal tickets for', never_important: '' };
try {
  prefs = { ...prefs, ...JSON.parse(readFileSync(join(ROOT, 'config', 'preferences.json'), 'utf8')) };
} catch { /* defaults are fine */ }

const HOURS = Number(process.env.JERRY_DIGEST_HOURS || 6);
const DELIVER_TO = process.env.JERRY_DIGEST_TO || 'vamsy.24@gmail.com';

// Appears in every digest subject, and is the string the Gmail query excludes so
// the workflow cannot read its own past output.
const SUBJECT_TAG = 'Inbox digest';

// What matters to YOU, which a general-purpose model cannot infer. A cinema
// ticket confirmation looks like routine admin until you know the tickets are a
// side business — then it is the most important mail of the day. Anything
// matching these is promoted to NEEDS YOU regardless of how the model filed it.
//   JERRY_DIGEST_PRIORITY='regal|ticket|invoice' node scripts/build-digest.mjs
// Anchored to the booking-confirmation subject Regal actually sends
// ("Regal Tickets for Idhayam Murali (Tamil) #WG7SDM9"). A bare /regal/ also
// matched "Regal Crown Club News", promoting a $5-off promo to NEEDS YOU.
const PRIORITY = process.env.JERRY_DIGEST_PRIORITY || prefs.always_important;
const DEMOTE = process.env.JERRY_DIGEST_DEMOTE ?? prefs.never_important;

// Local by default. `JERRY_DIGEST_PROVIDER=gemini` if you would rather have
// speed than privacy — but read the note at the top of this file first.
// qwen2.5:7b, not the faster qwen3.5:4b. On a 30-email benchmark qwen3.5:4b won
// on every measure — 87s vs 126s, 30/30 lines parsed. Given the real inbox (34
// emails, a 12.5k-char prompt) it abandoned the output format entirely and
// returned prose under markdown headings, so 33 of 34 emails went unclassified.
// qwen2.5:7b has handled the real thing repeatedly. Benchmarks are a proxy;
// this one is chosen on production behaviour.
const provider = selectProvider({
  JERRY_PROVIDER: process.env.JERRY_DIGEST_PROVIDER || 'ollama',
  JERRY_MODEL: process.env.JERRY_DIGEST_MODEL || 'qwen2.5:7b',
});

// Classification should not change between runs on the same inbox. At the
// default temperature the same 7B model put four cinema tickets in FYI on one
// run and NOISE on the next; nothing about the mail had changed.
provider.options = { ...provider.options, temperature: 0 };

const node = (name, type, typeVersion, position, parameters, extra = {}) => ({
  parameters, type, typeVersion, position, id: id(`digest:${name}`), name, ...extra,
});

// --- the prompt ------------------------------------------------------------
// Benchmarked against a synthetic inbox in scripts/bench-models.mjs: every
// local model down to 1.5B picked out the two emails that mattered from this
// wording and rejected the marketing.

// Classify every email individually against fixed labels, rather than asking
// for judgement plus ranking plus prose in one go. A 7B model given the latter
// produced a self-contradictory digest on a real inbox — it named one email,
// then said none mattered, then emitted a stray "0", and silently dropped four
// ticket confirmations and a credit-score alert. One line in, one line out, with
// the categories spelled out, is a job it can actually do.
const ABOUT = prefs.about_me?.length
  ? `Context about the person whose inbox this is. This overrides your general\ninstincts about what looks important:\n\n${prefs.about_me.map((l) => `- ${l}`).join('\n')}\n\n`
  : '';

const DIGEST_PROMPT = `${ABOUT}Classify every email below. These arrived in the last ${HOURS} hours.

For EACH email output exactly one line, in the same order, in this format:

<number>. <CATEGORY>

Nothing else on the line. Not the sender, not the subject, not an explanation —
I already have those. Only the number and the category.

CATEGORY must be exactly one of:
  ACTION  - the user has to do something: a reply is expected, a payment or account needs attention, a deadline or appointment applies to them.
  FYI     - worth knowing but nothing to do: a confirmation, receipt, ticket, statement, delivery notice, alert about their own account.
  NOISE   - marketing, promotions, newsletters, social or recommendation notifications.

Rules:
- Every email gets exactly one line. Do not skip any. Do not merge them.
- Do not count anything and do not write a summary line.
- A receipt, ticket or confirmation is FYI, never NOISE — the user paid for it.
- An alert about the user's own money, credit or account is FYI at minimum.
- "Filed by Gmail as" is a hint about where it landed, NOT the answer. Gmail routinely files real invoices, account alerts and personal mail under Promotions or Spam. Judge the content. A payment failure in Spam is still a payment failure.
- Equally, something in Primary can still be NOISE.
- Invent nothing. Use only what is in the subject and preview.
- Output only those lines. No preamble, no headings, no commentary.

EMAILS:
{emails}`;

// The Code-node bodies live in scripts/lib/code/*.js as real JavaScript, so they
// can be linted and syntax-checked instead of being escaped into a template
// literal. Build-time constants are substituted in here.
const codeFor = (file, subs) => {
  let src = readFileSync(join(ROOT, 'scripts', 'lib', 'code', file), 'utf8');
  for (const [k, v] of Object.entries(subs)) src = src.replaceAll(k, v);
  return src;
};

// --- nodes -----------------------------------------------------------------

const trigger = node('Every 6 hours', 'n8n-nodes-base.scheduleTrigger', 1.3, [0, 0], {
  rule: { interval: [{ field: 'hours', hoursInterval: HOURS }] },
});

// `readStatus: both` because an email you already opened on your phone can
// still be the one that matters. receivedAfter is what bounds the batch.
const fetch = node('Get recent mail', 'n8n-nodes-base.gmail', 2.2, [220, 0], {
  resource: 'message',
  operation: 'getAll',
  returnAll: false,
  limit: 50,
  simple: true,
  filters: {
    readStatus: 'both',
    receivedAfter: `={{ $now.minus({ hours: ${HOURS} }).toISO() }}`,
    // The whole point of a digest is to catch what you would otherwise miss,
    // and what you miss is precisely what Gmail filed under Spam or Promotions.
    // A real invoice lands in Spam often enough that excluding it defeats the
    // exercise. We read everything and judge it ourselves.
    includeSpamTrash: true,
    // Without this the digest reads its own output: run two summarises run
    // one's email, run three summarises run two's, and the real mail is
    // gradually crowded out by reflections of itself.
    q: `-subject:"${SUBJECT_TAG}"`,
  },
  options: {},
}, { credentials: CRED.gmail, alwaysOutputData: true });

// A Code node rather than an expression: this has to survive an empty inbox,
// and silently emailing a digest of nothing every six hours is worse than not
// running at all.
const format = node('Format for the model', 'n8n-nodes-base.code', 2, [440, 0], {
  mode: 'runOnceForAllItems',
  jsCode: codeFor('format.js', {}),
});

// Nothing arrived -> stop. No model call, no email.
const gate = node('Anything to report?', 'n8n-nodes-base.if', 2.2, [660, 0], {
  conditions: {
    options: { caseSensitive: true, leftValue: '', typeValidation: 'strict', version: 2 },
    conditions: [{
      id: 'has-mail',
      leftValue: '={{ $json.empty }}',
      rightValue: false,
      operator: { type: 'boolean', operation: 'false', singleValue: true },
    }],
    combinator: 'and',
  },
  options: {},
});

const model = node('Summariser', provider.type, provider.typeVersion, [880, 220], {
  [provider.modelParam]: provider.model,
  ...(provider.extraParams ?? {}),
  options: provider.options,
}, {
  credentials: { [provider.credential]: { id: provider.credId, name: provider.credName } },
  retryOnFail: true,
  maxTries: 2,
  waitBetweenTries: 5000,
});

const summarise = node('Write the digest', '@n8n/n8n-nodes-langchain.chainLlm', 1.9, [880, 0], {
  promptType: 'define',
  text: `=${DIGEST_PROMPT.replace('{emails}', '{{ $json.emails }}')}`,
  batching: {},
});

// The model is asked only to judge. Everything it is bad at — remembering which
// sender goes with which row, and counting — is done here instead.
//
// On a real inbox the 7B model labelled row 8 "Regal" when row 8 was actually
// AAA Northeast, and reported "6 action, 5 fyi, 3 noise" for a list that held
// 5/3/6. Joining on the row number rather than trusting the echoed sender makes
// the first mistake impossible, and counting the parsed rows makes the second.
const assemble = node('Assemble the digest', 'n8n-nodes-base.code', 2, [1100, 0], {
  mode: 'runOnceForAllItems',
  jsCode: codeFor('assemble.js', {
    PRIORITY_PATTERN: JSON.stringify(PRIORITY),
    DEMOTE_PATTERN: JSON.stringify(DEMOTE),
    HOURS_WINDOW: String(HOURS),
    MODEL_NAME: JSON.stringify(provider.model),
  }),
});

const send = node('Email me the digest', 'n8n-nodes-base.gmail', 2.2, [1320, 0], {
  resource: 'message',
  operation: 'send',
  sendTo: DELIVER_TO,
  subject: `=${SUBJECT_TAG} — {{ $json.action }} need you, {{ $json.fyi }} to know ({{ $now.setZone('${TIMEZONE}').toFormat('d LLL HH:mm') }})`,
  emailType: 'html',
  message: '={{ $json.html }}',
  options: { appendAttribution: false },
}, { credentials: CRED.gmail });

const nodes = [trigger, fetch, format, gate, model, summarise, assemble, send];

// --- connections -----------------------------------------------------------

const connections = {};
const main = (from, to, index = 0) => {
  const entry = (connections[from] ??= { main: [] });
  while (entry.main.length <= index) entry.main.push([]);
  entry.main[index].push({ node: to, type: 'main', index: 0 });
};

main(trigger.name, fetch.name);
main(fetch.name, format.name);
main(format.name, gate.name);
main(gate.name, summarise.name, 0); // true branch only; false branch ends here
main(summarise.name, assemble.name);
main(assemble.name, send.name);
connections[model.name] = {
  ai_languageModel: [[{ node: summarise.name, type: 'ai_languageModel', index: 0 }]],
};

const workflow = {
  id: 'JerryEmailDigest',
  name: 'Jerry - Email Digest',
  nodes,
  connections,
  settings: { executionOrder: 'v1', timezone: TIMEZONE },
  pinData: {},
  meta: { instanceId: 'jerry-local' },
  tags: [],
  versionId: id('digest:version:1'),
};

mkdirSync(join(ROOT, 'workflows'), { recursive: true });
const out = join(ROOT, 'workflows', 'digest.workflow.json');
writeFileSync(out, JSON.stringify(workflow, null, 2) + '\n');

console.log(`wrote ${out}`);
console.log(`  every ${HOURS}h -> last ${HOURS}h of mail -> ${provider.label} (${provider.model}) -> ${DELIVER_TO}`);
console.log('  the schedule is inactive until you switch it on in n8n');
