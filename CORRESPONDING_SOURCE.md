# Corresponding source information

This document applies when the portable Windows package includes Chiaki.

## Chiaki component

- Component: Chiaki 2.2.0 Windows build
- Upstream: https://git.sr.ht/~thestr4ng3r/chiaki
- License: GNU Affero General Public License v3.0 or later
- Source revision used for the local source archive:
  `89368f63c99d67cde8868c0269b66a1b0c507397`
- Main-repository source snapshot: `chiaki-v2.2.0-source-snapshot.zip`

The available upstream tag snapshot does not contain the contents of Chiaki's
Git submodules. It must not be described as complete corresponding source by
itself. Before distributing the portable binary package, prepare a complete
source archive for tag `v2.2.0` with every required submodule at the commit
recorded by that tag, place it at the same download location, and retain
`Chiaki/LICENSE.txt`. A link to an upstream repository is useful provenance but
does not replace providing the exact corresponding source for the distributed
binary.

The Chiaki source archive does not cover GBFR AutoReBattle, this adapter, the
ViGEmBus driver, RapidOCR, ONNX Runtime, or other third-party components. Their
separate license status is described in `NOTICE.md`,
`THIRD_PARTY_NOTICES.md`, and `PUBLISH_BLOCKERS.md`.

Do not publish the current automation source or binary merely because a Chiaki
source snapshot is available. Public redistribution remains blocked by the
missing licenses for the GBFR_AutoReBattle base and the vendored
rapidocr-onnxruntime-lite source, as well as the incomplete model provenance
audit.
