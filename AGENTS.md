# Agent guidance

## Repository layout

Owner of this section: the "Repository layout" section of
overnightworks/marketplace `AGENTS.md`, restated verbatim below; a change there
lands here. In this repository `scripts/check_root_layout.py` is the check, and
CI runs it.

The repository root holds only what a tool must find there — the package
manifest and its lockfile, container files, scanner configuration, the licence —
and the entry documents `README.md`, `AGENTS.md`, `CLAUDE.md` and, where the
repository carries one, `HEART.md`. Everything else lives in the directory of
its owner: a ratchet baseline next to the check that reads it, a fixture next to
its test, a helper under `scripts/`. No catch-all directory (`tooling/`,
`misc/`) and no deeper hierarchy than the owner needs. A root allowlist check in
CI keeps the rule, so nobody has to remember it (operator ruling 06.09.2026).


## Quality gate

CI calls the shared gate of overnightworks/quality (`quality` job in
`.github/workflows/ci.yml`, pinned by commit SHA; configuration in
`quality/config.toml`). Its `quality / verdict` check is the required check on
`main`; the `check` job is mandatory for it. SonarCloud is scanned only by the
gate, never by a step of this repository.
