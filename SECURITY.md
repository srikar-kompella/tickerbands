# Security policy

## Reporting

Please report suspected vulnerabilities via GitHub's private security
advisories ("Report a vulnerability" on the Security tab) rather than public
issues.

## Design posture

- **Local-first:** the app binds to localhost; there is no hosted service,
  no telemetry, and no data leaves your machine except calls you configure
  to your own data providers.
- **Read-only by construction:** no order placement or money movement exists
  in the codebase; broker access is read-only market/account data.
- **Secrets:** never stored in the repo. Resolution is OS keyring
  (`keyring set tickerlens <name>`) → `~/.tickerlens/.env` (chmod 600) →
  environment. Report any code path that logs or persists a credential —
  that is a bug of the highest severity here.
