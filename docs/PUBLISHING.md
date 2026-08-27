# Publishing to GitHub

The project directory is initialized with `main` as its default branch. Before publishing, verify that `vmr-data.json` is not listed by `git status`.

## With the GitHub CLI

Install and authenticate the GitHub CLI if it is not already available, then run these commands from the project directory:

```bash
gh auth login
gh repo create vehicle-maintenance-record --public --source=. --remote=origin --push
```

Change `--public` to `--private` if the source code should not be public. The private vehicle data file remains excluded either way.

## With an existing empty GitHub repository

Create an empty repository on GitHub without adding a README, license, or `.gitignore`, because those files already exist here. Then run:

```bash
git remote add origin https://github.com/OWNER/vehicle-maintenance-record.git
git push -u origin main
```

Replace `OWNER` and the repository name as needed. For SSH authentication, use the repository's SSH URL instead.

## Before every push

```bash
git status --short
git check-ignore -v vmr-data.json
npm run check
```

The ignore check should identify `.gitignore`. Never force-add `vmr-data.json`; it may contain registration numbers, VINs, and other private information.
