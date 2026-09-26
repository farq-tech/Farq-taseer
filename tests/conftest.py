"""Shared test plumbing.

The independent password sign-up door (/v1/auth/register) is CLOSED in production
unless TASEER_PASSWORD_SIGNUP=1. Every suite's journeys create their throwaway
accounts through that door, so it is opened for tests here, once; the tests that
prove the production default (test_credit_ledger.py) close it again themselves.
"""

import pytest


@pytest.fixture(autouse=True)
def _password_signup_open_for_tests(monkeypatch):
    monkeypatch.setenv("TASEER_PASSWORD_SIGNUP", "1")
