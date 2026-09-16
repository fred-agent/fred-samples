# AGENTS.md

This repository uses root `CLAUDE.md` as the primary development workflow and governance guide.

Codex and other AI coding assistants must read and follow root `CLAUDE.md` before making code or
documentation changes. Everything repository-specific — the layout of `agents/`,
`knowledge-bases/`, `apps/`, `servers/mcp/python/` and `dockerfiles/`, which packages are covered
by automated validation, and which commands to run — is described there and is not repeated here.

---

## Required read order

Before making any change, read and follow:

1. Root `CLAUDE.md`
2. This root `AGENTS.md`
3. Any nested `AGENTS.md` or `CLAUDE.md` in the target subdirectory
4. The relevant local `README.md`, `Makefile`, and package metadata

When `CLAUDE.md` refers to Claude or Claude Code, apply the same instruction to Codex unless
technically impossible.

---

## Conflict resolution order

1. Explicit user instruction
2. Closest nested `AGENTS.md` or `CLAUDE.md`
3. Root `CLAUDE.md`
4. Root `AGENTS.md`
5. Local README, Makefile, or package metadata guidance

If there is a conflict that cannot be resolved safely, stop and ask for clarification before
changing files.

---

## Mandatory defaults

- Keep changes minimal and didactic.
- Do not modify unrelated samples.
- Do not introduce architecture that belongs in Fred core.
- Do not copy internal Fred code unless explicitly requested.
- Prefer current public `fred-sdk` and `fred-runtime` APIs.
- Keep default validation offline.
- Inspect the actual filesystem before changing imports, paths, registry entries, or configuration.
- Describe only what this repository can prove: no citations of OpenSpec changes, RFCs, design
  documents or issue numbers that live in another repository, and no claims about Fred's behaviour
  that cannot be checked from this tree. See "Only claim what this repository can prove" in root
  `CLAUDE.md`.
- Update documentation when commands, package names, import paths, agent IDs, MCP dependencies,
  ports, configuration, or runtime behavior change. A command written in a README must exist.

---

## Validation

From the repository root, before reporting any change done:

```bash
make code-quality
make test
```

Both fan out to the packages registered in the root `Makefile`. To work on a single package, run
the same two targets from that package's directory.

Not every area is covered by these commands — `servers/mcp/python/` and `apps/` are outside them,
for the reasons given in root `CLAUDE.md`. Know which regime the code you touched is in before
reporting it validated.

Do not claim validation succeeded unless the commands were actually run successfully.
