import pytest
from pydantic import ValidationError
from rollforge_common.settings import Settings


def test_global_execution_limit_defaults_to_one_and_is_bounded():
    assert Settings(_env_file=None).global_execution_limit == 1
    assert Settings(_env_file=None, global_execution_limit=16).global_execution_limit == 16
    for value in (0, 257):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, global_execution_limit=value)
