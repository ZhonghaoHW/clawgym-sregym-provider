from tests.kubectl_tool_tests.nl2kubectl_agent import NL2KubectlAgent
from tests.kubectl_tool_tests import kubectl_tool_set_test


def test_kubectl_agent_reuses_one_session_identity_for_transport_and_tool() -> None:
    agent = NL2KubectlAgent(llm=None)

    exec_tool = agent.kubectl_tools[0]
    assert agent.session_id
    assert exec_tool.session_id == agent.session_id


def test_kubectl_fixture_validator_binds_command_output(monkeypatch) -> None:
    monkeypatch.setattr(kubectl_tool_set_test, "exec_shell_cmd", lambda _cmd: "ready\n")

    assert kubectl_tool_set_test.validate_condition(
        "ignored", "len(opt.strip()) > 0"
    )
