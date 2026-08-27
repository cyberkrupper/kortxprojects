# KORT-X Projects

Open-source projects and experimental tools from **KORT-X Laboratories**.

This repository is the public workshop for practical apps, utilities, and small tools built to be used, shared, and improved. Research prototypes and exploratory work live separately under KORT-X Research.

## Projects

### [WhatsApp Local AI Autopilot](whatsapp-llm-extension/)

A Chrome extension that drafts or automatically sends WhatsApp Web replies with a free, locally running Llama model through Ollama. Local mode requires no API key and keeps model traffic on the configured Ollama host.

- **Status:** Beta
- **Version:** 3.0.1
- **Platforms:** Current Google Chrome or Chromium on Windows, macOS, or Linux
- **Local AI:** Ollama with `llama3.2:1b` (approximately 1.3 GB; no dedicated GPU required)
- **Recommended system:** 8 GB RAM and at least 6 GB free disk space on Windows
- **Installation:** [Downloads, setup, testing, upgrades, and removal](whatsapp-llm-extension/docs/INSTALLATION.md)

[![WhatsApp Local AI Autopilot settings](whatsapp-llm-extension/docs/images/settings-popup.jpg)](whatsapp-llm-extension/)

### [Vehicle Maintenance Record](vehicle-maintenance-record/)

A private, local-first web app for tracking vehicle maintenance, documents, fuel, mileage, and costs.

- **Status:** Beta
- **Version:** 0.2.0
- **Platforms:** Windows, macOS, and Linux with Node.js 18+; browser-only mode is also available
- **Stack:** HTML, CSS, vanilla JavaScript, and Node.js
- **Demo:** [Open VMR in your browser](https://cyberkrupper.github.io/kortxprojects/vehicle-maintenance-record/VMR.html)

[![Vehicle Maintenance Record dashboard](vehicle-maintenance-record/docs/vehicle-maintenance-record.png)](vehicle-maintenance-record/)

### [FastReader](fast-reader/)

A private, browser-only speed reader for PDF, TXT, DOCX, and EPUB documents that processes and stores files locally.

- **Status:** Stable
- **Version:** 1.0.0
- **Platforms:** Current desktop and mobile browsers
- **Stack:** React, TypeScript, Vite, IndexedDB, PDF.js, Mammoth, and JSZip

[![FastReader actively reading a local test document](fast-reader/docs/images/reader.png)](fast-reader/)

More projects will be added over time. Each project has its own directory, documentation, requirements, and launch instructions.

## Repository layout

```text
kortxprojects/
├── whatsapp-llm-extension/
│   ├── docs/
│   ├── README.md
│   └── ...
├── vehicle-maintenance-record/
│   ├── docs/
│   ├── README.md
│   └── ...
├── fast-reader/
│   ├── docs/
│   ├── README.md
│   └── ...
├── CONTRIBUTING.md
├── SECURITY.md
└── README.md
```

## Contributing and security

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report suspected vulnerabilities according to [SECURITY.md](SECURITY.md), not through a public issue.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 KORT-X Laboratories.
