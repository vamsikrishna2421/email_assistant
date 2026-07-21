// Node factories for the Jerry workflow.
//
// Every node `type`, `typeVersion`, parameter name and enum value here was read
// out of the n8n packages installed under runtime/node_modules, not recalled.
// `node scripts/validate-workflow.mjs` re-checks them against that same install.

import { createHash } from 'node:crypto';

export const CRED = {
  gmail: { gmailOAuth2: { id: 'jerry-gmail', name: 'Jerry — Gmail' } },
  gcal: { googleCalendarOAuth2Api: { id: 'jerry-gcal', name: 'Jerry — Google Calendar' } },
  fireflies: { httpHeaderAuth: { id: 'jerry-fireflies', name: 'Jerry — Fireflies' } },
};

// Stable ids: rebuilding the workflow must not churn every node's identity,
// otherwise re-importing orphans the previous version's execution history.
export const id = (name) =>
  createHash('sha1').update(`jerry:${name}`).digest('hex').slice(0, 32)
    .replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, '$1-$2-$3-$4-$5');

const node = (name, type, typeVersion, position, parameters, extra = {}) => ({
  parameters,
  type,
  typeVersion,
  position,
  id: id(name),
  name,
  ...extra,
});

/** `$fromAI(key, description, type, default)` — the agent fills this at call time. */
export const ai = (key, description, type = 'string', dflt) =>
  `={{ ${aiRaw(key, description, type, dflt)} }}`;

/** The same call without the `={{ }}` wrapper, for embedding in a larger expression. */
function aiRaw(key, description, type = 'string', dflt) {
  const args = [`'${key}'`, JSON.stringify(description), `'${type}'`];
  if (dflt !== undefined) args.push(typeof dflt === 'string' ? JSON.stringify(dflt) : String(dflt));
  return `$fromAI(${args.join(', ')})`;
}

// ---------------------------------------------------------------------------
// The chat model. Every agent gets its own instance of whichever provider the
// build selected — see scripts/lib/providers.mjs. They all emit the same
// `ai_languageModel` connection, so swapping provider needs no rewiring.
// ---------------------------------------------------------------------------

export const chatModel = (name, position, provider) =>
  node(name, provider.type, provider.typeVersion, position, {
    [provider.modelParam]: provider.model,
    ...(provider.extraParams ?? {}),
    options: provider.options,
  }, {
    credentials: { [provider.credential]: { id: provider.credId, name: provider.credName } },
    // Free-tier rate limits are per minute, so a 429 is usually survivable by
    // simply waiting. Without this a burst turns into a failed conversation
    // rather than a slightly slow one. Harmless for a local provider.
    retryOnFail: true,
    maxTries: 3,
    waitBetweenTries: 5000,
  });

// Sub-agents run as tools, so `$json` is the tool call — not the chat message.
// Reaching back to the trigger keeps every agent on the same conversation.
// The `scope` suffix matters: without it all three sub-agents resolve to the
// same buffer key and evict each other's history, so the Calendar Agent ends up
// reading the Email Agent's context.
const SESSION_ID = "$('When chat message received').first().json.sessionId";

export const memory = (name, position, { fromTrigger = false, scope } = {}) =>
  node(name, '@n8n/n8n-nodes-langchain.memoryBufferWindow', 1.4, position,
    fromTrigger
      ? { sessionIdType: 'fromInput', contextWindowLength: 20 }
      : {
          sessionIdType: 'customKey',
          sessionKey: `={{ ${SESSION_ID} }}__${scope}`,
          contextWindowLength: 10,
        });

export const chatTrigger = (position) =>
  node('When chat message received', '@n8n/n8n-nodes-langchain.chatTrigger', 1.4, position,
    { options: {} }, { webhookId: id('webhook:chat') });

export const masterAgent = (position, systemMessage) =>
  node('Master Agent', '@n8n/n8n-nodes-langchain.agent', 3.1, position, {
    promptType: 'auto',
    text: '={{ $json.chatInput }}',
    options: { systemMessage, maxIterations: 12 },
  });

export const subAgent = (name, position, toolDescription, systemMessage) =>
  node(name, '@n8n/n8n-nodes-langchain.agentTool', 3, position, {
    toolDescription,
    text: ai('Prompt__User_Message_', 'The complete, self-contained instruction for this agent, including every name, address, ID and absolute date it needs'),
    options: { systemMessage, maxIterations: 6 },
  });

// ---------------------------------------------------------------------------
// Gmail tools
// ---------------------------------------------------------------------------

const gmail = (name, position, parameters) =>
  node(name, 'n8n-nodes-base.gmailTool', 2.2, position, parameters, { credentials: CRED.gmail });

export const gmailTools = (y) => [
  gmail('Get many messages in Gmail', [-980, y], {
    resource: 'message',
    operation: 'getAll',
    returnAll: false,
    // Hard ceiling, not a suggestion. Left to its own devices the model asks for
    // 50+ messages, and because every tool result stays in the agent's context
    // for the rest of the run, two of those searches is already tens of
    // thousands of tokens. Math.min caps it regardless of what the model picks.
    limit: `={{ Math.min(${aiRaw('limit', 'How many messages to return, up to 25', 'number', 10)}, 25) }}`,
    simple: true,
    filters: {
      // Pinned rather than left unset: the UI default for this option is
      // "unread", so an author opening the node would otherwise flip it.
      readStatus: 'both',
      q: ai('search_query', 'Gmail search query, e.g. "is:unread from:boss@example.com newer_than:7d". Empty string for no filter.', 'string', ''),
    },
    options: {},
  }),
  // `simple` decides the Gmail API format: true asks for `metadata`, which
  // returns headers and a snippet but no body at all. This is the only tool
  // that can read what a message actually says, so it must be false.
  gmail('Get a message in Gmail', [-800, y], {
    resource: 'message',
    operation: 'get',
    messageId: ai('message_id', 'The Gmail message ID, taken from a previous search'),
    simple: false,
    options: {},
  }),
  gmail('Reply to a message in Gmail', [-620, y], {
    resource: 'message',
    operation: 'reply',
    messageId: ai('message_id', 'The Gmail message ID to reply to'),
    emailType: 'text',
    message: ai('reply_body', 'The full plain-text body of the reply'),
    // Default is reply-all. Replying to the sender only is the safer default
    // for an agent; the model opts into the wider audience deliberately.
    options: {
      appendAttribution: false,
      replyToSenderOnly: ai('sender_only', 'true to reply only to the sender, false to reply to everyone on the original thread', 'boolean', true),
    },
  }),
  gmail('Create a draft in Gmail', [-440, y], {
    resource: 'draft',
    operation: 'create',
    subject: ai('subject', 'Subject line of the draft'),
    emailType: 'text',
    message: ai('body', 'The full plain-text body of the draft'),
    options: { sendTo: ai('to', 'Recipient email address, or several separated by commas') },
  }),
  gmail('Send a message in Gmail', [-260, y], {
    resource: 'message',
    operation: 'send',
    sendTo: ai('to', 'Recipient email address, or several separated by commas'),
    subject: ai('subject', 'Subject line'),
    emailType: 'text',
    message: ai('body', 'The full plain-text body of the email'),
    // From typeVersion 2.1 this defaults to TRUE, which appends
    // "This email was sent automatically with n8n" to every message the user
    // sends. Off, unless someone deliberately asks for it.
    options: { appendAttribution: false },
  }),
  gmail('Delete a message in Gmail', [-80, y], {
    resource: 'message',
    operation: 'delete',
    messageId: ai('message_id', 'The Gmail message ID to permanently delete'),
  }),
];

// ---------------------------------------------------------------------------
// Google Calendar tools
// ---------------------------------------------------------------------------

// "primary" is Google's alias for the account's own calendar, and is what keeps
// this workflow portable between accounts. It must go through `list` mode:
// the `id` mode validates its value against an email-address regex and rejects
// "primary" outright, which would break every calendar tool before it made a
// single API call. `list` mode has no such validation and passes the value
// straight through.
const PRIMARY = { __rl: true, mode: 'list', value: 'primary', cachedResultName: 'primary' };

// The timezone locator is a different property with its own regex, which does
// accept an IANA zone name in `id` mode.
const tz = (zone) => ({ __rl: true, mode: 'id', value: zone });

const gcal = (name, position, parameters) =>
  node(name, 'n8n-nodes-base.googleCalendarTool', 1.3, position, parameters, { credentials: CRED.gcal });

export const calendarTools = (y, zone) => [
  gcal('Get many events in Google Calendar', [180, y], {
    resource: 'event',
    operation: 'getAll',
    calendar: PRIMARY,
    returnAll: false,
    limit: ai('limit', 'Maximum number of events to return', 'number', 20),
    // The rule lives on the parameter, not just in the system prompt: a lighter
    // model reliably ignores a distant instruction but follows one attached to
    // the argument it is filling in right now.
    timeMin: ai('after', 'Start of the window, full ISO 8601 with offset. For any question about a period containing today ("today", "this week", "this month") this MUST be the current date and time, never the start of a later day. Only use a future date when the question is explicitly about a future period such as "next week".'),
    timeMax: ai('before', 'End of the window as a full ISO 8601 timestamp with offset'),
    options: { orderBy: 'startTime', recurringEventHandling: 'expand', timeZone: tz(zone) },
  }),
  gcal('Get availability in Google Calendar', [360, y], {
    resource: 'calendar',
    operation: 'availability',
    calendar: PRIMARY,
    timeMin: ai('after', 'Start of the window to check as a full ISO 8601 timestamp with offset'),
    timeMax: ai('before', 'End of the window to check as a full ISO 8601 timestamp with offset'),
    // NB: lowercase `timezone` here; the event operations spell it `timeZone`.
    options: { outputFormat: 'availability', timezone: tz(zone) },
  }),
  gcal('Create an event in Google Calendar', [540, y], {
    resource: 'event',
    operation: 'create',
    calendar: PRIMARY,
    start: ai('start', 'Event start as a full ISO 8601 timestamp with offset'),
    end: ai('end', 'Event end as a full ISO 8601 timestamp with offset'),
    useDefaultReminders: true,
    additionalFields: {
      summary: ai('title', 'Title of the event'),
      description: ai('description', 'Longer description, or an empty string', 'string', ''),
      location: ai('location', 'Location, or an empty string', 'string', ''),
    },
  }),
  gcal('Create an event with attendee in Google Calendar', [720, y], {
    resource: 'event',
    operation: 'create',
    calendar: PRIMARY,
    start: ai('start', 'Meeting start as a full ISO 8601 timestamp with offset'),
    end: ai('end', 'Meeting end as a full ISO 8601 timestamp with offset'),
    useDefaultReminders: true,
    additionalFields: {
      summary: ai('title', 'Title of the meeting'),
      description: ai('description', 'Agenda or description, or an empty string', 'string', ''),
      // `attendees` is a multi-value string; the node splits each entry on commas.
      attendees: [ai('attendees', 'Attendee email addresses separated by commas')],
      conferenceDataUi: { conferenceDataValues: { conferenceSolution: 'hangoutsMeet' } },
      sendUpdates: 'all',
    },
  }),
  gcal('Update an event in Google Calendar', [900, y], {
    resource: 'event',
    operation: 'update',
    calendar: PRIMARY,
    eventId: ai('event_id', 'ID of the event to change, taken from a previous listing'),
    useDefaultReminders: true,
    updateFields: {
      summary: ai('title', 'New title, or an empty string to leave it alone', 'string', ''),
      start: ai('start', 'New start as ISO 8601 with offset, or an empty string', 'string', ''),
      end: ai('end', 'New end as ISO 8601 with offset, or an empty string', 'string', ''),
      description: ai('description', 'New description, or an empty string', 'string', ''),
      location: ai('location', 'New location, or an empty string', 'string', ''),
      sendUpdates: 'all',
    },
  }),
  gcal('Delete an event in Google Calendar', [1080, y], {
    resource: 'event',
    operation: 'delete',
    calendar: PRIMARY,
    eventId: ai('event_id', 'ID of the event to delete, taken from a previous listing'),
    options: { sendUpdates: 'all' },
  }),
  // The prompt tells the agent what "now" is, but only this can do arithmetic
  // on it — "three weekdays from Thursday" is a calculation, not a lookup.
  node('Date & Time', 'n8n-nodes-base.dateTimeTool', 2, [1260, y], {
    operation: 'addToDate',
    magnitude: ai('date', 'The starting date as a full ISO 8601 timestamp'),
    timeUnit: ai('unit', "Unit to add: one of 'years', 'quarters', 'months', 'weeks', 'days', 'hours', 'minutes', 'seconds'", 'string', 'days'),
    duration: ai('amount', 'How many units to add. Use a negative number to go backwards in time.', 'number', 1),
    options: {},
  }),
];

// ---------------------------------------------------------------------------
// Fireflies tools (raw GraphQL over the HTTP Request tool)
// ---------------------------------------------------------------------------

// The whole body is ONE expression returning a JSON string, so JSON.stringify
// does the escaping. Keep every GraphQL brace space-separated: a literal `}}`
// anywhere in here would close the expression early.
const graphql = (query, variables) =>
  `={{ JSON.stringify({ query: ${JSON.stringify(query)}, variables: { ${variables} } }) }}`;

const LIST_QUERY =
  'query ListTranscripts($limit: Int, $skip: Int) { transcripts(limit: $limit, skip: $skip) ' +
  '{ id title dateString duration organizer_email participants meeting_link transcript_url ' +
  'summary { gist short_summary keywords action_items } } }';

const GET_QUERY =
  'query GetTranscript($transcriptId: String!) { transcript(id: $transcriptId) ' +
  '{ id title dateString duration organizer_email participants meeting_link transcript_url ' +
  'summary { overview gist short_summary keywords action_items outline bullet_gist topics_discussed } ' +
  'sentences { index speaker_name text start_time end_time } } }';

// Note there is deliberately no `truncateResponse`/`maxLength` here. Those two
// options only apply to the `text` and `html` response types — the JSON
// optimiser ignores them entirely — so setting them would look like a size
// guard while doing nothing. The payload is bounded at the request instead, by
// asking GraphQL for a narrow set of fields.
const fireflies = (name, position, toolDescription, jsonBody) =>
  node(name, 'n8n-nodes-base.httpRequestTool', 4.4, position, {
    toolDescription,
    method: 'POST',
    url: 'https://api.fireflies.ai/graphql',
    authentication: 'genericCredentialType',
    genericAuthType: 'httpHeaderAuth',
    sendBody: true,
    contentType: 'json',
    specifyBody: 'json',
    jsonBody,
    options: {},
    // Strip GraphQL's `{ data: ... }` envelope so the model sees the payload.
    optimizeResponse: true,
    responseType: 'json',
    dataField: 'data',
  }, { credentials: CRED.fireflies });

export const meetingTools = (y) => [
  fireflies('List of Transcripts', [1300, y],
    'List the most recent Fireflies meeting recordings, newest first. Returns each meeting\'s id, title, date, duration, participants and a short summary. Use this to find the id of a meeting before fetching its transcript.',
    graphql(LIST_QUERY, `limit: ${aiRaw('limit', 'How many recent meetings to list, maximum 50', 'number', 10)}, skip: 0`)),
  fireflies('Transcript', [1480, y],
    'Fetch one Fireflies meeting in full by its id: the complete summary, action items, topics, and the spoken transcript with speaker names and timestamps. The id must come from the List of Transcripts tool.',
    graphql(GET_QUERY, `transcriptId: ${aiRaw('transcript_id', 'The Fireflies transcript id, obtained from the List of Transcripts tool')}`)),
];
