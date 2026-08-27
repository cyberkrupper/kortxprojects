# Security policy

## Reporting a vulnerability

Do not open a public issue for a security vulnerability. Use GitHub's **Security → Report a vulnerability** private reporting feature when it is enabled. If private reporting is unavailable, contact the repository owner through their GitHub profile without including exploit details in a public message.

Include the affected version or commit, reproduction steps, impact, and any suggested mitigation. Please allow reasonable time for investigation before public disclosure.

## Security model

Fast Reader has no application backend. Documents are parsed and stored locally in the browser origin. Third-party parsing libraries still process untrusted files, so dependencies should be kept current and imported documents should come from trusted sources.

The project does not claim to provide encryption at rest. Anyone with access to the same unlocked browser profile may be able to access its site data.
