# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Disposable trusted plugin worker; only the parent commits typed results."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .api import InvocationRequest, InvocationResponse, ProcessingResult


def main() -> None:
    request = InvocationRequest.model_validate_json(Path(sys.argv[1]).read_text())
    request.item.bind_configuration(request.configuration)
    module_name, factory_name = request.plugin.manifest.entrypoint.split(":")
    try:
        spec = importlib.util.spec_from_file_location("processor", request.plugin.directory / f"{module_name}.py")
        if spec is None or spec.loader is None:
            raise ValueError("invalid entrypoint")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plugin = getattr(module, factory_name)()
        result = plugin.process(request.item)
        if not isinstance(result, ProcessingResult):
            raise TypeError("processor must return ProcessingResult")
        result = ProcessingResult(outcome=result.outcome, emissions=result.emissions,
                                  handoffs=result.handoffs, diagnostics=result.diagnostics,
                                  config_writes=request.configuration.pending_writes())
        response = InvocationResponse(result=result)
    except Exception as error:  # structured worker failure, never a successful checkpoint
        response = InvocationResponse(error=f"{type(error).__name__}: {error}")
    Path(sys.argv[2]).write_text(response.model_dump_json())


if __name__ == "__main__":
    main()
