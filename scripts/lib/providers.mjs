// Chat-model providers Jerry can run on.
//
// Every agent gets its model from here, so switching provider is one env var:
//
//   JERRY_PROVIDER=ollama ./scripts/setup.sh
//
// All of these emit the same `ai_languageModel` connection, so they are drop-in
// replacements for each other — only the node type, the credential and the name
// of the model parameter differ.
//
// Measured on this machine (2018 Intel i7-8850H, CPU-only — Ollama has no GPU
// path on Intel Macs), one Email Agent call with the real system prompt and six
// tool schemas:
//
//   qwen2.5:7b    90s   prefill 18 tok/s, gen 3.1 tok/s   tool args correct
//   llama3.2:3b   46s   prefill 34 tok/s, gen 5.7 tok/s   tool args malformed
//   gemini flash-lite (cloud)  ~2s                        tool args correct
//
// A Jerry message costs 4-6 of those calls. That is the whole story: local is
// minutes per message, cloud is seconds. Reproduce with scripts/bench-models.mjs.

export const PROVIDERS = {
  gemini: {
    label: 'Google Gemini',
    type: '@n8n/n8n-nodes-langchain.lmChatGoogleGemini',
    typeVersion: 1.1,
    credential: 'googlePalmApi',
    credId: 'jerry-gemini',
    credName: 'Jerry — Google Gemini',
    modelParam: 'modelName',
    defaultModel: 'models/gemini-3.1-flash-lite',
    options: { temperature: 0.3, maxOutputTokens: 4096 },
  },

  ollama: {
    label: 'Ollama (local)',
    type: '@n8n/n8n-nodes-langchain.lmChatOllama',
    typeVersion: 1,
    credential: 'ollamaApi',
    credId: 'jerry-ollama',
    credName: 'Jerry — Ollama',
    modelParam: 'model',
    // 7B is the smallest that got tool arguments right in benchmarking, so it
    // stays the default for the chat agent. The digest overrides this with
    // qwen3.5:4b, which classifies just as accurately and ~40% faster.
    defaultModel: 'qwen2.5:7b',
    options: {
      temperature: 0.3,
      // MEASURED, NOT PRECAUTIONARY. qwen3.5 is a reasoning model: left to its
      // default it emits a full chain-of-thought before answering. Same prompt,
      // same model, same two-character answer:
      //
      //   think default -> 324.0s
      //   think false   ->   1.4s
      //
      // 230x. On CPU that reasoning is minutes of tokens that get discarded.
      // Harmless on non-reasoning models like qwen2.5, which ignore it — so it
      // is set unconditionally rather than per-model.
      think: false,
    },
  },

  openrouter: {
    label: 'OpenRouter',
    type: '@n8n/n8n-nodes-langchain.lmChatOpenRouter',
    typeVersion: 1,
    credential: 'openRouterApi',
    credId: 'jerry-openrouter',
    credName: 'Jerry — OpenRouter',
    modelParam: 'model',
    defaultModel: 'google/gemini-2.0-flash-exp:free',
    options: { temperature: 0.3 },
  },

  groq: {
    label: 'Groq',
    type: '@n8n/n8n-nodes-langchain.lmChatGroq',
    typeVersion: 1,
    credential: 'groqApi',
    credId: 'jerry-groq',
    credName: 'Jerry — Groq',
    modelParam: 'model',
    defaultModel: 'llama-3.3-70b-versatile',
    options: { temperature: 0.3 },
  },

  // Anything speaking the OpenAI API: a LiteLLM proxy, a local gateway, or
  // OpenAI itself. The endpoint lives in the credential's `url` field, so this
  // is the entry to use for a router that fails over between providers.
  openai: {
    label: 'OpenAI-compatible endpoint',
    type: '@n8n/n8n-nodes-langchain.lmChatOpenAi',
    typeVersion: 1.3,
    credential: 'openAiApi',
    credId: 'jerry-openai',
    credName: 'Jerry — OpenAI-compatible',
    modelParam: 'model',
    defaultModel: 'gpt-5-mini',
    // The Responses API is OpenAI-specific; proxies almost never implement it,
    // and leaving it on makes every call fail against LiteLLM.
    extraParams: { responsesApiEnabled: false },
    options: { temperature: 0.3 },
  },
};

/** Resolve the provider and model for this build from the environment. */
export function selectProvider(env = process.env) {
  // Local by default. Google's free-tier terms permit training on submitted
  // content and human review of it, which is the wrong place for a mailbox.
  const name = (env.JERRY_PROVIDER || 'ollama').toLowerCase();
  const provider = PROVIDERS[name];
  if (!provider) {
    throw new Error(
      `Unknown JERRY_PROVIDER "${name}". Choose one of: ${Object.keys(PROVIDERS).join(', ')}`,
    );
  }
  return { name, ...provider, model: env.JERRY_MODEL || provider.defaultModel };
}
