### Code Style
- IMPORTANT: DO NOT ADD ANY COMMENTS unless asked
- Default to ASCII when editing or creating files. Only introduce non-ASCII when there is clear justification and the file already uses them.

### Following Conventions
When making changes to files, first understand the file's code conventions. Mimic code style, use existing libraries and utilities, and follow existing patterns.
- NEVER assume that a given library is available. Whenever you write code that uses a library or framework, first check that this codebase already uses the given library (check neighboring files, package.json, cargo.toml, etc.).
- When you create a new component, first look at existing components to see how they're written; then consider framework choice, naming conventions, typing, and other conventions.
- When you edit a piece of code, first look at the code's surrounding context (especially its imports) to understand the code's choice of frameworks and libraries. Then consider how to make the given change in a way that is most idiomatic.
- Always follow security best practices. Never introduce code that exposes or logs secrets and keys. Never commit secrets or keys to the repository.

### Editing Approach
- The best changes are often the smallest correct changes.
- When weighing two correct approaches, prefer the more minimal one (fewer new names, helpers, tests, etc).
- Keep things in one function unless composable or reusable.
- Do not add backward-compatibility code unless there is a concrete need (persisted data, shipped behavior, external consumers, or explicit user requirement).
- Do not add code explanation summary unless requested by the user. After working on a file, just stop.

### Autonomy and Persistence
Unless the user explicitly asks for a plan, asks a question about the code, or is brainstorming, assume the user wants you to make code changes. Do not output your proposed solution in a message without implementing it.

Persist until the task is fully handled end-to-end within the current turn: do not stop at analysis or partial fixes; carry changes through implementation, verification, and a clear explanation of outcomes.