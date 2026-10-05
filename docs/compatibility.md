# S0 runtime compatibility gate

Status: OFFLINE CONTRACTS VALIDATED; REAL EXECUTION NOT VALIDATED.
Do not enable Hub execution until the following evidence exists.

- Pin Harbor, E2B SDK/runtime and Agent CLI versions/commits.
- Verify Linux/KVM sandbox host, API/data routing, template builds and cleanup.
- Verify internal endpoint protocol matches the chosen Harness (OpenAI-compatible
  does not by itself prove Anthropic Messages compatibility).
- Run two representative Linux single-environment tasks with real tools and verifier.
- Collect success, zero reward, agent crash and verifier error native output fixtures.
- Record raw trajectory, artifact, reward, logs, usage and timing availability.
- Verify credentials are scoped by purpose and verifier secrets are isolated.

Keep private addresses and credentials in local configuration, not this document.
Harbor 0.24.0's published wheel was downloaded, SHA256-verified and inspected.
E2B SDK is pinned to 2.25.0. Python 3.12 is isolated from the control plane.
Six real dependency contract tests pass: two Task/TrialConfig validations,
installed Agent version mappings, release versions and E2B self-host env mapping.
These checks do not start a Sandbox or call inference and are not rollout E2E.

`examples/run_one_trial.py` invokes `await Trial.create(config)` then `await trial.run()`.
The pinned SDK reads E2B_DOMAIN, E2B_API_URL and E2B_SANDBOX_URL from the environment.
Harbor creates templates/sandboxes and tears them down; the platform does not duplicate
that lifecycle. Domain/API routes must be tested against the actual deployment.

Current blocking inputs: self-hosted E2B deployment and credential configuration,
internal model endpoint/protocol, selected Harness (and CLI version when installed).
Runtime release/commit and task base-image digest must also be recorded for real E2E.
See spike.md for commands, evidence semantics and failure-sample collection.
