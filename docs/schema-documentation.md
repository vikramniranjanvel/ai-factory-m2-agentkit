# Documenting the schemas

Plan for putting help text next to the schemas (agent definition, evaluation config, dataset). Not implemented yet.

Principle: write each explanation once, in the Pydantic model, and generate everything else from it.

## 1. Descriptions in the models (foundation)

- Add `Field(description=..., examples=[...])` to every field and a docstring to every class.
- Pydantic copies both into the generated JSON Schema, so editors show hover help and autocomplete.
- Each YAML file starts with `# yaml-language-server: $schema=<path or URL to the schema>` to switch this on in VS Code and JetBrains.

## 2. Generated reference pages

- A script writes `docs/reference/<schema>.md` from the JSON Schema: one table per schema with key, type, required or default, description.
- Run it from the same pre-commit hook and CI diff check that keep `schemas/*.json` in sync, so it cannot go stale.

## 3. Commented, tested examples

- `agentkit init` scaffolds `agent.yaml` and `evaluation.yaml` with inline comments (as v1's `evaluation.yaml` has).
- The templates are validated in the test suite, so they cannot drift from the schema.
- Include one short worked example in the guide (three scenarios, check results, gate table).

## 4. Error messages that teach

- `agentkit validate` prints the file, the key path (`gates[2].metric`) and the allowed values.
- Add a "did you mean" hint for misspelled keys and values.

## 5. Later: `agentkit explain <schema or field>`

- Prints the same descriptions in the terminal. Cheap once step 1 exists.

## Order

Do steps 1 to 3 in one PR after the dataset schema, so the descriptions are written once for all three schemas.
Reference docs stay as Markdown in the repo; a published docs site can come later.
