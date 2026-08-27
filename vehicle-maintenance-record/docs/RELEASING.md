# Releasing Vehicle Maintenance Record

VMR follows [Semantic Versioning](https://semver.org/). The version in `package.json` is the source of truth.

## Before creating a release

1. Confirm the release changes are merged into the default branch.
2. From `vehicle-maintenance-record/`, run:

   ```bash
   npm run check
   npm run start:no-open
   ```

3. Test vehicle creation, editing, maintenance entry, JSON export, and JSON restore in a modern browser.
4. Confirm no private data is tracked:

   ```bash
   git status --short
   git check-ignore -v vehicle-maintenance-record/vmr-data.json
   ```

5. Update `package.json` and the status/version shown in both READMEs.

## Create the GitHub release

Tag the merge commit as `v<version>`—for example, `v0.1.0`—and create a GitHub release from that tag. Summarize user-visible changes, installation requirements, known limitations, and any data-format migration notes.

Do not attach a real `vmr-data.json` or any archive that contains personal vehicle data.
