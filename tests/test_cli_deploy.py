from agentkit import cli
from agentkit.compiler import CompileError


def test_a_compile_error_is_reported_as_a_message_not_a_traceback(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise CompileError("MCP tools are not granted:\n- tool x.t is requested but not granted")

    monkeypatch.setattr(cli, "deploy_project", fail)
    assert cli.main(["deploy", "--env", "dev"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: MCP tools are not granted") and "Traceback" not in err
