# Download, installation, and removal

## 1. Download the extension

Choose one method:

### GitHub ZIP

1. On the repository page, select **Code** and **Download ZIP**.
2. Extract the ZIP to a permanent folder. Chrome cannot reliably use a compressed ZIP as an unpacked extension.

### Git

```sh
git clone https://github.com/cyberkrupper/whatsapp-llm-extension.git
cd whatsapp-llm-extension
```

Do not copy or commit the `ollama-models/` directory. Each machine downloads its own model through Ollama.

## 2. Install Ollama

Download only from [ollama.com/download](https://ollama.com/download).

### Windows

Ollama requires Windows 10 22H2 or newer. Install `OllamaSetup.exe`, then open PowerShell in the extension folder:

```powershell
.\setup-ollama.ps1
```

If PowerShell blocks local scripts:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup-ollama.ps1
```

The script:

1. verifies that `ollama` is installed;
2. saves `OLLAMA_ORIGINS=chrome-extension://*` for the user account;
3. saves `OLLAMA_MODELS=<repository>\ollama-models`;
4. restarts the Ollama process;
5. downloads and verifies `llama3.2:1b`.

It stops an existing Ollama process so environment changes take effect. Unsaved work in other Ollama clients should be closed first.

### macOS

Ollama requires macOS 14 Sonoma or newer. Install the official application, then start a terminal server with the required origin and pull the model:

```sh
OLLAMA_ORIGINS='chrome-extension://*' ollama serve
```

In another terminal:

```sh
ollama pull llama3.2:1b
```

To persist the origin, configure it in the environment used to launch Ollama. The repository's PowerShell automation is Windows-only.

### Linux

Follow the [official Ollama Linux instructions](https://docs.ollama.com/linux). Configure `OLLAMA_ORIGINS=chrome-extension://*` in the environment or systemd service that runs Ollama, restart the service, then run:

```sh
ollama pull llama3.2:1b
```

## 3. Verify Ollama

On Windows:

```powershell
.\test-ollama.ps1
```

On any platform, verify the model list:

```sh
ollama list
```

The model should appear as `llama3.2:1b`. The API health endpoint is `http://localhost:11434/api/tags`.

## 4. Load the extension

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Select the folder containing `manifest.json`, not its parent and not `ollama-models/`.
5. Pin **WhatsApp Local AI Autopilot** from Chrome's Extensions menu.

After every source update, select **Reload** on the extension card and then refresh WhatsApp Web. Manifest, service-worker, and content-script changes do not become active from a normal page refresh alone.

## 5. Configure and test

1. Open the extension popup.
2. Select **Ollama + Llama (local, free)**.
3. Select **Llama 3.2 1B** and keep `http://localhost:11434`.
4. Select **Send automatically** or the safer draft option.
5. Adjust context length and reply instructions.
6. Select **Test Ollama connection**, then **Save settings**.
7. Open WhatsApp Web and a conversation.
8. Select **AI: OFF**, confirm, and verify the label becomes **AI: ON (AUTO)** or **AI: ON (DRAFT)**.
9. Only a new incoming text message after enabling triggers a reply.

Test first in a conversation where all participants know automation is being tested. The extension never enables every chat globally.

## Upgrade

With Git:

```sh
git pull
npm test
```

Then reload the extension at `chrome://extensions` and refresh WhatsApp Web. Model files remain in the ignored `ollama-models/` directory.

For a downloaded ZIP, extract the new version over a clean application folder, retaining `ollama-models/` only if the folder remains at the same configured path. Reload the extension afterward.

## Change model

Lower requirements:

```powershell
.\setup-ollama.ps1 -Model llama3.2:1b
```

Better replies with higher requirements:

```powershell
.\setup-ollama.ps1 -Model llama3.2:3b
```

Select the matching exact model in the extension popup.

## Troubleshooting checklist

1. Confirm Ollama is running and `ollama list` contains the selected model.
2. In the popup, run **Test Ollama connection** and save.
3. Reload the extension and refresh WhatsApp Web.
4. Confirm the chat button says **AI: ON (AUTO)**.
5. Clear any existing text from the WhatsApp composer.
6. Wait for a new incoming text message; enabling AI intentionally ignores the message already visible.
7. Inspect the extension's Errors view and the WhatsApp Web console. When reporting a problem, copy the actual error message, not chat content or API keys.

## Uninstall

1. Disable AI in any enabled chats.
2. Remove the extension from `chrome://extensions`; Chrome removes its extension-local settings.
3. Stop Ollama before removing model data.
4. Remove models with `ollama rm llama3.2:1b`, or delete the configured `ollama-models/` directory after verifying its exact path.
5. If desired, remove the user environment variables `OLLAMA_ORIGINS` and `OLLAMA_MODELS`, then restart Ollama.
6. Uninstall Ollama using the official operating-system instructions.

Removing the Git repository alone does not uninstall Chrome's extension record or Ollama. Do not delete `ollama-models/` while Ollama is writing a model.
