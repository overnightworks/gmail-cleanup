# Agent guidance

## Quality gate

CI calls the shared gate of overnightworks/quality (`quality` job in
`.github/workflows/ci.yml`, pinned by commit SHA; configuration in
`quality/config.toml`). Its `quality / verdict` check is the required check on
`main`; the `check` job is mandatory for it. SonarCloud is scanned only by the
gate, never by a step of this repository.
