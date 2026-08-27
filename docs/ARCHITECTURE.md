# Architecture

Fast Reader is a single-page React application with no runtime backend.

```text
Local file picker
       |
       v
Browser text extraction ---- PDF.js / Mammoth / JSZip
       |
       v
IndexedDB document library
       |
       +---- document view ---- click a word
       |                            |
       |                            v
       +---------------------- RSVP reader
                                    |
                                    v
                         localStorage bookmark
```

## Modules

- `src/App.tsx`: interface, library state, document view, reader timing, and keyboard controls.
- `src/textExtraction.ts`: format validation and lazy-loaded TXT/PDF/DOCX/EPUB extraction.
- `src/storage.ts`: the versioned IndexedDB boundary.
- `src/index.css`: theme, responsive layout, and reduced-motion behavior.

## Data lifecycle

The selected `File` is read in browser memory. Extracted plain text and metadata are saved under the app's origin in IndexedDB. Reading positions are saved separately in `localStorage`. Removing a document removes both records. No file contents are sent over the network by application code.

## Static build

Vite emits relative asset URLs into `dist/`, allowing deployment at a domain root or a nested path such as a GitHub Pages project site. Format parsers are loaded on demand to keep the initial interface bundle small.
