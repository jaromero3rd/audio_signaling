"""Parse tracked Python sources without importing or executing project code."""

import ast
from pathlib import Path
import subprocess
import sys


def validate(root: Path) -> int:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "*.py"],
        check=True,
        capture_output=True,
    )
    paths = [name.decode() for name in result.stdout.split(b"\0") if name]
    if not paths:
        raise ValueError("No tracked Python files found.")
    for name in paths:
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("Python source must remain inside the owned worktree.")
        ast.parse(path.read_bytes(), filename=name)
    return len(paths)


if __name__ == "__main__":
    try:
        count = validate(Path.cwd())
    except Exception:
        # Do not echo source lines, credentials, or arbitrary exception messages.
        print("Python syntax validation failed; inspect tracked sources locally.", file=sys.stderr)
        sys.exit(1)
    print(f"Python syntax passed for {count} tracked files; no project code executed.")
