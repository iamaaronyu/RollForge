# S0 runtime validation workflow

The runtime uses Harbor 0.24.0, E2B SDK 2.25.0 and Python 3.12+. It is a separate
uv project to avoid pulling Harbor and serving SDK dependencies into the API process.
The runtime lockfile pins transitive packages; update it deliberately and rerun checks.

## 1. Install and check actual library contracts

From the repository root:

```sh
make runtime-install
make runtime-check
cp .env.spike.example .env.spike
```

`runtime-check` uses real installed Task, TrialConfig and E2B configuration models.
It makes no network requests and does not qualify as end-to-end execution.

## 2. Configure the environment locally

Fill in `.env.spike`, which is ignored by Git. Choose one matched pair:

| Harness | Required model protocol | Credential |
|---|---|---|
| terminus-2 | openai-chat-completions | OPENAI_API_KEY |
| codex | openai-responses | OPENAI_API_KEY |
| claude-code | anthropic-messages | ANTHROPIC_API_KEY |

Terminus is pinned by the Harbor release; installed Harnesses require an explicit
ROLLFORGE_SPIKE_AGENT_VERSION. The matrix is the scope supported by this spike;
it is not a guarantee about every model/provider combination. For Terminus use
`openai/<serving-name>` so LiteLLM selects the intended endpoint protocol.

Set ROLLFORGE_SPIKE_MODEL_BASE_URL to the appropriate API root. Do not embed keys
in URLs. The script uses the verified `api_base` option for Terminus, OPENAI_BASE_URL
for Codex or ANTHROPIC_BASE_URL for Claude Code. It does not implement a protocol gateway.

Set E2B_DOMAIN and E2B_API_KEY for the self-hosted deployment. SDK defaults to
`https://api.<domain>`; if the deployment needs a different route, set E2B_API_URL
and, if necessary, E2B_SANDBOX_URL locally. Confirm SDK/runtime compatibility,
DNS/TLS and sandbox data routing with the infrastructure owner.

No model key is passed as environment-wide Sandbox config by RollForge. Installed
Harnesses receive their canonical credentials through Harbor; verifier credentials
are not configured here. The sample tasks contain public tests and do not prove
private-test or verifier-secret isolation.

## 3. Run offline preflight

```sh
make spike
```

The default command checks required fields, protocol choice, pinned dependencies,
task file presence, content digest and actual Harbor task/config parsing. It does
not check network reachability. Missing configuration exits with code 2 and reports
field/variable names, never their values. `offline_preflight_passed` means only that
offline checks passed.

## 4. Execute both representative tasks

```sh
uv run --project integration/harbor-runtime python examples/run_one_trial.py --run
uv run --project integration/harbor-runtime python examples/run_one_trial.py \
  --task examples/tasks/coding-task --run
```

`--run` creates a real Sandbox, executes an Agent, calls the configured inference
endpoint and runs the verifier. Harbor owns the lifecycle. No automatic retry is
implemented in S0. The wrapper cancels an overlong trial; orphan cleanup must still
be verified against the deployment, rather than inferred from a completed process.

Native output and `rollforge-evidence.json` remain under ignored `outputs/spike`.
New output directories use mode 0700. Review local output before sharing; raw logs
and artifacts may still contain sensitive content. The tool does not upload evidence.

Evidence records installed dependency versions, canonical task digest, a checksummed
native output manifest and a versioned summary. The manifest is captured before the
evidence file itself is written. Unknown token/timing fields stay null.

`SCORED` means a finite reward map exists, including reward zero. It does not mean
the task passed. `RUNTIME_FAILED` preserves an exception type without printing raw
messages; `UNVERIFIED` means no score exists. Check reward values, trajectory and
artifact presence before accepting a successful S0 run.

## 5. Collect failure evidence and complete the gate

Keep successful and zero-score task outputs. In the isolated test deployment,
exercise Agent process failure and verifier failure separately. Record which phase
failed, available partial logs, whether a score exists and whether the sandbox was
destroyed. Never fake these outputs or alter passing tests to report E2E success.

Record E2B Runtime release/commit, image digest, actual Harness version, model
deployment/version reference and cleanup evidence. Example Dockerfiles use a mutable
development base-image tag; pin the resolved digest before claiming full reproducibility.

S0 is complete only when real self-hosted E2B + inference + Agent + verifier runs,
failure samples and cleanup evidence pass review. The Hub remains execution-disabled
until S1's authenticated persistent execution protocol is ready.
