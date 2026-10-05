import importlib.util
import json
from pathlib import Path

ok = False
try:
    spec = importlib.util.spec_from_file_location("solution", "/app/solution.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = json.loads(Path("/app/report.json").read_text())
    ok = (
        module.sum_even([1, 2, 3, 4]) == 6
        and module.sum_even([]) == 0
        and module.sum_even([-4, -3, 0, 6]) == 2
        and report == {"implemented": True}
    )
except Exception:
    pass
Path("/logs/verifier/reward.txt").write_text("1" if ok else "0")
