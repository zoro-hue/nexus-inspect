"""Structural validation of SARIF 2.1.0 logs, for the subset of the format that NEXUS INSPECT emits.

The official schema could not be fetched in the development environment (the egress proxy refuses the schema hosts),
so this module encodes the required properties, value sets and bounds that the 2.1.0 schema declares for that subset.
`tests/fixtures/sarif-2.1.0-subset.schema.json` states the same rules in JSON Schema form for an external
`jsonschema` run. Validation here is structural: it does not prove conformance with every optional property.
"""
import re
from urllib.parse import urlsplit

LEVELS = {"none", "note", "warning", "error"}
KINDS = {"notApplicable", "pass", "fail", "review", "open", "informational"}
SUPPRESSION_KINDS = {"inSource", "external"}
SUPPRESSION_STATUSES = {"accepted", "underReview", "rejected"}
URI_REF_SAFE = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@/%-]*$")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate(doc):
    """Return a list of human-readable problems. An empty list means the document passes these checks."""
    errors = []

    def need(obj, key, where, kind=None):
        if not isinstance(obj, dict) or key not in obj:
            errors.append(f"{where}: missing required property '{key}'")
            return None
        value = obj[key]
        if kind and not isinstance(value, kind):
            errors.append(f"{where}.{key}: expected {kind.__name__}")
        return value

    if not isinstance(doc, dict):
        return ["the log must be a JSON object"]
    schema = need(doc, "$schema", "log", str)
    if schema is not None:
        parts = urlsplit(schema)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            errors.append("log.$schema must be an absolute http(s) URI")
    if doc.get("version") != "2.1.0":
        errors.append("log.version must be the string '2.1.0'")
    runs = need(doc, "runs", "log", list)
    for ri, run in enumerate(runs or []):
        where = f"runs[{ri}]"
        tool = need(run, "tool", where, dict)
        driver = need(tool, "driver", where + ".tool", dict) if tool is not None else None
        if driver is not None:
            need(driver, "name", where + ".tool.driver", str)
            rules = driver.get("rules", [])
            if not isinstance(rules, list):
                errors.append(f"{where}.tool.driver.rules must be an array")
                rules = []
            ids = []
            for k, rule in enumerate(rules):
                rw = f"{where}.tool.driver.rules[{k}]"
                rid = need(rule, "id", rw, str)
                if rid is not None:
                    ids.append(rid)
                if isinstance(rule, dict) and "defaultConfiguration" in rule:
                    level = rule["defaultConfiguration"].get("level")
                    if level is not None and level not in LEVELS:
                        errors.append(f"{rw}.defaultConfiguration.level is not a valid level")
        else:
            ids, rules = [], []
        for invocation in run.get("invocations", []) if isinstance(run, dict) else []:
            need(invocation, "executionSuccessful", where + ".invocations[]", bool)
        results = run.get("results", []) if isinstance(run, dict) else []
        if not isinstance(results, list):
            errors.append(f"{where}.results must be an array")
            results = []
        for ci, res in enumerate(results):
            rw = f"{where}.results[{ci}]"
            message = need(res, "message", rw, dict)
            if message is not None and "text" not in message and "id" not in message:
                errors.append(f"{rw}.message must have 'text' or 'id'")
            if isinstance(res, dict):
                if "ruleId" in res and not isinstance(res["ruleId"], str):
                    errors.append(f"{rw}.ruleId must be a string")
                if "ruleIndex" in res:
                    idx = res["ruleIndex"]
                    if not _is_int(idx) or idx < -1 or (idx >= 0 and idx >= len(rules)):
                        errors.append(f"{rw}.ruleIndex is out of range")
                    elif idx >= 0 and "ruleId" in res and rules[idx].get("id") != res["ruleId"]:
                        errors.append(f"{rw}.ruleIndex does not point at rule {res['ruleId']}")
                if "ruleId" in res and res["ruleId"] not in ids:
                    errors.append(f"{rw}.ruleId '{res['ruleId']}' has no entry in tool.driver.rules")
                if "level" in res and res["level"] not in LEVELS:
                    errors.append(f"{rw}.level is not a valid level")
                if "kind" in res and res["kind"] not in KINDS:
                    errors.append(f"{rw}.kind is not a valid kind")
                for fp_name, fp in (res.get("partialFingerprints") or {}).items():
                    if not isinstance(fp, str):
                        errors.append(f"{rw}.partialFingerprints.{fp_name} must be a string")
                for li, loc in enumerate(res.get("locations", [])):
                    phys = loc.get("physicalLocation") if isinstance(loc, dict) else None
                    if phys is None:
                        continue
                    lw = f"{rw}.locations[{li}].physicalLocation"
                    art = phys.get("artifactLocation", {})
                    uri = art.get("uri")
                    if uri is not None and (not isinstance(uri, str) or not URI_REF_SAFE.match(uri)
                                            or "\\" in uri or uri.startswith("/")):
                        errors.append(f"{lw}.artifactLocation.uri must be a relative URI reference")
                    region = phys.get("region")
                    if region is not None:
                        for key in ("startLine", "endLine"):
                            if key in region and (not _is_int(region[key]) or region[key] < 1):
                                errors.append(f"{lw}.region.{key} must be an integer >= 1")
                        if _is_int(region.get("startLine")) and _is_int(region.get("endLine")) \
                                and region["endLine"] < region["startLine"]:
                            errors.append(f"{lw}.region.endLine is before startLine")
                for si, sup in enumerate(res.get("suppressions", [])):
                    sw = f"{rw}.suppressions[{si}]"
                    need(sup, "kind", sw)
                    if isinstance(sup, dict):
                        if sup.get("kind") not in SUPPRESSION_KINDS:
                            errors.append(f"{sw}.kind is not a valid suppression kind")
                        if "status" in sup and sup["status"] not in SUPPRESSION_STATUSES:
                            errors.append(f"{sw}.status is not a valid suppression status")
    return errors
