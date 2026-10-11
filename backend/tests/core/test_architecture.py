"""Layering rules of backend/app, checked on the source code."""

import ast
from collections.abc import Iterator
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def modules() -> Iterator[tuple[str, bool, ast.Module]]:
    for path in sorted(APP.rglob("*.py")):
        parts = ("app", *path.relative_to(APP).with_suffix("").parts)
        package = parts[-1] == "__init__"
        yield ".".join(parts[:-1] if package else parts), package, ast.parse(path.read_text(), str(path))


def handlers(tree: ast.Module) -> set[str]:
    """Functions a router serves: decorated with @router.get(...), @router.post(...) and so on."""
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in HTTP_METHODS for decorator in node.decorator_list)}


def imported(tree: ast.Module, module: str, package: bool) -> Iterator[tuple[str, str]]:
    """(module, name) pairs of every import, function-level ones included; relative imports resolved."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = module.split(".")
                anchor = anchor[:len(anchor) - node.level + (1 if package else 0)]
                base = ".".join([*anchor, base] if base else anchor)
            for alias in node.names:
                yield base, alias.name
                yield f"{base}.{alias.name}", ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ""


def test_endpoints_are_not_called_as_functions():
    """Handlers are called by FastAPI only; shared work lives in plain functions they delegate to."""
    served = {module: handlers(tree) for module, _, tree in modules()}
    offenders = sorted(f"{module} imports {source}.{name}" for module, package, tree in modules()
                       for source, name in imported(tree, module, package) if name in served.get(source, ()))
    assert not offenders


def test_route_modules_are_only_included_by_main():
    """Other modules import the subdomain module that owns a helper, never a package's routes.py."""
    aggregators = {module for module, _, _ in modules() if module.endswith(".routes")}
    offenders = sorted(f"{module} imports {source}" for module, package, tree in modules() if module != "app.main"
                       for source, _ in imported(tree, module, package) if source in aggregators)
    assert not offenders
