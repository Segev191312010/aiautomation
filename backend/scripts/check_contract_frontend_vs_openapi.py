"""Fail when a dashboard runtime API call has no matching FastAPI route.

The checker deliberately reads the current ``app.openapi()`` document rather
than a historical snapshot.  A JSON document can be supplied with
``--openapi`` for deterministic fixtures and forensic checks.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put"})
CLIENT_HELPERS = {
    "del": "DELETE",
    "get": "GET",
    "post": "POST",
    "postWithStatus": "POST",
    "put": "PUT",
}
SOURCE_SUFFIXES = frozenset({".ts", ".tsx"})
IGNORED_SOURCE_PATHS = frozenset({"services/api/client.ts"})


@dataclass(frozen=True, order=True)
class Endpoint:
    method: str
    path: str


@dataclass(frozen=True, order=True)
class CallSite:
    method: str
    path: str
    source: str
    line: int

    @property
    def endpoint(self) -> Endpoint:
        return Endpoint(self.method, self.path)


@dataclass(frozen=True, order=True)
class ScanIssue:
    source: str
    line: int
    message: str


@dataclass(frozen=True)
class ScanResult:
    calls: tuple[CallSite, ...]
    issues: tuple[ScanIssue, ...]


@dataclass(frozen=True)
class ContractReport:
    calls: tuple[CallSite, ...]
    missing: tuple[CallSite, ...]
    issues: tuple[ScanIssue, ...]
    openapi_operation_count: int

    @property
    def ok(self) -> bool:
        return bool(self.calls) and not self.missing and not self.issues

    @property
    def unique_frontend_operation_count(self) -> int:
        return len({call.endpoint for call in self.calls})


def normalize_path(path: str) -> str:
    """Return a structural route path suitable for frontend/OpenAPI matching."""

    path = path.strip()
    query_index = len(path)
    for marker in ("?", "#"):
        marker_index = path.find(marker)
        if marker_index >= 0:
            query_index = min(query_index, marker_index)
    path = path[:query_index]
    path = re.sub(r"\{[^{}\/]+\}", "{param}", path)
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def openapi_endpoints(document: Mapping[str, Any]) -> frozenset[Endpoint]:
    """Extract HTTP operations from an OpenAPI document."""

    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("OpenAPI document does not contain a 'paths' object")

    endpoints: set[Endpoint] = set()
    for raw_path, path_item in paths.items():
        if not isinstance(raw_path, str) or not isinstance(path_item, Mapping):
            continue
        for method in path_item:
            method_name = str(method).lower()
            if method_name in HTTP_METHODS:
                endpoints.add(Endpoint(method_name.upper(), normalize_path(raw_path)))
    return frozenset(endpoints)


def _mask_non_code(source: str) -> str:
    """Mask comments and string literals while preserving indexes/newlines."""

    chars = list(source)
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        next_char = source[index + 1] if index + 1 < length else ""
        if char == "/" and next_char == "/":
            end = source.find("\n", index + 2)
            end = length if end < 0 else end
            for masked_index in range(index, end):
                chars[masked_index] = " "
            index = end
            continue
        if char == "/" and next_char == "*":
            end = source.find("*/", index + 2)
            end = length if end < 0 else end + 2
            for masked_index in range(index, end):
                if chars[masked_index] not in "\r\n":
                    chars[masked_index] = " "
            index = end
            continue
        if char in "'\"`":
            end = _skip_string(source, index)
            for masked_index in range(index, end):
                if chars[masked_index] not in "\r\n":
                    chars[masked_index] = " "
            index = end
            continue
        index += 1
    return "".join(chars)


def _skip_quoted(source: str, start: int) -> int:
    delimiter = source[start]
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == delimiter:
            return index + 1
        index += 1
    return len(source)


def _skip_line_comment(source: str, start: int) -> int:
    end = source.find("\n", start + 2)
    return len(source) if end < 0 else end


def _skip_block_comment(source: str, start: int) -> int:
    end = source.find("*/", start + 2)
    return len(source) if end < 0 else end + 2


def _skip_template(source: str, start: int) -> int:
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == "`":
            return index + 1
        if source.startswith("${", index):
            expression_end = _find_matching_brace(source, index + 2)
            if expression_end is None:
                return len(source)
            index = expression_end + 1
            continue
        index += 1
    return len(source)


def _skip_string(source: str, start: int) -> int:
    return _skip_template(source, start) if source[start] == "`" else _skip_quoted(source, start)


def _find_matching_brace(source: str, start: int) -> int | None:
    depth = 1
    index = start
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if char in "'\"`":
            index = _skip_string(source, index)
            continue
        if char == "/" and next_char == "/":
            index = _skip_line_comment(source, index)
            continue
        if char == "/" and next_char == "*":
            index = _skip_block_comment(source, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _find_matching_paren(source: str, open_index: int) -> int | None:
    depth = 1
    index = open_index + 1
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if char in "'\"`":
            index = _skip_string(source, index)
            continue
        if char == "/" and next_char == "/":
            index = _skip_line_comment(source, index)
            continue
        if char == "/" and next_char == "*":
            index = _skip_block_comment(source, index)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _decode_literal(value: str) -> str:
    """Decode only the simple JavaScript escapes relevant to URL literals."""

    return re.sub(r"\\([\\/'\"`])", r"\1", value)


def _parse_template(source: str, start: int) -> tuple[list[tuple[str, str]], int] | None:
    parts: list[tuple[str, str]] = []
    literal_start = start + 1
    index = literal_start
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == "`":
            parts.append(("literal", _decode_literal(source[literal_start:index])))
            return parts, index + 1
        if source.startswith("${", index):
            parts.append(("literal", _decode_literal(source[literal_start:index])))
            expression_end = _find_matching_brace(source, index + 2)
            if expression_end is None:
                return None
            parts.append(("expression", source[index + 2 : expression_end]))
            index = expression_end + 1
            literal_start = index
            continue
        index += 1
    return None


def _literal_prefixes(expression: str) -> list[str]:
    prefixes: list[str] = []
    index = 0
    while index < len(expression):
        char = expression[index]
        next_char = expression[index + 1] if index + 1 < len(expression) else ""
        if char in "'\"":
            end = _skip_quoted(expression, index)
            if end <= len(expression) and end > index + 1:
                prefixes.append(_decode_literal(expression[index + 1 : end - 1]))
            index = end
            continue
        if char == "`":
            parsed = _parse_template(expression, index)
            if parsed is None:
                return prefixes
            parts, end = parsed
            first_literal = next((value for kind, value in parts if kind == "literal"), "")
            prefixes.append(first_literal)
            index = end
            continue
        if char == "/" and next_char == "/":
            index = _skip_line_comment(expression, index)
            continue
        if char == "/" and next_char == "*":
            index = _skip_block_comment(expression, index)
            continue
        index += 1
    return prefixes


def _expression_is_query_suffix(expression: str) -> bool:
    prefixes = _literal_prefixes(expression)
    non_empty = [prefix.strip() for prefix in prefixes if prefix.strip()]
    return bool(non_empty) and all(prefix.startswith(("?", "#")) for prefix in non_empty)


def _template_path(parts: Iterable[tuple[str, str]]) -> str:
    output: list[str] = []
    query_started = False
    for kind, value in parts:
        if query_started:
            continue
        if kind == "expression":
            if _expression_is_query_suffix(value):
                query_started = True
            else:
                output.append("{param}")
            continue

        stop = len(value)
        for marker in ("?", "#"):
            marker_index = value.find(marker)
            if marker_index >= 0:
                stop = min(stop, marker_index)
        output.append(value[:stop])
        query_started = stop < len(value)
    return normalize_path("".join(output))


def _skip_space(masked_source: str, index: int) -> int:
    while index < len(masked_source) and masked_source[index].isspace():
        index += 1
    return index


def _skip_trivia(source: str, index: int) -> int:
    """Skip whitespace and comments, but stop before a URL literal."""

    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        if source.startswith("//", index):
            index = _skip_line_comment(source, index)
            continue
        if source.startswith("/*", index):
            index = _skip_block_comment(source, index)
            continue
        break
    return index


def _call_open_paren(masked_source: str, identifier_end: int) -> int | None:
    index = _skip_space(masked_source, identifier_end)
    if index < len(masked_source) and masked_source[index] == "<":
        depth = 1
        index += 1
        while index < len(masked_source) and depth:
            if masked_source[index] == "<":
                depth += 1
            elif masked_source[index] == ">" and (
                index == 0 or masked_source[index - 1] != "="
            ):
                depth -= 1
            index += 1
        if depth:
            return None
        index = _skip_space(masked_source, index)
    return index if index < len(masked_source) and masked_source[index] == "(" else None


def _parse_url_argument(source: str, open_paren: int) -> tuple[str | None, int, str | None]:
    start = _skip_trivia(source, open_paren + 1)
    if start >= len(source):
        return None, start, "call has no URL argument"

    if source[start] in "'\"":
        end = _skip_quoted(source, start)
        if end > len(source) or end == len(source) and source[-1] != source[start]:
            return None, end, "URL string is not terminated"
        path = normalize_path(_decode_literal(source[start + 1 : end - 1]))
    elif source[start] == "`":
        parsed = _parse_template(source, start)
        if parsed is None:
            return None, len(source), "URL template is not terminated"
        parts, end = parsed
        path = _template_path(parts)
    else:
        return None, start, "URL argument must be a string or template literal"

    next_index = _skip_trivia(source, end)
    if next_index >= len(source) or source[next_index] not in {",", ")"}:
        return None, end, "URL argument uses an unsupported expression"
    return path, end, None


def _client_helper_bindings(source: str) -> dict[str, str]:
    bindings: dict[str, str] = {}
    masked_source = _mask_non_code(source)
    import_pattern = re.compile(
        r"\bimport\s*\{(?P<items>.*?)\}\s*from\s*(['\"])(?P<module>[^'\"]+)\2",
        re.DOTALL,
    )
    for match in import_pattern.finditer(source):
        if masked_source[match.start() : match.start() + len("import")] != "import":
            continue
        module = match.group("module")
        if not (module == "./client" or module.endswith("/api/client")):
            continue
        for item in match.group("items").split(","):
            item = re.sub(r"/\*.*?\*/", "", item, flags=re.DOTALL).strip()
            item = re.sub(r"^type\s+", "", item)
            alias_parts = re.split(r"\s+as\s+", item)
            imported = alias_parts[0].strip() if alias_parts else ""
            local = alias_parts[1].strip() if len(alias_parts) == 2 else imported
            if imported in CLIENT_HELPERS and re.fullmatch(r"[A-Za-z_$][\w$]*", local):
                bindings[local] = CLIENT_HELPERS[imported]
    return bindings


def _line_number(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _raw_fetch_method(
    source: str, open_paren: int, argument_end: int
) -> tuple[str, str | None]:
    close_paren = _find_matching_paren(source, open_paren)
    if close_paren is None:
        return "GET", "fetch call is not terminated"
    options = source[argument_end:close_paren]
    static_method = re.search(
        r"\bmethod\s*:\s*(['\"`])\s*([A-Za-z]+)\s*\1", options, re.IGNORECASE
    )
    if static_method:
        return static_method.group(2).upper(), None
    if re.search(r"\bmethod\s*:", _mask_non_code(options)):
        return "GET", "fetch method must be a static string literal"
    return "GET", None


def _scan_source(source: str, display_path: str) -> ScanResult:
    masked = _mask_non_code(source)
    calls: list[CallSite] = []
    issues: list[ScanIssue] = []

    candidates = _client_helper_bindings(source)
    candidates["fetch"] = "FETCH"
    for local_name, configured_method in sorted(candidates.items()):
        identifier_pattern = re.compile(rf"(?<![\w$\.]){re.escape(local_name)}\b")
        for match in identifier_pattern.finditer(masked):
            open_paren = _call_open_paren(masked, match.end())
            if open_paren is None:
                continue
            line = _line_number(source, match.start())
            path, argument_end, error = _parse_url_argument(source, open_paren)
            if error:
                issues.append(ScanIssue(display_path, line, f"{local_name}: {error}"))
                continue

            assert path is not None
            if configured_method == "FETCH":
                if not path.startswith("/api/") and path != "/api":
                    continue
                method, method_error = _raw_fetch_method(source, open_paren, argument_end)
                if method_error:
                    issues.append(ScanIssue(display_path, line, f"fetch: {method_error}"))
                    continue
            else:
                method = configured_method
                if not path.startswith("/api/") and path != "/api":
                    issues.append(
                        ScanIssue(display_path, line, f"{local_name}: URL does not resolve under /api")
                    )
                    continue
            calls.append(CallSite(method, path, display_path, line))

    return ScanResult(tuple(sorted(calls)), tuple(sorted(issues)))


def _is_runtime_source(path: Path, frontend_root: Path) -> bool:
    relative = path.relative_to(frontend_root).as_posix()
    if relative in IGNORED_SOURCE_PATHS:
        return False
    if "__tests__" in path.parts:
        return False
    if ".test." in path.name or ".spec." in path.name:
        return False
    return path.suffix in SOURCE_SUFFIXES


def scan_frontend(frontend_root: Path) -> ScanResult:
    frontend_root = frontend_root.resolve()
    calls: list[CallSite] = []
    issues: list[ScanIssue] = []
    for path in sorted(frontend_root.rglob("*")):
        if not path.is_file() or not _is_runtime_source(path, frontend_root):
            continue
        display_path = path.relative_to(frontend_root).as_posix()
        result = _scan_source(path.read_text(encoding="utf-8"), display_path)
        calls.extend(result.calls)
        issues.extend(result.issues)
    return ScanResult(tuple(sorted(calls)), tuple(sorted(issues)))


def check_contract(frontend_root: Path, openapi_document: Mapping[str, Any]) -> ContractReport:
    scan = scan_frontend(frontend_root)
    backend = openapi_endpoints(openapi_document)
    missing = tuple(sorted(call for call in scan.calls if call.endpoint not in backend))
    issues = list(scan.issues)
    if not scan.calls:
        issues.append(ScanIssue(".", 1, "no frontend runtime API calls were found"))
    return ContractReport(
        calls=scan.calls,
        missing=missing,
        issues=tuple(sorted(issues)),
        openapi_operation_count=len(backend),
    )


def load_current_openapi() -> Mapping[str, Any]:
    backend_root = Path(__file__).resolve().parents[1]
    backend_root_text = str(backend_root)
    if backend_root_text not in sys.path:
        sys.path.insert(0, backend_root_text)
    from main import app

    return app.openapi()


def _load_openapi(path: Path | None) -> Mapping[str, Any]:
    if path is None:
        return load_current_openapi()
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, Mapping):
        raise ValueError("OpenAPI JSON root must be an object")
    return document


def _default_frontend_root() -> Path:
    return Path(__file__).resolve().parents[2] / "dashboard" / "src"


def _format_call(call: CallSite) -> str:
    return f"{call.source}:{call.line} {call.method} {call.path}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frontend-root",
        type=Path,
        default=_default_frontend_root(),
        help="dashboard source root (default: repository dashboard/src)",
    )
    parser.add_argument(
        "--openapi",
        type=Path,
        help="OpenAPI JSON file; defaults to the current FastAPI app.openapi()",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    frontend_root = args.frontend_root.resolve()
    if not frontend_root.is_dir():
        print(f"Contract check configuration error: frontend root not found: {frontend_root}")
        return 2

    try:
        document = _load_openapi(args.openapi)
        report = check_contract(frontend_root, document)
    except (ImportError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"Contract check configuration error: {exc}")
        return 2

    if report.ok:
        print(
            "Contract check passed: "
            f"{len(report.calls)} call sites / "
            f"{report.unique_frontend_operation_count} unique frontend operations matched "
            f"{report.openapi_operation_count} OpenAPI operations."
        )
        return 0

    print("Contract check failed.")
    if report.missing:
        print("Missing FastAPI operations:")
        for call in report.missing:
            print(f"  - {_format_call(call)}")
    if report.issues:
        print("Unresolved frontend API calls:")
        for issue in report.issues:
            print(f"  - {issue.source}:{issue.line} {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
