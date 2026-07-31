"""Deterministic guardrails for Agent inputs, untrusted context, tools and output.

The policy deliberately records hashes and detector IDs instead of raw matched
content. Model prompts are defense in depth; authorization and secret handling
must not depend on an LLM following instructions.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

SECURITY_POLICY_VERSION = "1.0.0"
QUARANTINE_MARKER = "[SECURITY: embedded instruction quarantined]"
SAFE_REFUSAL = (
    "I can help explain security concepts, but I can’t reveal hidden prompts, "
    "credentials, private configuration, or bypass the application’s safeguards."
)
SAFE_REFUSAL_ZH = (
    "我可以讲解安全概念，但不能泄露隐藏提示词、凭据、私有配置，也不能绕过应用的安全限制。"
)

SecurityAction = Literal["allow", "block", "quarantine", "redact"]
SecuritySeverity = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class SecurityFinding:
    category: str
    severity: SecuritySeverity
    action: SecurityAction
    detector_id: str
    evidence_hash: str

    def as_payload(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityDecision:
    action: SecurityAction
    findings: tuple[SecurityFinding, ...] = ()
    safe_text: str | None = None
    policy_version: str = SECURITY_POLICY_VERSION
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.action != "block"

    def as_payload(self) -> dict[str, object]:
        return {
            "action": self.action,
            "allowed": self.allowed,
            "policy_version": self.policy_version,
            "findings": [finding.as_payload() for finding in self.findings],
            **self.metadata,
        }


@dataclass(frozen=True)
class _Detector:
    detector_id: str
    category: str
    severity: SecuritySeverity
    pattern: re.Pattern[str]


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


_USER_DETECTORS = (
    _Detector(
        "USR-PROMPT-EXTRACT-EN",
        "system_prompt_extraction",
        "high",
        _rx(
            r"\b(?:reveal|show|print|repeat|dump|expose|give\s+me)\b.{0,60}"
            r"\b(?:system|developer|hidden|internal)\b.{0,35}"
            r"\b(?:prompt|instructions?|message)\b"
        ),
    ),
    _Detector(
        "USR-PROMPT-EXTRACT-ZH",
        "system_prompt_extraction",
        "high",
        _rx(
            r"(?:显示|泄露|打印|重复|告诉我|给我|导出).{0,35}"
            r"(?:系统|开发者|隐藏|内部).{0,25}(?:提示词|指令|消息)"
            r"|(?:系统|开发者|隐藏|内部).{0,25}(?:提示词|指令|消息).{0,35}"
            r"(?:显示|泄露|打印|重复|告诉我|给我|导出)"
        ),
    ),
    _Detector(
        "USR-SECRET-EXFIL-EN",
        "sensitive_information_request",
        "critical",
        _rx(
            r"\b(?:show|read|print|dump|reveal|send|give\s+me|extract)\b.{0,60}"
            r"(?:\.env\b|api[_ -]?keys?\b|access[_ -]?tokens?\b|passwords?\b|"
            r"secrets?\b|credentials?\b|private[_ -]?keys?\b)"
        ),
    ),
    _Detector(
        "USR-SECRET-EXFIL-ZH",
        "sensitive_information_request",
        "critical",
        _rx(
            r"(?:显示|读取|打印|导出|泄露|发送|告诉我|给我).{0,45}(?:\.env|API.?密钥|令牌|密码|凭据|私钥|秘密)"
        ),
    ),
    _Detector(
        "USR-GOAL-HIJACK",
        "agent_goal_hijack",
        "high",
        _rx(
            r"(?:ignore|disregard|forget|override).{0,35}(?:previous|prior|system|developer)"
            r".{0,35}(?:instructions?|rules?|prompt)"
            r"|(?:忽略|无视|覆盖|绕过).{0,35}(?:之前|系统|开发者|安全).{0,30}(?:指令|规则|提示词|限制)"
        ),
    ),
)

_DISCUSSION_PATTERN = _rx(
    r"\b(?:what\s+is|explain|describe|how\s+does|example\s+of|defend|prevent|detect)\b"
    r"|(?:什么是|解释|讲解|为什么|如何防|如何检测|举例)"
)

_CONTEXT_DETECTORS = (
    _Detector(
        "CTX-IGNORE-INSTRUCTIONS",
        "indirect_prompt_injection",
        "high",
        _rx(
            r"(?:ignore|disregard|forget|override).{0,40}(?:previous|prior|system|developer)"
            r".{0,35}(?:instructions?|rules?|prompt)"
            r"|(?:忽略|无视|覆盖|绕过).{0,35}(?:之前|系统|开发者).{0,30}(?:指令|规则|提示词)"
        ),
    ),
    _Detector(
        "CTX-ROLE-SPOOF",
        "indirect_prompt_injection",
        "high",
        _rx(r"^\s*(?:system|developer|assistant)\s*(?:message)?\s*:"),
    ),
    _Detector(
        "CTX-EXFILTRATE",
        "data_exfiltration_instruction",
        "critical",
        _rx(
            r"(?:send|upload|exfiltrate|post).{0,80}(?:secret|token|password|conversation|"
            r"private|credential).{0,80}https?://"
            r"|(?:发送|上传|外传).{0,60}(?:密钥|令牌|密码|对话|隐私|凭据).{0,60}https?://"
        ),
    ),
    _Detector(
        "CTX-ACTIVE-CONTENT",
        "improper_output_handling",
        "high",
        _rx(r"<(?:img|script|iframe)\b[^>]*(?:src|href)\s*=\s*[\"']?https?://"),
    ),
)

_OUTPUT_SECRET_DETECTORS = (
    _Detector(
        "OUT-OPENAI-KEY",
        "credential_disclosure",
        "critical",
        _rx(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    ),
    _Detector(
        "OUT-AWS-KEY",
        "credential_disclosure",
        "critical",
        _rx(r"\bAKIA[A-Z0-9]{16}\b"),
    ),
    _Detector(
        "OUT-BEARER-TOKEN",
        "credential_disclosure",
        "critical",
        _rx(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}\b"),
    ),
    _Detector(
        "OUT-PRIVATE-KEY",
        "credential_disclosure",
        "critical",
        _rx(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)

_OUTPUT_PROMPT_LEAK = _Detector(
    "OUT-SYSTEM-PROMPT",
    "system_prompt_disclosure",
    "high",
    _rx(r"(?:BEGIN|START)\s+(?:OF\s+)?(?:SYSTEM|DEVELOPER)\s+(?:PROMPT|MESSAGE)"),
)


def _finding(detector: _Detector, evidence: str, action: SecurityAction) -> SecurityFinding:
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    return SecurityFinding(
        category=detector.category,
        severity=detector.severity,
        action=action,
        detector_id=detector.detector_id,
        evidence_hash=digest,
    )


def assess_user_query(text: str) -> SecurityDecision:
    """Block requests to extract protected internals while allowing safety education."""
    findings: list[SecurityFinding] = []
    discussing = bool(_DISCUSSION_PATTERN.search(text))
    for detector in _USER_DETECTORS:
        match = detector.pattern.search(text)
        if match is None:
            continue
        if discussing and detector.category == "agent_goal_hijack":
            continue
        findings.append(_finding(detector, match.group(0), "block"))
    return SecurityDecision(
        action="block" if findings else "allow",
        findings=tuple(findings),
        safe_text=(
            SAFE_REFUSAL_ZH
            if findings and re.search(r"[\u4e00-\u9fff]", text)
            else SAFE_REFUSAL
            if findings
            else text
        ),
        metadata={"surface": "user_input"},
    )


def sanitize_untrusted_content(text: str) -> SecurityDecision:
    """Quarantine instruction-like segments in retrieved or web content.

    Splitting by sentence/line preserves nearby factual material and citations
    instead of discarding an entire source because one segment is malicious.
    """
    segments = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    safe_segments: list[str] = []
    findings: list[SecurityFinding] = []
    for segment in segments:
        if not segment:
            continue
        segment_findings = []
        for detector in _CONTEXT_DETECTORS:
            match = detector.pattern.search(segment)
            if match is not None:
                segment_findings.append(_finding(detector, match.group(0), "quarantine"))
        if segment_findings:
            findings.extend(segment_findings)
            safe_segments.append(QUARANTINE_MARKER)
        else:
            safe_segments.append(segment)
    return SecurityDecision(
        action="quarantine" if findings else "allow",
        findings=tuple(findings),
        safe_text="\n".join(safe_segments),
        metadata={
            "surface": "untrusted_context",
            "quarantined_segments": len(
                [segment for segment in safe_segments if segment == QUARANTINE_MARKER]
            ),
        },
    )


def assess_untrusted_collection(texts: list[str]) -> dict[str, object]:
    decisions = [sanitize_untrusted_content(text) for text in texts]
    findings = [finding.as_payload() for decision in decisions for finding in decision.findings]
    return {
        "action": "quarantine" if findings else "allow",
        "allowed": True,
        "policy_version": SECURITY_POLICY_VERSION,
        "surface": "untrusted_context",
        "items_scanned": len(texts),
        "findings": findings,
        "quarantined_segments": sum(
            int(decision.metadata.get("quarantined_segments", 0)) for decision in decisions
        ),
    }


def inspect_agent_output(text: str) -> SecurityDecision:
    """Redact credential-shaped output and block explicit system-prompt dumps."""
    prompt_match = _OUTPUT_PROMPT_LEAK.pattern.search(text)
    if prompt_match is not None:
        finding = _finding(_OUTPUT_PROMPT_LEAK, prompt_match.group(0), "block")
        return SecurityDecision(
            action="block",
            findings=(finding,),
            safe_text=SAFE_REFUSAL,
            metadata={"surface": "agent_output"},
        )

    safe_text = text
    findings: list[SecurityFinding] = []
    for detector in _OUTPUT_SECRET_DETECTORS:
        matches = list(detector.pattern.finditer(safe_text))
        findings.extend(_finding(detector, match.group(0), "redact") for match in matches)
        safe_text = detector.pattern.sub("[REDACTED_CREDENTIAL]", safe_text)
    return SecurityDecision(
        action="redact" if findings else "allow",
        findings=tuple(findings),
        safe_text=safe_text,
        metadata={"surface": "agent_output"},
    )


def authorize_tool(
    tool: str,
    *,
    deployment_enabled: bool,
    user_authorized: bool,
) -> SecurityDecision:
    allowed = deployment_enabled and user_authorized
    if allowed:
        return SecurityDecision(
            action="allow",
            metadata={"surface": "tool", "tool": tool, "user_authorized": True},
        )
    evidence = f"{tool}:{deployment_enabled}:{user_authorized}"
    finding = SecurityFinding(
        category="excessive_agency",
        severity="high",
        action="block",
        detector_id="TOOL-EXPLICIT-CONSENT",
        evidence_hash=hashlib.sha256(evidence.encode()).hexdigest(),
    )
    return SecurityDecision(
        action="block",
        findings=(finding,),
        metadata={
            "surface": "tool",
            "tool": tool,
            "deployment_enabled": deployment_enabled,
            "user_authorized": user_authorized,
        },
    )
