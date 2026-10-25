# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Describe registered processor dataflow without invoking any processor.
# Each pipeline has MIME-type inputs, processor nodes and possible typed outputs.
# The registry's shared expansion supplies known outputs of MIME-part producers.
# An additional terminal node represents their open-ended document vocabulary.
# Separate pipeline panels preserve the dispatcher's per-pipeline DAG semantics.
# The desktop bridge serializes these typed values for offline Dagre layout.
"""Manifest-derived, pipeline-scoped processor graph for the About window."""
from __future__ import annotations

from typing import Literal

from .processing.api import Model, Pipeline, ProcessorManifest
from .processing.registry import emitted_types

PIPELINES: tuple[Pipeline, ...] = ("ingest", "message", "content")


class GraphNode(Model):
    id: str
    kind: Literal["mime", "processor", "other"]
    label: str
    detail: str = ""


class GraphEdge(Model):
    source: str
    target: str
    dynamic: bool = False


class PipelineGraph(Model):
    pipeline: Pipeline
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class ProcessorGraphStatus(Model):
    context: str
    pipelines: tuple[PipelineGraph, ...] = ()
    error: str | None = None


def processor_graph(manifests: tuple[ProcessorManifest, ...]) -> tuple[PipelineGraph, ...]:
    """Graph input subscriptions and potential emissions, independently of execution order."""
    graphs = []
    for pipeline in PIPELINES:
        plugins = sorted((manifest for manifest in manifests if manifest.pipeline == pipeline),
                         key=lambda manifest: (manifest.rank, manifest.kind))
        if not plugins:
            continue
        types = sorted({value for plugin in plugins for value in (*plugin.subscribes, *emitted_types(plugin, manifests))})
        nodes = [GraphNode(id=f"mime:{value}", kind="mime", label=value) for value in types]
        edges = []
        for plugin in plugins:
            identity = f"processor:{plugin.kind}"
            nodes.append(GraphNode(id=identity, kind="processor", label=plugin.name,
                detail=f"{plugin.kind} · rank {plugin.rank} · {plugin.scope} · version {plugin.implementation_version}"))
            edges.extend(GraphEdge(source=f"mime:{value}", target=identity) for value in plugin.subscribes)
            edges.extend(GraphEdge(source=identity, target=f"mime:{value}",
                dynamic=plugin.emits_mime_parts and value not in plugin.emits) for value in emitted_types(plugin, manifests))
            if plugin.emits_mime_parts:
                other = f"other:{plugin.kind}"
                nodes.append(GraphNode(id=other, kind="other", label="Other MIME types",
                    detail="Any document MIME type, including application/pdf and image/jpeg; internal ECT types are excluded."))
                edges.append(GraphEdge(source=identity, target=other, dynamic=True))
        graphs.append(PipelineGraph(pipeline=pipeline, nodes=tuple(nodes), edges=tuple(edges)))
    return tuple(graphs)
