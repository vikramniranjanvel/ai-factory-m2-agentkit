# __REPO__

__DESCRIPTION__

An AgentKit agent project, scaffolded by `agentkit init`.

## Layout

| Path | What |
|---|---|
| `agent.yaml` | the agent: name, model, prompt, memory |
| `prompts/system.md` | the system prompt |
| `evals/evaluation.yaml` | suites, checks, evaluators and gates |
| `evals/smoke.json`, `evals/dataset.json` | the scenarios of the smoke and full suites |
| `.github/workflows/deploy.yml` | deploys by GitHub event and evaluates |
| `.github/workflows/promote.yml` | manual: evaluate a version, then make it live (or roll back) |
| `scripts/setup.sh` | one-off: GitHub repo, environments, variable, platform registry entry, model keys |

## Working on it

    agentkit validate                       # check the files against the schemas
    agentkit deploy --env dev               # deploy to dev with your AWS credentials
    agentkit evaluate --env dev --suite smoke

## Delivery

| Event | Deploys to | Evaluates |
|---|---|---|
| push to a branch other than `main` | dev | smoke |
| pull request | test | smoke + full |
| push to `main` | prod (candidate) | smoke + full |
| `promote` workflow, run by hand | nothing new | full, then makes the version live |

Deploying does not change what callers get; promoting does. See `docs/delivery.md` in the agentkit repo.

## First time

1. Run `scripts/setup.sh` once (it explains each step before doing it).
2. Merge the registry entry it produces in the platform repo, so the deploy roles trust this repository.
3. Push a branch: it deploys to `dev` and runs smoke.
