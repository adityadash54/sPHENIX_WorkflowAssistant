"""
Runtime path and cache configuration shared by local and Docker entrypoints.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"
CACHE_DIR = APP_DIR / ".cache"
HOME_DIR = CACHE_DIR / "home"
CONFIG_DIR = CACHE_DIR / "config"
INDEX_DIR = APP_DIR / "index"
REPOS_DIR = APP_DIR / "repos"


def configure_local_environment() -> None:
    """Keep caches and writable state inside the application directory."""
    os.environ.setdefault("HOME", str(HOME_DIR))
    os.environ.setdefault("XDG_CACHE_HOME", str(CACHE_DIR))
    os.environ.setdefault("XDG_CONFIG_HOME", str(CONFIG_DIR))
    os.environ.setdefault("HF_HOME", str(CACHE_DIR / "huggingface"))
    os.environ.setdefault(
        "HUGGINGFACE_HUB_CACHE",
        str(CACHE_DIR / "huggingface" / "hub"),
    )
    os.environ.setdefault(
        "TRANSFORMERS_CACHE",
        str(CACHE_DIR / "huggingface" / "transformers"),
    )
    os.environ.setdefault(
        "SENTENCE_TRANSFORMERS_HOME",
        str(CACHE_DIR / "sentence-transformers"),
    )
    os.environ.setdefault("TORCH_HOME", str(CACHE_DIR / "torch"))
