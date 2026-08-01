# Notice and provenance

Release candidate: `v0.1.0` (Windows build V26), prepared 2026-08-01.

This directory is a local integration work named **GBFR AutoReBattle - Chiaki
Adapter**. It adds a local controller/UI adapter, PSN AccountID helper, virtual
DS4 background-input path, and build material around the following projects.

## GBFR_AutoReBattle base

- Upstream: https://github.com/xinbaji/GBFR_AutoReBattle
- Base revision used locally: `206e4f083e2129d0b3235876ab167c5e9e1fa8d3`
- Copyright: the respective upstream authors.

At the time this package was prepared, the upstream repository contained no
license text and GitHub reported no detected license. Its README badge is not a
substitute for a license grant. Do **not** publish this full derivative source
repository publicly or label it MIT until the upstream copyright holder has
provided written permission or added an explicit license. See
`PUBLISH_BLOCKERS.md`.

## Chiaki

- Upstream: https://git.sr.ht/~thestr4ng3r/chiaki
- License: GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later)

Chiaki is not included in this source tree or its build output. The local
portable release candidate adds Chiaki 2.2.0 separately. Its corresponding
source archive is pinned to revision
`89368f63c99d67cde8868c0269b66a1b0c507397`. If that package is distributed,
provide the archive at the same download location and retain all notices. See
`CORRESPONDING_SOURCE.md`.

## Local changes

The adapter-specific work in this tree was produced locally. It must still be
distributed only in a manner compatible with the rights of the base project and
all third-party components.

No license is granted here for third-party code. No part of this notice should
be read as affiliation with or endorsement by any game, platform, or upstream
project owner.
