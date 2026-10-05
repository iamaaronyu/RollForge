from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SandboxCapabilities:
    pause: bool = False
    resume: bool = False
    snapshot: bool = False
    fork: bool = False


class SandboxProvider(Protocol):
    """Configuration boundary; Harbor owns sandbox creation and teardown.

    Concrete providers must be backed by tested Harbor Environment adapters.
    """

    name: str
    capabilities: SandboxCapabilities

    def environment_config(self) -> dict[str, object]: ...
