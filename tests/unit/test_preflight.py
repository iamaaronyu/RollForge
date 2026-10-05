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
