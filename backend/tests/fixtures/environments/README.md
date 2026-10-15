# Fixture environments

Small Python bug-fix environments: `slugify()` crashes on an empty string. Each has its three
parts in their own directories (`task/`, `grader/`, `reference/`) and a `manifest.json` with the
test command. The demo submits the first four through the public API; the sandbox tests use all
of them.

| Fixture | What's wrong | Caught by |
|---|---|---|
| `sound` | Nothing | Passes all three checks |
| `passes_anything` | The tests only check the function exists | The empty-solution check |
| `unsolvable` | Two tests want different output for the same input | The reference check |
| `flaky` | One test passes at random | The repeat check (or the reference check, if it fails first) |
| `hostile_network` | A test reaches for the network | The reference check: there's no network |
| `hostile_runaway` | A test never ends | The time limit |
| `hostile_memory` | A test allocates far more than the limit | The memory limit |
| `hostile_peek` | The prepare step looks for the grader and reference | Nothing to find: neither enters the attempt step |
