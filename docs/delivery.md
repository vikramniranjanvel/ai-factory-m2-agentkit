# Delivery: environments, events, evaluation and promotion

How an agent gets from a branch to production in AI Factory Maturity 2, and what to run when something goes wrong.
Everything here is implemented unless marked as a limit.

## Starting a new agent

    agentkit init myAgent [directory] [--description ...] [--provider bedrock|open_ai|gemini|lite_llm]
                          [--model-id ...] [--credential openai-key] [--repo ai-factory-m2-my-agent]

Creates a valid project from the template in this package (`src/agentkit/templates/agent/`): `agent.yaml`, a system
prompt, `evals/` with a smoke and a full suite and gates, both workflows and a README. A starter agent answers
"ping" with "pong" so the first deploy and evaluation pass; replace the prompt and the scenarios. It refuses a
directory that already has files.

Then run `scripts/setup.sh` in the new project (once; each step checks before it changes anything). It:

1. creates the private GitHub repository and pushes the scaffold;
2. creates the GitHub environments `dev`, `test`, `prod` (`prod` restricted to `main`);
3. sets the variable `AWS_ACCOUNT_ID`;
4. prints the platform registry entry, or writes it with `PLATFORM_DIR=../platform scripts/setup.sh`;
5. asks for the model API key for each environment and stores it in SSM (Enter skips one).

Merge the registry entry in the platform repo: its deploy workflow updates the deploy roles' trust (dev and test on the
merge; prod when the workflow is started by hand). After that, a push to a branch deploys to `dev`.

The two workflow files in the template are copies of `platform/templates/`; when those change, update the copies in
`src/agentkit/templates/agent/.github/workflows/`.

## Environments

`dev`, `test` and `prod` live in one AWS account and region (167769274046, us-east-1), told apart by a resource prefix
(`dev_`, `test_`, `prod_`). Each has its own:

| Part | Where | Name |
|---|---|---|
| Settings | SSM | `/agentkit/<env>/...` (account, region, resource-prefix, deploy-role-arn, eval/state-machine-arn, eval/results-bucket) |
| Model API key | SSM SecureString | `/agentkit/<env>/credentials/openai-key` (put by hand, never in git) |
| Deploy role | IAM | `agentkit-<env>-deploy` (projects), `agentkit-<env>-platform-deploy` (platform CI) |
| Evaluation infra | CloudFormation | `AgentkitEvaluation-<env>`: Step Functions `<env>-agentkit-eval`, Fargate task, results bucket |
| Agent | CloudFormation | `AgentCore-<agent>-<env>`: Harness `<env>_<agent>` |

The platform repo (`ai-factory-m2-platform`) owns the first four rows. Its deploy workflow applies `dev` then `test` on a
push to `main`; `prod` is applied only when the workflow is started by hand. Adding an environment is described in the
platform README.

Harness versions are local to an environment: version 3 in `test` is unrelated to version 3 in `prod`. What moves
between environments is the content (the same commit is deployed again), never a version number.

## What each GitHub event does

A project repo uses two workflows from `platform/templates/`: `project-deploy.yml` and `project-promote.yml`.

| Event | Deploys a candidate to | Evaluates it |
|---|---|---|
| Push to a branch other than `main` | `dev` | smoke |
| Pull request (opened or updated) | `test` | smoke and full |
| Push to `main` (merge) | `prod` | smoke and full |
| Run the promote workflow by hand | nothing new | full (unless rollback), then promotes |

Deploying creates a new Harness version and leaves the dedicated endpoint alone: **callers keep getting the version
that was promoted last** until someone promotes. The evaluation uses a temporary endpoint `eval_<run id>` pinned to
the version just deployed, so it tests that exact version.

Notes:
- A branch with an open pull request deploys twice: to `dev` (push) and `test` (pull request).
- Deploys to one environment run one at a time (`concurrency: deploy-<env>`), so pull requests queue on `test`.
  All pull requests share the one `test` stack, so the latest deploy there is the latest pull request's.
- `prod` deploys only from `main` (the GitHub environment `prod` is restricted to that branch). Required reviewers
  are not available on this GitHub plan, so the manual gate is the promote workflow itself.

## Evaluation

`agentkit evaluate --env <env> --suite smoke|full [--version N] [--remote]`

1. Deterministic checks on each scenario's final reply (`response_contains`, `response_not_contains`,
   `response_matches_regex`; a scenario without the expected value skips the check).
2. For suites with `score: true`, an AgentCore batch evaluation (Helpfulness, GoalSuccessRate, Harmfulness) after
   a 180 second wait for telemetry.
3. Gates from `evals/evaluation.yaml` (`completion_rate`, `deterministic_pass_rate`, `<evaluator>_pass_rate`).

Exit codes: 0 PASS, 1 a gate failed, 2 ERROR (anything unrun or unscored is an error, never a pass).
The JSON report records the dataset hash, the Harness version and the commit.

Direct mode runs in the caller's process and needs rights an engineer has locally. `--remote` is what CI uses:
it uploads the project to the environment's results bucket, starts the state machine, waits and reads the report
(`results/<agent>/<env>/<run id>/report.json`). A full run takes about 14 minutes; smoke about 2.
The LLM-scored gates vary between runs (helpfulness has been 0.60 to 1.00 on the same version), so a borderline
failure is worth one rerun.

## Promotion

`agentkit promote --env <env> [--version N|latest] [--report report.json]`

- Points the environment's dedicated endpoint, named `live`, at the version. Creates it on first use. Waits until the
  endpoint is READY and serving that version.
- `--report` must be a PASS report of the same agent, environment and Harness version, otherwise promote refuses.
  Without `--report` it warns and goes ahead.
- Writes `reports/release-<agent>-<env>.json` (version, previous version, commit, evidence). Promoting the version
  that is already live changes nothing and keeps that manifest.
- A version older than the live one is a rollback.

In CI use the **promote** workflow (Actions, promote, Run workflow, on `main`): choose the environment and version
(default latest). It evaluates that exact version with the full suite and promotes only if it passes. Tick
`rollback` to skip the evaluation and go back to an older version.

### Releasing to production

1. Merge the pull request. The deploy workflow creates the prod candidate and runs smoke and full on it.
2. Check the run is green, then run the promote workflow for `prod` (version latest).
3. Confirm: `aws bedrock-agentcore-control get-harness-endpoint` through boto3, or read the manifest in the
   workflow's `release-reports` artifact.

### Rolling back

Run the promote workflow with the previous version number (see `previous_version` in the last manifest) and
`rollback` ticked. Locally: `agentkit promote --env prod --version <previous>`.

## Secrets and access

- The model API key lives only in SSM (`/agentkit/<env>/credentials/<name>`) and is written to a temporary
  `.env.local` during deploy.
- CI has no stored AWS keys: GitHub OIDC assumes `agentkit-<env>-deploy`, and only for a registered repository in a
  job whose GitHub environment is that target (immutable subjects, repo ids in `registry/`).
- The deploy role can run `bedrock-agentcore:*`, start and read evaluations, and read the environment's settings.
  It cannot read other environments' parameters.

## Cost

Roughly $5 to $15 a month for test and prod together, mostly full evaluations at about $0.10 to $0.30 each
(Fargate, model tokens, AgentCore scoring). Idle environments cost close to nothing. Each pull request update now
runs a full evaluation on `test`, so the number of pushes to open pull requests drives the bill.

## Known limits

- All pull requests share one `test` stack; concurrent pull requests overwrite each other's candidate.
- No required reviewer on `prod` (plan limit); the promote workflow is started by a person on purpose.
- `dev` and `test` are in the same account as `prod` (the design calls for separate accounts).
- Gates are fixed thresholds on a small dataset scored by an LLM.
- No concurrency limit on remote evaluation runs; a killed run leaves an `eval_` endpoint that the next run sweeps
  after 60 minutes.
- The evaluation image installs agentkit at the commit pinned as `agentkitRef` in the platform repo's
  `config/<env>.json`; change it there (and merge) to update the evaluating code.
