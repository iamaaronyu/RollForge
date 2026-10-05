# S0 runtime compatibility gate

Status: NOT VALIDATED. Do not enable execution until the following evidence exists.

- Pin Harbor, E2B SDK/runtime and Agent CLI versions/commits.
- Verify Linux/KVM sandbox host, API/data routing, template builds and cleanup.
- Verify internal endpoint protocol matches the chosen Harness (OpenAI-compatible
  does not by itself prove Anthropic Messages compatibility).
- Run two representative Linux single-environment tasks with real tools and verifier.
- Collect success, zero reward, agent crash and verifier error native output fixtures.
- Record raw trajectory, artifact, reward, logs, usage and timing availability.
- Verify credentials are scoped by purpose and verifier secrets are isolated.

Keep private addresses and credentials in local configuration, not this document.
Deliver an executable run-one-trial example only after inspecting the selected
Harbor version's real API. No example here claims to be runnable against Harbor yet.
