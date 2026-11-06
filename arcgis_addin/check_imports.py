"""배포 Python에서 판독 코드의 모든 import(함수 안 지연 import 포함)가 되는지 확인한다.

  <배포 python.exe> -s check_imports.py <app 폴더>
arcpy·arcgis(Pro 전용), osgeo(Pro Python의 형식 변환 보조 스크립트), winreg(Windows 표준)는 건너뛴다.
"""
import ast
import importlib
import sys
from pathlib import Path

SKIP = {"src", "arcgis", "arcpy", "winreg", "osgeo"}

app = Path(sys.argv[1])
names = set()
for py in (app / "src").rglob("*.py"):
    for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
missing = []
for name in sorted(names):
    if name.split(".")[0] in SKIP or name.split(".")[0] in sys.stdlib_module_names:
        continue
    try:
        importlib.import_module(name)
    except Exception as e:  # noqa: BLE001 - 어떤 이유로든 못 불러오면 누락으로 본다
        missing.append(f"{name}: {type(e).__name__}: {e}")
print("\n".join(missing) if missing else "imports ok")
sys.exit(1 if missing else 0)
