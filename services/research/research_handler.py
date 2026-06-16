"""Compatibility wrapper for the canonical research handler.

The implementation lives in :mod:`src.research_handler`. Keep this module so
older imports through ``services.research`` do not fork behavior again.
"""

from src.research_handler import RESEARCH_DATA_DIR, ResearchHandler

__all__ = ["RESEARCH_DATA_DIR", "ResearchHandler"]
