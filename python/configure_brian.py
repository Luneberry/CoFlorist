"""Keep Brian2's Windows import-time CPU cache inside the project venv.

Brian2 2.9 unconditionally creates ~/.brian before its public preferences can be
configured. Only the private project environment is patched, never a system or
bundled runtime. Normal external Python environments do not need this patch.
"""
from pathlib import Path
import sys
import sysconfig

root = Path(__file__).resolve().parents[1]
if Path(sys.prefix).resolve() != (root / ".venv").resolve():
    raise SystemExit("This compatibility patch only applies to the project's .venv.")
target = Path(sysconfig.get_paths()["purelib"]) / "brian2" / "codegen" / "cpp_prefs.py"
source = target.read_text(encoding="utf-8")
original = 'user_dir = os.path.join(os.path.expanduser("~"), ".brian")'
patched = 'user_dir = os.environ.get("COFLORIST_BRIAN_CACHE", os.path.join(os.path.expanduser("~"), ".brian"))'
if patched not in source:
    if original not in source:
        raise SystemExit("Brian2 cache code changed; inspect before applying the compatibility patch.")
    target.write_text(source.replace(original, patched, 1), encoding="utf-8")
print("Brian2 project-cache compatibility configured.")
