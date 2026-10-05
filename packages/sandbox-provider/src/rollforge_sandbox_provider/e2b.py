from rollforge_sandbox_provider.base import SandboxCapabilities


class E2BProvider:
    name = "e2b"
    capabilities = SandboxCapabilities()

    def environment_config(self) -> dict[str, object]:
        raise NotImplementedError("Validate the pinned Harbor/E2B self-host configuration in S0")
