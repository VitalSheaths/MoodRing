from __future__ import annotations
import asyncio

from ingestion.pipeline.run_ingestion import run_once


if __name__ == "__main__":
    asyncio.run(run_once())
