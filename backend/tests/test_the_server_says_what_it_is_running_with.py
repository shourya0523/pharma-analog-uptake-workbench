"""A number is conditional on the settings the run read, and nothing said them.

The settings reach the server from the environment of whichever shell started
it, and a re-run from a fresh shell gets the declared defaults instead. No run
records which it had, so a score could be reported and never reproduced. The
`/config` route is what the eval reads to print them.

Three properties matter. It has to report every field the settings model
declares - a route naming the fields it knows about reports the ones someone
remembered, and a setting added later is invisible exactly when it is new. It
must say what the code declares beside what the server has, or a value cannot
be read as configured-or-left-alone. And it must never carry a credential's
value, whoever asks and whichever field it arrived in.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app import main
from app.config import Settings


def _config() -> dict:
    return TestClient(main.app).get("/config").json()


def test_every_declared_setting_is_reported():
    reported = {field["field"] for field in _config()["settings"]}
    assert reported == set(Settings.model_fields), (
        "reported but not declared: " + str(sorted(reported - set(Settings.model_fields)))
        + "; declared but not reported: " + str(sorted(set(Settings.model_fields) - reported))
    )


def test_a_setting_says_what_the_code_declares_beside_what_the_server_has():
    """Without the default beside it, a value says nothing about whether the
    server was configured or left alone.

    ``overridden`` is checked against the model rather than against the route's
    own two strings, because a credential is reported in a lossy form: the flag
    has to be true of the settings, not merely consistent with what was
    printed.
    """
    for field in _config()["settings"]:
        assert "value" in field and "default" in field, field
        declared = Settings.model_fields[field["field"]].get_default()
        live = getattr(main.settings, field["field"])
        assert field["overridden"] == (live != declared), field


def test_the_overridden_list_is_the_fields_that_say_so():
    body = _config()
    assert body["overridden"] == [
        f["field"] for f in body["settings"] if f["overridden"]
    ]


def test_no_credential_leaves_the_server(monkeypatch):
    """Whatever a credential is set to, the route says only that it is set."""
    secret = "acme-not-a-real-credential-0000000000"
    monkeypatch.setattr(main.settings, "openrouter_api_key", secret)
    body = TestClient(main.app).get("/config").text
    assert secret not in body
    reported = {field["field"]: field["value"] for field in _config()["settings"]}
    assert reported["openrouter_api_key"] == "set"


def test_a_credential_is_recognised_by_how_it_is_named():
    """The route redacts on the field's name, so a field holding a credential
    has to be named like one. This is the check that says so out loud."""
    named = {name for name in Settings.model_fields
             if any(marker in name for marker in main.SECRET_MARKERS)}
    assert "openrouter_api_key" in named
    assert "db_password" in named


def test_a_password_inside_a_connection_string_does_not_leave_either(monkeypatch):
    """The name rule alone is not enough, which is why there is a second one.

    A DSN holds a credential under a field named for the address it points at,
    so the value's structure decides. The rest of the string is kept: which
    host and database a run read is part of what the number is conditional on.
    """
    secret = "acme-not-a-real-password-0000000000"
    dsn = f"postgresql+psycopg2://workbench:{secret}@db.invalid:5432/workbench"
    monkeypatch.setattr(main.settings, "database_url", dsn)
    body = TestClient(main.app).get("/config").text
    assert secret not in body
    reported = {field["field"]: field["value"] for field in _config()["settings"]}
    assert reported["database_url"] == (
        "postgresql+psycopg2://workbench:***@db.invalid:5432/workbench"
    )


def test_two_different_credentials_are_reported_alike():
    """Which is why ``overridden`` is not decided from what was printed.

    Redaction is lossy by design, so the reported pair cannot answer whether
    the server was configured away from the code. A route comparing its own
    two strings would call a configured server left alone - the one thing
    this route exists to make impossible - so the flag is taken from the
    values themselves.
    """
    dsn = "beta://svc:{}@host:5432/db"
    assert (main._reportable("database_url", dsn.format("acme-one"))
            == main._reportable("database_url", dsn.format("acme-two")))
    assert (main._reportable("openrouter_api_key", "acme-one")
            == main._reportable("openrouter_api_key", "acme-two"))


def test_every_declared_setting_is_read_by_something():
    """A knob an operator can turn has to reach the code that would honour it.

    `/config` reports every field the model declares, so a field nothing reads
    is printed to an operator as though turning it did something. One did:
    a cap on how many search queries a job may make, documented beside a cap on
    URLs that works, and read by nothing - an operator capping search volume got
    no cap and no error.

    Derived rather than listed: a field is read when some line of `app/` other
    than its own declaration names it, which covers the ones only `Settings`
    itself reads.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    lines = [
        (path, line)
        for path in root.rglob("*.py")
        for line in path.read_text().splitlines()
    ]
    unread = [
        name
        for name in Settings.model_fields
        if not any(
            re.search(rf"\b{name}\b", line)
            for _path, line in lines
            if not re.match(rf"\s*{name}\s*:", line)
        )
    ]
    assert unread == [], f"declared and read by nothing: {unread}"
