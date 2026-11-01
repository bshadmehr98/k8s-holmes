from k8sholmes.evidence import REDACTED, Result, cap, evidence_from_result, redact, redact_text

JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP_mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


def test_env_values_with_telling_names_are_redacted() -> None:
    env = [{"name": "DATABASE_URL", "value": "postgres://db"}, {"name": "API_TOKEN", "value": "abc123def"}]
    cleaned, n = redact(env)
    assert cleaned[0]["value"] == "postgres://db"
    assert cleaned[1]["value"] == REDACTED
    assert n == 1


def test_secret_refs_keep_their_names() -> None:
    data = {"valueFrom": {"secretKeyRef": {"name": "db-creds", "key": "password"}}}
    cleaned, n = redact(data)
    assert cleaned == data
    assert n == 0


def test_tokens_in_text_are_redacted() -> None:
    text = f"auth ok token={JWT} AKIAABCDEFGHIJKLMNOP ghp_{'a' * 30} Authorization: Bearer sk-live-abcdefghij"
    cleaned, n = redact_text(text)
    assert JWT not in cleaned
    assert "AKIAABCDEFGHIJKLMNOP" not in cleaned
    assert "ghp_" not in cleaned
    assert "sk-live" not in cleaned
    assert n >= 4


def test_assignments_and_urls_are_redacted() -> None:
    cleaned, n = redact_text("password: hunter22 and https://bob:pw123@host/db and api_key=XYZ-999")
    assert "hunter22" not in cleaned
    assert "pw123" not in cleaned
    assert "XYZ-999" not in cleaned
    assert n == 3


def test_private_keys_are_redacted() -> None:
    cleaned, n = redact_text("x -----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY----- y")
    assert cleaned == f"x {REDACTED} y"
    assert n == 1


def test_cap_cuts_logs_and_lists() -> None:
    text, truncated = cap("\n".join(f"line {i}" for i in range(3000)), max_lines=100)
    assert truncated
    assert text.endswith("line 2999")
    assert text.splitlines()[0] == "line 2900"
    items, truncated = cap([{"x": "y" * 100}] * 100, max_bytes=2000)
    assert truncated
    assert 0 < len(items) < 100
    small, truncated = cap({"a": 1})
    assert (small, truncated) == ({"a": 1}, False)


def test_evidence_from_result_hashes_redacts_and_caps() -> None:
    result = Result(
        summary="2 pods, token=abcdefgh",
        data={"env": [{"name": "PASSWORD", "value": "x1"}]},
        coverage="observed",
    )
    ev = evidence_from_result(
        result,
        source="k8s.get",
        systems=["kubernetes"],
        query={"name": "api"},
        environment_id="e",
        investigation_id="i",
        step_id="s",
        call_id="c",
    )
    assert ev.data == {"env": [{"name": "PASSWORD", "value": REDACTED}]}
    assert ev.redactions == 2
    assert "abcdefgh" not in ev.summary
    assert len(ev.sha256) == 64
    assert ev.coverage == "observed"
    assert not ev.truncated
