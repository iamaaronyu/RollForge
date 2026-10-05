# Contributing

Start with the README and docs/roadmap.md. Discuss runtime or shared schema changes
before broad implementation. Keep contributions scoped to one vertical slice.

Run `make install` and `make check`. Include tests for meaningful invariants and
error paths. Real execution changes require real runtime evidence; do not replace
the entire Harbor/E2B chain with mocks and call it integrated.

Never upload keys, internal endpoints, datasets or raw user trajectories. Use local
environment configuration. Respect the single source of truth in packages/schemas.

A license has not been selected yet. Check the repository license status before
contributing or reusing code.
