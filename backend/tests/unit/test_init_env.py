from scripts import init_env


def test_writes_random_credentials_and_never_overwrites(tmp_path, capsys):
    path = tmp_path / ".env"
    assert init_env.main(["--path", str(path), "--gemini-key", " k-123 "]) == 0
    env = dict(l.split("=", 1) for l in path.read_text().splitlines() if "=" in l and not l.startswith("#"))
    assert len(env["SECRET_KEY"]) >= 32 and len(env["OFFICER_PASSCODE"]) >= 16
    assert env["OFFICER_PASSCODE"] != env["ADMIN_PASSCODE"] and env["GEMINI_API_KEY"] == "k-123"
    assert env["OFFICER_PASSCODE"] not in capsys.readouterr().out      # secrets are never echoed
    before = path.read_text()
    assert init_env.main(["--path", str(path)]) == 1
    assert path.read_text() == before
