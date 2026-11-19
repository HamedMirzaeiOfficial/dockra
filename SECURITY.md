# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Security → Report a vulnerability** (private advisory) on this repository. Include the affected version or commit, steps to reproduce and the impact you observed. You can expect an acknowledgement within a few days; fixes are released as soon as they are ready and credited unless you prefer otherwise.

Please do not open public issues or pull requests for unfixed vulnerabilities.

## Scope

Dockra's threat model and known limitations are documented in [docs/security.md](docs/security.md). In particular, Docker containers are not treated as a sandbox for hostile code; reports that rely only on that documented limitation are still welcome but are handled as hardening work.
