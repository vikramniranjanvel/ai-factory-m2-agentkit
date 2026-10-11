# Pending work

This is the one list for all repos. Tick items off as they land. Details of the documentation plan are in `schema-documentation.md`.

Sections are named for the repo the work belongs in: **agentkit** (everything down to "Deploy follow-ups"), **platform**
(`ai-factory-m2-platform`), **project repos** (`support-triage`, `knowledge-assistant`), and **cross-repo** items.

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
- [x] The native CLI keeps deployed state in `agentcore/.cli`; confirmed 2026-10-10: a second `deploy` from a fresh temp directory updated the same stack (`AgentCore-supportTriage-dev`, harness version stayed 1)
- [x] Write `.env.local` from the SSM SecureString (`/agentkit/<env>/credentials/<name>`) just before `agentcore deploy`
- [x] First real deploy to dev done 2026-10-10 (`agentkit deploy --env dev --no-assume-role`): harness `dev_supportTriage` READY
- [x] Invoked the deployed harness once (2026-10-10): the model answered, so the API key and model work end to end
- [x] Deploy through the role (`agentkit-dev-deploy`, created by the platform repo) verified 2026-10-11: `agentkit deploy --env dev` with no `--no-assume-role` updated `AgentCore-supportTriage-dev` with the role's permissions as they are

## Platform: onboarding a project repo (found 2026-10-11)

The flow works: PR a file into the platform's `registry/`, merge, then the repo deploys with `templates/project-deploy.yml`. Gaps:

- [ ] Registry PRs have no checks beyond `cdk synth`: verify the repo exists, the id matches the name, targets are known environments, and project names are unique
- [ ] The project repo's GitHub environment (`dev`) has no branch rule or reviewers, so any PR deploys to dev and two open PRs overwrite each other's stack; set rules when the repo is registered and add reviewers on `test` and `prod`
- [ ] All projects share one deploy role (broad AgentCore and CDK rights), so a project can overwrite another's stack if agent names clash; consider a role per project or a name check
- [ ] API keys are put into SSM by hand, one per credential name, and shared by every project using that name; document who does it, or add a script
- [ ] Looking up the repo id and setting `AWS_ACCOUNT_ID` in each repo are manual; use an org-level variable, or have `agentkit init` do both
- [ ] A registry-only change has not yet been through platform self-deploy (a stack change has)
- [x] Registry (`registry/<project>.json`), per-target trust through GitHub environments and the project workflow template: done and proven with `support-triage` (2026-10-11)

## Platform: CI/CD and environments

- [x] Platform CI applies the stack on merge to `main` through its own OIDC role: done 2026-10-11
- [ ] Required reviewers on the platform repo's GitHub environments for `test` and `prod`
- [ ] The platform deploy role can reach anything the CDK bootstrap roles can (inherent to CDK self-deploy); revisit when accounts are split
- [ ] A new environment's first deploy is run by hand, because its platform role does not exist yet
- [ ] Temporary platform test stack (separate storage, no deletion of retained records)
- [ ] `test` and `prod` environments (config file, registry targets, GitHub environments)
- [ ] Separate dev, test and prod accounts (today one account, separated by resource prefix)

## Platform: infra not built yet

- [x] `infra/evaluation/` (Step Functions + ECS Fargate, results bucket, `eval/*` settings) deployed to dev 2026-10-11; test and prod copies come with those environments
- [ ] `infra/mlflow/` (tracking server, retention protection, `mlflow/tracking-uri`)
- [ ] `agentkit-platform init` scaffolding
- [ ] MCP gateway (out of scope this iteration)

## Agentkit: evaluation and delivery (phases 6 and 7)

- [x] `agentkit evaluate` direct mode: runs in the caller's process with the caller's AWS credentials; temporary pinned `eval_` endpoint, deterministic checks, AgentCore batch scoring, gates, JSON report, stale endpoint sweep
- [x] `agentkit evaluate --remote` through the eval infra (2026-10-11): uploads the project, runs the state machine, reads the report; smoke and full proven on `supportTriage` in dev
- [x] Remote evaluation in the CI workflow template and `support-triage` (2026-10-11): smoke on every run, full on main; both passed in CI
- [ ] Remote runs have no per-agent concurrency guard: two runs of one agent use two endpoints and both score, which is fine, but nothing limits the count
- [ ] Gates are fixed thresholds on a small dataset scored by an LLM (helpfulness moved 0.60 to 1.00 across runs); consider more scenarios or a rerun-on-borderline rule
- [ ] The evaluation image was never built locally (no Docker daemon); the first CI deploy built it. Changing `agentkitRef` in the platform repo is how the evaluating code is updated
- [ ] `agentkit promote`: update the dedicated endpoint to the approved version
- [ ] Endpoint-level smoke test scripts
- [ ] Workflow templates beyond dev deploy: PR to test with smoke and eval, main to prod candidate with approval and dedicated endpoint

## Project repos

- [ ] Port `knowledge-assistant` into its repo (registered, still empty)
- [ ] `support-triage` has the sample agent from the agentkit fixtures; replace with the ported M1 agent and its real datasets

## Cross-repo / hardening (phase 9)

- [ ] Retention protection, cleanup of orphaned `eval_` endpoints, rollback drill, docs
