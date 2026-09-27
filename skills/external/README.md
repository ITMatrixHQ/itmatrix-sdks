# Agent skill

The [itmatrix-api skill](itmatrix-api/SKILL.md) helps an agent work with the ITMatrixHQ API. It prefers the Python and TypeScript SDKs when they are installed, and it can also call the documented public operations directly over HTTPS. It covers calendar bars, GEX analysis, provenance, entitlements and streams. Copy the entire `itmatrix-api` folder into your agent's skills directory. For Codex, that is `${CODEX_HOME:-$HOME/.codex}/skills/itmatrix-api`. Installing a package does not install agent instructions.
