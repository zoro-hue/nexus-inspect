"""Context-aware AI investigation assistant for NEXUS INSPECT.

Provides the Nexus AI conversational engine, capable of reasoning over current view context,
active repository analyses, specific findings, and platform documentation.
Supports both LLM-driven responses when NEXUS_AI_API_KEY is configured and high-fidelity
deterministic responses from the local knowledge engine when offline.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from . import ai
from .report import CATEGORIES
from .rules import RULES, RULES_BY_ID


# Suggested question chips mapped by route/view context
SUGGESTIONS: Dict[str, List[str]] = {
    "default": [
        "What rules does NEXUS check?",
        "How does AI slop detection work?",
        "What is loose-end discovery?",
        "How do I analyze a repository?",
    ],
    "docs": [
        "What rules does NEXUS check?",
        "How are risk scores calculated?",
        "How do I integrate SARIF with GitHub?",
        "What is the difference between slop and gaps?",
    ],
    "overview": [
        "Summarize this repository's health",
        "What are the most critical risks?",
        "Explain coding gaps vs loose ends",
        "Which files have the highest finding density?",
    ],
    "findings": [
        "How should I prioritize these findings?",
        "What is the remediation for the highest severity issue?",
        "How do I filter by confirmed evidence?",
        "How do I mark a finding as fixed or ignored?",
    ],
    "analyze": [
        "What repository formats are supported?",
        "Does NEXUS send my source code externally?",
        "How fast does deterministic analysis run?",
    ],
    "reports": [
        "How do I export SARIF for GitHub Code Scanning?",
        "Explain the risk score formula",
        "What are root-cause clusters?",
        "How do I share this report with my team?",
    ],
    "projects": [
        "How do project scan baselines work?",
        "How do I compare two scans with diff?",
        "Can I rename or archive a project?",
    ],
}


def get_suggestions(route: str = "", has_analysis: bool = False) -> List[str]:
    """Return tailored suggestion chips for the current UI context."""
    r = (route or "").lower().replace("#", "").split("/")[0].strip()
    if r in ("overview", "dash", "dashboard") and has_analysis:
        return SUGGESTIONS["overview"]
    if r in ("findings", "finding") and has_analysis:
        return SUGGESTIONS["findings"]
    if r in ("reports", "report") and has_analysis:
        return SUGGESTIONS["reports"]
    if r in ("projects", "project"):
        return SUGGESTIONS["projects"]
    if r in ("analyze", "import"):
        return SUGGESTIONS["analyze"]
    if r in ("docs", "documentation", "api", "reference"):
        return SUGGESTIONS["docs"]
    if has_analysis:
        return SUGGESTIONS["overview"]
    return SUGGESTIONS["default"]


def sanitize_input(text: str) -> str:
    """Sanitize user input to prevent prompt manipulation and strip excessive control characters."""
    if not isinstance(text, str):
        return ""
    # Remove control characters except standard whitespace
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Bound input length
    return clean.strip()[:2000]


def format_deterministic_reply(
    message: str,
    route: str,
    analysis: Optional[Dict[str, Any]] = None,
    findings: Optional[List[Dict[str, Any]]] = None,
    finding: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate intelligent deterministic replies from the NEXUS knowledge base."""
    msg = message.lower()

    # 1. Specific finding query
    if finding:
        rule = RULES_BY_ID.get(finding.get("ruleId", ""))
        rec = finding.get("recommendation") or (rule.recommendation if rule else "Inspect the excerpt.")
        impact = finding.get("impact") or (rule.impact if rule else "Potential security or stability concern.")
        return (
            f"### Finding Details: `{finding.get('ruleId')}` — {finding.get('title')}\n\n"
            f"- **Severity:** `{finding.get('severity', '').upper()}`\n"
            f"- **Location:** `{finding.get('filePath')}:{finding.get('startLine')}`\n"
            f"- **Impact:** {impact}\n\n"
            f"**Recommended Remediation:**\n{rec}\n\n"
            f"> [!TIP]\n"
            f"> To resolve this finding, apply the fix in your source code, or click **Mark as fixed** / **Ignore** in the drawer."
        )

    # 2. Repository Health & Overview Summary
    if analysis and ("health" in msg or "summar" in msg or "overview" in msg or "status" in msg or "risk" in msg):
        total = len(findings) if findings is not None else 0
        by_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        by_cat = {c: 0 for c in CATEGORIES}
        max_risk = 0
        if findings:
            for f in findings:
                sev = f.get("severity", "medium").lower()
                if sev in by_sev:
                    by_sev[sev] += 1
                cat = f.get("category", "").lower()
                if cat in by_cat:
                    by_cat[cat] += 1
                if f.get("riskScore", 0) > max_risk:
                    max_risk = f.get("riskScore", 0)

        risk_score = analysis.get("riskScore") or max_risk
        risk_display = int(risk_score) if float(risk_score).is_integer() else round(float(risk_score), 1)
        repo_name = analysis.get("projectName") or analysis.get("name") or "Repository"

        summary = (
            f"### Repository Health Summary: `{repo_name}`\n\n"
            f"- **Overall Risk Score:** `{risk_display}/100`\n"
            f"- **Total Findings:** **{total}** issues detected across 4 intelligence categories:\n"
            f"  - **Security:** `{by_cat.get('security', 0)}` findings\n"
            f"  - **AI Slop:** `{by_cat.get('slop', 0)}` findings\n"
            f"  - **Coding Gaps:** `{by_cat.get('gap', 0)}` findings\n"
            f"  - **Loose Ends:** `{by_cat.get('loose', 0)}` findings\n\n"
            f"**Severity Breakdown:**\n"
            f"- **Critical:** `{by_sev['critical']}` | **High:** `{by_sev['high']}` | "
            f"**Medium:** `{by_sev['medium']}` | **Low:** `{by_sev['low']}`\n\n"
        )

        if by_sev["critical"] > 0 or by_sev["high"] > 0:
            summary += (
                f"> [!IMPORTANT]\n"
                f"> Priority attention recommended: **{by_sev['critical']} critical** and "
                f"**{by_sev['high']} high** severity findings require remediation before production deployment."
            )
        else:
            summary += "> [!NOTE]\n> No critical security vulnerabilities detected. Focus on code quality and loose-end cleanup."
        return summary

    # 3. Critical / High risks query with active analysis
    if analysis and ("critical" in msg or "priorit" in msg or "top" in msg or "urgent" in msg):
        high_findings = [f for f in (findings or []) if f.get("severity") in ("critical", "high")][:5]
        if not high_findings:
            return "No critical or high-severity findings were detected in this analysis. Your codebase satisfies NEXUS primary security guardrails."
        lines = [f"### Top Priority Findings for `{analysis.get('name', 'Repository')}`:\n"]
        for i, f in enumerate(high_findings, 1):
            lines.append(f"{i}. **[{f.get('severity', '').upper()}]** `{f.get('filePath')}:{f.get('startLine')}` — {f.get('title')} (`{f.get('ruleId')}`)")
        lines.append("\nNavigate to **Findings** to inspect source excerpts and remediation instructions.")
        return "\n".join(lines)

    # 4. Coding Gaps vs Loose Ends explanation
    if "gap" in msg and "loose" in msg or "difference" in msg and ("gap" in msg or "loose" in msg or "slop" in msg):
        return (
            "### NEXUS Intelligence Categories Explained\n\n"
            "- **Coding Gaps (`gap`):** Incomplete implementations, unhandled exception branches, missing typed contract validations, or broken relative imports where functionality exists on paper but fails under real-world conditions.\n"
            "- **Loose Ends (`loose`):** Dormant code, unreferenced helper functions, unreachable logic after terminal returns, or abandoned temporary resources that accumulate debt.\n"
            "- **AI Slop (`slop`):** Hallucinated imports, repetitive structural boilerplate, unpopulated `TODO`/`pass` stubs, and synthetic mock structures frequently left by LLM code generators.\n"
            "- **Security (`security`):** Actionable vulnerabilities including hardcoded credentials, SQL concatenations, command injection risks, and unvalidated redirects."
        )

    # 5. Rules catalog inquiry
    if "rule" in msg or "check" in msg or "catalog" in msg:
        return (
            f"### NEXUS Inspect Rule Engine\n\n"
            f"NEXUS runs **{len(RULES)} deterministic detection rules** across four specialized categories:\n\n"
            f"1. **Security Intelligence:** Secrets detection (`SEC-KEY-*`, `SEC-TOK-*`), SQL injection patterns (`SEC-SQL-*`), unsafe system execution.\n"
            f"2. **AI Slop Detection:** Hallucinated imports (`SLOP-IMP-*`), repetitive structural patterns (`SLOP-REP-*`), unpopulated placeholders (`SLOP-STUB-*`).\n"
            f"3. **Coding Gap Analysis:** Unhandled errors (`GAP-ERR-*`), missing parameter validation (`GAP-VAL-*`), broken imports (`GAP-INTEG-*`).\n"
            f"4. **Loose-End Discovery:** Dead functions (`LOOSE-DEAD-*`), unreferenced modules, abandoned file handles.\n\n"
            f"Each rule outputs verified line-level evidence, deterministic confidence ratings (0.0–1.0), and remediation guidance."
        )

    # 6. AI Slop inquiry
    if "slop" in msg:
        return (
            "### AI Slop Detection in NEXUS\n\n"
            "AI-assisted code generation often leaves behind distinct artifacts:\n"
            "- **Hallucinated Dependencies:** Importing packages or internal modules that do not exist in the project tree.\n"
            "- **Stubbed Placeholders:** Functions created with generic `pass`, `...`, or `// TODO implement later` that pretend to be complete.\n"
            "- **Repetitive Boilerplate:** Excessive boilerplate patterns repeated across files without abstraction.\n\n"
            "NEXUS isolates these patterns with deterministic AST and regex inspections to keep generated code clean and maintainable."
        )

    # 7. Loose ends inquiry
    if "loose" in msg:
        return (
            "### Loose-End Discovery in NEXUS\n\n"
            "Loose ends represent code paths and artifacts that were started but never connected or cleaned up:\n"
            "- Unreferenced internal helpers and private utilities.\n"
            "- Unreachable code segments placed after returns, throws, or breaks.\n"
            "- Orphaned configuration keys and unconsumed parameters.\n\n"
            "Cleaning up loose ends reduces cognitive overhead, shrinks bundle size, and prevents subtle runtime regressions."
        )

    # 8. SARIF / GitHub / Export inquiry
    if "sarif" in msg or "github" in msg or "export" in msg:
        return (
            "### Exporting & Integration in NEXUS\n\n"
            "NEXUS supports three standard export formats:\n"
            "- **SARIF 2.1.0 (`/api/analyses/<id>/report?format=sarif`):** Standard Static Analysis Results Interchange Format. Upload directly to **GitHub Code Scanning** via the `upload-sarif` action.\n"
            "- **Markdown (`format=markdown`):** Human-readable executive summary suitable for pull request comments or security audits.\n"
            "- **JSON (`format=json`):** Full structured data for CI/CD pipeline automation and custom reporting.\n\n"
            "You can export from the **Reports** view in the dashboard or via the REST API."
        )

    # 9. Repository Import & Analysis process
    if "analyze" in msg or "import" in msg or "upload" in msg:
        return (
            "### Analyzing a Repository\n\n"
            "You can analyze code in two ways:\n"
            "1. **ZIP Archive Upload:** Upload a `.zip` file of your project repository (up to 25 MB).\n"
            "2. **GitHub URL Import:** Provide a public GitHub repository URL and branch name.\n\n"
            "Deterministic scanning executes instantly in-memory, streaming live progress over Server-Sent Events (SSE) before transitioning to the interactive findings workspace."
        )

    # 10. Default contextual response
    if analysis:
        repo_name = analysis.get("projectName") or analysis.get("name") or "Repository"
        max_r = analysis.get("riskScore") or (max((f.get("riskScore", 0) for f in findings), default=0) if findings else 0)
        return (
            f"I am examining analysis `{repo_name}`. "
            f"NEXUS found **{len(findings or [])} issues** with a risk score of **{max_r}/100**.\n\n"
            f"You can ask me to:\n"
            f"- **Summarize repository health**\n"
            f"- **List critical or high severity risks**\n"
            f"- **Explain specific rules like AI slop or loose ends**\n"
            f"- **Provide remediation instructions** for any finding"
        )

    return (
        "I am **Nexus AI**, your repository investigation assistant. "
        "I can help you understand security vulnerabilities, AI slop, coding gaps, and loose ends across your codebase.\n\n"
        "Try asking:\n"
        "- *'What rules does NEXUS check?'*\n"
        "- *'How does AI slop detection work?'*\n"
        "- *'How do I analyze a repository?'*\n"
        "- *'How do I export SARIF for GitHub Actions?'*"
    )


def chat(
    store: Any,
    user: Optional[Dict[str, Any]],
    message: str,
    context: Optional[Dict[str, Any]] = None,
    env: Optional[Dict[str, str]] = None,
    opener: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute a context-aware chat turn, trying the LLM if configured and falling back to deterministic knowledge."""
    clean_msg = sanitize_input(message)
    if not clean_msg:
        raise ai.AIError("invalid_message", "Message must not be empty.", 400)

    ctx = context or {}
    route = str(ctx.get("route", "")).strip()
    aid = ctx.get("analysisId")
    fid = ctx.get("findingId")

    analysis = None
    findings = None
    finding = None

    if aid:
        if not user:
            raise ai.AIError("unauthorized", "Sign in to query repository analysis.", 401)
        if store.analysis_owner(aid) != user["id"]:
            raise ai.AIError("not_found", "Analysis not found.", 404)
        analysis = store.get_analysis(aid)
        findings = store.list_findings(aid)
        if fid:
            finding = store.get_finding(aid, fid)

    # Check if live AI is configured
    ai_status = ai.status(env)
    if ai_status.get("enabled"):
        try:
            # Build LLM context prompt with strict boundary enforcement
            context_blocks = [
                "You are Nexus AI, a context-aware repository investigation assistant for NEXUS INSPECT.",
                "Answer the user's question concisely, professionally, and accurately.",
                "Use markdown formatting with code blocks where helpful.",
                ai.REPO_DATA_BOUNDARY,
                f"Current UI Route: {route or 'landing'}",
            ]
            if analysis:
                repo_name = analysis.get("projectName") or analysis.get("name") or "Repository"
                max_r = analysis.get("riskScore") or (max((f.get("riskScore", 0) for f in findings), default=0) if findings else 0)
                context_blocks.append(
                    f"Repository: {repo_name} | Risk Score: {max_r}/100 | "
                    f"Total Findings: {len(findings or [])}"
                )
            if finding:
                context_blocks.append(
                    f"Selected Finding: [{finding.get('severity')}] {finding.get('title')} ({finding.get('ruleId')}) "
                    f"at {finding.get('filePath')}:{finding.get('startLine')}\n"
                    f"Impact: {finding.get('impact')}\n"
                    f"Recommendation: {finding.get('recommendation')}"
                )

            system_instruction = "\n".join(context_blocks)
            prompt = f"{system_instruction}\n\nUser Question:\n{clean_msg}"

            reply_text, model = ai._send(prompt, env=env, opener=opener, timeout=25)
            if reply_text and reply_text.strip():
                return {
                    "reply": reply_text.strip(),
                    "model": model,
                    "provider": ai_status.get("provider", "gemini"),
                    "fallback": False,
                    "notice": None,
                    "suggestedQuestions": get_suggestions(route, bool(analysis)),
                }
        except Exception:
            # LLM service failed or unreachable, seamlessly fall back to local knowledge engine
            pass

    # Deterministic knowledge engine fallback
    det_reply = format_deterministic_reply(clean_msg, route, analysis, findings, finding)
    return {
        "reply": det_reply,
        "model": "nexus-knowledge-engine",
        "fallback": True,
        "notice": "Response generated from the NEXUS deterministic knowledge base.",
        "suggestedQuestions": get_suggestions(route, bool(analysis)),
    }
