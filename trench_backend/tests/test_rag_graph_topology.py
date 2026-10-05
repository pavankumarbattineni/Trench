"""Asserts the compiled graph's actual shape -- catches drift between the
code and the module docstring/README diagram (see rag_graph.py's own
ASCII diagram and app/graph/README.md's Mermaid version), and specifically
guards against the input guardrail node/edges ever being reintroduced."""

from app.graph.rag_graph import build_graph


def test_start_goes_directly_to_load_thread_state_no_input_guardrail():
    graph = build_graph()

    assert "check_input_guardrail" not in graph.nodes
    assert ("__start__", "load_thread_state") in graph.edges
    start_edges = [edge for edge in graph.edges if edge[0] == "__start__"]
    assert start_edges == [("__start__", "load_thread_state")]


def test_access_denied_is_still_reachable_from_validate_knowledge_access():
    graph = build_graph()

    assert "access_denied" in graph.nodes
    assert "validate_knowledge_access" in graph.branches
    assert ("access_denied", "persist") in graph.edges


def test_graph_has_exactly_the_expected_nodes():
    graph = build_graph()

    assert set(graph.nodes) == {
        "load_thread_state",
        "validate_knowledge_access",
        "access_denied",
        "condense_query",
        "hybrid_retrieve",
        "assess_retrieval_sufficiency",
        "reformulate_query",
        "generate",
        "check_output_guardrail",
        "build_citations",
        "persist",
    }
