# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature if it is enabled for this repository. Otherwise, open an issue that asks the maintainer for a private reporting channel. Do not post API keys, private messages, phone numbers, or working exploit details in a public issue.

## Security notes

- Automatic sending is the default, but it remains disabled for every chat until the user explicitly enables that chat. Select draft mode when replies require review.
- Treat custom provider URLs as trusted code-adjacent infrastructure: the selected endpoint receives conversation text and its configured key.
- Do not expose Ollama's port to a public network. The default setup binds use to the local host.
- Review changes before loading an unpacked extension, because browser extensions can read pages covered by their host permissions.
- The extension depends on WhatsApp Web's changing DOM and cannot guarantee that every message is classified correctly.

Only the latest release is expected to receive security fixes.
