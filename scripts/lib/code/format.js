// Body of the "Format for the model" Code node.
//
// Emits ONE ITEM PER BATCH, not one item for the whole inbox. n8n runs the LLM
// chain once per input item, so this turns a single huge classification into
// several small reliable ones.
//
// This is not premature caution. Given 34 emails in one 12.5k-character prompt,
// the model stopped emitting "<n>. CATEGORY" lines altogether and returned prose
// under markdown headings — 33 of 34 emails came back unclassified. The same
// model handles a dozen emails without trouble. Volume was the variable.
//
// Each batch carries its own `offset` so the assembler can map a batch-local
// line number back to the right email.

const BATCH_SIZE = 12;

const items = $input.all().filter((i) => i.json && i.json.id);

if (items.length === 0) {
  return [{ json: { empty: true, count: 0, offset: 0, emails: '', rows: [] } }];
}

// Where Gmail filed each message. Passed to the model as a HINT, never a
// verdict — the reason this digest reads Spam and Promotions at all is that
// Gmail's filing is wrong often enough to matter.
const labelsOf = (m) => (m.labels ?? m.labelIds ?? [])
  .map((l) => (typeof l === 'string' ? l : (l.name ?? l.id ?? '')));

const filedAs = (labels) => {
  if (labels.includes('SPAM')) return 'Gmail put this in SPAM';
  if (labels.includes('TRASH')) return 'Gmail put this in TRASH';
  if (labels.includes('CATEGORY_PROMOTIONS')) return 'Gmail tab: Promotions';
  if (labels.includes('CATEGORY_SOCIAL')) return 'Gmail tab: Social';
  if (labels.includes('CATEGORY_UPDATES')) return 'Gmail tab: Updates';
  if (labels.includes('CATEGORY_FORUMS')) return 'Gmail tab: Forums';
  return 'Gmail tab: Primary';
};

const buriedIn = (labels) =>
  labels.includes('SPAM') ? 'Spam'
    : labels.includes('TRASH') ? 'Trash'
    : labels.includes('CATEGORY_PROMOTIONS') ? 'Promotions'
    : labels.includes('CATEGORY_SOCIAL') ? 'Social'
    : '';

const prepared = items.map((item) => {
  const m = item.json;
  const labels = labelsOf(m);
  return {
    from: String(m.From ?? m.from ?? 'unknown sender').replace(/\s*<.*/, '').replace(/"/g, '').trim(),
    subject: String(m.Subject ?? m.subject ?? '(no subject)').trim(),
    // Gmail's own preview. Capped so one newsletter cannot dominate the batch.
    preview: String(m.snippet ?? '').replace(/\s+/g, ' ').slice(0, 260),
    filedAs: filedAs(labels),
    buried: buriedIn(labels),
  };
});

const batches = [];
for (let start = 0; start < prepared.length; start += BATCH_SIZE) {
  const slice = prepared.slice(start, start + BATCH_SIZE);
  batches.push({
    json: {
      empty: false,
      count: prepared.length,
      offset: start,
      batch: batches.length + 1,
      batches: Math.ceil(prepared.length / BATCH_SIZE),
      rows: slice.map(({ from, subject, buried }) => ({ from, subject, buried })),
      emails: slice.map((e, n) =>
        `[${n + 1}] From: ${e.from}\n    Subject: ${e.subject}\n    Filed by Gmail as: ${e.filedAs}\n    Preview: ${e.preview}`,
      ).join('\n\n'),
    },
  });
}

return batches;
