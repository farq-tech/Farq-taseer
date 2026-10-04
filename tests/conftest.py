"""Shared test plumbing.

The independent password sign-up door (/v1/auth/register) is CLOSED in production
unless TASEER_PASSWORD_SIGNUP=1. Every suite's journeys create their throwaway
accounts through that door, so it is opened for tests here, once; the tests that
prove the production default (test_credit_ledger.py) close it again themselves.

The listing-match gate on invites (FARQ_REQUIRE_LISTING_MATCH, on in production) needs a
recorded live search with ad listings. The older journeys pick sellers from the local
corpus or skip search altogether, so the gate is off for them here; the outreach tests
(test_outreach.py) turn it on and prove it.
"""

import pytest


@pytest.fixture(autouse=True)
def _password_signup_open_for_tests(monkeypatch):
    monkeypatch.setenv("TASEER_PASSWORD_SIGNUP", "1")
    monkeypatch.setenv("FARQ_REQUIRE_LISTING_MATCH", "0")
