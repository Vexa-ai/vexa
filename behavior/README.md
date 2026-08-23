# behavior — the product's voice (top-level, peer of the machinery)

THE BOUNDARY: **machinery is what compiles into the runtime; behavior is what the runtime
loads.** The images are the interpreter — this tree (and its private sibling) is the program.
Corollary: **machinery contains no prose.** Everything a human or an agent reads is behavior —
highest-level, diverse, and largely PROPRIETARY. This top-level tree holds only the PUBLISHED
showcase; the real voice is a private tree of the same shape, mounted at `VEXA_BEHAVIOR_DIR`
(the `_global` deployment pattern) and resolved before these files. Flow params override both.

- `prompts/`     flow kickoffs and instructions (showcase examples)
- `workspaces/`  the workspace seeds — what a new personal/shared/org workspace is born as
- `flows/`       flow compositions (canonical exports of the registry)

Machinery (core/, clients/) knows HOW; this tree knows WHAT TO SAY. It changes at content speed —
a git commit here or in the private tree, zero image rebuilds.
