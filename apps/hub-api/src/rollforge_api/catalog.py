"""由部署者审核、启动时冻结的任务绑定；不是通用 Registry。"""

import json
import os
import stat

from rollforge_schemas.api import ApprovedTaskBinding


def load_catalog(path):
    if path is None:
        return {}
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("目录配置必须为私密普通文件")
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("目录配置超限")
        raw = json.loads(data)
        if not isinstance(raw, list) or len(raw) > 100:
            raise ValueError("目录配置必须为最多 100 项的数组")
        items = [ApprovedTaskBinding.model_validate(item) for item in raw]
        if any(item.snapshot.runtime is None for item in items):
            raise ValueError("已审核任务需要运行绑定")
        catalog = {item.id: item for item in items}
        if len(catalog) != len(items):
            raise ValueError("任务标识重复")
        return catalog
    except (OSError, ValueError, TypeError):
        raise ValueError("已审核任务配置无效；检查私密文件、契约和重复标识") from None
