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

## Compiler follow-ups

- [ ] Confirm how the native CLI resolves a skill `path` (we emit it relative to the harness directory and copy the skill there); no skills are deployed yet
- [ ] Execution role and network settings are not set (the native CDK creates the role, as in v1); revisit if a project needs them
- [ ] `agentkit compile` CLI command (the function exists; the command comes with `deploy`)

## Deploy follow-ups (found while assembling the temporary project)

- [ ] The CDK template from the CLI has no `package-lock.json`, so `npm install` at deploy time is not reproducible; consider vendoring a lockfile
- [ ] The API-key credential provider is named after the credential (`openai-key`) and lives in the account's token vault, so environments in one account share it; revisit when environments get their own accounts
- [ ] The native CLI keeps deployed state in `agentcore/.cli`; confirm on the first real deploy that a second `deploy` from a fresh temp directory updates the same stack
- [x] Write `.env.local` from the SSM SecureString (`/agentkit/<env>/credentials/<name>`) just before `agentcore deploy`
- [ ] Run the first real deploy to dev (`agentkit deploy --env dev --no-assume-role`) and tick the `.cli` state item above
- [ ] The deploy role (`agentkit-dev-deploy`) does not exist yet; until the platform slice creates it, deploy with `--no-assume-role`
