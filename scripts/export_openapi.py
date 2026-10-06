"""离线导出真实 API 契约；不连接数据库、不包含鉴权配置值。"""

import argparse
import json
from pathlib import Path

from pydantic import SecretStr
from rollforge_api.main import create_app
from rollforge_common.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    app = create_app(
        Settings(
            _env_file=None,
            api_credentials=SecretStr("[]"),
            control_plane_writes_enabled=False,
            database_url=SecretStr("sqlite+aiosqlite:///:memory:"),
        )
    )
    schema = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output = Path(__file__).resolve().parents[1] / "docs/openapi.json"
    if args.check:
        if not output.exists() or output.read_text() != schema:
            raise SystemExit("OpenAPI 已变化：请运行 make api-types 并提交生成文件")
    else:
        output.write_text(schema)


if __name__ == "__main__":
    main()
