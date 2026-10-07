"""Regression checks for merge artifacts in the backend test suite."""

import ast
from collections import Counter
from pathlib import Path


def test_backend_test_modules_have_unique_top_level_test_names():
    """A duplicate definition silently replaces the earlier collected test."""
    tests_dir = Path(__file__).parent
    duplicates = {}

    for path in sorted(tests_dir.glob("test_*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        counts = Counter(
            node.name
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        repeated = sorted(name for name, count in counts.items() if count > 1)
        if repeated:
            duplicates[path.name] = repeated

    assert duplicates == {}


def test_operator_verification_block_remains_windows_powershell_51_compatible():
    repository = Path(__file__).parents[2]
    runbook = (repository / "docs/milestone-41-liquidity-discovery.md").read_text(
        encoding="utf-8"
    )
    heading = runbook.index("### Exact read-only post-merge verification")
    start = runbook.index("```powershell", heading)
    end = runbook.index("\n```", start)
    block = runbook[start:end]

    assert block.count("ConvertFrom-Json -ErrorAction Stop") == 3
    assert "ConvertFrom-Json -Depth" not in block
    assert block.startswith("```powershell\n& {")
    assert "$Repo = \"C:\\Users\\Juan Estrada\\Projects\\SignalLens\"" in block
    assert "finally {" in block
    assert "research_sha256_after" in block
    assert "production_sha256_after" in block
