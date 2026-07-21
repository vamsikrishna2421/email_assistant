// Body of the "Assemble the digest" Code node.
//
// Kept as a real .js file rather than a string inside build-digest.mjs: this is
// ~150 lines of logic with regexes and HTML, and escaping all of it into a
// template literal is how subtle bugs get in.
//
// The division of labour matters. The model returns ONE category per email and
// nothing else. Everything it is bad at happens here:
//   - which sender goes with which row (joined by index, so drift is impossible)
//   - counting (it reported 6/5/3 for a list of 5/3/6)
//   - topic grouping (deterministic patterns beat a 7B's judgement)
//   - never letting a receipt or a personal-priority item be filed as marketing

// One model output per batch, paired with that batch's rows. Line numbers are
// batch-local, so each is shifted by the batch's offset to get a global index.
const outputs = $input.all();
const batches = $('Format for the model').all();

const CATEGORIES = ['ACTION', 'FYI', 'NOISE'];
const byIndex = new Map();
const source = [];

for (const [b, batch] of batches.entries()) {
  const offset = batch.json.offset ?? 0;
  const rows = batch.json.rows ?? [];
  rows.forEach((r, i) => { source[offset + i] = r; });

  const raw = String(outputs[b]?.json?.text ?? '');
  for (const line of raw.split('\n')) {
    // "<n>. <CATEGORY>" — tolerate stray bullets, bold markers, trailing text.
    const m = line.match(/^\s*[-*]?\s*(\d+)\s*[.)]\s*\**\s*([A-Za-z]+)/);
    if (!m) continue;
    const category = m[2].toUpperCase();
    if (!CATEGORIES.includes(category)) continue;
    const local = Number(m[1]);
    if (local < 1 || local > rows.length) continue; // out-of-range = hallucinated
    byIndex.set(offset + local, category);
  }
}

// Anything the user paid for, or that concerns their own money, must never be
// filed as marketing. Plurals matter: \bticket\b misses "Regal Tickets for ...".
const MONEY = /\b(tickets?|receipts?|orders?|invoices?|payments?|statements?|refunds?|confirmations?|bookings?|reservations?|deliver(?:y|ies|ed)|shipp(?:ed|ing)|fico|credit score|balances?|transactions?)\b/i;
const PRIORITY = new RegExp(PRIORITY_PATTERN, 'i');
// Stated preference, enforced in code. Applied LAST so it overrides both the
// model and the money/receipt guards: a recruiter writing "urgent contract, W2"
// trips several importance heuristics, and the user still does not want it.
const DEMOTE = DEMOTE_PATTERN ? new RegExp(DEMOTE_PATTERN, 'i') : null;

// Topic grouping is deterministic. Asking the model for a second field alongside
// the category made it drop the output format entirely on a real inbox, and
// sender/subject patterns classify this mail more reliably anyway.
const TOPICS = [
  { key: 'Tickets & bookings', test: (t) => /regal|ticket|booking|showtime|cinema/i.test(t) },
  { key: 'Money & accounts', test: (t) => /bank|payment|invoice|statement|fico|credit|zelle|robinhood|sofi|experian|transfer|refund|balance|card/i.test(t) },
  // Job titles, not just words like "hiring". Most of this inbox's recruiter
  // mail comes from individuals whose subject is only a role description:
  // "GCP Data Engineer - Full-Time (Charlotte, NC)" contains none of the
  // obvious recruiting vocabulary and was landing under "Other".
  { key: 'Jobs & recruiters', test: (t) => /hiring|recruit|job alert|roles?\b|position|\bw2\b|c2c|contract|opening|vacancy|resume|candidate|full[- ]time|indeed|monster|lensa|dice|\b(?:data|cloud|software|azure|aws|gcp|devops|ml|ai|backend|frontend|full[- ]?stack)\s+(?:engineer|developer|architect|analyst|scientist)\b|\b(?:engineer|developer|architect)\s+(?:with|at|@)\b/i.test(t) },
  { key: 'Property & home', test: (t) => /zillow|rental|for rent|mortgage|apartment|realty|listing/i.test(t) },
  { key: 'Shopping & offers', test: (t) => /sale|% off|deal|discount|offer|coupon|shop|save \$/i.test(t) },
  { key: 'Social & networking', test: (t) => /linkedin|connect|invitation|follower|network/i.test(t) },
];
const topicOf = (text) => (TOPICS.find((t) => t.test(text)) ?? { key: 'Other' }).key;

const rows = source.map((row, i) => {
  const haystack = row.from + ' ' + row.subject;
  let category = byIndex.get(i + 1) ?? 'UNCLASSIFIED';
  if (category === 'NOISE' && MONEY.test(row.subject)) category = 'FYI';
  if (PRIORITY.test(haystack)) category = 'ACTION';
  if (DEMOTE && DEMOTE.test(haystack) && !PRIORITY.test(haystack)) category = 'NOISE';
  return {
    from: row.from,
    subject: row.subject,
    buried: row.buried,
    category,
    topic: topicOf(haystack),
  };
});

const pick = (c) => rows.filter((r) => r.category === c);
const action = pick('ACTION');
const fyi = pick('FYI');
const noise = pick('NOISE');
const missed = pick('UNCLASSIFIED');
const rescued = [...action, ...fyi].filter((r) => r.buried);

// --- rendering -------------------------------------------------------------
// Inline styles only. Gmail strips <style> blocks, so anything in a stylesheet
// silently disappears and the mail arrives unformatted.

const esc = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const STYLE = {
  ACTION: { accent: '#c0392b', bg: '#fdf3f2', label: 'Needs you', text: '#2c2c2c' },
  FYI: { accent: '#2b6cb0', bg: '#f2f7fc', label: 'Worth knowing', text: '#2c2c2c' },
  UNCLASSIFIED: { accent: '#b7791f', bg: '#fdf8ef', label: 'Unsorted', text: '#2c2c2c' },
  NOISE: { accent: '#b0b0b0', bg: '#fafafa', label: 'Not for you', text: '#8a8a8a' },
};

const item = (r, kind) => {
  const s = STYLE[kind];
  const badge = r.buried
    ? `<span style="display:inline-block;margin-left:8px;padding:1px 6px;border-radius:3px;background:#8e44ad;color:#fff;font-size:10px;letter-spacing:.04em;">was in ${esc(r.buried)}</span>`
    : '';
  return `<tr><td style="padding:7px 12px;border-left:3px solid ${s.accent};background:${s.bg};">
    <div style="font:600 13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;color:${s.text};">${esc(r.from)}${badge}</div>
    <div style="font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:${s.text};opacity:.85;">${esc(r.subject)}</div>
  </td></tr>`;
};

/** Group a bucket by topic so related mail reads together. */
const grouped = (list, kind) => {
  if (!list.length) return '';
  const s = STYLE[kind];
  const byTopic = new Map();
  for (const r of list) {
    if (!byTopic.has(r.topic)) byTopic.set(r.topic, []);
    byTopic.get(r.topic).push(r);
  }
  const blocks = [...byTopic.entries()].map(([topic, items]) => `
    <div style="font:600 11px/1 -apple-system,Segoe UI,Roboto,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#7a7a7a;margin:16px 0 6px;">${esc(topic)} <span style="color:#b0b0b0;">· ${items.length}</span></div>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:separate;border-spacing:0 3px;">
      ${items.map((r) => item(r, kind)).join('')}
    </table>`).join('');

  return `<div style="margin:26px 0 0;">
    <div style="font:700 15px/1.3 -apple-system,Segoe UI,Roboto,sans-serif;color:${s.accent};border-bottom:2px solid ${s.accent};padding-bottom:5px;">${s.label} <span style="font-weight:400;color:#9a9a9a;">(${list.length})</span></div>
    ${blocks}
  </div>`;
};

const chip = (n, label, colour) =>
  `<span style="display:inline-block;margin-right:14px;font:13px -apple-system,Segoe UI,Roboto,sans-serif;color:#555;"><b style="color:${colour};font-size:17px;">${n}</b> ${label}</span>`;

const html = `<div style="max-width:640px;margin:0 auto;padding:22px;background:#ffffff;">
  <div style="font:700 19px/1.2 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;">Inbox digest</div>
  <div style="font:13px -apple-system,Segoe UI,Roboto,sans-serif;color:#8a8a8a;margin-top:3px;">${rows.length} emails in the last ${HOURS_WINDOW} hours</div>

  <div style="margin:16px 0 4px;padding:11px 13px;background:#f6f6f6;border-radius:5px;">
    ${chip(action.length, 'need you', STYLE.ACTION.accent)}
    ${chip(fyi.length, 'worth knowing', STYLE.FYI.accent)}
    ${chip(noise.length, 'ignored', '#9a9a9a')}
    ${missed.length ? chip(missed.length, 'unsorted', STYLE.UNCLASSIFIED.accent) : ''}
  </div>

  ${rescued.length ? `<div style="margin-top:16px;padding:11px 13px;border-left:3px solid #8e44ad;background:#f8f4fb;font:13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#3a3a3a;">
    <b>${rescued.length} ${rescued.length === 1 ? 'message was' : 'messages were'} buried by Gmail</b> in Spam or Promotions but looks worth seeing. Marked below.
  </div>` : ''}

  ${grouped(action, 'ACTION')}
  ${grouped(fyi, 'FYI')}
  ${grouped(missed, 'UNCLASSIFIED')}

  ${noise.length ? `<div style="margin:26px 0 0;font:12px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#a0a0a0;border-top:1px solid #eee;padding-top:12px;">
    Ignored ${noise.length} as marketing or newsletters${(() => {
      const t = [...new Set(noise.map((r) => r.topic))];
      return t.length ? ' — mostly ' + t.slice(0, 3).join(', ').toLowerCase() : '';
    })()}.
  </div>` : ''}

  <div style="margin-top:14px;font:11px -apple-system,Segoe UI,Roboto,sans-serif;color:#c0c0c0;">
    Summarised locally by ${MODEL_NAME}. Nothing left this machine.
  </div>
</div>`;

// Plain-text fallback for clients that refuse HTML.
const text = [
  `${rows.length} emails in the last ${HOURS_WINDOW} hours.`,
  '',
  ...(action.length ? ['NEEDS YOU:', ...action.map((r) => `  • ${r.from} — ${r.subject}${r.buried ? ` [was in ${r.buried}]` : ''}`), ''] : []),
  ...(fyi.length ? ['WORTH KNOWING:', ...fyi.map((r) => `  • ${r.from} — ${r.subject}${r.buried ? ` [was in ${r.buried}]` : ''}`), ''] : []),
  `Ignored ${noise.length} as marketing or newsletters.`,
].join('\n');

return [{
  json: {
    html,
    text,
    action: action.length,
    fyi: fyi.length,
    noise: noise.length,
    unsorted: missed.length,
    rescued: rescued.length,
    total: rows.length,
  },
}];
