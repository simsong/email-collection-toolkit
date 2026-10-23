# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Shared durable processor framework for production ingestion and test harnesses.

Purpose: Define the durable processing framework package used by production ingest.

How it works:
Keep package initialization inert while API, queue, dispatch and publication modules own
processing behavior.

Fundamental dependents:

- ``processing.production``: application processing lives in this shared package.
- ``processing.__main__``: the standalone harness exercises its same framework modules.
"""
