const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');

for (const file of ['background.js', 'content.js', 'popup.js']) {
  execFileSync(process.execPath, ['--check', path.join(root, file)], { stdio: 'inherit' });
}

const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
assert.equal(manifest.manifest_version, 3);
assert.equal(manifest.version, '3.0.1');
assert.deepEqual(manifest.permissions, ['storage']);
for (const size of ['16', '48', '128']) {
  assert.ok(fs.existsSync(path.join(root, manifest.icons[size])), `missing ${size}px icon`);
}

let messageListener;
let installListener;
let lastFetch;
let storedUpdates;
const context = {
  chrome: {
    runtime: {
      onInstalled: { addListener(listener) { installListener = listener; } },
      onMessage: { addListener(listener) { messageListener = listener; } }
    },
    storage: {
      local: {
        get(_keys, callback) { callback({ provider: 'ollama', model: 'llama3.2', replyMode: 'draft' }); },
        set(updates) { storedUpdates = updates; }
      }
    }
  },
  URL,
  AbortController,
  Error,
  Promise,
  setTimeout,
  clearTimeout,
  fetch: async (url, options) => {
    lastFetch = { url, options };
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      async json() { return { message: { content: 'Hello there!' } }; }
    };
  }
};
vm.runInNewContext(fs.readFileSync(path.join(root, 'background.js'), 'utf8'), context, { filename: 'background.js' });
assert.equal(typeof messageListener, 'function');
assert.equal(typeof installListener, 'function');
installListener({ reason: 'update', previousVersion: '3.0.0' });
assert.equal(storedUpdates.model, 'llama3.2:1b');
assert.equal(storedUpdates.replyMode, 'send');

function dispatch(request) {
  return new Promise((resolve) => {
    const asyncResponse = messageListener(request, {}, resolve);
    assert.equal(asyncResponse, true);
  });
}

(async () => {
  const result = await dispatch({
    action: 'generateResponse',
    provider: 'ollama',
    messages: [{ role: 'user', content: 'Hi' }]
  });
  assert.equal(result.success, true);
  assert.equal(result.reply, 'Hello there!');
  assert.equal(lastFetch.url, 'http://localhost:11434/api/chat');
  const body = JSON.parse(lastFetch.options.body);
  assert.equal(body.model, 'llama3.2:1b');
  assert.equal(body.stream, false);
  assert.equal(body.messages.at(-1).content, 'Hi');

  const empty = await dispatch({ action: 'generateResponse', provider: 'ollama', messages: [] });
  assert.equal(empty.success, false);

  console.log('All repository checks passed.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
