import ast
import io
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_application_layer_has_no_framework_or_database_dependencies(self) -> None:
        forbidden = {"fastapi", "sqlalchemy", "app.db", "app.infrastructure"}
        violations: list[str] = []
        for path in sorted((APP / "application").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    modules = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = (node.module,)
                for module in modules:
                    if any(module == item or module.startswith(f"{item}.") for item in forbidden):
                        violations.append(f"{path.name}:{node.lineno}:{module}")
        self.assertEqual(violations, [])

    def test_python_application_code_contains_no_comments(self) -> None:
        violations: list[str] = []
        for path in sorted(APP.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.COMMENT:
                    violations.append(f"{path.relative_to(ROOT)}:{token.start[0]}")
        self.assertEqual(violations, [])

    def test_web_layer_does_not_parse_untyped_json(self) -> None:
        violations = [
            str(path.relative_to(ROOT))
            for path in sorted((APP / "web").rglob("*.py"))
            if "request.json(" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(violations, [])

    def test_composition_root_stays_small(self) -> None:
        lines = (APP / "main.py").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 40)


if __name__ == "__main__":
    unittest.main()
