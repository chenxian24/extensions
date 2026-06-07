OpenClaw provides multiple sandbox backends for isolated code execution:

- **local** — subprocess with timeout and working directory isolation
- **docker** — container-based isolation with volume mounts, network-disabled, memory-limited
- **ssh** — remote execution via SSH
- **openshell** — local subprocess with sanitized environment, restricted PATH, no secrets leaked

### When to Use Sandboxing
- **Always** for code from untrusted sources
- **Always** for code that modifies system state
- **Recommended** for exploratory commands (rm, chmod, etc.)
- **Optional** for read-only operations on trusted code