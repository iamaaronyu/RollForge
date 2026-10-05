from rollforge_sandbox_provider.base import SandboxCapabilities


class E2BProvider:
    name = "e2b"
    capabilities = SandboxCapabilities()

    def environment_config(self) -> dict[str, object]:
        # SDK reads E2B_DOMAIN/E2B_API_KEY at runtime; Hub never creates the sandbox.
        return {"type": "e2b", "delete": True}
