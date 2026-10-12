# Developer guide: build, evaluate and deploy an agent

For someone writing an agent. Platform maintenance (environments, roles, evaluation infrastructure) is in the platform
repo's `docs/maintaining.md`. How code moves from a branch to production is in [delivery.md](delivery.md); this guide
shows what you do and refers to it for the reasons.

## The idea in one minute

- An agent is a small repo: `agent.yaml` (model, prompt, memory), a system prompt, and `evals/` (scenarios and pass
  rules). No application code.
- `agentkit` validates it, deploys it to AWS Bedrock AgentCore as a **Harness** and evaluates it.
- There are three environments, `dev`, `test` and `prod`. GitHub does the deploying: what you push decides where it goes.
- **Deploying never changes what callers get.** Callers use the environment's dedicated `live` endpoint, which only
  moves when someone **promotes** a version.

## Set up your machine

- `uv` (Python), Node 22 or newer, and the native AgentCore CLI: `npm install -g @aws/agentcore@0.31.1` (this exact version).
- AgentKit: `uv tool install "git+https://github.com/vikramniranjanvel/ai-factory-m2-agentkit"` (the repo is public).
- AWS credentials for the account that the platform trusts for `dev` (your project's deploy role `agentkit-dev-<project>-deploy` is assumed for you). `test` and `prod` deploy only from CI.
  CI needs no keys: it uses GitHub OIDC.

## Your first agent, end to end

Do these in order. Steps 5 and 6 need someone on the platform side (if that is you, it is two small pull requests).

1. **Install** (see above) and check `agentkit --help` and `agentcore --version` (0.31.1).
2. **Create it.**

       agentkit init myAgent --description "Answers questions about orders"
       cd my-agent

   Name: letters and digits, starting with a letter, up to 32 characters. Options: `--provider bedrock|open_ai|gemini|lite_llm`,
   `--model-id`, `--credential` (name of the model API key in SSM; not for bedrock), `--repo`. The starter agent answers
   "ping" with "pong", so the first deploy passes.
3. **Make it yours.** Edit `prompts/system.md`, `agent.yaml` and the scenarios in `evals/`, then `agentkit validate`
   (offline, instant). Keep the "ping"/"pong" scenario until you have seen one green run.
4. **Create the GitHub repo and environments, once:** `PLATFORM_DIR=../platform scripts/setup.sh`. It creates and pushes the
   repo, creates the environments `dev`, `test` and `prod` (`prod` only deploys from `main`), sets `AWS_ACCOUNT_ID`, writes
   `registry/<project>.json` into your platform checkout (without `PLATFORM_DIR` it prints the entry) and asks for the model key per
   environment (Enter skips one). The `project` field in `agent.yaml` is the registry name.
5. **Register the project.** In the platform repo, open a pull request with `registry/<project>.json` and merge it.
   Dev and test are applied by the merge; prod trust is applied only when someone runs the platform's `deploy` workflow by hand
   (Actions, deploy, Run workflow on `main`). Also update the project list in the platform's
   `infra/governance/test/environment-stack.test.ts`, or CI fails. Until this is done the deploy job cannot assume its AWS role,
   so the first run triggered by the setup push to `main` fails: re-run it afterwards.
6. **Make sure the model key exists** for each environment you will use (`dev`, `test`, `prod`), at
   `/agentkit/<env>/projects/<project>/credentials/<name>` (the setup script offers to store it).
7. **First deploy from your machine:**

       agentkit deploy --env dev
       agentkit evaluate --env dev --suite smoke      # expect: OUTCOME: PASS

   The first deploy takes a few minutes and creates the stack `AgentCore-<name>-dev` and the Harness `dev_<name>`.
8. **Ship through GitHub.** Push a branch (deploys to `dev`), open a pull request (deploys to `test`, runs smoke and full), merge
   (deploys a prod candidate), then run **promote** ("Release to production" below). A prod candidate serves nobody until it is promoted.

## What is in the repo

| File | Purpose |
|---|---|
| `agent.yaml` | the agent definition |
| `prompts/system.md` | system prompt, referenced by `agent.yaml` |
| `evals/evaluation.yaml` | suites, checks, evaluators, gates |
| `evals/smoke.json` | smoke scenarios |
| `evals/dataset.json` | full scenarios |
| `.github/workflows/deploy.yml`, `promote.yml` | delivery (copied from the template; normally untouched) |

### agent.yaml

    schema_version: 1
    project: support-triage        # the platform registry entry; names the deploy role and the SSM record
    name: supportTriage            # becomes the Harness name <env>_supportTriage
    description: ...
    model:
      provider: open_ai            # bedrock | open_ai | gemini | lite_llm
      model_id: gpt-6-luna
      credential: openai-key       # SSM key /agentkit/<env>/projects/<project>/credentials/openai-key; omit for bedrock
    prompt: prompts/system.md
    memory: {mode: disabled}       # or managed (AgentCore memory, with strategies)

Also available: `limits` (iterations, timeout, tokens), `truncation`, `skills`, `tools` (MCP tools you request: `- server: kb-search` with `tools: [search]`, explicit names only; the platform registry must grant them, wiring into deploys comes later), `tags`, and model sampling
settings (`temperature`, `top_p`, `max_tokens`). Every field has a description; use the schema in your editor
(`schemas/agent-definition.schema.json`, already referenced by the first line of the generated file).
Unknown keys are errors. See also [schema-documentation.md](schema-documentation.md).

### Scenarios (`evals/*.json`)

    {"schema_version": 1, "scenarios": [
      {"scenario_id": "full-001-upload-limit",
       "turns": [{"input": "TCK-1042: uploads fail for files above 83 MB"}],
       "expected": {"answer": "(?i)split"},
       "assertions": ["The agent says to split the file, with priority P3"]}
    ]}

- `turns`: user messages sent in order in one session.
- `expected.answer`: a regular expression matched against the **final reply**. This is the deterministic check;
  scenarios without it skip the check.
- `assertions`: plain-language statements the LLM judge uses for **GoalSuccessRate** (full suite only).
- Smoke scenarios should be few and deterministic (they run on every push). Put judgement-based scenarios in `full`.

### evaluation.yaml

- `suites`: `smoke: {dataset: smoke.json}` and `full: {dataset: dataset.json, score: true}` (`score: true` adds the
  AgentCore evaluators).
- `checks`: deterministic checks applied to every scenario that has `expected.answer`
  (`response_matches_regex`, `response_contains`, `response_not_contains`).
- `evaluators`: `Builtin.Helpfulness`, `Builtin.GoalSuccessRate`, `Builtin.Harmfulness`. Default minimum scores per
  scenario: 0.67, 1.0, 1.0 (override with `min_score`).
- `gates`: the run passes only if every gate holds. Metric names: `completion_rate`, `deterministic_pass_rate`,
  `<evaluator in snake case>_pass_rate` (for example `goal_success_rate_pass_rate`). Gates for scores that were not
  produced (for instance on the smoke suite) are not applied.

The LLM-judged rates move between runs (helpfulness has varied 0.60 to 1.00 for the same version). Do not set a gate
at the edge of what the agent usually scores; with few scenarios one answer changes the rate by 0.2.

## Daily loop

    agentkit validate                              # schemas and datasets, offline, fast
    agentkit deploy --env dev                      # deploy your working copy to dev (as your project's deploy role)
    agentkit evaluate --env dev --suite smoke      # runs here, with your AWS credentials
    agentkit evaluate --env dev --suite full       # also scores with AgentCore (about 8 to 14 minutes)

Evaluation creates a temporary endpoint `eval_<id>` pinned to the Harness version, sends every scenario, checks the
replies, scores the sessions (the full suite waits 180 s for telemetry first), applies the gates, deletes the endpoint
and writes `reports/<agent>-<env>-<suite>-<time>.json`. Exit code 0 = PASS, 1 = a gate failed, 2 = error (anything
that could not be run or scored is an error, never a pass). Add `--remote` to run it in the environment's evaluation
infrastructure instead, as CI does.

Local deploys to `dev` share the stack with every branch push, so someone else's push can replace your version.

## What happens when you push

| You do | Result |
|---|---|
| push a branch (not `main`) | deploys to `dev`, runs smoke |
| open or update a pull request | deploys to `test`, runs smoke and full (it should be green before you merge) |
| merge to `main` | deploys a **prod candidate**, runs smoke and full; `live` is untouched |
| run the **promote** workflow | evaluates the chosen version (full), then makes it live |

Reports are attached to each run as the `evaluation-reports` artifact. All pull requests share one `test` stack, so two
open pull requests overwrite each other's candidate (their runs queue one after the other).

Deploying content identical to what is already deployed creates **no new version**. To release you need a real change.

## Release to production

1. Merge your pull request and wait for the `main` run to go green.
2. GitHub, repo, Actions, **promote**, Run workflow: branch `main`, env `prod`, version `latest`, `rollback` unticked.
3. The workflow evaluates that exact version and promotes only on PASS. The manifest `release-<agent>-prod.json`
   (version, previous version, commit, evidence) is attached to the run as `release-reports`.

You can do the same from a terminal with
`agentkit promote --env prod --version N --report reports/<report>.json`. The report must be a PASS of the same
agent, environment and version; without `--report`, promote warns and goes ahead.

## Roll back

Run **promote** with env `prod`, the previous version number (the manifest's `previous_version`) and `rollback`
ticked. No evaluation runs (that version was evaluated when first promoted). It takes about two minutes.

## Commands

| Command | What it does |
|---|---|
| `agentkit init <name> [dir]` | create a project from the template |
| `agentkit validate [path]` | check the files against the schemas |
| `agentkit deploy --env E [--no-assume-role]` | deploy to environment E |
| `agentkit evaluate --env E [--suite S] [--version N] [--remote] [--report FILE]` | evaluate; exit 0/1/2 |
| `agentkit promote --env E [--version N\|latest] [--report FILE]` | make a version live (older = rollback) |
| `agentkit cleanup-endpoint --env E --run-id ID` | delete one run's temporary endpoint (the evaluation infrastructure uses this) |

`--no-assume-role` is for CI, where the OIDC session already is the project's deploy role. `AGENTKIT_SSM_PREFIX` changes the
SSM prefix (default `/agentkit`).

## When something fails

| Message or symptom | Cause and fix |
|---|---|
| `Could not assume role with OIDC: Request ARN is invalid` in CI | the repo variable `AWS_ACCOUNT_ID` is not set (`scripts/setup.sh` sets it); set it and re-run |
| `API key not found in SSM: /agentkit/<env>/projects/<project>/credentials/<name>` | the model key is missing for that environment; put it as a SecureString (the setup script prompts for it) |
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` in CI | the repo is not registered for that target in the platform repo, or the job's GitHub environment name is not the target |
| `no Harness named '<env>_<agent>' ... Deploy it first` | evaluating or promoting an agent that was never deployed to that environment |
| `Harness version N is ..., not READY` | the deployment is still in progress or failed; check the stack `AgentCore-<agent>-<env>` |
| `cannot assume ...agentkit-<env>-<project>-deploy: ... not registered` | the project is not registered for that environment in the platform repo, or your identity is not trusted there (`test` and `prod` deploy only from CI) |
| `no settings for project '<project>' in <env>` | the project's record is missing: it is not registered for that environment, or the platform stack is not deployed; ask the platform owner |
| `unknown environment` | `--env` must be `dev`, `test` or `prod` |
| `--remote` needs `eval/state-machine-arn` | same: the environment's evaluation stack is missing |
| Gate fails only on helpfulness, by one scenario | LLM variance; rerun once. If it keeps failing, fix the prompt or the scenario rather than lowering the gate |
| Helpfulness gate fails on an agent that is supposed to refuse most questions | the judge scores a correct refusal as unhelpful. Keep the refusals enforced by a regex in `expected.answer` and set a lower helpfulness gate for that agent (for example 0.5), recording why in `evaluation.yaml` |
| Outcome ERROR | something could not be run or scored (model error, missing telemetry, scoring job failed); read `error` in the report |
| Promote says the report is for another version | evaluate again with `--version N` for the version you are promoting |
| Leftover endpoint named `eval_...` | a killed run; the next evaluation sweeps endpoints older than 60 minutes |

## Limits you should know about

- No required reviewer gates `prod` (a GitHub plan limit): the promote workflow, started by a person, is the gate.
- `dev`, `test` and `prod` share one AWS account, separated by the resource prefix.
- The model API key is one SSM value per project, environment and credential name; a project reads only its own.
