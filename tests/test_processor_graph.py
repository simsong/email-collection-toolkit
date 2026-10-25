# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify the registered MIME graph against real built-in processor manifests.
# Tests inspect subscriptions and possible emissions, including open-ended MIME parts.
# Custom typed manifests exercise output-only types and pipeline-specific identities.
# Topological ordering proves each displayed pipeline remains a DAG.
# The tests execute no processors and read no user archive or mailbox.
# Browser and native lifecycle checks separately cover layout and window creation.
"""Processing requirements: truthful, pipeline-scoped processor graph data."""
from graphlib import TopologicalSorter

from mailarchiver.plugin_loader import builtin_plugin_directory
from mailarchiver.processing.api import RAW_MESSAGE
from mailarchiver.processing.registry import emitted_types, load_processors
from mailarchiver.processor_graph import PipelineGraph, processor_graph


def assert_dag(panel: PipelineGraph) -> None:
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for node in panel.nodes:
        sorter.add(node.id)
    for edge in panel.edges:
        sorter.add(edge.target, edge.source)
    assert set(sorter.static_order()) == {node.id for node in panel.nodes}


def test_builtin_graph_includes_mime_fanout_and_conversion_chains() -> None:
    """MIME parts can reach every registered external type, with explicit open-ended output."""
    manifests = tuple(plugin.manifest for plugin in load_processors((builtin_plugin_directory(),)))
    panels = processor_graph(manifests)
    assert [panel.pipeline for panel in panels] == ["ingest", "message", "content"]
    assert sum(node.kind == "processor" for panel in panels for node in panel.nodes) == len(manifests)
    for panel in panels:
        assert_dag(panel)
        for manifest in (item for item in manifests if item.pipeline == panel.pipeline):
            assert {edge.source.removeprefix("mime:") for edge in panel.edges
                    if edge.target == f"processor:{manifest.kind}"} == set(manifest.subscribes)
    content = next(panel for panel in panels if panel.pipeline == "content")
    targets = {edge.target for edge in content.edges if edge.source == "processor:mime"}
    assert targets == {f"mime:{kind}" for kind in {"text/plain", "text/html", "text/rtf", "application/rtf", "application/x-rtf", "message/rfc822"}} | {"other:mime"}
    assert f"mime:{RAW_MESSAGE}" not in targets
    assert all(edge.dynamic for edge in content.edges if edge.source == "processor:mime")
    assert any(edge.source == "processor:html-text" and edge.target == "mime:text/plain" for edge in content.edges)
    assert any(edge.source == "mime:text/plain" and edge.target == "processor:text-index" for edge in content.edges)
    identity = next(node for node in content.nodes if node.id == "processor:identity-evidence")
    assert "rank 2" in identity.detail and "body" in identity.detail


def test_graph_keeps_custom_output_types_and_separates_pipeline_cycles() -> None:
    """Opposite conversions in separate pipelines do not create a false combined cycle."""
    base = load_processors((builtin_plugin_directory(),))[0].manifest
    first = base.model_copy(update={"kind": "first", "name": "Custom <first>", "pipeline": "ingest",
                                   "subscribes": ("application/a",), "emits": ("application/b",)})
    second = first.model_copy(update={"kind": "second", "pipeline": "content", "subscribes": ("application/b",),
                                     "emits": ("application/a", "application/pdf")})
    for panel in processor_graph((first, second)):
        assert_dag(panel)
    content = processor_graph((first, second))[1]
    assert any(node.label == "application/pdf" for node in content.nodes)
    assert any(edge.source == "processor:second" and edge.target == "mime:application/pdf" for edge in content.edges)
    mime = first.model_copy(update={"kind": "mime", "subscribes": (RAW_MESSAGE,), "emits": (), "emits_mime_parts": True})
    assert "application/pdf" in emitted_types(mime, (mime, second))
    assert RAW_MESSAGE not in emitted_types(mime, (mime, second))
