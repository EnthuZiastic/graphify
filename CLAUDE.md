# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`graphifyy` (PyPI) / `graphify` (CLI). Turns any folder of code, docs, papers, images, or video/audio into a queryable knowledge graph. Distributed as both a Python library and an AI-assistant skill that installs into 15+ platforms (Claude Code, Codex, OpenCode, Cursor, Gemini CLI, Copilot CLI/VS Code, Aider, Trae, Kiro, Antigravity, etc.).

## Commands

```bash
# install with all dev deps used in CI
pip install -e ".[mcp,pdf,watch]"

# full test suite (CI uses Python 3.10 and 3.12)
pytest tests/ -q

# single test file / single test
pytest tests/test_extract.py -q
pytest tests/test_extract.py::test_python_extract -q

# benchmark fixtures (not part of default suite)
python tests/bench_extract.py

# end-to-end install smoke check (CI step)
graphify --help
graphify install
```

There is no Makefile, no lint/format config, and no separate build script — `pyproject.toml` is the single source of truth (setuptools backend). Releases are published to PyPI as `graphifyy`; the in-repo version lives in `pyproject.toml` under `[project].version`.

CI workflow: `.github/workflows/ci.yml` runs on pushes/PRs to branches `v1`-`v4` and `main`. The current dev branch is `v6` — if you push it, CI may not trigger automatically; check or update the workflow's branch filter.

## Architecture

The library is a flat package: every stage of the pipeline is one module under `graphify/`, communicating only through plain dicts and `networkx.Graph` instances. No shared state, no I/O outside `graphify-out/`.

```
detect → extract → build_graph → cluster → analyze → report → export
```

| Module | Responsibility |
|---|---|
| `detect.py` | `collect_files(root)` — walks corpus, applies `.graphifyignore`, never crosses VCS roots |
| `extract.py` | `extract(path)` — dispatches per file type. Code via tree-sitter AST, docs/images via Claude subagents, audio/video via faster-whisper |
| `build.py` | `build_graph(extractions)` — merges extraction dicts into a NetworkX graph |
| `cluster.py` | Leiden community detection (graspologic) — no embeddings, edge density only |
| `analyze.py` | God nodes, surprising connections, suggested questions |
| `report.py` | Renders `GRAPH_REPORT.md` |
| `export.py` | `graph.json`, `graph.html` (vis.js), Obsidian vault, SVG, GraphML, Cypher |
| `ingest.py` | URL/paper/tweet/video fetch into the corpus dir |
| `cache.py` | SHA256-keyed semantic-extraction cache in `graphify-out/cache/` |
| `validate.py` | Schema check before `build_graph()` consumes extractor output |
| `security.py` | URL/path/label validation — see "Security boundary" below |
| `serve.py` | MCP stdio server over `graph.json` |
| `watch.py` | File-watch loop, writes a flag file on change |
| `benchmark.py` | Token comparison: raw corpus vs subgraph (the README's "71.5x" number) |
| `hooks.py` | Platform-agnostic git post-commit/post-checkout hook installer |
| `manifest.py` | Tracks file mtimes for incremental updates (mtime-based — invalid after `git clone`, always gitignored) |
| `__main__.py` | Single argparse CLI (no Click). All `graphify <subcommand>` routing lives here, including the per-platform installer functions (`claude_install`, `_install_codex_hook`, `_agents_install`, `_kiro_install`, `_cursor_install`, `_install_opencode_plugin`, `_antigravity_install`, etc.) |

### Extraction output schema

Every extractor (per language, per file type) returns the same shape:

```json
{
  "nodes": [{"id": "...", "label": "...", "source_file": "...", "source_location": "L42"}],
  "edges": [{"source": "...", "target": "...", "relation": "calls|imports|uses|...",
             "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}]
}
```

`validate.py` enforces this before `build_graph()`. INFERRED edges carry a `confidence_score` (0.0–1.0); EXTRACTED is always 1.0.

### Adding a language extractor

1. `extract_<lang>(path) -> dict` in `extract.py` (tree-sitter parse → walk → emit `nodes`/`edges` → second pass for `calls`).
2. Register the suffix in `extract()` dispatch + `collect_files()`.
3. Add the suffix to `CODE_EXTENSIONS` in `detect.py` and `_WATCHED_EXTENSIONS` in `watch.py`.
4. Add the `tree-sitter-<lang>` package to `pyproject.toml` deps **only if it ships Linux + macOS wheels** — Windows-only wheels (e.g. the previous `tree-sitter-vbnet`) break CI and have been removed historically.
5. Fixture under `tests/fixtures/` + tests in `tests/test_languages.py`.

### Platform installer pattern

Each supported AI assistant has its own `<platform>_install` / `_install_<platform>_hook` pair in `__main__.py` plus a `skill-<platform>.md` packaged via `[tool.setuptools.package-data]`. Installer behavior splits three ways:

- **Hook-capable** (Claude Code, Codex, Gemini CLI, OpenCode): writes a per-project hook config that injects the "read GRAPH_REPORT.md before grepping" reminder before tool calls.
- **Rules-file-only** (Cursor, Kiro, Antigravity, VS Code Copilot Chat): writes an always-included rules/steering file — no hook needed.
- **AGENTS.md only** (Aider, OpenClaw, Factory Droid, Trae, Hermes): no hook mechanism; relies on always-on AGENTS.md.

`_resolve_graphify_exe()` resolves an absolute path at install time so hooks work on Windows where `python3` may be missing (this was the root of issues #651 / #522 — see CHANGELOG entries for 0.6.4–0.6.6 before changing hook generation).

### Security boundary

All external input must pass through `graphify/security.py`:

- URLs → `validate_url()` (http/https only) + `_NoFileRedirectHandler` (blocks `file://` redirects)
- Fetched bytes/text → `safe_fetch()` / `safe_fetch_text()` (size cap + timeout)
- Graph file paths → `validate_graph_path()` (must resolve inside `graphify-out/`)
- Node labels → `sanitize_label()` (strips control chars, caps 256 chars, HTML-escapes)

Threat model in `SECURITY.md`. Never bypass these on `ingest`, `add`, `clone`, `serve`, or any HTML/SVG export path.

## Repo conventions

- `graphify-out/` is the canonical output dir. Subdirs: `cache/` (SHA256 semantic cache, optional to commit), `transcripts/` (Whisper output), plus `GRAPH_REPORT.md`, `graph.html`, `graph.json`, `manifest.json` (always gitignore — mtime-based), `cost.json` (local token tracking, gitignore).
- Tests are pure unit tests — no network, no FS writes outside `tmp_path`. Keep this property when adding tests.
- This repo has its own `AGENTS.md` telling assistants to read `graphify-out/GRAPH_REPORT.md` before answering architecture questions and to run `graphify update .` after editing code. Follow it.
- Optional dependency groups (`mcp`, `neo4j`, `pdf`, `watch`, `svg`, `leiden`, `office`, `video`, `kimi`, `sql`, `all`) gate heavy or platform-specific imports. Code that uses them must import lazily inside the function and produce a clear "install with `pip install graphifyy[<group>]`" error if missing.
