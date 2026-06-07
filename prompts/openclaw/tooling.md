Available tools are policy-filtered. Names are case-sensitive; call exactly as listed.
TOOLS.md is usage guidance, not availability.
For long waits, avoid rapid poll loops: use exec with enough yieldMs or process(action=poll, timeout=<ms>).
Larger work: use sessions_spawn; completion is push-based.