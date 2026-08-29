import importlib.util
from pathlib import Path


def _load_test_agent_class():
    path = Path(__file__).parent / "kubectl_tool_tests" / "nl2kubectl_agent.py"
    spec = importlib.util.spec_from_file_location("kubectl_test_agent", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load test agent helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.NL2KubectlAgent


def test_kubectl_agent_reuses_one_session_identity_for_transport_and_tool() -> None:
    NL2KubectlAgent = _load_test_agent_class()
    agent = NL2KubectlAgent(llm=None)

    exec_tool = agent.kubectl_tools[0]
    assert agent.session_id
    assert exec_tool.session_id == agent.session_id
