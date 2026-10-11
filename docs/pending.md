# Pending work

This is the one list for all repos. Tick items off as they land. Details of the documentation plan are in `schema-documentation.md`.

Sections are named for the repo the work belongs in: **agentkit** (schema documentation down to "Deploy follow-ups", and
"Agentkit: evaluation and delivery"), **platform** (`ai-factory-m2-platform`, the "Platform" sections), **project repos**
(`support-triage`, `knowledge-assistant`), and **cross-repo** items. How delivery works is in `delivery.md`.

## Schema documentation

- [x] Step 1: descriptions, examples and class docstrings on every field (agent definition, evaluation config, dataset); a test fails if one is missing
- [ ] Step 2: generate `docs/reference/<schema>.md` from the JSON Schema (key, type, required or default, description); run it from the pre-commit hook and the CI diff check
- [x] Step 3: `agentkit init` scaffolds `agent.yaml`, `evaluation.yaml` and sample datasets; a test validates the scaffold (2026-10-11)
- [ ] Step 3 still open: a worked example in the guide (three scenarios, check results, gate table)
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

- [ ] Registry PRs: `cdk synth` now checks the project name, the file name and the targets; still unchecked: that the repo exists and the id matches it
- [ ] The project repo's GitHub environments `dev` and `test` have no branch rule or reviewers (`prod` is restricted to `main` by `scripts/setup.sh`, and was done by hand for `support-triage`); two open PRs overwrite each other's `test` stack. Required reviewers need a paid GitHub plan
- [x] Role and SSM record per project and environment; a project reads only its own settings and keys (platform branch `project-scoped-access`)
- [ ] The CDK bootstrap and CloudFormation execution roles are still shared, so a project could change another's stack; needs an execution role or permissions boundary per project
- [ ] API keys are one per project, environment and credential name; `scripts/setup.sh` prompts for them, but who owns each key is not decided. `prod`'s `openai-key` is a copy of `test`'s, and the dev/test key was pasted into a chat: rotate it and give `prod` its own
- [x] Looking up the repo id and setting `AWS_ACCOUNT_ID`: done by the scaffolded `scripts/setup.sh`
- [ ] `scripts/setup.sh` is untested against real GitHub and AWS (only its syntax is checked); the registry entry is still merged by hand
- [ ] The workflow files in the init template are copies of `platform/templates/`; no check keeps them equal
- [ ] A registry-only change has not yet been through platform self-deploy (a stack change has)
- [x] Registry (`registry/<project>.json`), per-target trust through GitHub environments and the project workflow template: done and proven with `support-triage` (2026-10-11)

## Platform: CI/CD and environments

- [x] Platform CI applies the stack on merge to `main` through its own OIDC role: done 2026-10-11
- [ ] Required reviewers on the GitHub environments of the platform and project repos: blocked by the GitHub plan (private repos); `prod` is restricted to `main` and the manual promote workflow is the gate
- [ ] The platform deploy role can reach anything the CDK bootstrap roles can (inherent to CDK self-deploy); revisit when accounts are split
- [ ] A new environment's first deploy is run by hand, because its platform role does not exist yet (done for `test` and `prod` 2026-10-11; steps are in the platform README)
- [ ] Temporary platform test stack (separate storage, no deletion of retained records)
- [x] `test` and `prod` environments (2026-10-11): config files, registry targets, GitHub environments, governance and evaluation stacks
- [ ] Separate dev, test and prod accounts (today one account, separated by resource prefix)

## Platform: infra not built yet

- [x] `infra/evaluation/` (Step Functions + ECS Fargate, results bucket, `eval/*` settings) deployed to dev, test and prod (2026-10-11)
- [ ] `infra/mlflow/` (tracking server, retention protection, `mlflow/tracking-uri`)
- [ ] `agentkit-platform init` scaffolding
- [ ] MCP gateway (out of scope this iteration)

## Agentkit: evaluation and delivery (phases 6 and 7)

- [x] `agentkit evaluate` direct mode: runs in the caller's process with the caller's AWS credentials; temporary pinned `eval_` endpoint, deterministic checks, AgentCore batch scoring, gates, JSON report, stale endpoint sweep
- [x] `agentkit evaluate --remote` through the eval infra (2026-10-11): uploads the project, runs the state machine, reads the report; smoke and full proven on `supportTriage` in dev, test and prod
- [x] Remote evaluation in the CI workflow template and `support-triage` (2026-10-11): smoke on every run, full on main; both passed in CI
- [ ] Remote runs have no per-agent concurrency guard: two runs of one agent use two endpoints and both score, which is fine, but nothing limits the count
- [ ] Gates are fixed thresholds on a small dataset scored by an LLM (helpfulness moved 0.60 to 1.00 across runs); consider more scenarios or a rerun-on-borderline rule
- [ ] The evaluation image was never built locally (no Docker daemon); the first CI deploy built it. Changing `agentkitRef` in the platform repo is how the evaluating code is updated
- [x] `agentkit promote`: update the dedicated `live` endpoint to a version (also rollback); `--report` must be a PASS of exactly that version
- [ ] Endpoint-level smoke test scripts
- [x] Workflow templates for the delivery events (2026-10-11): branch to dev, PR to test, main to prod candidate, manual promote workflow; see docs/delivery.md
- [x] `test` and `prod` environments (2026-10-11): stacks, roles, settings, `support-triage` deployed, evaluated and promoted in both
- [x] Promote and rollback proven in prod from CI (2026-10-11): version 2 evaluated (full) and promoted, then rolled back to version 1 with the evaluation skipped; `live` served 2 then 1
- [ ] A deploy of unchanged content creates no new Harness version, so there is nothing to promote; a re-run of main after a no-op change just re-evaluates version N
- [ ] All pull requests share one `test` stack

## Project repos

- [ ] Port `knowledge-assistant` into its repo (registered, still empty)
- [ ] `support-triage` has the sample agent from the agentkit fixtures; replace with the ported M1 agent and its real datasets

## Cross-repo / hardening (phase 9)

- [ ] Retention protection and cleanup of orphaned `eval_` endpoints (rollback drill and docs: done, see `delivery.md`)
- [ ] No AWS budget alert is set (suggested: $25 a month). The account's cost data also shows RDS, ELB and WAF spend that looks like v1; confirm before reading the account total as v2's cost
- [ ] `dev` has no dedicated `live` endpoint (never promoted); only `test` and `prod` do
