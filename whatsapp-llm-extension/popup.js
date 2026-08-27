const MODELS = {
  ollama: [
    { value: 'llama3.2:1b', label: 'Llama 3.2 1B (recommended, ~1.3 GB)' },
    { value: 'llama3.2:3b', label: 'Llama 3.2 3B (better quality, ~2 GB)' },
    { value: 'tinyllama', label: 'TinyLlama (lowest requirements)' }
  ],
  gemini: [
    { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
    { value: 'gemini-2.5-flash-lite', label: 'Gemini 2.5 Flash-Lite' }
  ],
  openai: [
    { value: 'gpt-4.1-mini', label: 'GPT-4.1 mini' },
    { value: 'gpt-4o-mini', label: 'GPT-4o mini' }
  ],
  custom: []
};

const DEFAULTS = {
  provider: 'ollama',
  model: 'llama3.2:1b',
  contextLength: 10,
  replyMode: 'send',
  ollamaBaseUrl: 'http://localhost:11434'
};

document.addEventListener('DOMContentLoaded', async () => {
  const elements = Object.fromEntries([
    'provider', 'model', 'customModelInput', 'contextLength', 'apiKey', 'apiKeyGroup',
    'apiKeyNote', 'persona', 'saveBtn', 'status', 'customBaseUrlGroup', 'customBaseUrl',
    'ollamaUrlGroup', 'ollamaBaseUrl', 'testBtn', 'replyMode', 'sendWarning'
  ].map((id) => [id, document.getElementById(id)]));

  let apiKeys = {};
  let selectedModels = {};

  function showStatus(message, type = '') {
    elements.status.textContent = message;
    elements.status.className = type;
  }

  function selectedModel() {
    return elements.model.value === 'custom'
      ? elements.customModelInput.value.trim()
      : elements.model.value;
  }

  function rememberProviderFields() {
    const provider = elements.provider.dataset.previous;
    if (!provider) return;
    apiKeys[provider] = elements.apiKey.value.trim();
    const model = selectedModel();
    if (model) selectedModels[provider] = model;
  }

  function populateModels(provider, selected) {
    elements.model.replaceChildren();
    for (const item of MODELS[provider] || []) {
      const option = document.createElement('option');
      option.value = item.value;
      option.textContent = item.label;
      elements.model.append(option);
    }
    const custom = document.createElement('option');
    custom.value = 'custom';
    custom.textContent = 'Custom model name…';
    elements.model.append(custom);

    const known = (MODELS[provider] || []).some((item) => item.value === selected);
    elements.model.value = known ? selected : 'custom';
    elements.customModelInput.value = known ? '' : (selected || '');
    elements.customModelInput.classList.toggle('hidden', known);
  }

  function updateProviderUI(provider, selected) {
    populateModels(provider, selected || selectedModels[provider] || MODELS[provider]?.[0]?.value);
    elements.apiKeyGroup.classList.toggle('hidden', provider === 'ollama');
    elements.ollamaUrlGroup.classList.toggle('hidden', provider !== 'ollama');
    elements.customBaseUrlGroup.classList.toggle('hidden', provider !== 'custom');
    elements.apiKey.value = apiKeys[provider] || '';
    elements.apiKeyNote.textContent = provider === 'custom'
      ? 'Optional for local proxies; stored locally by Chrome.'
      : 'Stored locally by Chrome. Never commit API keys.';
    elements.provider.dataset.previous = provider;
  }

  elements.model.addEventListener('change', () => {
    const custom = elements.model.value === 'custom';
    elements.customModelInput.classList.toggle('hidden', !custom);
    if (custom) elements.customModelInput.focus();
  });

  elements.provider.addEventListener('change', () => {
    rememberProviderFields();
    updateProviderUI(elements.provider.value);
  });

  elements.replyMode.addEventListener('change', () => {
    const sends = elements.replyMode.value === 'send';
    elements.sendWarning.textContent = sends
      ? 'Automatic mode sends AI text without review. Enable it separately in each chat.'
      : 'Draft mode lets you review every AI reply before sending it.';
  });

  elements.testBtn.addEventListener('click', async () => {
    elements.testBtn.disabled = true;
    showStatus('Checking Ollama…');
    try {
      const response = await chrome.runtime.sendMessage({
        action: 'testOllama',
        ollamaBaseUrl: elements.ollamaBaseUrl.value.trim()
      });
      if (!response?.success) throw new Error(response?.error || 'No response from the extension worker.');
      const models = response.models || [];
      showStatus(models.length ? `Connected. Installed: ${models.join(', ')}` : 'Connected, but no models are installed yet.', 'success');
    } catch (error) {
      showStatus(error.message, 'error');
    } finally {
      elements.testBtn.disabled = false;
    }
  });

  async function requestCustomHostPermission(baseUrl) {
    const parsed = new URL(baseUrl);
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('The custom URL must use http:// or https://.');
    const origin = `${parsed.origin}/*`;
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) throw new Error(`Permission for ${parsed.origin} was not granted.`);
  }

  elements.saveBtn.addEventListener('click', async () => {
    elements.saveBtn.disabled = true;
    showStatus('Saving…');
    try {
      rememberProviderFields();
      const provider = elements.provider.value;
      const model = selectedModels[provider] || selectedModel();
      if (!model) throw new Error('Enter a model name.');
      const customBaseUrl = elements.customBaseUrl.value.trim();
      if (provider === 'custom') {
        if (!customBaseUrl) throw new Error('Enter a custom API base URL.');
        await requestCustomHostPermission(customBaseUrl);
      }
      const contextLength = Math.min(30, Math.max(1, Number.parseInt(elements.contextLength.value, 10) || 10));
      await chrome.storage.local.set({
        provider,
        model,
        selectedModels,
        apiKeys,
        persona: elements.persona.value.trim(),
        contextLength,
        customBaseUrl,
        ollamaBaseUrl: elements.ollamaBaseUrl.value.trim() || DEFAULTS.ollamaBaseUrl,
        replyMode: elements.replyMode.value
      });
      showStatus('Settings saved.', 'success');
    } catch (error) {
      showStatus(error.message, 'error');
    } finally {
      elements.saveBtn.disabled = false;
    }
  });

  const stored = await chrome.storage.local.get([
    'provider', 'model', 'selectedModels', 'apiKeys', 'apiKey', 'persona', 'contextLength',
    'customBaseUrl', 'ollamaBaseUrl', 'replyMode'
  ]);
  const provider = stored.provider || DEFAULTS.provider;
  apiKeys = stored.apiKeys || {};
  if (stored.apiKey && !apiKeys[provider]) apiKeys[provider] = stored.apiKey;
  selectedModels = stored.selectedModels || {};
  if (stored.model) selectedModels[provider] = stored.model;
  elements.provider.value = provider;
  elements.persona.value = stored.persona || '';
  elements.contextLength.value = stored.contextLength || DEFAULTS.contextLength;
  elements.customBaseUrl.value = stored.customBaseUrl || '';
  elements.ollamaBaseUrl.value = stored.ollamaBaseUrl || DEFAULTS.ollamaBaseUrl;
  elements.replyMode.value = stored.replyMode || DEFAULTS.replyMode;
  updateProviderUI(provider, selectedModels[provider] || (provider === 'ollama' ? DEFAULTS.model : undefined));
  elements.replyMode.dispatchEvent(new Event('change'));
});
