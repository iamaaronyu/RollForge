from pathlib import Path

ok = Path("/app/answer.txt").exists() and Path("/app/answer.txt").read_text() == "RollForge ready\n"
Path("/logs/verifier/reward.txt").write_text("1" if ok else "0")
