const SELECTORS = {
  composer: '#main footer div[contenteditable="true"][role="textbox"], #main footer div[contenteditable="true"][data-tab="10"]',
  footer: '#main footer',
  headerTitle: '#main header span[title], #main header span[dir="auto"]',
  messageRow: '#main div[role="row"]',
  sendButton: '#main footer button[aria-label="Send"], #main footer button[data-tab="11"], #main footer button:has(span[data-icon="send"]), #main footer span[data-icon="send"]'
};

const DEFAULT_SETTINGS = {
  provider: 'ollama',
  model: 'llama3.2:1b',
  contextLength: 10,
  replyMode: 'send',
  ollamaBaseUrl: 'http://localhost:11434'
};

let activeChats = new Set();
let currentChatTitle = '';
let isGenerating = false;
let debounceTimer;
let currentReplyMode = DEFAULT_SETTINGS.replyMode;
const processedRows = new WeakSet();
const lastProcessedSignature = new Map();

chrome.storage.local.get(['autoReplyChats', 'replyMode'], ({ autoReplyChats, replyMode }) => {
  if (Array.isArray(autoReplyChats)) activeChats = new Set(autoReplyChats);
  currentReplyMode = replyMode || DEFAULT_SETTINGS.replyMode;
  console.info('[WhatsApp Local AI] Loaded.');
  updateButton();
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === 'local' && changes.replyMode) {
    currentReplyMode = changes.replyMode.newValue || DEFAULT_SETTINGS.replyMode;
    updateButton();
  }
});

function getCurrentChatTitle() {
  const element = document.querySelector(SELECTORS.headerTitle);
  return (element?.getAttribute('title') || element?.textContent || '').trim();
}

function getMessageRows() {
  return Array.from(document.querySelectorAll(SELECTORS.messageRow))
    .filter((row) => row.querySelector('.message-in, .message-out, [data-icon="tail-in"], [data-icon="tail-out"]'));
}

function messageDirection(row) {
  if (row.matches('.message-out') || row.querySelector('.message-out, [data-icon="tail-out"]')) return 'out';
  if (row.matches('.message-in') || row.querySelector('.message-in, [data-icon="tail-in"]')) return 'in';
  return null;
}

function messageText(row) {
  const textBlocks = Array.from(row.querySelectorAll('span.selectable-text'))
    .filter((node) => !node.parentElement?.closest('span.selectable-text'))
    .map((node) => node.innerText.trim())
    .filter(Boolean);
  return textBlocks.join('\n').trim();
}

function messageSignature(row) {
  const id = row.getAttribute('data-id') || row.querySelector('[data-id]')?.getAttribute('data-id');
  const metadata = row.querySelector('[data-pre-plain-text]')?.getAttribute('data-pre-plain-text') || '';
  return id || `${metadata}|${messageDirection(row)}|${messageText(row)}`;
}

function latestIncomingRow() {
  return getMessageRows().reverse().find((row) => messageDirection(row) === 'in' && messageText(row));
}

function createControlPanel() {
  const container = document.createElement('div');
  container.id = 'whatsapp-local-ai-controls';
  container.style.cssText = 'display:flex;align-items:center;gap:6px;margin:0 8px;z-index:9999;';

  const button = document.createElement('button');
  button.id = 'whatsapp-local-ai-toggle';
  button.type = 'button';
  button.style.cssText = 'padding:7px 10px;background:#66736f;color:#fff;border:0;border-radius:18px;cursor:pointer;font-weight:700;font-size:12px;white-space:nowrap;';
  button.addEventListener('click', toggleAutopilot);
  container.append(button);
  return container;
}

function injectControls() {
  const footer = document.querySelector(SELECTORS.footer);
  if (footer && !document.getElementById('whatsapp-local-ai-controls')) {
    footer.prepend(createControlPanel());
    updateButton();
  }
}

function updateButton(message) {
  const button = document.getElementById('whatsapp-local-ai-toggle');
  if (!button) return;
  const enabled = activeChats.has(currentChatTitle);
  const enabledLabel = currentReplyMode === 'send' ? 'AI: ON (AUTO)' : 'AI: ON (DRAFT)';
  button.textContent = message || (enabled ? enabledLabel : 'AI: OFF');
  button.style.background = enabled ? '#d92d20' : '#66736f';
  button.title = enabled ? `AI replies enabled for ${currentChatTitle}` : 'Enable AI replies for this chat';
}

function showStatus(message, isError = false) {
  console[isError ? 'error' : 'info'](`[WhatsApp Local AI] ${message}`);
  updateButton(message.length > 22 ? (isError ? 'AI: ERROR' : 'AI: READY') : message);
  const button = document.getElementById('whatsapp-local-ai-toggle');
  if (button) button.title = message;
  setTimeout(() => updateButton(), 3500);
}

async function toggleAutopilot() {
  if (!currentChatTitle) {
    showStatus('Open a chat first.', true);
    return;
  }

  if (activeChats.has(currentChatTitle)) {
    activeChats.delete(currentChatTitle);
  } else {
    const wording = currentReplyMode === 'send' ? 'generate and automatically send' : 'generate a draft';
    if (!confirm(`Enable AI for "${currentChatTitle}"? New incoming messages will ${wording}.`)) return;
    activeChats.add(currentChatTitle);
    const latest = latestIncomingRow();
    if (latest) {
      processedRows.add(latest);
      lastProcessedSignature.set(currentChatTitle, messageSignature(latest));
    }
  }

  await chrome.storage.local.set({ autoReplyChats: Array.from(activeChats) });
  updateButton();
}

function checkForNewMessage() {
  if (!currentChatTitle || !activeChats.has(currentChatTitle) || isGenerating) return;
  const row = latestIncomingRow();
  if (!row || processedRows.has(row)) return;

  const signature = messageSignature(row);
  if (!signature || lastProcessedSignature.get(currentChatTitle) === signature) return;
  processedRows.add(row);
  lastProcessedSignature.set(currentChatTitle, signature);

  clearTimeout(debounceTimer);
  const triggerChat = currentChatTitle;
  debounceTimer = setTimeout(() => generateReply(triggerChat), 1200);
}

function getConversationHistory(limit) {
  return getMessageRows()
    .slice(-limit)
    .map((row) => ({
      role: messageDirection(row) === 'out' ? 'assistant' : 'user',
      content: messageText(row)
    }))
    .filter((message) => message.content);
}

async function generateReply(triggerChat) {
  if (isGenerating || triggerChat !== currentChatTitle || !activeChats.has(triggerChat)) return;
  isGenerating = true;
  updateButton('AI: THINKING…');

  try {
    const settings = { ...DEFAULT_SETTINGS, ...await chrome.storage.local.get([
      'provider', 'model', 'persona', 'contextLength', 'customBaseUrl', 'ollamaBaseUrl',
      'replyMode', 'apiKeys', 'apiKey'
    ]) };
    currentReplyMode = settings.replyMode;
    const apiKey = settings.apiKeys?.[settings.provider] || settings.apiKey || '';
    const messages = getConversationHistory(settings.contextLength);
    if (!messages.length) throw new Error('No text messages found in this chat.');

    const response = await chrome.runtime.sendMessage({
      action: 'generateResponse',
      provider: settings.provider,
      apiKey,
      model: settings.model,
      persona: settings.persona,
      customBaseUrl: settings.customBaseUrl,
      ollamaBaseUrl: settings.ollamaBaseUrl,
      messages
    });

    if (!response?.success) throw new Error(response?.error || 'The model did not return a reply.');
    if (triggerChat !== getCurrentChatTitle() || !activeChats.has(triggerChat)) {
      throw new Error('Chat changed while generating; reply was discarded.');
    }
    if (!insertResponse(response.reply)) throw new Error('Could not insert the reply into WhatsApp.');

    if (settings.replyMode === 'send') {
      await delay(200);
      if (triggerChat !== getCurrentChatTitle()) throw new Error('Chat changed before send; reply left as a draft.');
      await sendCurrentDraft();
      showStatus('AI: SENT');
    } else {
      showStatus('AI: DRAFT READY');
    }
  } catch (error) {
    showStatus(error.message || String(error), true);
  } finally {
    isGenerating = false;
    setTimeout(() => updateButton(), 500);
  }
}

function insertResponse(text) {
  const composer = document.querySelector(SELECTORS.composer);
  if (!composer) return false;
  if ((composer.innerText || '').trim()) {
    throw new Error('The message box already contains text; AI reply was not inserted.');
  }
  composer.focus();
  const inserted = document.execCommand('insertText', false, text);
  return inserted || (composer.innerText || '').trim().length > 0;
}

async function sendCurrentDraft() {
  const composer = document.querySelector(SELECTORS.composer);
  if (!composer) throw new Error('Message box not found; reply could not be sent.');

  let sendControl = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const candidate = document.querySelector(SELECTORS.sendButton);
    sendControl = candidate?.closest('button') || candidate;
    if (sendControl) break;
    await delay(100);
  }

  if (!sendControl) throw new Error('Send button not found; reply left as a draft.');
  sendControl.click();
  await delay(350);

  if ((composer.innerText || '').trim()) {
    throw new Error('WhatsApp did not confirm the send; reply remains in the message box.');
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

const observer = new MutationObserver(() => {
  injectControls();
  const title = getCurrentChatTitle();
  if (title !== currentChatTitle) {
    currentChatTitle = title;
    updateButton();
  }
  checkForNewMessage();
});

observer.observe(document.body, { childList: true, subtree: true });
injectControls();
