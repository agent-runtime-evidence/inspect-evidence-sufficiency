# Contributing

Thanks for your interest. This is a small research artifact; contributions and issues are welcome.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,inspect]"   # dev = ruff + mdformat; inspect = the Inspect scorer e2e path

make verify   # stdlib tests + offline demos (no network)
make lint     # ruff + mdformat checks
make format   # ruff + mdformat autoformat
make test     # unit tests (Inspect e2e runs when inspect-ai is installed)
```

`make verify` is offline. `make verify-public` additionally fetches the pinned, checksum-verified public-dataset demos (Trace Commons, Assayo).

## Design constraints

Please keep changes within the artifact's discipline:

- **Stdlib-first.** The core package has no runtime dependencies; `inspect-ai` is an optional extra. Don't add a required dependency without discussion.
- **Evidence-bound, never self-scored.** A field is `present` / `partial` / `missing` because of evidence actually in the trace, not an asserted number. Don't add scoring that isn't grounded in extracted features.
- **No overclaiming.** Respect the claim boundary (see `README.md` and the `claim_boundary` block every card emits): no upstream-acceptance, benchmark, safety, or audit/compliance/legal sufficiency claims.
- **Deterministic.** Cards must stay byte-reproducible given fixed inputs and a pinned timestamp. New non-deterministic fields need a determinism test.
- **No vendored traces.** Public inputs are fetched at runtime, pinned to a revision, and checksum-verified; raw trace contents stay git-ignored.
- **Markdown is no-wrap.** Prose is one line per paragraph (no manual hard-wrapping at a column), enforced by `mdformat` (`.mdformat.toml`); let the renderer/editor soft-wrap. Run `make format` before committing docs.

## Before opening a PR

Run `make lint` and `make test` (and `make verify`). New behavior should come with a test, and changes to extraction or scoring should re-confirm the demo spectrum.
