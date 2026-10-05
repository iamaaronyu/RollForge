from rollforge_harbor_adapter.preflight import preflight
from rollforge_schemas.runtime import RuntimeSpec


def test_missing_credentials_report_names_not_values(tmp_path):
    spec = RuntimeSpec(
        agent="terminus-2",
        protocol="openai-chat-completions",
        model_name="openai/test",
        model_base_url="https://example.invalid/v1",
        task_dir=tmp_path,
        output_dir=tmp_path / "outputs",
    )
    errors = preflight(spec, {"E2B_API_KEY": "do-not-print-this"})
    assert "Missing local OPENAI_API_KEY" in errors
    assert "Missing local E2B_DOMAIN" in errors
    assert any("outside" in error for error in errors)
    assert "do-not-print-this" not in str(errors)


def test_default_cloud_domain_does_not_pass_self_host_gate(tmp_path):
    spec = RuntimeSpec(
        agent="terminus-2",
        protocol="openai-chat-completions",
        model_name="openai/test",
        model_base_url="https://example.invalid/v1",
        task_dir=tmp_path,
        output_dir=tmp_path.parent / "outputs",
    )
    assert any("self-hosted" in error for error in preflight(spec, {"E2B_DOMAIN": "e2b.app"}))


def test_embed_endpoint_pair_does_not_require_domain(tmp_path):
    from rollforge_harbor_adapter.preflight import spec_from_env

    spec = spec_from_env({}, tmp_path, tmp_path.parent / "output")
    assert spec.agent == "claude-code"
    assert spec.model_name == "deepseek-flash"
    env = {
        "E2B_API_KEY": "local-test-only",
        "E2B_API_URL": "http://127.0.0.1:3300",
        "E2B_SANDBOX_URL": "http://127.0.0.1:3302",
        "ANTHROPIC_AUTH_TOKEN": "local-test-only",
    }
    errors = preflight(spec, env)
    assert not any("E2B_DOMAIN" in error or "ANTHROPIC" in error for error in errors)
    env["E2B_SANDBOX_URL"] = "http://user:secret@localhost:3302"
    assert "Invalid self-hosted E2B_SANDBOX_URL" in preflight(spec, env)
    del env["E2B_SANDBOX_URL"]
    assert "Configure both E2B_API_URL and E2B_SANDBOX_URL" in preflight(spec, env)
    env["CLAUDE_FORCE_OAUTH"] = "1"
    assert any("conflicting CLAUDE_FORCE_OAUTH" in error for error in preflight(spec, env))
