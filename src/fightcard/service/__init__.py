"""The engine as a long-running service: a scheduler and nginx in one container (docs/SERVICE.md).

Stdlib only: the supervisor stays small, and each pipeline stage runs as its own `fightcard` process."""
