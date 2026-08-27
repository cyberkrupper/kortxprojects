# Privacy

## Data the extension accesses

When AI is enabled for a chat, the extension reads recent visible text messages in that open WhatsApp Web conversation. It also stores these settings in Chrome's local extension storage:

- selected provider and model;
- provider API keys, if supplied;
- prompt, context length, reply mode, and endpoint URLs;
- the names of chats for which AI was enabled.

## Where data goes

- **Ollama:** message context is sent to the configured Ollama URL, which defaults to `http://localhost:11434` on your computer.
- **Gemini, OpenAI, or custom API:** message context is sent directly from the extension to that selected provider. Its privacy and retention policies apply.
- **This project:** the extension contains no analytics, telemetry, advertising, or project-operated server.

API keys remain in Chrome local extension storage, but any software or person with access to the browser profile may be able to obtain them. Remove a key in the popup and revoke it with the provider if the profile or computer is compromised.

Uninstalling the extension removes its Chrome-managed local storage. Ollama models remain installed until removed with Ollama.
