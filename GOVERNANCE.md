# Governance

Filo is currently a single-maintainer project. Matteo Dante is the project maintainer and has
final responsibility for scope, releases, security response, repository access, and protocol
changes.

The public alpha is published without charge by Matteo Dante as an individual. There is no company
operator, hosted service, paid support, donation, or sponsorship program. The AGPL permits commercial
use; the publisher's alpha operating scope is not a non-commercial restriction on original code.
Separate commercial licensing enquiries follow [COMMERCIAL-LICENSING.md](COMMERCIAL-LICENSING.md).
No commercial deliverable is pre-cleared, and commercial activity requires reassessing the existing
release assumptions before proceeding.

## Decisions

Small fixes may be accepted directly when they preserve the documented scope and pass the repository
checks. Discuss major behavioral, protocol, dependency, storage, security, licensing, or architecture
changes in an issue before implementation. Decisions prioritize:

1. user safety, privacy, and data integrity;
2. evidence-linked and correctable behavior;
3. the smallest maintainable implementation;
4. compatibility with the current single-user milestone.

The maintainer may reject changes that exceed the documented scope, add maintenance burden without a
current need, weaken validation or safety boundaries, or make unsupported clinical claims.

## Alpha governance constraints

For the current single-maintainer phase, changes are pushed directly to `main`; the repository does
not require a pull request or independent review. The maintainer must run the local release checks
before pushing and verify the resulting GitHub CI and CodeQL runs before tagging a release.

There is no second administrator, recovery owner, or confidential project-specific conduct inbox.
These are accepted alpha risks, not mature-project controls. The project must not claim independent
review, continuous availability, confidential conduct case handling, or succession coverage. These
decisions must be revisited before adding a second maintainer, accepting material operational
responsibility for users, or promoting beyond a controlled alpha.

## Contributions and succession

Contributors retain copyright and use the DCO plus explicit, versioned [CLA](CLA.md) acceptance
under [CONTRIBUTING.md](CONTRIBUTING.md). Matteo Dante receives non-exclusive sublicensing rights
subject to the CLA's AGPL availability commitment. Prior contributions and third-party material
require separate rights verification; a policy change grants no retroactive permission.
Project roles will be expanded only when sustained contribution makes them real rather than
aspirational. Any successor receiving CLA rights must also assume the CLA obligations.

If the maintainer can no longer maintain the project, they may appoint a successor, add co-maintainers,
or archive the repository with its status documented publicly. No current successor or recovery
owner is promised.
