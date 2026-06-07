All tool calls and responses are recorded in a trajectory for audit and replay.

### What's Recorded
- Every tool call with full arguments
- Every tool response with output
- Timestamps and session context
- Security policy decisions

### Session DAG
OpenClaw maintains a directed acyclic graph (DAG) of session branches:
- create_branch — create a new session branch from the current state
- merge_branch — merge a branch back into the main session
- list_branches — view all session branches