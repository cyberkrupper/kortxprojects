# Contributing

Thanks for improving Fast Reader.

## Development workflow

1. Fork the repository and create a focused branch from `main`.
2. Install locked dependencies with `npm ci`.
3. Make the change without introducing document uploads or backend dependencies.
4. Run `npm run lint` and `npm run build`.
5. Test TXT import plus every document format affected by the change.
6. Open a pull request using the repository template.

Keep pull requests small, explain user-visible behavior, and include updated screenshots for visual changes. Never commit documents used for local testing, generated `dist/`, `node_modules`, logs, local paths, or credentials.

## Architecture constraints

- Document contents must remain inside the browser.
- Prefer Web Platform APIs over new dependencies.
- Keep storage access in `src/storage.ts` and parsers in `src/textExtraction.ts`.
- Preserve keyboard access and responsive behavior.

By contributing, you confirm that you have the right to submit the work and agree that your contribution will be licensed under the MIT License.
