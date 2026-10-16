# Security policy

## Supported versions

Only the latest revision of the default branch is supported. The project is experimental and has
not received a formal security or clinical audit.

## Reporting a vulnerability

Do not open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/matteodante/filo/security/advisories/new)
and include the affected version, environment, reproduction steps, and expected impact.

Do not include access tokens, real conversations, or other sensitive personal data. Use synthetic
examples and redact secrets. Reports about an immediate personal or medical emergency are outside
this process; contact local emergency services instead.

The maintainer will acknowledge reports and coordinate validation, remediation, and disclosure on a
best-effort basis. This experimental project does not promise a response or release deadline. Please
do not disclose an unpatched vulnerability publicly before coordinated disclosure is complete.

## In scope

- loss, corruption, or unintended disclosure of encrypted application data;
- authentication, authorization, Telegram allowlist, or secret-storage bypasses;
- prompt or tool paths that bypass persisted-memory validation or user consent;
- installer, update, dependency, or release supply-chain compromise.

Model quality, general feature requests, and non-reproducible clinical claims are not security
vulnerabilities. Report reproducible behavioral or safety failures through the dedicated issue form
using synthetic data only.
