const DEFAULT_PERSONA = 'Write a natural, concise WhatsApp reply. Match the language and tone of the conversation. Return only the reply text.';
const DEFAULT_OLLAMA_MODEL = 'llama3.2:1b';
const REQUEST_TIMEOUT_MS = 180000;

chrome.runtime.onInstalled.addListener((details) => {
  chrome.storage.local.get(['provider', 'model', 'replyMode'], (stored) => {
    const updates = {};
    if (!stored.provider) updates.provider = 'ollama';
    if (!stored.model || stored.model === 'llama3.2') updates.model = DEFAULT_OLLAMA_MODEL;
    if (!stored.replyMode || details.previousVersion === '3.0.0') updates.replyMode = 'send';
    if (Object.keys(updates).length) chrome.storage.local.set(updates);
  });
});

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request?.action === 'generateResponse') {
    handleGeneration(request).then(sendResponse);
    return true;
  }
  if (request?.action === 'testOllama') {
    testOllama(request.ollamaBaseUrl).then(sendResponse);
    return true;
  }
  return false;
});

async function handleGeneration(request) {
  const provider = request.provider || 'ollama';
  const messages = sanitizeMessages(request.messages);
  if (messages.length === 0) return failure('No conversation text was found.');

  try {
    if (provider === 'ollama') return await callOllama(request.model, request.persona, messages, request.ollamaBaseUrl);
    if (provider === 'gemini') return await callGemini(request.apiKey, request.model, request.persona, messages);
    if (provider === 'custom') return await callOpenAICompatible(request.customBaseUrl, request.apiKey, request.model, request.persona, messages, 'Custom API');
    if (provider === 'openai') return await callOpenAICompatible('https://api.openai.com/v1', request.apiKey, request.model, request.persona, messages, 'OpenAI');
    return failure(`Unsupported provider: ${provider}`);
  } catch (error) {
    return failure(error instanceof Error ? error.message : 'Unknown generation error');
  }
}

function sanitizeMessages(messages) {
  if (!Array.isArray(messages)) return [];
  return messages
    .filter((message) => message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string')
    .map((message) => ({ role: message.role, content: message.content.trim() }))
    .filter((message) => message.content)
    .slice(-50);
}

function normalizeBaseUrl(value, fallback) {
  const raw = (value || fallback).trim().replace(/\/+$/, '');
  const parsed = new URL(raw);
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('The provider URL must use http:// or https://.');
  return parsed.toString().replace(/\/+$/, '');
}

async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error('The model did not respond within 3 minutes.');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function readApiError(response) {
  const data = await response.json().catch(() => null);
  return data?.error?.message || data?.error || data?.message || `${response.status} ${response.statusText}`;
}

async function callOllama(model, persona, messages, baseUrl) {
  const root = normalizeBaseUrl(baseUrl, 'http://localhost:11434');
  const selectedModel = model?.trim() || DEFAULT_OLLAMA_MODEL;
  let response;
  try {
    response = await fetchWithTimeout(`${root}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: selectedModel,
        messages: [{ role: 'system', content: persona || DEFAULT_PERSONA }, ...messages],
        stream: false,
        keep_alive: '10m',
        options: { num_predict: 240, temperature: 0.7 }
      })
    });
  } catch (error) {
    throw new Error(`Cannot reach Ollama at ${root}. Start Ollama and run the setup script. ${error.message}`);
  }
  if (!response.ok) {
    const detail = await readApiError(response);
    if (response.status === 404 || /not found/i.test(String(detail))) {
      throw new Error(`Ollama model "${selectedModel}" is not installed. Run: ollama pull ${selectedModel}`);
    }
    throw new Error(`Ollama: ${detail}`);
  }
  const data = await response.json();
  const reply = data?.message?.content?.trim();
  if (!reply) throw new Error('Ollama returned an empty reply.');
  return { success: true, reply };
}

async function testOllama(baseUrl) {
  try {
    const root = normalizeBaseUrl(baseUrl, 'http://localhost:11434');
    const response = await fetchWithTimeout(`${root}/api/tags`);
    if (!response.ok) throw new Error(await readApiError(response));
    const data = await response.json();
    const models = Array.isArray(data.models) ? data.models.map((item) => item.name).filter(Boolean) : [];
    return { success: true, models };
  } catch (error) {
    return failure(`Ollama connection failed: ${error.message}`);
  }
}

async function callOpenAICompatible(baseUrl, apiKey, model, persona, messages, label) {
  const root = normalizeBaseUrl(baseUrl, 'https://api.openai.com/v1');
  if (!model?.trim()) throw new Error(`${label}: enter a model name.`);
  if (label === 'OpenAI' && !apiKey) throw new Error('OpenAI: enter an API key.');
  const headers = { 'Content-Type': 'application/json' };
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
  const response = await fetchWithTimeout(`${root}/chat/completions`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      model: model.trim(),
      messages: [{ role: 'system', content: persona || DEFAULT_PERSONA }, ...messages],
      ...(label === 'OpenAI' ? { max_completion_tokens: 240 } : { max_tokens: 240 })
    })
  });
  if (!response.ok) throw new Error(`${label}: ${await readApiError(response)}`);
  const data = await response.json();
  const reply = data?.choices?.[0]?.message?.content?.trim();
  if (!reply) throw new Error(`${label} returned an empty reply.`);
  return { success: true, reply };
}

async function callGemini(apiKey, model, persona, messages) {
  if (!apiKey) throw new Error('Gemini: enter an API key.');
  const selectedModel = model?.trim() || 'gemini-2.5-flash';
  const contents = messages.map((message) => ({
    role: message.role === 'assistant' ? 'model' : 'user',
    parts: [{ text: message.content }]
  }));
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(selectedModel)}:generateContent?key=${encodeURIComponent(apiKey)}`;
  const response = await fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      contents,
      systemInstruction: { parts: [{ text: persona || DEFAULT_PERSONA }] },
      generationConfig: { maxOutputTokens: 240, temperature: 0.7 }
    })
  });
  if (!response.ok) throw new Error(`Gemini: ${await readApiError(response)}`);
  const data = await response.json();
  const reply = data?.candidates?.[0]?.content?.parts?.map((part) => part.text || '').join('').trim();
  if (!reply) throw new Error('Gemini returned an empty reply.');
  return { success: true, reply };
}

function failure(error) {
  return { success: false, error };
}
