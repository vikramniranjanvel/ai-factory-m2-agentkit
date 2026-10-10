# Pending work

Tick items off as they land. Details of the documentation plan are in `schema-documentation.md`.

## Schema documentation

- [x] Step 1: descriptions, examples and class docstrings on every field (agent definition, evaluation config, dataset); a test fails if one is missing
- [ ] Step 2: generate `docs/reference/<schema>.md` from the JSON Schema (key, type, required or default, description); run it from the pre-commit hook and the CI diff check
- [ ] Step 3: `agentkit init` scaffolds commented `agent.yaml`, `evaluation.yaml` and a sample dataset; the templates are validated in tests; add one worked example (three scenarios, check results, gate table)
- [ ] Step 4: error messages that teach: file, key path and allowed values are done in `agentkit validate`; still to do: "did you mean" for misspelled keys and values
- [ ] Step 5: `agentkit explain <schema or field>` prints the descriptions in the terminal

## Small schema follow-ups

- [ ] Editor header for dataset JSON files (JSON has no comments; would need an optional `$schema` key)
- [ ] `response_contains` and `response_not_contains` read `expected.answer`, which is documented as a regex; decide whether to add a separate plain-text field or a rule
