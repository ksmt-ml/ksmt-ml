# Security policy

## Supported versions

Security fixes apply to the current default branch and released model bundles.
No model bundle has been released yet.

## Reporting a vulnerability

Do not publish vulnerability details in an issue. Use the repository's GitHub
**Security** tab and **Report a vulnerability** flow. If unavailable, open an
issue without technical details and request a private reporting channel.

## Scope

Relevant reports include unsafe processing of workload profiles, dependency or
deserialization vulnerabilities, checksum bypasses, malicious ONNX behavior,
and disclosure of private traces, formulas, identifiers, or credentials.

Model bundles are untrusted input until their manifest, checksums, schema, and
parameter domains are validated. Solver workloads require process isolation and
resource limits appropriate to the production consumer.
