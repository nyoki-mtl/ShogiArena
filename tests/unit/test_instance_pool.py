"""Tests for InstancePool YAML loading behavior."""

from pathlib import Path

import pytest

from shogiarena.arena.instances.pool import InstancePool


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_single_instance_defaults_name(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        "local.yaml",
        """
type: local
slots: 2
""".strip(),
    )

    pool = InstancePool.load_from_yaml(yaml_path)

    instances = pool.list_instances()
    assert len(instances) == 1
    instance = instances[0]
    assert instance.name == "local"
    assert instance.config.slots == 2
    assert instance.is_local


def test_load_hosts_expands_instances(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        "remote.yaml",
        """
type: ssh
name: gcp
user: arena
host: ignored
hosts:
  - 192.0.2.1
  - 192.0.2.2
slots: 4
identity_file: ~/.ssh/id_rsa
strict_host_key_checking: true
""".strip(),
    )

    pool = InstancePool.load_from_yaml(yaml_path)

    names = sorted(instance.name for instance in pool.list_instances())
    assert names == ["gcp-001", "gcp-002"]
    hosts = sorted(instance.config.host for instance in pool.list_instances())
    assert hosts == ["192.0.2.1", "192.0.2.2"]


def test_load_invalid_hosts_list_raises(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        "invalid.yaml",
        """
type: ssh
hosts: []
user: arena
slots: 1
""".strip(),
    )

    with pytest.raises(ValueError):
        InstancePool.load_from_yaml(yaml_path)


def test_load_with_instances_key_rejected(tmp_path: Path) -> None:
    yaml_path = _write_yaml(
        tmp_path,
        "bad.yaml",
        """
instances:
  - name: foo
    type: local
    slots: 1
""".strip(),
    )

    with pytest.raises(ValueError):
        InstancePool.load_from_yaml(yaml_path)


def test_load_default_local_when_available(monkeypatch, tmp_path: Path) -> None:
    local_yaml = tmp_path / "local.yaml"
    local_yaml.write_text(
        """
type: local
slots: 2
    """.strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(InstancePool, "DEFAULT_LOCAL_INSTANCES_PATH", local_yaml)

    pool = InstancePool.load_default_local()
    assert pool is not None
    instances = pool.list_instances()
    assert instances
    assert any(instance.is_local for instance in instances)


def test_load_default_local_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(InstancePool, "DEFAULT_LOCAL_INSTANCES_PATH", tmp_path / "missing.yaml")
    pool = InstancePool.load_default_local()
    assert pool is None
