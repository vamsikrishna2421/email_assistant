#!/usr/bin/env node
//
// Generates workflows/jerry.workflow.json — the "Jerry" multi-agent assistant.
//
//   node scripts/build-workflow.mjs
//
// The workflow is generated rather than hand-written so the four agent groups
// (master + email + calendar + meeting) share one definition of how an agent is
// wired: its own Gemini chat model, its own memory buffer, and its own tools.

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  chatTrigger, masterAgent, subAgent, chatModel, memory,
  gmailTools, calendarTools, meetingTools, id,
} from './lib/nodes.mjs';
import {
  MASTER_PROMPT, EMAIL_PROMPT, CALENDAR_PROMPT, MEETING_PROMPT, TIMEZONE,
} from './lib/prompts.mjs';
import { selectProvider } from './lib/providers.mjs';

// Which LLM every agent runs on. Override per build:
//   JERRY_PROVIDER=ollama JERRY_MODEL=qwen2.5:7b node scripts/build-workflow.mjs
const provider = selectProvider();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const SUB_Y = 480;   // row holding the three sub-agents
const TOOL_Y = 760;  // row holding their models, memories and tools

// --- nodes -----------------------------------------------------------------

const master = {
  agent: masterAgent([300, 0], MASTER_PROMPT),
  model: chatModel('Chat Model', [180, 240], provider),
  memory: memory('Simple Memory', [340, 240], { fromTrigger: true }),
};

const groups = [
  {
    agent: subAgent('Email Agent', [-540, SUB_Y],
      'Handles everything to do with the user\'s Gmail: searching and reading mail, summarising it, drafting, replying, sending and deleting. Give it one complete instruction including any addresses, search terms or message IDs it needs.',
      EMAIL_PROMPT),
    model: chatModel('Chat Model1', [-1340, TOOL_Y], provider),
    memory: memory('Simple Memory1', [-1160, TOOL_Y], { scope: 'email' }),
    tools: gmailTools(TOOL_Y),
  },
  {
    agent: subAgent('Calendar Agent', [560, SUB_Y],
      'Handles everything to do with the user\'s Google Calendar: listing events, checking availability, creating, updating and deleting events, and scheduling meetings with attendees. Pass absolute ISO 8601 times, never relative ones.',
      CALENDAR_PROMPT),
    model: chatModel('Chat Model2', [-180, TOOL_Y], provider),
    memory: memory('Simple Memory2', [0, TOOL_Y], { scope: 'calendar' }),
    tools: calendarTools(TOOL_Y, TIMEZONE),
  },
  {
    agent: subAgent('Meeting Agent', [1560, SUB_Y],
      'Retrieves past meeting recordings and transcripts from Fireflies.ai: which meetings happened and when, who was there, what was said, and what the action items were. Use it for anything about a meeting that already took place.',
      MEETING_PROMPT),
    model: chatModel('Chat Model3', [1660, TOOL_Y], provider),
    memory: memory('Simple Memory3', [1840, TOOL_Y], { scope: 'meeting' }),
    tools: meetingTools(TOOL_Y),
  },
];

// --- layout -----------------------------------------------------------------
//
// Positions are computed rather than hand-picked: each group's sub-nodes are
// laid out as one contiguous run and the sub-agent is centred above it. Doing
// this by hand meant groups silently overlapped as tools were added.

const COL = 200;  // horizontal gap between sibling sub-nodes
const GAP = 260;  // extra gap between one agent's group and the next

let cursor = 0;
for (const g of groups) {
  const row = [g.model, g.memory, ...g.tools];
  for (const n of row) {
    n.position = [cursor, TOOL_Y];
    cursor += COL;
  }
  const start = row[0].position[0];
  const end = row[row.length - 1].position[0];
  g.agent.position = [Math.round((start + end) / 2), SUB_Y];
  cursor += GAP;
}

// Centre the master agent and its own sub-nodes over the three groups.
const centre = Math.round((groups[0].agent.position[0] + groups.at(-1).agent.position[0]) / 2);
master.agent.position = [centre, 0];
master.model.position = [centre - 120, 240];
master.memory.position = [centre + 120, 240];

const trigger = chatTrigger([centre - 380, 0]);

const nodes = [
  trigger, master.agent, master.model, master.memory,
  ...groups.flatMap((g) => [g.agent, g.model, g.memory, ...g.tools]),
];

// --- connections -----------------------------------------------------------
//
// n8n keys `connections` by SOURCE node. For the AI connection types the
// sub-node is the source and the agent it feeds is the target, which is the
// reverse of how the canvas reads left-to-right.

const connections = {};
const connect = (from, type, to) => {
  (connections[from] ??= {})[type] = [[{ node: to, type, index: 0 }]];
};

connect(trigger.name, 'main', master.agent.name);
connect(master.model.name, 'ai_languageModel', master.agent.name);
connect(master.memory.name, 'ai_memory', master.agent.name);

for (const g of groups) {
  connect(g.agent.name, 'ai_tool', master.agent.name);
  connect(g.model.name, 'ai_languageModel', g.agent.name);
  connect(g.memory.name, 'ai_memory', g.agent.name);
  for (const tool of g.tools) connect(tool.name, 'ai_tool', g.agent.name);
}

// --- workflow --------------------------------------------------------------

const workflow = {
  // Fixed id so re-running this script updates the same workflow in n8n
  // instead of piling up duplicates every time.
  id: 'JerryMultiAgent1',
  name: 'Jerry - Multi Agent Assistant System',
  nodes,
  connections,
  // Pin the timezone rather than inheriting the n8n instance's, so `$now` in
  // the prompts and the times written to Google Calendar cannot disagree.
  settings: { executionOrder: 'v1', timezone: TIMEZONE },
  pinData: {},
  meta: { instanceId: 'jerry-local' },
  tags: [],
  versionId: id('version:1'),
};

mkdirSync(join(ROOT, 'workflows'), { recursive: true });
const out = join(ROOT, 'workflows', 'jerry.workflow.json');
writeFileSync(out, JSON.stringify(workflow, null, 2) + '\n');

const toolCount = groups.reduce((n, g) => n + g.tools.length, 0);
console.log(`wrote ${out}`);
console.log(`  ${nodes.length} nodes — 1 trigger, 1 master agent, ${groups.length} sub-agents, ${toolCount} tools`);
console.log(`  ${Object.keys(connections).length} connection sources`);
console.log(`  model: ${provider.label} — ${provider.model}`);
