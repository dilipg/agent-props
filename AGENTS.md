# AGENTS.md

Read [CLAUDE.md](CLAUDE.md) in full before doing anything in this repository. It holds the invariants,
the layering rule, the commands, the testing bar and the document map, and it applies to every agent
working here regardless of harness — this file is a pointer so that guidance has one home and cannot
drift between two copies.

Then read [docs/build-handoff.md](docs/build-handoff.md) for the milestone you are building.

When authoring a dataset, set `provenance.author.agent` to the harness you actually are: `claude-code`,
`codex`, `human` or `generator` (DS-030). `provenance.author` is attribution, not authentication.
