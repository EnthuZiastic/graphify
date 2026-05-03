import json
from pathlib import Path
from graphify.build import build_from_json, build

FIXTURES = Path(__file__).parent / "fixtures"

def load_extraction():
    return json.loads((FIXTURES / "extraction.json").read_text())

def test_build_from_json_node_count():
    G = build_from_json(load_extraction())
    assert G.number_of_nodes() == 4

def test_build_from_json_edge_count():
    G = build_from_json(load_extraction())
    assert G.number_of_edges() == 4

def test_nodes_have_label():
    G = build_from_json(load_extraction())
    assert G.nodes["n_transformer"]["label"] == "Transformer"

def test_edges_have_confidence():
    G = build_from_json(load_extraction())
    data = G.edges["n_attention", "n_concept_attn"]
    assert data["confidence"] == "INFERRED"

def test_ambiguous_edge_preserved():
    G = build_from_json(load_extraction())
    data = G.edges["n_layernorm", "n_concept_attn"]
    assert data["confidence"] == "AMBIGUOUS"

def test_legacy_node_source_canonicalized():
    """Legacy 'source' key on nodes is renamed to 'source_file' before graph build."""
    ext = {"nodes": [{"id": "n1", "label": "A", "file_type": "code", "source": "a.py"}],
           "edges": [], "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    assert "source_file" in G.nodes["n1"]
    assert G.nodes["n1"]["source_file"] == "a.py"
    assert "source" not in G.nodes["n1"]


def test_legacy_edge_from_to_canonicalized():
    """Legacy 'from'/'to' keys on edges are accepted alongside 'source'/'target'."""
    ext = {"nodes": [{"id": "n1", "label": "A", "file_type": "code", "source_file": "a.py"},
                     {"id": "n2", "label": "B", "file_type": "code", "source_file": "b.py"}],
           "edges": [{"from": "n1", "to": "n2", "relation": "calls",
                      "confidence": "EXTRACTED", "source_file": "a.py", "weight": 1.0}],
           "input_tokens": 0, "output_tokens": 0}
    G = build_from_json(ext)
    assert G.number_of_edges() == 1


def test_build_merges_multiple_extractions():
    ext1 = {"nodes": [{"id": "n1", "label": "A", "file_type": "code", "source_file": "a.py"}],
            "edges": [], "input_tokens": 0, "output_tokens": 0}
    ext2 = {"nodes": [{"id": "n2", "label": "B", "file_type": "document", "source_file": "b.md"}],
            "edges": [{"source": "n1", "target": "n2", "relation": "references",
                       "confidence": "INFERRED", "source_file": "b.md", "weight": 1.0}],
            "input_tokens": 0, "output_tokens": 0}
    G = build([ext1, ext2])
    assert G.number_of_nodes() == 2
    assert G.number_of_edges() == 1


def test_dedup_prefers_ast_node_over_llm_with_same_label():
    """When AST and LLM emit the same entity with different IDs, the AST node wins.

    AST nodes carry `source_location` (precise line info). LLM-style IDs are
    typically shorter (no parent-dir prefix). The merge must keep the AST id and
    redirect edges that referenced the LLM id.
    """
    ext = {
        "nodes": [
            # AST-shape node — has source_location
            {"id": "auth_session_validate_token", "label": "ValidateToken",
             "file_type": "code", "source_file": "src/auth/session.py",
             "source_location": "L42"},
            # LLM-shape node — same label, shorter id, no source_location
            {"id": "session_validatetoken", "label": "ValidateToken",
             "file_type": "code", "source_file": "src/auth/session.py"},
            {"id": "caller", "label": "Caller", "file_type": "code",
             "source_file": "src/handler.py"},
        ],
        # Edge points at the LLM id; after merge it should land on the AST id.
        "edges": [
            {"source": "caller", "target": "session_validatetoken",
             "relation": "calls", "confidence": "EXTRACTED",
             "source_file": "src/handler.py", "weight": 1.0},
        ],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(ext)
    assert G.number_of_nodes() == 2  # validatetoken merged
    assert "auth_session_validate_token" in G.nodes
    assert "session_validatetoken" not in G.nodes
    # Edge survived and points at the AST node
    assert G.has_edge("caller", "auth_session_validate_token")


def test_orphan_semantic_node_anchored_to_source_file():
    """Zero-degree semantic concept nodes get attached to their source file's AST anchor."""
    ext = {
        "nodes": [
            # AST file/module anchor
            {"id": "config_app", "label": "config/app.exs",
             "file_type": "code", "source_file": "config/app.exs",
             "source_location": "L1"},
            # Semantic-only concept extracted from the same file — no edges, no source_location
            {"id": "oauth_providers_config", "label": "OAuth Providers Config",
             "file_type": "rationale", "source_file": "config/app.exs"},
        ],
        "edges": [],
        "input_tokens": 0, "output_tokens": 0,
    }
    G = build_from_json(ext)
    # The orphan concept should now connect to its file anchor via part_of.
    assert G.has_edge("oauth_providers_config", "config_app")
    edge_data = G.edges["oauth_providers_config", "config_app"]
    assert edge_data["relation"] == "part_of"
    assert edge_data["confidence"] == "INFERRED"
