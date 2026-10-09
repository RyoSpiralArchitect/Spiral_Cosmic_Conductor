# Security and scope

This is an alpha research harness, not a security-certified execution platform.

- All default demos use scripted workers and no internet.
- The optional local chat adapter permits only loopback IP endpoints with explicit ports; it disables redirect following and environment proxies.
- Model text is untrusted. Model output must be a strict JSON patch and cannot verify itself or mint authorization tickets.
- The SQLite ledger and local run outputs are not encrypted. Do not put sensitive or proprietary documents in a task unless you have explicitly chosen storage and deletion procedures.
- In-process private contexts are logically separated, **not** protected against malicious Python code in the same interpreter.
- The audit SHA-256 chain and artifact manifest detect accidental tampering under a trusted local environment, **not** an adversary able to rewrite all records.
- The SpiralTorch adapter is currently a typed *proposal only*. A simulated proof receipt is not proof of actual accelerator readiness.

For reported security bugs, avoid posting confidential exploit details in a public issue. Contact the repository maintainer through a channel they explicitly publish.
