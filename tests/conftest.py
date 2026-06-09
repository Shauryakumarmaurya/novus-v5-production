"""Shared test config — dummy env so modules import without real credentials."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("NOVUS_API_KEY", "test-key-123")
os.environ.setdefault("NOVUS_ENV", "development")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-dummy")
os.environ.setdefault("VOYAGE_API_KEY", "test-dummy")
os.environ.setdefault("GEMINI_API_KEY", "replace_me")
