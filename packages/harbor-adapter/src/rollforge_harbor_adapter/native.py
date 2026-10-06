import asyncio
from pathlib import Path
from uuid import uuid4

from rollforge_sandbox_provider.e2b import E2BProvider
from rollforge_schemas.runtime import RuntimeSpec

from rollforge_harbor_adapter.preflight import HARBOR_VERSION, runtime_versions


def build_trial_config(spec: RuntimeSpec, trial_name: str):
    """Use the actual Harbor 0.24.0 configuration model, imported only at runtime."""
    if runtime_versions()["harbor"] != HARBOR_VERSION:
        raise RuntimeError("Pinned Harbor runtime is not installed")
    from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, TrialConfig

    kwargs = (
        {"api_base": spec.model_base_url, "max_turns": 10}
        if spec.agent == "terminus-2"
        else {"version": spec.agent_version}
    )
    if spec.agent == "claude-code":
        kwargs.update(max_turns=10, disable_web_search=True)
    return TrialConfig(
        task=TaskConfig(path=spec.task_dir.resolve()),
        trial_name=trial_name,
        trials_dir=spec.output_dir.resolve(),
        agent=AgentConfig(
            name=spec.agent,
            model_name=spec.model_name,
            kwargs=kwargs,
            override_timeout_sec=spec.timeout_sec,
            override_setup_timeout_sec=spec.timeout_sec,
        ),
        environment=EnvironmentConfig(
            **E2BProvider().environment_config(), force_build=spec.force_build
        ),
    )


async def await_native_trial(trial):
    """Forward cancellation once, then let Harbor finish its owned cleanup.

    Repeated caller cancellation must not interrupt output recovery or abandon
    Harbor's shielded sandbox deletion when the event loop shuts down.
    """
    running = asyncio.create_task(trial.run())
    try:
        return await asyncio.shield(running)
    except asyncio.CancelledError:
        running.cancel()
        while not running.done():
            try:
                await asyncio.shield(running)
            except asyncio.CancelledError:
                continue
            except Exception as error:
                raise asyncio.CancelledError from error
        if not running.cancelled():
            # Retrieve a cleanup error rather than leaving an unobserved task.
            error = running.exception()
            if error is not None:
                raise asyncio.CancelledError from error
        raise


async def run_native_trial(spec: RuntimeSpec) -> Path:
    """Harbor owns cleanup in Trial.run's finally path, including cancellation.

    Do not swallow exceptions or claim that a result's existence means success.
    The process CLI performs preflight and configures canonical env before imports.
    """
    from harbor.trial.trial import Trial

    name = "spike-" + uuid4().hex
    config = build_trial_config(spec, name)
    trial = await Trial.create(config)
    # Include time for provisioning, agent setup, verification and cleanup.
    async with asyncio.timeout(spec.timeout_sec * 4):
        await await_native_trial(trial)
    return spec.output_dir.resolve() / name
