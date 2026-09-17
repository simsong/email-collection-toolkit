# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Validate trusted manifests and graph structure without executing plugin code."""
from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from .api import PluginSpec, ProcessingObject, ProcessorManifest


def load_processors(roots: tuple[Path, ...]) -> tuple[PluginSpec, ...]:
    plugins: list[PluginSpec] = []
    for root in sorted({p.resolve() for p in roots}):
        if not root.is_dir():
            raise ValueError(f"plugin root does not exist: {root}")
        for manifest_path in sorted(root.glob("processors/*/plugin.toml")):
            directory = manifest_path.parent.resolve()
            if not directory.is_relative_to(root) or not manifest_path.resolve().is_relative_to(root):
                raise ValueError("plugin path escapes trusted root")
            manifest = ProcessorManifest.model_validate(tomllib.loads(manifest_path.read_text()))
            if manifest.kind != directory.name:
                raise ValueError("plugin kind must match directory")
            module, separator, entry = manifest.entrypoint.partition(":")
            if separator != ":" or not module.isidentifier() or not entry.isidentifier():
                raise ValueError("entrypoint must be a local module:function")
            code = directory / f"{module}.py"
            if not code.is_file() or not code.resolve().is_relative_to(directory):
                raise ValueError("entrypoint must resolve inside plugin directory")
            plugins.append(PluginSpec(manifest=manifest, directory=directory))
    ordered = tuple(sorted(plugins, key=lambda p: (p.manifest.rank, p.manifest.kind)))
    kinds = [p.manifest.kind for p in ordered]
    if len(kinds) != len(set(kinds)):
        raise ValueError("duplicate processor kind")
    for plugin in ordered:
        spec = plugin.manifest
        for kind in spec.requires:
            required = next((p.manifest for p in ordered if p.manifest.kind == kind), None)
            if required is None or required.pipeline != spec.pipeline or required.rank >= spec.rank:
                raise ValueError(f"missing or contradictory dependency: {spec.kind} requires {kind}")
            if not set(spec.subscribes) <= set(required.subscribes) or required.scope not in ("both", spec.scope):
                raise ValueError("dependencies must cover all input types and scopes of their consumer")
    # Type publication must be acyclic independently for each pipeline.
    def visit(phase: str, content_type: str, ancestors: frozenset[str]) -> None:
        if content_type in ancestors:
            raise ValueError(f"cyclic content graph: {content_type}")
        for plugin in ordered:
            spec = plugin.manifest
            if spec.pipeline == phase and content_type in spec.subscribes:
                targets = set(spec.emits)
                if spec.emits_mime_parts:
                    targets.update(value for candidate in ordered for value in candidate.manifest.subscribes
                                   if not value.startswith("application/x-mailarchiver-"))
                for emitted in targets:
                    visit(phase, emitted, ancestors | {content_type})
    for plugin in ordered:
        for content_type in plugin.manifest.subscribes:
            visit(plugin.manifest.pipeline, content_type, frozenset())
    return ordered


def subscribers(plugins: tuple[PluginSpec, ...], item: ProcessingObject) -> tuple[PluginSpec, ...]:
    return tuple(p for p in plugins if p.manifest.pipeline == item.pipeline
                 and item.content_type in p.manifest.subscribes
                 and p.manifest.scope in ("both", item.scope))


def fingerprint(plugins: tuple[PluginSpec, ...]) -> str:
    """Changing manifests or local Python helpers cannot reuse stale checkpoints."""
    digest = hashlib.sha256()
    for plugin in plugins:
        digest.update(plugin.manifest.model_dump_json().encode())
        for source in sorted(plugin.directory.rglob("*.py")):
            digest.update(source.relative_to(plugin.directory).as_posix().encode())
            digest.update(source.read_bytes())
    return digest.hexdigest()
