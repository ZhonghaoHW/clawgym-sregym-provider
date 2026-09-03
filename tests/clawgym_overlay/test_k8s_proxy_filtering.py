from sregym.service.k8s_proxy import _response_filter_type


def test_namespaced_resource_collections_are_filtered() -> None:
    assert _response_filter_type("/api/v1/namespaces/hotel-reservation/pods") == "resources"
    assert _response_filter_type("/apis/apps/v1/namespaces/hotel-reservation/deployments?limit=50") == "resources"


def test_namespace_and_cluster_collections_are_filtered() -> None:
    assert _response_filter_type("/api/v1/namespaces?limit=50") == "namespaces"
    assert _response_filter_type("/api/v1/pods") == "resources"
    assert _response_filter_type("/apis/batch/v1/jobs?limit=50") == "resources"


def test_individual_resources_are_not_misclassified_as_lists() -> None:
    assert _response_filter_type("/api/v1/namespaces/hotel-reservation/pods/checkout-123") is None
    assert _response_filter_type("/api/v1/nodes/worker-1") is None
