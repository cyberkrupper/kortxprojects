# Requirements

## End users

Fast Reader is a static browser application. It requires a current desktop or mobile browser with JavaScript, Web Workers, IndexedDB, `localStorage`, `crypto.randomUUID`, and ES modules enabled.

Supported targets:

- Latest two stable versions of Chrome and Edge
- Latest two stable versions of Firefox
- Safari 16.4 or newer

Internet Explorer and browsers with JavaScript or local storage disabled are not supported. Private browsing modes may limit storage or discard the library when the session ends.

No backend, database server, account, or internet connection is required after the static assets have loaded. Browsers normally require static files to be served from an HTTP(S) origin; use the development/preview commands or deploy `dist/` to any static host.

## Developers

- Node.js `^20.19.0` or `>=22.12.0`
- npm (included with Node.js)
- Approximately 300 MB free disk space for dependencies and build output

Install exact locked dependencies with:

```bash
npm ci
```

## Format limitations

- Scanned/image-only PDFs are unsupported because the app does not include OCR.
- Password-protected documents are unsupported.
- EPUB extraction reads HTML/XHTML content and may not perfectly reproduce custom reading order.
- Maximum practical file size depends on browser memory and per-origin IndexedDB quota.
