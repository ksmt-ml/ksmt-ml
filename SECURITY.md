# Security policy

## Supported versions

Security fixes are applied to the current default branch. Research snapshots,
superseded schemas, and historical experiment artifacts are retained for
provenance but are not maintained as supported releases.

## Reporting a vulnerability

Do not publish vulnerability details in an issue. Use the repository's GitHub
**Security** tab and its **Report a vulnerability** flow. If that flow is not
available, open an issue containing no technical details and ask a maintainer
for a private reporting channel. Include affected revisions, reproduction
steps, impact, and any known mitigation only in the private report.

Public issues are appropriate for ordinary correctness bugs that do not expose
credentials, enable code execution, compromise artifact integrity, or disclose
private data.

## Scope

Relevant reports include unsafe processing of untrusted SMT-LIB input,
dependency or deserialization vulnerabilities, checksum bypasses, malicious
model-bundle behavior, and accidental disclosure of private benchmark data or
credentials.

The current ONNX bundle is an interface prototype, not a security boundary.
Callers must validate bundle provenance and run untrusted solver inputs with
process isolation and resource limits.
