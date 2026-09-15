from types import SimpleNamespace

import pytest

from sregym.service.cluster_state import PROTECTED_NAMESPACES, ClusterBaseline, ClusterStateManager


class _FakeKubectl:
    def __init__(self):
        self.deleted_namespaces = []

    def delete_namespace(self, namespace):
        self.deleted_namespaces.append(namespace)

    def gc_orphan_localpv_dirs(self):
        return {}


class _FakeCoreApi:
    def __init__(self, pvs):
        self.pvs = pvs
        self.deleted_pvs = []

    def list_persistent_volume(self):
        return SimpleNamespace(items=self.pvs)

    def delete_persistent_volume(self, name):
        self.deleted_pvs.append(name)


class _FakeRbacApi:
    def __init__(self, bindings):
        self.bindings = bindings
        self.deleted_roles = []
        self.deleted_bindings = []

    def list_cluster_role_binding(self):
        return SimpleNamespace(items=self.bindings)

    def delete_cluster_role(self, name):
        self.deleted_roles.append(name)

    def delete_cluster_role_binding(self, name):
        self.deleted_bindings.append(name)


class _FakeStorageApi:
    def __init__(self):
        self.deleted_storage_classes = []

    def delete_storage_class(self, name):
        self.deleted_storage_classes.append(name)


def _manager_for_reconciliation(namespaces, cluster_roles, bindings, pvs, storage_classes):
    manager = object.__new__(ClusterStateManager)
    manager.baseline = ClusterBaseline(namespaces={"default"})
    manager.kubectl = _FakeKubectl()
    manager.core_v1 = _FakeCoreApi(pvs)
    manager.rbac_v1 = _FakeRbacApi(bindings)
    manager.storage_v1 = _FakeStorageApi()
    manager.apiextensions_v1 = SimpleNamespace()
    manager.admission_v1 = SimpleNamespace()

    manager._get_namespaces = lambda: set(namespaces)
    manager._get_cluster_roles = lambda: set(cluster_roles)
    manager._get_cluster_role_bindings = lambda: {binding.metadata.name for binding in bindings}
    manager._get_persistent_volumes = lambda: {pv.metadata.name for pv in pvs}
    manager._get_storage_classes = lambda: set(storage_classes)
    manager._get_crds = lambda: set()
    manager._get_validating_webhook_configs = lambda: set()
    manager._get_mutating_webhook_configs = lambda: set()
    manager._reconcile_node_labels = lambda: []
    manager._reconcile_node_taints = lambda: []
    manager._is_coredns_modified = lambda: False
    return manager


def test_reconciliation_preserves_shared_namespaces_and_cluster_resources_from_bare_baseline():
    bindings = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="openebs-binding"),
            role_ref=SimpleNamespace(name="openebs-provisioner"),
            subjects=[SimpleNamespace(namespace="openebs")],
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="observe-binding"),
            role_ref=SimpleNamespace(name="observe-reader"),
            subjects=[SimpleNamespace(namespace="observe")],
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="unowned-binding"),
            role_ref=SimpleNamespace(name="unowned-role"),
            subjects=[SimpleNamespace(namespace="unowned")],
        ),
    ]
    pvs = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="observe-pv"),
            spec=SimpleNamespace(claim_ref=SimpleNamespace(namespace="observe")),
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="unowned-pv"),
            spec=SimpleNamespace(claim_ref=SimpleNamespace(namespace="unowned")),
        ),
    ]
    manager = _manager_for_reconciliation(
        namespaces={"default", "observe", "openebs", "khaos", "unowned-run"},
        cluster_roles={"openebs-provisioner", "observe-reader", "unowned-role"},
        bindings=bindings,
        pvs=pvs,
        storage_classes={"openebs-device", "unowned-storage"},
    )

    changes = manager.reconcile_to_baseline()

    assert {"observe", "openebs", "khaos"} <= PROTECTED_NAMESPACES
    assert manager.kubectl.deleted_namespaces == ["unowned-run"]
    assert manager.rbac_v1.deleted_roles == ["unowned-role"]
    assert manager.rbac_v1.deleted_bindings == ["unowned-binding"]
    assert manager.core_v1.deleted_pvs == ["unowned-pv"]
    assert manager.storage_v1.deleted_storage_classes == ["unowned-storage"]
    assert changes["namespaces_deleted"] == ["unowned-run"]


def test_reconciliation_fails_closed_before_deleting_when_shared_inventory_fails():
    manager = _manager_for_reconciliation(
        namespaces={"default", "unowned-run"},
        cluster_roles=set(),
        bindings=[],
        pvs=[],
        storage_classes=set(),
    )
    manager._get_protected_cluster_resources = lambda: None

    with pytest.raises(RuntimeError, match="shared-resource ownership inventory"):
        manager.reconcile_to_baseline()

    assert manager.kubectl.deleted_namespaces == []
