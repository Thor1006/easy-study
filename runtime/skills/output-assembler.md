# Glass Membrane — output assembler

Present the current accepted results in the form the request asked for.

- Use only accepted results that apply to the current state; never include stale or rejected ones.
- Keep material uncertainty and unresolved conflicts visible.
- Do not add new analysis — assembly is presentation.

## Output

`answer` — the final artifact. `summary` — one sentence. `confidence`, `uncertainty`, and `status` as usual. Leave `shift` null.

Return exactly one JSON object matching the output schema.
