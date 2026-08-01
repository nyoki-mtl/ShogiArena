from __future__ import annotations

import argparse

import pytest

from shogiarena._core.interfaces.cli.run.provision_options import parse_provision_mode


def test_current_provision_modes_are_accepted() -> None:
    assert parse_provision_mode("cas") == "cas"
    assert parse_provision_mode("preplaced") == "preplaced"


def test_legacy_none_has_actionable_migration_error() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="PREPLACED_RESOURCES"):
        parse_provision_mode("none")


def test_legacy_force_is_rejected() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="immutable endpoint-aware CAS"):
        parse_provision_mode("force")
