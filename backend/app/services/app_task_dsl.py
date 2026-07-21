"""Declarative step-queue DSL for the platform "tasks" capability.

Apps ship no server code, so background work is a validated JSON program —
never arbitrary functions (ECOSYSTEM.md §5.2). This module is the pure part:
definition validation, reference/template resolution, conditions, and the
llm chunking/validation policies. Execution lives in app_task_service.py.

Task definition (all keys camelCase, matching the manifest):

    {
      "name": "paperless-sync",
      "schedule": {"everyMinutes": 60},            // optional → recurring
      "concurrency": 2,                            // item worker pool (1..cap)
      "setup": [ <step>, ... ],                    // once, sequential
      "items": [{"vars": {...}}, ...]              // literal item list, OR
      "items": {"from": "$setup.docs.items",       // fan-out over a step output
                                                   // (or "fromEach": [$ref, …] —
                                                   //  lists concatenated)
                "vars": {"id": "{item.id}"},
                "limit": 500,
                "skipIfStored": "synced/{item.id}"},   // dedup fast path
      "steps": [ <step>, ... ],                    // per item, sequential
      "finally": [ <step>, ... ]                   // once, after all items
    }

Steps carry exactly one type key plus optional "id" (context name) and
"when" (condition — false skips the step, its id resolves to null):

    {"id": "docs", "http": {"method": "GET", "url": "{config.BASE_URL}/api/…",
        "headers"?, "body"?, "contentType"?, "responseType"?: "json"|"text",
        "auth"?: {"bearer": "$token.body.access_token"} | {"basic": "…"},
        "paginate"?: {"items": "results", "next": "next",
                       "maxPages"?: 20, "keyBy"?: "id"}}}
    {"id": "listing", "webdav": {"url": "{config.BASE_URL}/remote.php/dav/…",
        "depth"?: 0|1|"infinity", "auth"?: <as http>}}      // PROPFIND listing,
        // multistatus XML parsed server-side → {items: [{href, name, etag,
        // lastModified, size, contentType, isDir, fileId?}], count}
    {"cortex": {"method": "POST", "path": "upload?start_processing=true",
        "body"?, "multipart"?: {"content": "$md.text", "filename": "x.md",
                                  "field"?: "file", "contentType"?}
                | {"fromUrl": "{item.url}", "filename": "x.pdf",   // binary
                    "method"?: "GET"|"POST", "headers"?, "auth"?,   // passthrough:
                    "field"?, "contentType"?}}}                     // fetch → upload,
        // bytes never enter the step context (same http gates as http steps)
    {"llm": {"prompt": "…{chunk}…", "system"?, "input"?: "$transcribe.body.transcript",
        "chunk"?: {"words": 1000}, "maxTokens"?, "temperature"?,
        "validate"?: {"minLengthRatio": 0.5, "minWordOverlap": 0.6,
                       "onFail": "keepOriginal" | "fail"}}}
    {"store": {"get": "key"} | {"put": "key", "value": <any>}
             | {"delete": "key"} | {"list": "prefix", "limit"?}}
    {"template": {"text": "…"} | {"lines": ["…", {"text": "…", "when": <cond>}],
        "joiner"?: "\\n"}}
    {"skipItem": {"when": <cond>, "reason"?: "…"}}       // item steps only

References ("$" prefix) resolve raw values by dotted path against the run
context: $vars.* $setup.<id>.* $steps.<id>.* $run.* $config.* — a bare first
segment falls back to a step id in scope ($full ≡ $steps.full). Numeric
segments index arrays. Ref paths may embed templates for dynamic lookups:
$setup.tags.map.{full.body.correspondent}.name

Templates interpolate "{path|filter|filter:arg}" into strings; {{ and }}
escape literal braces. Filters: slug lower upper trim ext json urlencode
default:<arg> join:<sep> pluck:<field> slice:<n> truncate:<n>.

Conditions: {"empty": v} {"notEmpty": v} {"found": v} {"eq": [a,b]}
{"neq": [a,b]} {"gt": [a,b]} {"lt": [a,b]} {"contains": [a,b]}
{"and": [...]} {"or": [...]} {"not": <cond>} — operands are refs, templates,
or literals.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger(__name__)

STEP_TYPES = ("http", "webdav", "cortex", "llm", "store", "template", "skipItem")
_CONDITION_OPS = ("empty", "notEmpty", "found", "eq", "neq", "gt", "lt",
                  "contains", "and", "or", "not")
_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
_RESERVED_ROOTS = ("vars", "setup", "steps", "run", "config", "item", "chunk")


class TaskDefinitionError(ValueError):
    """Task definition rejected — carries every issue found."""

    def __init__(self, issues: List[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


class StepError(RuntimeError):
    """A step failed at execution time (bad ref, upstream error, cap hit)."""


class SkipItem(Exception):
    """Control flow: a skipItem step matched — mark the item skipped."""

    def __init__(self, reason: str = ""):
        self.reason = reason
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_task_definition(defn: Any, *, capabilities: set, settings) -> List[str]:
    """Structural validation at submission time. Reference/template errors
    surface at execution (they can depend on runtime data), but shape, step
    vocabulary, caps, and capability gating are all enforced here."""
    issues: List[str] = []
    if not isinstance(defn, dict):
        return ["task definition must be a JSON object"]

    name = defn.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 80):
        issues.append('"name" is required (1-80 chars)')

    known = {"name", "schedule", "concurrency", "setup", "items", "steps", "finally"}
    for key in defn:
        if key not in known:
            issues.append(f'unknown task field "{key}"')

    schedule = defn.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, dict) or set(schedule) != {"everyMinutes"}:
            issues.append('"schedule" must be {"everyMinutes": N}')
        else:
            minutes = schedule.get("everyMinutes")
            floor = settings.app_task_min_schedule_minutes
            if not isinstance(minutes, int) or minutes < floor:
                issues.append(f'"schedule.everyMinutes" must be an integer ≥ {floor}')

    concurrency = defn.get("concurrency", 1)
    if not isinstance(concurrency, int) or not (
        1 <= concurrency <= settings.app_task_max_concurrency
    ):
        issues.append(
            f'"concurrency" must be 1-{settings.app_task_max_concurrency}'
        )

    items = defn.get("items")
    steps = defn.get("steps")
    if items is not None and steps is None:
        issues.append('"items" requires per-item "steps"')
    if steps is not None and items is None:
        issues.append('"steps" requires "items" (use "setup" for one-shot steps)')
    if items is None and not defn.get("setup"):
        issues.append('a task needs "setup" steps and/or "items" + "steps"')

    if isinstance(items, list):
        if len(items) > settings.app_task_max_items:
            issues.append(
                f"items list exceeds the {settings.app_task_max_items} item cap"
            )
        for i, item in enumerate(items[:50]):
            if not isinstance(item, dict) or not isinstance(item.get("vars"), dict):
                issues.append(f'items[{i}] must be {{"vars": {{...}}}}')
                break
    elif isinstance(items, dict):
        has_from = "from" in items
        has_from_each = "fromEach" in items
        if has_from == has_from_each:
            issues.append('"items" needs "from" OR "fromEach" (not both)')
        elif has_from and (
            not isinstance(items["from"], str) or not items["from"].startswith("$")
        ):
            issues.append('"items.from" must be a "$" reference to a list')
        elif has_from_each and (
            not isinstance(items["fromEach"], list)
            or not items["fromEach"]
            or not all(
                isinstance(r, str) and r.startswith("$") for r in items["fromEach"]
            )
        ):
            issues.append(
                '"items.fromEach" must be a non-empty list of "$" references '
                "(their lists are concatenated)"
            )
        if not isinstance(items.get("vars"), dict) or not items["vars"]:
            issues.append('"items.vars" must map var names to templates')
        if "limit" in items and (
            not isinstance(items["limit"], int) or items["limit"] < 1
        ):
            issues.append('"items.limit" must be a positive integer')
        if "skipIfStored" in items:
            if not isinstance(items["skipIfStored"], str):
                issues.append('"items.skipIfStored" must be a key template string')
            elif "storage" not in capabilities:
                issues.append('"items.skipIfStored" requires the "storage" capability')
        unknown = set(items) - {"from", "fromEach", "vars", "limit", "skipIfStored"}
        if unknown:
            issues.append(f'unknown items fields: {", ".join(sorted(unknown))}')
    elif items is not None:
        issues.append('"items" must be a list of {vars} or a fan-out object')

    for section in ("setup", "steps", "finally"):
        section_steps = defn.get(section)
        if section_steps is None:
            continue
        if not isinstance(section_steps, list):
            issues.append(f'"{section}" must be a list of steps')
            continue
        if len(section_steps) > settings.app_task_max_steps:
            issues.append(
                f'"{section}" exceeds the {settings.app_task_max_steps} step cap'
            )
        seen_ids: set = set()
        for i, step in enumerate(section_steps):
            issues.extend(
                _validate_step(step, f"{section}[{i}]", section, capabilities, seen_ids)
            )
    return issues


def _validate_step(
    step: Any, where: str, section: str, capabilities: set, seen_ids: set
) -> List[str]:
    issues: List[str] = []
    if not isinstance(step, dict):
        return [f"{where}: step must be an object"]

    type_keys = [k for k in step if k in STEP_TYPES]
    if len(type_keys) != 1:
        return [
            f"{where}: step must have exactly one type key "
            f"({', '.join(STEP_TYPES)})"
        ]
    step_type = type_keys[0]
    spec = step[step_type]

    for key in step:
        if key not in (step_type, "id", "when"):
            issues.append(f'{where}: unknown step field "{key}"')
    step_id = step.get("id")
    if step_id is not None:
        if not isinstance(step_id, str) or not _ID_RE.match(step_id):
            issues.append(f'{where}: "id" must match [a-zA-Z][a-zA-Z0-9_]* (max 40)')
        elif step_id in _RESERVED_ROOTS:
            issues.append(f'{where}: "id" {step_id!r} is a reserved context root')
        elif step_id in seen_ids:
            issues.append(f'{where}: duplicate step id {step_id!r}')
        else:
            seen_ids.add(step_id)
    if "when" in step:
        issues.extend(_validate_condition(step["when"], f'{where}.when'))

    if not isinstance(spec, dict):
        return issues + [f"{where}: {step_type} spec must be an object"]

    if step_type == "http":
        if "http" not in capabilities:
            issues.append(f'{where}: http steps require the "http" capability')
        if str(spec.get("method", "")).upper() not in _HTTP_METHODS:
            issues.append(f'{where}: http.method must be one of {"/".join(_HTTP_METHODS)}')
        if not isinstance(spec.get("url"), str) or not spec["url"]:
            issues.append(f"{where}: http.url is required")
        if "headers" in spec and not (
            isinstance(spec["headers"], dict)
            and all(isinstance(v, str) for v in spec["headers"].values())
        ):
            issues.append(f"{where}: http.headers must map names to strings")
        if spec.get("responseType") not in (None, "json", "text"):
            issues.append(f'{where}: http.responseType must be "json" or "text"')
        issues.extend(_validate_auth(spec.get("auth"), f"{where}: http"))
        paginate = spec.get("paginate")
        if paginate is not None:
            if not isinstance(paginate, dict) or not isinstance(
                paginate.get("items"), str
            ):
                issues.append(f'{where}: http.paginate needs an "items" path')
            else:
                unknown = set(paginate) - {"items", "next", "maxPages", "keyBy"}
                if unknown:
                    issues.append(
                        f'{where}: unknown paginate fields: {", ".join(sorted(unknown))}'
                    )
                if "maxPages" in paginate and not (
                    isinstance(paginate["maxPages"], int)
                    and 1 <= paginate["maxPages"] <= 50
                ):
                    issues.append(f"{where}: paginate.maxPages must be 1-50")
        unknown = set(spec) - {
            "method", "url", "headers", "body", "contentType", "responseType",
            "auth", "paginate",
        }
        if unknown:
            issues.append(f'{where}: unknown http fields: {", ".join(sorted(unknown))}')

    elif step_type == "webdav":
        if "http" not in capabilities:
            issues.append(f'{where}: webdav steps require the "http" capability')
        if not isinstance(spec.get("url"), str) or not spec["url"]:
            issues.append(f"{where}: webdav.url is required")
        if spec.get("depth") not in (None, 0, 1, "infinity"):
            issues.append(f'{where}: webdav.depth must be 0, 1, or "infinity"')
        if spec.get("filter") not in (None, "files", "dirs"):
            issues.append(f'{where}: webdav.filter must be "files" or "dirs"')
        issues.extend(_validate_auth(spec.get("auth"), f"{where}: webdav"))
        unknown = set(spec) - {"url", "depth", "filter", "auth"}
        if unknown:
            issues.append(f'{where}: unknown webdav fields: {", ".join(sorted(unknown))}')

    elif step_type == "cortex":
        if str(spec.get("method", "")).upper() not in _HTTP_METHODS:
            issues.append(f'{where}: cortex.method must be one of {"/".join(_HTTP_METHODS)}')
        path = spec.get("path")
        if not isinstance(path, str) or not path or path.startswith("/"):
            issues.append(f"{where}: cortex.path must be an /api/-relative path")
        multipart = spec.get("multipart")
        if multipart is not None:
            if not isinstance(multipart, dict):
                issues.append(f"{where}: cortex.multipart must be an object")
            else:
                has_content = isinstance(multipart.get("content"), str)
                has_from_url = isinstance(multipart.get("fromUrl"), str)
                if has_content == has_from_url:
                    issues.append(
                        f'{where}: cortex.multipart needs "content" (text ref/template) '
                        f'OR "fromUrl" (binary passthrough fetch), not both'
                    )
                if not isinstance(multipart.get("filename"), str):
                    issues.append(f'{where}: cortex.multipart needs a "filename"')
                if has_from_url:
                    if "http" not in capabilities:
                        issues.append(
                            f'{where}: multipart.fromUrl requires the "http" capability'
                        )
                    if str(multipart.get("method", "GET")).upper() not in ("GET", "POST"):
                        issues.append(f"{where}: multipart.method must be GET or POST")
                    if "headers" in multipart and not (
                        isinstance(multipart["headers"], dict)
                        and all(isinstance(v, str) for v in multipart["headers"].values())
                    ):
                        issues.append(f"{where}: multipart.headers must map names to strings")
                    issues.extend(_validate_auth(multipart.get("auth"), f"{where}: multipart"))
                    allowed = {"fromUrl", "method", "headers", "auth",
                               "filename", "field", "contentType"}
                else:
                    allowed = {"content", "filename", "field", "contentType"}
                if set(multipart) - allowed:
                    issues.append(f"{where}: unknown multipart fields")
            if "body" in spec:
                issues.append(f"{where}: cortex step takes body OR multipart, not both")
        unknown = set(spec) - {"method", "path", "body", "multipart"}
        if unknown:
            issues.append(f'{where}: unknown cortex fields: {", ".join(sorted(unknown))}')

    elif step_type == "llm":
        if "llm" not in capabilities:
            issues.append(f'{where}: llm steps require the "llm" capability')
        if not isinstance(spec.get("prompt"), str) or not spec["prompt"]:
            issues.append(f"{where}: llm.prompt is required")
        chunk = spec.get("chunk")
        if chunk is not None:
            if not isinstance(chunk, dict) or not isinstance(chunk.get("words"), int) \
                    or not (100 <= chunk["words"] <= 8000):
                issues.append(f"{where}: llm.chunk.words must be an integer 100-8000")
            if not isinstance(spec.get("input"), str) or not spec["input"].startswith("$"):
                issues.append(f'{where}: chunked llm steps need "input" as a "$" reference')
            if isinstance(spec.get("prompt"), str) and "{chunk}" not in spec["prompt"]:
                issues.append(f"{where}: chunked llm.prompt must contain {{chunk}}")
        validate = spec.get("validate")
        if validate is not None:
            if not isinstance(validate, dict) or set(validate) - {
                "minLengthRatio", "minWordOverlap", "onFail",
            }:
                issues.append(f"{where}: unknown llm.validate fields")
            elif validate.get("onFail") not in (None, "keepOriginal", "fail"):
                issues.append(f'{where}: llm.validate.onFail must be "keepOriginal" or "fail"')
            elif chunk is None and spec.get("input") is None:
                issues.append(f'{where}: llm.validate needs an "input" to compare against')
        if "temperature" in spec and not isinstance(spec["temperature"], (int, float)):
            issues.append(f"{where}: llm.temperature must be a number")
        if "maxTokens" in spec and not (
            isinstance(spec["maxTokens"], int) and 1 <= spec["maxTokens"] <= 32000
        ):
            issues.append(f"{where}: llm.maxTokens must be 1-32000")
        unknown = set(spec) - {
            "prompt", "system", "input", "chunk", "validate", "maxTokens", "temperature",
        }
        if unknown:
            issues.append(f'{where}: unknown llm fields: {", ".join(sorted(unknown))}')

    elif step_type == "store":
        if "storage" not in capabilities:
            issues.append(f'{where}: store steps require the "storage" capability')
        ops = [k for k in ("get", "put", "delete", "list") if k in spec]
        if len(ops) != 1:
            issues.append(f"{where}: store needs exactly one of get/put/delete/list")
        else:
            op = ops[0]
            if not isinstance(spec[op], str):
                issues.append(f"{where}: store.{op} must be a key/prefix string")
            allowed = {op, "value"} if op == "put" else (
                {op, "limit"} if op == "list" else {op}
            )
            if op == "put" and "value" not in spec:
                issues.append(f'{where}: store.put needs a "value"')
            unknown = set(spec) - allowed
            if unknown:
                issues.append(f'{where}: unknown store fields: {", ".join(sorted(unknown))}')

    elif step_type == "template":
        has_text = isinstance(spec.get("text"), str)
        lines = spec.get("lines")
        has_lines = isinstance(lines, list) and lines
        if has_text == bool(has_lines):
            issues.append(f'{where}: template needs "text" or "lines" (not both)')
        if has_lines:
            for j, line in enumerate(lines):
                if isinstance(line, str):
                    continue
                if not isinstance(line, dict) or not isinstance(line.get("text"), str):
                    issues.append(f'{where}: lines[{j}] must be a string or {{"text", "when"?}}')
                elif "when" in line:
                    issues.extend(_validate_condition(line["when"], f"{where}.lines[{j}].when"))
        unknown = set(spec) - {"text", "lines", "joiner"}
        if unknown:
            issues.append(f'{where}: unknown template fields: {", ".join(sorted(unknown))}')

    elif step_type == "skipItem":
        if section != "steps":
            issues.append(f"{where}: skipItem is only valid in per-item steps")
        if "when" not in spec:
            issues.append(f'{where}: skipItem needs a "when" condition')
        else:
            issues.extend(_validate_condition(spec["when"], f"{where}.when"))
        unknown = set(spec) - {"when", "reason"}
        if unknown:
            issues.append(f'{where}: unknown skipItem fields: {", ".join(sorted(unknown))}')

    return issues


def _validate_auth(auth: Any, where: str) -> List[str]:
    """Dynamic per-request credential: {"bearer": <ref/template>} or
    {"basic": <ref/template>}. The value must come from the run context (a
    token minted DURING the run, e.g. an OAuth refresh response) — config
    secrets are unreachable from templates by construction, so this cannot
    leak them; static credentials belong in config auth_header vars."""
    if auth is None:
        return []
    if (
        not isinstance(auth, dict)
        or len(auth) != 1
        or next(iter(auth)) not in ("bearer", "basic")
        or not isinstance(next(iter(auth.values())), str)
        or not next(iter(auth.values()))
    ):
        return [f'{where}.auth must be {{"bearer": <ref/template>}} or {{"basic": …}}']
    return []


def _validate_condition(cond: Any, where: str) -> List[str]:
    if not isinstance(cond, dict) or len(cond) != 1:
        return [f"{where}: condition must be a single-operator object"]
    op, operand = next(iter(cond.items()))
    if op not in _CONDITION_OPS:
        return [f"{where}: unknown condition operator {op!r}"]
    issues: List[str] = []
    if op in ("and", "or"):
        if not isinstance(operand, list) or not operand:
            issues.append(f"{where}: {op} needs a non-empty list of conditions")
        else:
            for i, sub in enumerate(operand):
                issues.extend(_validate_condition(sub, f"{where}.{op}[{i}]"))
    elif op == "not":
        issues.extend(_validate_condition(operand, f"{where}.not"))
    elif op in ("eq", "neq", "gt", "lt", "contains"):
        if not isinstance(operand, list) or len(operand) != 2:
            issues.append(f"{where}: {op} needs a two-element list")
    return issues


# ---------------------------------------------------------------------------
# Reference & template resolution
# ---------------------------------------------------------------------------

def _apply_filter(value: Any, name: str, arg: Optional[str]) -> Any:
    if name == "slug":
        text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
        return text[:60] or "untitled"
    if name == "lower":
        return str(value or "").lower()
    if name == "upper":
        return str(value or "").upper()
    if name == "trim":
        return str(value or "").strip()
    if name == "ext":
        # lowercase file extension without the dot ("" when there is none) —
        # lets tasks type-filter listings that carry no MIME type (Dropbox):
        # {"contains": [" pdf docx md ", " {vars.name|ext} "]}
        text = str(value or "")
        return text.rsplit(".", 1)[1].lower() if "." in text.strip(".") else ""
    if name == "json":
        return json.dumps(value, default=str)
    if name == "urlencode":
        return quote(str(value or ""), safe="")
    if name == "default":
        return value if value not in (None, "") else (arg or "")
    if name == "join":
        sep = arg if arg is not None else ", "
        return sep.join(str(v) for v in value) if isinstance(value, list) else value
    if name == "pluck":
        if isinstance(value, list) and arg:
            return [v.get(arg) for v in value if isinstance(v, dict)]
        return value
    if name == "slice":
        try:
            return value[: int(arg)] if arg else value
        except (TypeError, ValueError):
            return value
    if name == "truncate":
        try:
            return str(value or "")[: int(arg)] if arg else value
        except ValueError:
            return value
    raise StepError(f"unknown template filter {name!r}")


def _resolve_path(path: str, ctx: Dict[str, Any]) -> Any:
    """Resolve a dotted path against the context. A bare first segment falls
    back to steps.<id>, then setup.<id> (scope shorthand)."""
    segments = [s for s in path.split(".") if s != ""]
    if not segments:
        raise StepError("empty reference path")
    root = segments[0]
    if root in _RESERVED_ROOTS:
        current: Any = ctx.get(root)
        segments = segments[1:]
    elif root in (ctx.get("steps") or {}):
        current = ctx["steps"][root]
        segments = segments[1:]
    elif root in (ctx.get("setup") or {}):
        current = ctx["setup"][root]
        segments = segments[1:]
    else:
        raise StepError(
            f"unknown reference root {root!r} (known: vars, setup, steps, run, "
            f"config, item, chunk, or a step id in scope)"
        )
    for seg in segments:
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(seg)
        else:
            return None
    return current


def resolve_ref(ref: str, ctx: Dict[str, Any]) -> Any:
    """Resolve a "$path" reference to its raw value. Embedded {templates}
    are interpolated first (dynamic map lookups)."""
    if not isinstance(ref, str) or not ref.startswith("$"):
        raise StepError(f"not a reference: {ref!r}")
    path = ref[1:]
    if "{" in path:
        path = interpolate(path, ctx)
    return _resolve_path(path, ctx)


def _render_expr(expr: str, ctx: Dict[str, Any]) -> str:
    parts = expr.split("|")
    value = _resolve_path(parts[0].strip(), ctx)
    for filt in parts[1:]:
        name, _, arg = filt.partition(":")
        value = _apply_filter(value, name.strip(), arg if _ else None)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def interpolate(template: str, ctx: Dict[str, Any]) -> str:
    """Render "{path|filter|filter:arg}" placeholders; {{ }} escape braces.

    Placeholders may nest for dynamic lookups —
    "{setup.tags.map.{full.body.correspondent}.name}" — so this is a
    balanced-brace scanner, not a flat regex. Recursion applies ONLY to the
    author-written expression text; resolved values are inserted verbatim and
    never re-scanned (data containing braces is not a template)."""
    if "{" not in template:
        return template
    out: List[str] = []
    i, n = 0, len(template)
    while i < n:
        ch = template[i]
        if ch == "{":
            if template.startswith("{{", i):
                out.append("{")
                i += 2
                continue
            depth, j = 1, i + 1
            while j < n and depth:
                if template[j] == "{":
                    depth += 1
                elif template[j] == "}":
                    depth -= 1
                j += 1
            if depth:  # unbalanced — keep the rest literally
                out.append(template[i:])
                break
            expr = template[i + 1 : j - 1]
            if "{" in expr:
                expr = interpolate(expr, ctx)  # inner placeholders first
            out.append(_render_expr(expr, ctx))
            i = j
        elif template.startswith("}}", i):
            out.append("}")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def resolve_value(value: Any, ctx: Dict[str, Any]) -> Any:
    """Resolve any DSL value: "$refs" → raw values, strings → interpolated,
    dicts/lists → resolved recursively."""
    if isinstance(value, str):
        if value.startswith("$") and not value.startswith("$$"):
            return resolve_ref(value, ctx)
        if value.startswith("$$"):  # escaped literal "$…"
            value = value[1:]
        return interpolate(value, ctx)
    if isinstance(value, dict):
        return {k: resolve_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v, ctx) for v in value]
    return value


def eval_condition(cond: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    op, operand = next(iter(cond.items()))
    if op == "and":
        return all(eval_condition(c, ctx) for c in operand)
    if op == "or":
        return any(eval_condition(c, ctx) for c in operand)
    if op == "not":
        return not eval_condition(operand, ctx)
    if op in ("empty", "notEmpty", "found"):
        value = resolve_value(operand, ctx)
        if isinstance(value, str):
            empty = not value.strip()
        elif isinstance(value, (list, dict)):
            empty = not value
        else:
            empty = value is None or value is False
        return empty if op == "empty" else not empty
    left, right = (resolve_value(v, ctx) for v in operand)
    if op == "eq":
        return left == right
    if op == "neq":
        return left != right
    if op == "gt":
        try:
            return float(left) > float(right)
        except (TypeError, ValueError):
            return False
    if op == "lt":
        try:
            return float(left) < float(right)
        except (TypeError, ValueError):
            return False
    if op == "contains":
        if isinstance(left, (list, str)):
            return right in left
        return False
    raise StepError(f"unknown condition operator {op!r}")


# ---------------------------------------------------------------------------
# LLM chunking + validation policies (yt-transcriber's chunk-safety rules,
# promoted to platform policies per ECOSYSTEM.md §5.2)
# ---------------------------------------------------------------------------

def split_into_chunks(text: str, words_per_chunk: int) -> List[str]:
    """Split on paragraph boundaries into ~words_per_chunk chunks; paragraphs
    larger than the budget are split on sentence boundaries."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks: List[str] = []
    current: List[str] = []
    count = 0

    def flush():
        nonlocal current, count
        if current:
            chunks.append("\n\n".join(current))
            current, count = [], 0

    for para in paragraphs:
        para_words = len(para.split())
        if para_words > words_per_chunk:
            flush()
            sentences = re.split(r"(?<=[.!?])\s+", para)
            piece: List[str] = []
            piece_count = 0
            for sentence in sentences:
                n = len(sentence.split())
                if piece and piece_count + n > words_per_chunk:
                    chunks.append(" ".join(piece))
                    piece, piece_count = [], 0
                piece.append(sentence)
                piece_count += n
            if piece:
                chunks.append(" ".join(piece))
            continue
        if count + para_words > words_per_chunk:
            flush()
        current.append(para)
        count += para_words
    flush()
    return chunks or ([text] if (text or "").strip() else [])


def chunk_output_valid(
    original: str,
    produced: str,
    *,
    min_length_ratio: float = 0.5,
    min_word_overlap: float = 0.6,
) -> bool:
    """Guard against LLM truncation/rewrites: the output must retain most of
    the input's length AND vocabulary."""
    if not (produced or "").strip():
        return False
    if len(produced) < len(original) * min_length_ratio:
        return False
    original_words = set(re.findall(r"[a-z0-9']+", original.lower()))
    if not original_words:
        return True
    produced_words = set(re.findall(r"[a-z0-9']+", produced.lower()))
    overlap = len(original_words & produced_words) / len(original_words)
    return overlap >= min_word_overlap


def output_size_ok(value: Any, max_kb: int) -> Tuple[bool, int]:
    """Cheap size check on a step output before it enters the item context."""
    try:
        size = len(json.dumps(value, default=str).encode())
    except (TypeError, ValueError):
        return False, 0
    return size <= max_kb * 1024, size
