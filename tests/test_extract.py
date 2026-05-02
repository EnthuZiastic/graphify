import json
from pathlib import Path
from graphify.extract import (
    extract_python,
    extract,
    extract_js,
    collect_files,
    _make_id,
    _resolve_js_import_to_file,
    _TSCONFIG_ALIAS_CACHE,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_make_id_strips_dots_and_underscores():
    assert _make_id("_auth") == "auth"
    assert _make_id(".httpx._client") == "httpx_client"


def test_make_id_consistent():
    """Same input always produces same output."""
    assert _make_id("foo", "Bar") == _make_id("foo", "Bar")


def test_make_id_no_leading_trailing_underscores():
    result = _make_id("__init__")
    assert not result.startswith("_")
    assert not result.endswith("_")


def test_extract_python_finds_class():
    result = extract_python(FIXTURES / "sample.py")
    labels = [n["label"] for n in result["nodes"]]
    assert "Transformer" in labels


def test_extract_python_finds_methods():
    result = extract_python(FIXTURES / "sample.py")
    labels = [n["label"] for n in result["nodes"]]
    assert any("__init__" in l or "forward" in l for l in labels)


def test_extract_python_no_dangling_edges():
    """All edge sources must reference a known node (targets may be external imports)."""
    result = extract_python(FIXTURES / "sample.py")
    node_ids = {n["id"] for n in result["nodes"]}
    for edge in result["edges"]:
        assert edge["source"] in node_ids, f"Dangling source: {edge['source']}"


def test_structural_edges_are_extracted():
    """contains / method / inherits / imports edges must always be EXTRACTED."""
    result = extract_python(FIXTURES / "sample.py")
    structural = {"contains", "method", "inherits", "imports", "imports_from"}
    for edge in result["edges"]:
        if edge["relation"] in structural:
            assert edge["confidence"] == "EXTRACTED", f"Expected EXTRACTED: {edge}"


def test_extract_merges_multiple_files():
    files = list(FIXTURES.glob("*.py"))
    result = extract(files)
    assert len(result["nodes"]) > 0
    assert result["input_tokens"] == 0


def test_collect_files_from_dir():
    from graphify.detect import CODE_EXTENSIONS
    files = collect_files(FIXTURES)
    assert all(f.suffix in CODE_EXTENSIONS for f in files)
    assert len(files) > 0


def test_collect_files_skips_hidden():
    files = collect_files(FIXTURES)
    for f in files:
        assert not any(part.startswith(".") for part in f.parts)


def test_collect_files_follows_symlinked_directory(tmp_path):
    real_dir = tmp_path / "real_src"
    real_dir.mkdir()
    (real_dir / "lib.py").write_text("x = 1")
    (tmp_path / "linked_src").symlink_to(real_dir)

    files_no = collect_files(tmp_path, follow_symlinks=False)
    files_yes = collect_files(tmp_path, follow_symlinks=True)

    assert [f.name for f in files_no].count("lib.py") == 1
    assert [f.name for f in files_yes].count("lib.py") == 2


def test_collect_files_handles_circular_symlinks(tmp_path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("x = 1")
    (sub / "cycle").symlink_to(tmp_path)

    files = collect_files(tmp_path, follow_symlinks=True)
    assert any(f.name == "mod.py" for f in files)


def test_no_dangling_edges_on_extract():
    """After merging multiple files, no internal edges should be dangling."""
    files = list(FIXTURES.glob("*.py"))
    result = extract(files)
    node_ids = {n["id"] for n in result["nodes"]}
    internal_relations = {"contains", "method", "inherits", "calls"}
    for edge in result["edges"]:
        if edge["relation"] in internal_relations:
            assert edge["source"] in node_ids, f"Dangling source: {edge}"
            assert edge["target"] in node_ids, f"Dangling target: {edge}"


def test_calls_edges_emitted():
    """Call-graph pass must produce INFERRED calls edges."""
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = [e for e in result["edges"] if e["relation"] == "calls"]
    assert len(calls) > 0, "Expected at least one calls edge"


def test_calls_edges_are_extracted():
    """AST-resolved call edges are deterministic and should be EXTRACTED/1.0."""
    result = extract_python(FIXTURES / "sample_calls.py")
    for edge in result["edges"]:
        if edge["relation"] == "calls":
            assert edge["confidence"] == "EXTRACTED"
            assert edge["weight"] == 1.0


def test_python_call_edges_have_call_context():
    result = extract_python(FIXTURES / "sample_calls.py")
    call_edges = [e for e in result["edges"] if e["relation"] == "calls"]
    assert call_edges
    assert all(e.get("context") == "call" for e in call_edges)


def test_calls_no_self_loops():
    result = extract_python(FIXTURES / "sample_calls.py")
    for edge in result["edges"]:
        if edge["relation"] == "calls":
            assert edge["source"] != edge["target"], f"Self-loop: {edge}"


def test_run_analysis_calls_compute_score():
    """run_analysis() calls compute_score() - must appear as a calls edge."""
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"}
    node_by_label = {n["label"]: n["id"] for n in result["nodes"]}
    src = node_by_label.get("run_analysis()")
    tgt = node_by_label.get("compute_score()")
    assert src and tgt, "run_analysis or compute_score node not found"
    assert (src, tgt) in calls, f"run_analysis -> compute_score not found in {calls}"


def test_run_analysis_calls_normalize():
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"}
    node_by_label = {n["label"]: n["id"] for n in result["nodes"]}
    src = node_by_label.get("run_analysis()")
    tgt = node_by_label.get("normalize()")
    assert src and tgt
    assert (src, tgt) in calls


def test_method_calls_module_function():
    """Analyzer.process() calls run_analysis() - cross class→function calls edge."""
    result = extract_python(FIXTURES / "sample_calls.py")
    calls = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"}
    node_by_label = {n["label"]: n["id"] for n in result["nodes"]}
    src = node_by_label.get(".process()")
    tgt = node_by_label.get("run_analysis()")
    assert src and tgt
    assert (src, tgt) in calls


def test_calls_deduplication():
    """Same caller→callee pair must appear only once even if called multiple times."""
    result = extract_python(FIXTURES / "sample_calls.py")
    call_pairs = [(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "calls"]
    assert len(call_pairs) == len(set(call_pairs)), "Duplicate calls edges found"


def test_cross_file_calls_skip_ambiguous_duplicate_labels(tmp_path):
    """Unqualified cross-file calls must not guess between duplicate helper names."""
    caller = tmp_path / "caller.py"
    helper_a = tmp_path / "a.py"
    helper_b = tmp_path / "b.py"
    caller.write_text("def run():\n    log()\n")
    helper_a.write_text("def log():\n    return 'a'\n")
    helper_b.write_text("def log():\n    return 'b'\n")

    result = extract([caller, helper_a, helper_b], cache_root=tmp_path)
    nodes = {n["id"]: n for n in result["nodes"]}
    calls = [
        e for e in result["edges"]
        if e["relation"] == "calls" and e["confidence"] == "INFERRED"
    ]

    assert not any(
        nodes[e["source"]]["label"] == "run()" and nodes[e["target"]]["label"] == "log()"
        for e in calls
    )


# ── Svelte alias / extension resolution (fix for $lib import undercount) ──────

def _build_svelte_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """Set up a minimal admin-style corpus: tsconfig with `$lib/*` alias, a TS
    helper at the alias target, and a Svelte component importing from it."""
    _TSCONFIG_ALIAS_CACHE.clear()
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {
            "baseUrl": ".",
            "paths": {
                "$lib": ["./svelte/lib"],
                "$lib/*": ["./svelte/lib/*"],
            },
        },
    }))
    lib = assets / "svelte" / "lib" / "utils"
    lib.mkdir(parents=True)
    helper = lib / "rbac.domain.ts"
    helper.write_text(
        "export function getFeatureContext() { return null; }\n"
        "export const F_FOO = 'foo';\n"
    )
    page_dir = assets / "svelte" / "Ops"
    page_dir.mkdir(parents=True)
    page = page_dir / "Page.svelte"
    page.write_text(
        '<script lang="ts">\n'
        "  import { getFeatureContext, F_FOO } from '$lib/utils/rbac.domain';\n"
        "  const ctx = getFeatureContext();\n"
        "</script>\n"
        "<div>{ctx} {F_FOO}</div>\n"
    )
    return page, helper


def test_resolve_js_import_appends_ts_extension(tmp_path):
    target = tmp_path / "rbac.domain"
    (tmp_path / "rbac.domain.ts").write_text("export const X = 1;")
    resolved = _resolve_js_import_to_file(target)
    assert resolved.suffix == ".ts"
    assert resolved.is_file()


def test_resolve_js_import_prefers_ts_over_js_when_both_exist(tmp_path):
    target = tmp_path / "helper"
    (tmp_path / "helper.js").write_text("module.exports = {};")
    (tmp_path / "helper.ts").write_text("export const X = 1;")
    resolved = _resolve_js_import_to_file(target)
    # TS wins because it's first in the resolution order (TS-first corpora
    # are the common case for Svelte/Vite admin frontends).
    assert resolved.suffix == ".ts"


def test_resolve_js_import_falls_back_to_index_file(tmp_path):
    pkg = tmp_path / "icons"
    pkg.mkdir()
    (pkg / "index.ts").write_text("export const liveGif = 'x';")
    resolved = _resolve_js_import_to_file(pkg)
    assert resolved.name == "index.ts"


def test_resolve_js_import_returns_unchanged_when_missing(tmp_path):
    target = tmp_path / "nope"
    resolved = _resolve_js_import_to_file(target)
    assert resolved == target


def test_extract_js_parses_svelte_imports(tmp_path):
    """Svelte SFC must go through the lenient TS grammar so script-block
    imports are captured. JS grammar fails on the `<script>` HTML wrapper."""
    page, _ = _build_svelte_corpus(tmp_path)
    result = extract_js(page)
    imports = [e for e in result["edges"] if e["relation"] == "imports_from"]
    assert imports, "Svelte component imports must be extracted"


def _file_node_id(result: dict, path: Path) -> str:
    """`extract()` remaps file ids and source_file from absolute → project-
    relative. Match by label (always the basename) and source_file ending."""
    name = path.name
    for n in result["nodes"]:
        if (n.get("label") == name
                and n.get("file_type") == "code"
                and n.get("source_location") == "L1"):
            sf = n.get("source_file") or ""
            if sf == str(path) or sf.endswith(name):
                return n["id"]
    raise AssertionError(f"no file node for {path}; nodes={result['nodes']}")


def test_svelte_alias_import_links_to_real_file_node(tmp_path):
    """`$lib/utils/rbac.domain` (no extension) must resolve to the .ts file
    node id so the import edge isn't dangling — the regression we hit on
    packages/admin where 50+ batch components looked unrelated to rbac.domain."""
    page, helper = _build_svelte_corpus(tmp_path)
    result = extract([page, helper], cache_root=tmp_path)
    page_id = _file_node_id(result, page)
    helper_id = _file_node_id(result, helper)
    pairs = {(e["source"], e["target"]) for e in result["edges"]
             if e["relation"] == "imports_from"}
    assert (page_id, helper_id) in pairs, (
        f"Expected {page_id} → {helper_id} imports_from edge; got {pairs}"
    )


def test_svelte_alias_import_emits_symbol_edges(tmp_path):
    """Named imports through an alias should still emit per-symbol `imports`
    edges that match the symbol nodes the helper file defines."""
    page, helper = _build_svelte_corpus(tmp_path)
    result = extract([page, helper], cache_root=tmp_path)
    page_id = _file_node_id(result, page)
    sym_targets = {
        e["target"] for e in result["edges"]
        if e["source"] == page_id and e["relation"] == "imports"
    }
    node_labels = {n["id"]: n.get("label", "") for n in result["nodes"]}
    imported_labels = {node_labels.get(t, "") for t in sym_targets}
    assert any("getFeatureContext" in l for l in imported_labels), imported_labels


def test_relative_import_without_extension_resolves(tmp_path):
    """`./helper` (no extension) must resolve to helper.ts, mirroring how
    Vite/TS bundler resolution works at runtime."""
    _TSCONFIG_ALIAS_CACHE.clear()
    caller = tmp_path / "caller.ts"
    caller.write_text("import { x } from './helper';\nconsole.log(x);\n")
    helper = tmp_path / "helper.ts"
    helper.write_text("export const x = 1;\n")
    result = extract([caller, helper], cache_root=tmp_path)
    pairs = {(e["source"], e["target"]) for e in result["edges"]
             if e["relation"] == "imports_from"}
    assert (_file_node_id(result, caller), _file_node_id(result, helper)) in pairs
