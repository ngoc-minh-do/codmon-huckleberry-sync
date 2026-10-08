# Security Policy

## Supported versions

Only the latest commit on `main` is supported. This project has no released
versions and receives best-effort fixes.

| Version | Supported |
| --- | --- |
| `main` (latest) | ✅ |
| older commits | ❌ |

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report privately using GitHub's
[private vulnerability reporting](https://github.com/ngoc-minh-do/codmon-huckleberry-sync/security/advisories/new).
If you cannot use that, email **ngochust56@gmail.com** with:

- a description of the issue and its impact,
- steps to reproduce (a minimal proof of concept if possible),
- any suggested remediation.

Please include only the minimum data needed; never send real credentials or a
child's personal data.

## What to expect

This is a volunteer-maintained project, so responses are best-effort. We aim to
acknowledge a report within a few days and to coordinate disclosure once a fix
is available.

## Scope

This project stores third-party account credentials in a local `.env` and
writes to the Huckleberry service on your behalf. Relevant reports include
credential leakage, unsafe logging of secrets or personal data, and injection
through untrusted report content. Vulnerabilities in the upstream
[`huckleberry-api`](https://github.com/Woyken/py-huckleberry-api) library or in
Codmon/Huckleberry themselves should be reported to those projects.
