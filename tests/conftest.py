"""Shared test fixtures."""

import pytest

from pkm_bridge import model_catalog


@pytest.fixture(autouse=True)
def _no_model_discovery(monkeypatch):
    """Keep tests off the network: the model catalog uses its built-in list."""
    monkeypatch.setattr(model_catalog, "_DISCOVERERS", {})
    monkeypatch.setattr(model_catalog, "_discovered", {})
    monkeypatch.setattr(model_catalog, "_next_refresh", 0.0)
