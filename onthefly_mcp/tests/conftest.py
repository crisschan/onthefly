"""Pytest config: add project root to sys.path and stub a scratch OTf root."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def otf_tmp(tmp_path, monkeypatch):
    """Point OTF_ROOT at a fresh tmp dir for the duration of the test."""
    monkeypatch.setenv("OTF_ROOT", str(tmp_path))
    yield tmp_path
