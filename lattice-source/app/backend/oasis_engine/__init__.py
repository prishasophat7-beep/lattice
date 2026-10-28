"""Lattice engine (package still named oasis_engine): offline, vectorless question answering over a shipped knowledge base."""
import os

# PydanticAI prints a promotional banner to stdout on first use; the packaged backend has no console.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

__version__ = "0.2.8"
