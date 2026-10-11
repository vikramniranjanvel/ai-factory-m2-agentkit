# AgentKit CLI

AgentKit for the AI Factory (Maturity 2).

## Setup

    uv sync
    git config core.hooksPath .githooks   # once per clone

The pre-commit hook regenerates `schemas/*.schema.json` from the Pydantic models in `src/agentkit/schemas/` whenever a
model is committed. The Pydantic models are the source of truth; a test fails if a committed JSON Schema is stale.
Regenerate by hand with `uv run python -m agentkit.schemas.export`.

## Tests

    uv run pytest

## Documentation

- [docs/delivery.md](docs/delivery.md): environments, what each GitHub event does, evaluation, promotion and rollback
- [docs/pending.md](docs/pending.md): what is done and what is open, across all repos
- [docs/schema-documentation.md](docs/schema-documentation.md): `agent.yaml`, evaluation config and datasets
