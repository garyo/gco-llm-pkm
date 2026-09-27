"""Tests for credential redaction in logs and subprocess environments."""

from pkm_bridge.redact import MASK, redact_obj, redact_secrets, subprocess_env


def test_authorization_bearer_header():
    cmd = 'curl -s -H "Authorization: Bearer abcDEF123456789xyz" https://api.example.com/x'
    out = redact_secrets(cmd)
    assert "abcDEF123456789xyz" not in out
    assert f'Authorization: Bearer {MASK}"' in out
    assert "https://api.example.com/x" in out


def test_curl_user_password_keeps_user():
    out = redact_secrets('curl -u garyo:s3cr3t-pa55 -d "hi" https://ntfy.example.com/pkm')
    assert "s3cr3t-pa55" not in out
    assert f"-u garyo:{MASK}" in out


def test_url_embedded_credentials():
    out = redact_secrets("git clone https://user:hunter2hunter2@example.com/repo.git")
    assert "hunter2hunter2" not in out
    assert "@example.com/repo.git" in out


def test_key_value_forms():
    out = redact_secrets('GET /x?api_key=AAAABBBBCCCC&page=2 {"password": "pw123456"}')
    assert "AAAABBBBCCCC" not in out
    assert "pw123456" not in out
    assert "page=2" in out


def test_env_secret_values_masked(monkeypatch):
    monkeypatch.setenv("NTFY_PASS", "literal-ntfy-password")
    out = redact_secrets("echo literal-ntfy-password | base64")
    assert "literal-ntfy-password" not in out


def test_ordinary_text_untouched():
    for text in (
        'rg -i "bearer of bad news" journals/ && echo token count: 5',
        "sed -n 1,20p journals/2026-09-27.md",
        "date -u +%Y-%m-%dT%H:%M:%SZ",
    ):
        assert redact_secrets(text) == text


def test_redact_obj_recurses():
    params = {"command": "curl -H 'Authorization: Bearer zzzzzzzzzzzz' x", "n": 3, "l": ["ok"]}
    out = redact_obj(params)
    assert "zzzzzzzzzzzz" not in out["command"]
    assert out["n"] == 3 and out["l"] == ["ok"]


def test_subprocess_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxxxxxxx")
    monkeypatch.setenv("NTFY_PASS", "pw")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("ORG_DIR", "/data/org")
    env = subprocess_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "NTFY_PASS" not in env
    assert "DATABASE_URL" not in env
    assert env["ORG_DIR"] == "/data/org"
    assert "PATH" in env
