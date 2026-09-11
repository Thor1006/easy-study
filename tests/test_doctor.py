"""`gm doctor`: readiness checks without provider usage."""

import json

from glass_membrane import doctor


def test_demo_check_passes_and_self_test_runs_the_kernel(tmp_path, capsys):
    assert doctor.run_doctor(demo=True, data_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert "[ OK ] Runtime self-test" in out and "Ready." in out
    assert "uses no provider quota" in out


def test_live_check_fails_when_no_provider_cli_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "find_command", lambda name: None)
    checks = doctor.collect_checks(demo=False, data_dir=tmp_path)
    assert any(c.name == "Providers" and c.status == "fail" for c in checks)
    assert doctor.run_doctor(demo=False, data_dir=tmp_path) == 1


def test_live_check_reads_both_login_statuses(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "find_command", lambda name: f"C:/bin/{name}.exe")

    def fake_run(argv, timeout=30):
        if argv[1:] == ["--version"]:
            return 0, f"{argv[0]} 1.2.3"
        if argv[0] == "claude":
            return 0, json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"})
        return 0, "Logged in using ChatGPT"

    monkeypatch.setattr(doctor, "run_command", fake_run)
    checks = {c.name: c for c in doctor.collect_checks(demo=False, data_dir=tmp_path)}
    assert checks["Claude Code CLI"].status == "ok" and "pro plan" in checks["Claude Code CLI"].detail
    assert checks["Codex CLI"].status == "ok" and "ChatGPT" in checks["Codex CLI"].detail
    assert "Providers" not in checks


def test_signed_out_provider_is_a_warning_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "find_command", lambda name: f"C:/bin/{name}.exe")
    monkeypatch.setattr(doctor, "run_command", lambda argv, timeout=30: (
        (0, "1.0") if argv[1:] == ["--version"]
        else (0, json.dumps({"loggedIn": False})) if argv[0] == "claude"
        else (0, "Logged in using ChatGPT")))
    checks = {c.name: c for c in doctor.collect_checks(demo=False, data_dir=tmp_path)}
    assert checks["Claude Code CLI"].status == "warn" and "claude auth login" in checks["Claude Code CLI"].detail
    assert "Providers" not in checks, "one signed-in provider is enough to start"
