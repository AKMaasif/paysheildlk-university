"""Which web sites may call the API from a browser."""
import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import DEV_ORIGINS, Settings, _origins
from app.main import app, install_cors

PRODUCTION = dict(app_env="production", demo_mode="production", jwt_secret="j" * 40, answer_pepper="a" * 40, encryption_key="e" * 40, hmac_key="h" * 40, demo_assist=False)


def settings(**over):
    return dataclasses.replace(Settings.from_env(), **over)


# ------------------------------------------------------------------ reading the setting

def test_nothing_set_means_the_local_development_addresses():
    assert _origins("") == DEV_ORIGINS and _origins("  ,, ") == DEV_ORIGINS


def test_a_list_is_trimmed_deduplicated_and_loses_trailing_slashes():
    assert _origins(" https://bank.example/ , https://bank.example,https://www.bank.example ") == ("https://bank.example", "https://www.bank.example")


# ------------------------------------------------------------------ production refuses the unsafe choices

@pytest.mark.parametrize("origins", [DEV_ORIGINS, ("*",), ("http://bank.example",), ("https://bank.example", "*"), ("https://ok.example", "http://not-ok.example")])
def test_production_refuses_the_development_default_a_wildcard_and_plain_http(origins):
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        settings(**PRODUCTION, allowed_origins=origins).assert_safe_for_environment()


def test_production_accepts_real_https_addresses():
    settings(**PRODUCTION, allowed_origins=("https://bank.example", "https://www.bank.example")).assert_safe_for_environment()


def test_development_may_use_anything():
    settings(app_env="development", allowed_origins=("*",)).assert_safe_for_environment()


# ------------------------------------------------------------------ what the browser is told

def probe(application, origin, method="POST", path="/api/auth/login"):
    with TestClient(application) as client:
        return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": "authorization,content-type"})


def test_a_listed_site_is_allowed_and_an_unlisted_one_is_not():
    site = FastAPI()
    install_cors(site, ("https://bank.example",))
    good, bad = probe(site, "https://bank.example"), probe(site, "https://evil.example")
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == "https://bank.example"
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers


def test_cookies_and_other_credentials_are_never_allowed():
    site = FastAPI()
    install_cors(site, ("https://bank.example",))
    assert "access-control-allow-credentials" not in probe(site, "https://bank.example").headers


def test_only_the_methods_and_headers_the_app_uses_are_allowed():
    site = FastAPI()
    install_cors(site, ("https://bank.example",))
    reply = probe(site, "https://bank.example")
    assert set(reply.headers["access-control-allow-methods"].replace(" ", "").split(",")) == {"GET", "POST", "OPTIONS"}
    assert probe(site, "https://bank.example", method="DELETE").status_code == 400
    assert "authorization" in reply.headers["access-control-allow-headers"].lower()


def test_the_real_app_answers_the_local_front_end_and_nobody_else():
    assert probe(app, "http://localhost:3000").headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in probe(app, "https://evil.example").headers
    assert "access-control-allow-origin" not in probe(app, "null").headers
