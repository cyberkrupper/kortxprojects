# System and software requirements

This document separates vendor-supported requirements from practical recommendations for this extension.

## Supported software

### Required for all installations

- A current Google Chrome or Chromium-based browser that supports Manifest V3, developer mode, and unpacked extensions.
- An active WhatsApp account signed in to [WhatsApp Web](https://web.whatsapp.com/).
- Internet access for initial downloads. After the extension, Ollama, and model are installed, the default local provider does not require a cloud AI API.

### Required for local, free AI

- [Ollama](https://ollama.com/download).
- The `llama3.2:1b` model, downloaded by `setup-ollama.ps1` or `ollama pull llama3.2:1b`.
- Ollama running on `http://localhost:11434` with `chrome-extension://*` included in `OLLAMA_ORIGINS`.

Official Ollama operating-system baselines:

| Operating system | Ollama baseline |
| --- | --- |
| Windows | Windows 10 22H2 or newer, Home or Pro |
| macOS | macOS Sonoma 14 or newer; Apple M-series supports CPU/GPU, Intel Macs use CPU |
| Linux | A distribution capable of running the official Ollama package or install script; GPU drivers are optional |

On Windows, Ollama documents NVIDIA driver 452.39+ for NVIDIA acceleration and a current AMD Radeon driver for Radeon acceleration. GPU support evolves, so consult [Ollama hardware support](https://docs.ollama.com/gpu) for the current compatibility list.

## Hardware guidance

The following are project recommendations, not guarantees from Ollama or Meta:

| Usage tier | RAM | Model | GPU | Expected experience |
| --- | ---: | --- | --- | --- |
| Low requirement | 8 GB recommended | `llama3.2:1b` (~1.3 GB) | Not required | Best default for CPU-only and older systems; shorter, less capable replies |
| Better quality | 12–16 GB recommended | `llama3.2:3b` (~2.0 GB) | Optional | Better instruction following with higher memory/CPU use |
| Cloud API | Browser-dependent | Provider model | Not required locally | Local model RAM is unnecessary; provider cost and privacy rules apply |

Available RAM must also cover the operating system, Chrome, WhatsApp Web, and Ollama. Close memory-heavy applications if the model loads slowly or the system begins paging. A modern multi-core CPU improves response time. SSD storage is strongly recommended.

## Disk space

- The extension source is under 2 MB without local model data.
- `llama3.2:1b` is approximately 1.3 GB in the Ollama library.
- `llama3.2:3b` is approximately 2.0 GB.
- Ollama's Windows documentation says to allow at least 4 GB for the binary installation, plus model storage.
- For the Windows default, keep at least 6 GB free; 8 GB or more leaves safer room for updates and temporary files.

The Windows setup script stores model data in `ollama-models/` inside the repository. This directory is in `.gitignore` and must never be committed or included in an extension ZIP.

## Development requirements

Development and CI checks require:

- Node.js 18 or newer;
- Git;
- PowerShell 5.1+ for the provided Windows setup/test scripts;
- no npm package installation, bundler, or build tool.

Run `npm test` to validate JavaScript syntax, the Manifest V3 file, icon references, default local-model requests, and settings migration.

## Optional API providers

Gemini, OpenAI, and custom OpenAI-compatible APIs require a provider account and may require an API key, billing, or a free-tier quota. Their current requirements, prices, availability, and data policies are controlled by those providers and are not guaranteed by this project.
