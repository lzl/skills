# Rules

- Use Conventional Commits for code commits.
- If `uv` is available, use `uv` to manage Python dependencies and run Python skill scripts.
- Keep generated or downloaded skill artifacts under `output/<skill-name>/`.
- Keep the root `.env` local and ignored for secrets and machine-specific config.
- Do not add per-skill artifact ignore rules; route new skill outputs through `output/<skill-name>/`.
