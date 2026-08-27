# Changelog

All notable project changes are documented here.

## 3.0.1 - 2026-08-27

### Added

- Free local `llama3.2:1b` default through Ollama.
- Windows setup and live inference-test scripts.
- Ollama connection test in the extension popup.
- Automatic and draft reply modes with a visible per-chat mode label.
- Provider-specific API-key storage and optional custom-host permission prompts.
- CI, privacy, security, contribution, requirements, and installation documentation.

### Fixed

- Bound generated replies to the chat that triggered them.
- Removed the duplicate click-plus-Enter send path.
- Removed duplicate composer input dispatch that could repeat reply text.
- Added current WhatsApp send-button fallbacks and confirmation that the composer cleared.
- Migrated the old `llama3.2` model alias to `llama3.2:1b`.
- Migrated version 3.0.0 settings to automatic mode.
- Removed unused Chrome permissions, heartbeat alarms, stale cloud-only options, hard-coded extension IDs, mojibake, and unsafe cleanup scripts.

### Security

- Custom API domains now require explicit Chrome host permission.
- Chat switching, pre-existing composer text, missing controls, and failed sends stop the automatic flow.

## 2.x

Pre-release local files supported several LLM providers but had no formal GitHub release, stable documentation, or automated checks.
