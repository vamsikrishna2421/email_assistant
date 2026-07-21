#!/usr/bin/env node
//
// Benchmarks local Ollama models on the two jobs Jerry actually asks of them.
//
//   node scripts/bench-models.mjs                    # all installed models
//   node scripts/bench-models.mjs qwen2.5:7b         # just these
//
// The two jobs have very different requirements, and a model can be fine at one
// and useless at the other:
//
//   digest  — read a batch of emails, decide what matters, summarise. One call,
//             no tools, runs on a schedule, so a slow answer is still a useful
//             answer. This is the job the user actually wants done locally.
//
//   agent   — pick the right tool and fill in its arguments correctly. Runs
//             4-6 times per chat message, and a wrong argument means a wrong
//             Gmail query, silently.
//
// Timings are wall clock on this machine. Ollama has no GPU path on Intel Macs,
// so these are CPU numbers and will look nothing like an M-series laptop.

const OLLAMA = process.env.OLLAMA_BASE_URL || 'http://localhost:11434';
const NS = 1e9;

// A realistic six hours of mail: two that matter, four that do not.
const INBOX = [
  { from: 'priya.raman@northwind.com', subject: 'Re: Q3 budget review — need your numbers by Tue',
    snippet: 'Following up on the finance sync. I still need the infrastructure line items from you before Tuesday, otherwise we go to the board with last quarter figures. Can you send whatever you have?' },
  { from: 'noreply@linkedin.com', subject: 'You appeared in 9 searches this week',
    snippet: 'See who is looking at your profile. Upgrade to Premium to see all 9 viewers and get insights about your industry.' },
  { from: 'billing@awsvendor.com', subject: 'Payment failed — action required',
    snippet: 'We were unable to process the payment method on file for invoice INV-88213 ($842.00). Services may be suspended after 5 days. Please update your card.' },
  { from: 'newsletter@techdigest.io', subject: 'The 12 AI tools everyone is talking about',
    snippet: 'This week: agent frameworks, a new vector database, and why everyone is rewriting their RAG pipeline again.' },
  { from: 'calendar-notification@google.com', subject: 'Invitation: Design sync @ Thu 24 Jul 14:00',
    snippet: 'Alex Chen has invited you to Design sync. Thursday 24 July 2026, 14:00 - 14:30. Google Meet link included.' },
  { from: 'deals@shopmart.com', subject: 'FLASH SALE ends tonight — 40% off everything',
    snippet: 'Last chance! Our biggest sale of the season ends at midnight. Free shipping on orders over $50.' },
];

// Padding to realistic volume. A six-email test is not a test: qwen3.5:4b passed
// it cleanly and then, given 34 real emails, abandoned the output format and
// wrote prose with markdown headings instead of one line per email. Whatever the
// benchmark does not measure, the inbox will.
const FILLER = Array.from({ length: 24 }, (_, i) => ({
  from: `sender${i}@example.com`,
  subject: ['Your job alert for: Data Engineer', 'Up to 40% OFF this weekend',
            'You appeared in searches', 'New listings near you',
            'Your statement is ready', 'Invitation to connect'][i % 6] + ` (${i})`,
  // 300 chars, matching what the digest actually sends. With short filler the
  // prompt came to ~6k chars and both models passed; the real inbox sends
  // ~12.5k and qwen3.5:4b fell apart. Length is the variable that mattered.
  snippet: ('Routine automated message with no action required. ' +
    'Includes the usual footer, unsubscribe language, address block and legal ' +
    'boilerplate that real senders attach to every message they send out.').slice(0, 300),
}));
const FULL_INBOX = [...INBOX, ...FILLER];

const DIGEST_PROMPT = `You are an email assistant. Below are the emails that arrived in the last 6 hours.

Do three things:
1. Decide which of these actually matter to the user and which are noise. Things that matter: someone is waiting on the user, money or an account is at risk, a commitment has a deadline. Noise: newsletters, marketing, automated social notifications.
2. Rank the ones that matter, most urgent first.
3. Write a short digest. For each important email give one line: who it is from, what they need, and by when. Then a single line listing how many were ignored as noise.

Be concise. Do not invent anything that is not in the emails.

EMAILS:
${FULL_INBOX.map((e, i) => `[${i + 1}] From: ${e.from}\n    Subject: ${e.subject}\n    Preview: ${e.snippet}`).join('\n\n')}`;

// The digest's real prompt asks for one "<n>. CATEGORY" line per email. Format
// adherence at volume is the thing that actually breaks, so measure it.
const CLASSIFY_PROMPT = `Classify every email below.

For EACH email output exactly one line: <number>. <CATEGORY>
CATEGORY is one of ACTION, FYI, NOISE. Nothing else on the line.
Every email gets a line. Do not skip any. No preamble, no headings.

EMAILS:
${FULL_INBOX.map((e, i) => `[${i + 1}] From: ${e.from}\n    Subject: ${e.subject}\n    Preview: ${e.snippet}`).join('\n\n')}`;

const TOOLS = [
  ['get_many_messages', 'Search the mailbox', { search_query: 'string', limit: 'number' }],
  ['get_a_message', 'Fetch one message with its full body', { message_id: 'string' }],
  ['send_a_message', 'Send an email immediately', { to: 'string', subject: 'string', body: 'string' }],
].map(([name, description, props]) => ({
  type: 'function',
  function: {
    name, description,
    parameters: {
      type: 'object',
      properties: Object.fromEntries(Object.entries(props).map(([k, t]) => [k, { type: t, description: k }])),
      required: Object.keys(props),
    },
  },
}));

// Reasoning models (qwen3.5 and friends) emit a long chain-of-thought before
// the answer. At CPU speeds that is minutes of tokens you then throw away, and
// it silently blew past undici's 300s header timeout as a bare "fetch failed".
// `think: false` turns it off; the long timeouts cover the rest.
const { Agent } = await import('node:https').then(() => import('undici')).catch(() => ({ Agent: null }));
const dispatcher = Agent ? new Agent({ headersTimeout: 1_800_000, bodyTimeout: 1_800_000 }) : undefined;

async function call(model, messages, tools) {
  const t0 = Date.now();
  const res = await fetch(`${OLLAMA}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...(dispatcher ? { dispatcher } : {}),
    body: JSON.stringify({ model, stream: false, think: false, messages, ...(tools ? { tools } : {}) }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const j = await res.json();
  return {
    wall: (Date.now() - t0) / 1000,
    prefill: j.prompt_eval_count / (j.prompt_eval_duration / NS),
    gen: j.eval_count / (j.eval_duration / NS),
    inTokens: j.prompt_eval_count,
    outTokens: j.eval_count,
    text: j.message?.content ?? '',
    toolCalls: j.message?.tool_calls ?? [],
  };
}

/** Did the digest find the two emails that matter and reject the noise? */
function scoreDigest(text) {
  const t = text.toLowerCase();
  const found = {
    'budget deadline': t.includes('priya') || t.includes('budget'),
    'failed payment': t.includes('payment') || t.includes('billing') || t.includes('88213'),
  };
  const leaked = {
    'linkedin noise': t.includes('linkedin') && !/ignor|noise|not important|filtered|discard/.test(t),
    'flash sale noise': t.includes('flash sale') && !/ignor|noise|not important|filtered|discard/.test(t),
  };
  return {
    hits: Object.entries(found).filter(([, v]) => v).map(([k]) => k),
    misses: Object.entries(found).filter(([, v]) => !v).map(([k]) => k),
    leaks: Object.entries(leaked).filter(([, v]) => v).map(([k]) => k),
  };
}

/** Did it choose the right tool and produce a usable Gmail query? */
function scoreAgent({ toolCalls }) {
  const c = toolCalls[0];
  if (!c) return { ok: false, why: 'no tool call at all' };
  if (c.function?.name !== 'get_many_messages') return { ok: false, why: `wrong tool: ${c.function?.name}` };
  const args = c.function.arguments ?? {};
  const q = String(args.search_query ?? '');
  const problems = [];
  if (!/newer_than:|after:|is:unread/.test(q)) problems.push(`query has no time or unread filter: "${q}"`);
  if (/\d{4}\/\d{2}\/\d{2}-\d{4}\/\d{2}\/\d{2}/.test(q)) problems.push(`invalid Gmail date range: "${q}"`);
  if (args.limit !== undefined && typeof args.limit !== 'number') problems.push(`limit is ${typeof args.limit}, not a number`);
  return { ok: problems.length === 0, why: problems.join('; ') || 'correct', query: q };
}

const installed = await (async () => {
  const r = await fetch(`${OLLAMA}/api/tags`);
  if (!r.ok) throw new Error('Ollama is not running — start it with: ollama serve');
  return (await r.json()).models.map((m) => m.name);
})();

const models = process.argv.slice(2).length ? process.argv.slice(2) : installed;
if (!models.length) {
  console.error('No models installed. Try: ollama pull qwen2.5:7b');
  process.exit(1);
}

console.log(`Ollama at ${OLLAMA} — benchmarking ${models.length} model(s)\n`);

for (const model of models) {
  console.log(`━━ ${model}`);
  try {
    const d = await call(model, [{ role: 'user', content: DIGEST_PROMPT }]);
    const s = scoreDigest(d.text);
    console.log(`   DIGEST   ${d.wall.toFixed(0)}s  (${d.inTokens} in @ ${d.prefill.toFixed(0)} tok/s, ${d.outTokens} out @ ${d.gen.toFixed(1)} tok/s)`);
    console.log(`            found: ${s.hits.join(', ') || 'nothing'}`);
    if (s.misses.length) console.log(`            MISSED: ${s.misses.join(', ')}`);
    if (s.leaks.length) console.log(`            treated noise as important: ${s.leaks.join(', ')}`);

    const f = await call(model, [{ role: 'user', content: CLASSIFY_PROMPT }]);
    const lines = f.text.split('\n').filter((l) => /^\s*\d+\s*[.)]\s*\**\s*(ACTION|FYI|NOISE)/i.test(l));
    const ok = lines.length === FULL_INBOX.length;
    console.log(`   FORMAT   ${f.wall.toFixed(0)}s  ${lines.length}/${FULL_INBOX.length} lines parsed ${ok ? 'OK' : '<- BREAKS THE DIGEST'}`);

    const a = await call(model, [{ role: 'user', content: 'How many unread emails do I have from the last 3 days?' }], TOOLS);
    const as = scoreAgent(a);
    console.log(`   AGENT    ${a.wall.toFixed(0)}s  tool use ${as.ok ? 'OK' : 'BROKEN'} — ${as.why}`);
  } catch (err) {
    console.log(`   failed: ${err.message}`);
  }
  console.log();
}

console.log('DIGEST is the scheduled email summary — slow is fine, it runs unattended.');
console.log('AGENT is interactive chat — needs 4-6 calls per message, so multiply that time.');
