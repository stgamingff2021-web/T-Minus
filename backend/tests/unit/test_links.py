"""Sealed URL tokens: round trip, determinism, tamper and cross-kind rejection."""
import pytest

from app import links
from app.errors import APIError


def test_round_trip_is_deterministic_and_opaque():
    t1 = links.seal("district", "chennai")
    assert t1 == links.seal("district", "chennai")
    assert "chennai" not in t1
    assert links.unseal("district", t1) == "chennai"


def test_distinct_ids_give_distinct_tokens():
    assert links.seal("run", "run_aaaa") != links.seal("run", "run_bbbb")


def test_token_is_bound_to_its_kind():
    token = links.seal("district", "chennai")
    with pytest.raises(APIError) as e:
        links.unseal("run", token)
    assert e.value.status == 404 and e.value.code == "invalid_link"


@pytest.mark.parametrize("mutate", [lambda t: t[:-2] + ("AA" if t[-2:] != "AA" else "BB"), lambda t: t[1:], lambda t: t + "x", lambda t: "", lambda t: "!!!"])
def test_tampered_tokens_are_rejected(mutate):
    token = links.seal("district", "chennai")
    with pytest.raises(APIError) as e:
        links.unseal("district", mutate(token))
    assert e.value.code == "invalid_link"


def test_token_from_another_key_is_rejected(monkeypatch):
    token = links.seal("district", "chennai")
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "secret_key", "another-secret-key-another-secret-key-99")
    with pytest.raises(APIError):
        links.unseal("district", token)


@pytest.mark.parametrize("bad", ["", "a b", "../etc", "x" * 97, "a/b"])
def test_seal_rejects_unsafe_ids(bad):
    with pytest.raises(APIError) as e:
        links.seal("district", bad)
    assert e.value.code == "invalid_id"


def test_unknown_kind_rejected():
    with pytest.raises(APIError) as e:
        links.seal("plan", "x")
    assert e.value.code == "invalid_kind"
