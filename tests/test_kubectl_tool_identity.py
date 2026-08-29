from tests.kubectl_tool_tests.nl2kubectl_agent import NL2KubectlAgent


def test_kubectl_agent_reuses_one_session_identity_for_transport_and_tool() -> None:
    agent = NL2KubectlAgent(llm=None)

    exec_tool = agent.kubectl_tools[0]
    assert agent.session_id
    assert exec_tool.session_id == agent.session_id
