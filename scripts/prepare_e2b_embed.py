"""下载固定版本官方 E2B Embed 文件；不会启动服务或修改宿主。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

RUNTIME_REVISION = "08436994a1c0bd48a7f03a08c020bf7a4a673dfa"
BASE_URL = f"https://raw.githubusercontent.com/e2b-dev/runtime/{RUNTIME_REVISION}/embed/compose"
FILES = ("compose.yaml", ".env", "README.md")


def prepare(directory: Path) -> None:
    if directory.exists() or directory.is_symlink():
        raise ValueError("目标目录已存在，请使用新的目录以保留原配置")
    # 先下载全部文件；失败时不留下不完整的部署目录。
    payloads = {}
    for name in FILES:
        with urlopen(f"{BASE_URL}/{name}", timeout=30) as response:
            payloads[name] = response.read()
    directory.mkdir(parents=True, mode=0o700)
    records = {}
    for name, data in payloads.items():
        target = directory / name
        with target.open("xb") as stream:
            stream.write(data)
        os.chmod(target, 0o600)
        records[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    (directory / "provenance.json").write_text(
        json.dumps({"runtime_revision": RUNTIME_REVISION, "files": records}, indent=2) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("outputs/e2b-embed"))
    args = parser.parse_args()
    try:
        prepare(args.directory)
    except Exception as exc:
        # 错误信息不包含网络响应、配置内容或密钥。
        print(json.dumps({"status": "prepare_failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"status": "prepared", "revision": RUNTIME_REVISION, "started": False}))
