import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx2 import Response
from starlette.testclient import TestClient

from finanze.storage import db
from finanze.web import app as webapp
from finanze.web import auth
from tests.web.builders import BUILT

SITE = "http://127.0.0.1:8765"
WORDS = "correct horse battery"
STORED = auth.digest(WORDS, salt=bytes(16))


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def wait(self, **how_long: float) -> None:
        self.now += timedelta(**how_long)


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("login") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    conn.close()
    return path


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(database: Path, clock: Clock) -> TestClient:
    app = webapp.create_app(database, clock, password=STORED, names=["finanze.home"])
    return TestClient(app, base_url=SITE, follow_redirects=False)


def token(page: str) -> str:
    found = re.search(r'name="token" value="([^"]+)"', page)
    assert found is not None
    return found.group(1)


def sign_in(client: TestClient, password: str = WORDS, then: str = "/") -> Response:
    form = {"token": token(client.get("/login").text), "password": password, "next": then}
    return client.post("/login", data=form)


PAGES = ["/", "/networth", "/networth.json", "/holdings", "/cashflow", "/months"]


@pytest.mark.parametrize("path", [*PAGES, "/transactions", "/transactions.csv", "/nowhere"])
def test_a_browser_that_is_not_signed_in_is_sent_to_the_password(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next=%2F{path[1:]}"
    assert response.text == ""
    assert response.headers["content-security-policy"].startswith("default-src 'self'")


def test_the_page_asked_for_is_kept_with_its_query(client: TestClient) -> None:
    response = client.get("/transactions?owner=Anna&text=a%26b")
    asked = "%2Ftransactions%3Fowner%3DAnna%26text%3Da%2526b"
    assert response.headers["location"] == f"/login?next={asked}"
    page = client.get(response.headers["location"])
    assert 'name="next" value="/transactions?owner=Anna&amp;text=a%26b"' in page.text


@pytest.mark.parametrize("path", ["/import/commit", "/logout", "/"])
def test_nothing_is_changed_by_a_browser_that_is_not_signed_in(
    client: TestClient, path: str
) -> None:
    response = client.post(path, data={"token": "x"})
    assert response.status_code == 401
    assert response.text == "sign in first\n"


def test_the_login_page_and_the_stylesheet_are_all_it_can_see(client: TestClient) -> None:
    page = client.get("/login")
    assert page.status_code == 200
    assert 'type="password"' in page.text
    assert "<nav" not in page.text  # no names of the people, no way around
    assert "Sign out" not in page.text
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/loginx").status_code == 303
    assert client.get("/static").status_code == 303


def test_the_password_signs_in_and_leads_to_the_page_asked_for(client: TestClient) -> None:
    response = sign_in(client, then="/months?owner=Anna")
    assert response.status_code == 303
    assert response.headers["location"] == "/months?owner=Anna"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert f"max-age={30 * 24 * 3600}" in cookie
    assert "secure" not in cookie  # plain HTTP here: a browser would drop it
    for path in PAGES:
        assert client.get(path).status_code == 200, path
    assert "Sign out" in client.get("/months").text


def test_over_https_the_cookie_is_for_https_only(database: Path, clock: Clock) -> None:
    app = webapp.create_app(database, clock, password=STORED, names=["finanze.home"])
    client = TestClient(app, base_url="https://finanze.home", follow_redirects=False)
    response = sign_in(client)
    assert "secure" in response.headers["set-cookie"].lower()


def test_a_wrong_password_shows_nothing_and_the_next_attempt_waits(
    client: TestClient, clock: Clock
) -> None:
    wrong = sign_in(client, "guess", then="/months")
    assert wrong.status_code == 401
    assert "That is not the password." in wrong.text
    assert 'name="next" value="/months"' in wrong.text
    assert "set-cookie" not in wrong.headers
    assert client.get("/").status_code == 303

    soon = sign_in(client)  # the right one, but within the second
    assert soon.status_code == 429
    assert "Wait 1 second and try again." in soon.text
    assert client.get("/").status_code == 303

    clock.wait(seconds=1)
    assert sign_in(client).status_code == 303
    assert client.get("/").status_code == 200


def test_a_session_ends_after_thirty_days(client: TestClient, clock: Clock) -> None:
    sign_in(client)
    clock.wait(days=29, hours=23)
    assert client.get("/").status_code == 200
    clock.wait(hours=1)
    assert client.get("/").status_code == 303


def test_a_cookie_that_was_not_given_out_opens_nothing(client: TestClient) -> None:
    client.cookies.set("finanze_session", "guessed")
    assert client.get("/").status_code == 303


def test_signing_out_ends_the_session_for_good(client: TestClient) -> None:
    sign_in(client)
    session = client.cookies["finanze_session"]
    out = client.post("/logout", data={"token": token(client.get("/months").text)})
    assert out.status_code == 303
    assert out.headers["location"] == "/login"
    assert "finanze_session" not in client.cookies
    client.cookies.set("finanze_session", session)  # a copy someone kept
    assert client.get("/").status_code == 303


def test_signing_in_and_out_want_the_token_of_these_pages(client: TestClient) -> None:
    assert client.post("/login", data={"password": WORDS}).status_code == 403
    good = {"token": token(client.get("/login").text), "password": WORDS}
    elsewhere = client.post("/login", data=good, headers={"origin": "https://evil.example"})
    assert elsewhere.status_code == 403
    assert client.get("/").status_code == 303
    sign_in(client)
    assert client.post("/logout", data={"token": "x"}).status_code == 403
    assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "then",
    ["https://evil.example/", "//evil.example/", "/\\evil.example", "months", ""],
)
def test_signing_in_never_leads_to_another_site(client: TestClient, then: str) -> None:
    page = client.get("/login", params={"next": then})
    assert 'name="next" value="/"' in page.text
    response = sign_in(client, then=then)
    assert response.headers["location"] == "/"


def test_the_app_answers_under_the_names_it_was_given(client: TestClient) -> None:
    named = {"host": "finanze.home:8765"}
    assert client.get("/login", headers=named).status_code == 200
    assert client.get("/login", headers={"host": "other.home"}).status_code == 400
    form = {"token": token(client.get("/login").text), "password": WORDS}
    sent = client.post("/login", data=form, headers=named | {"origin": "http://finanze.home:8765"})
    assert sent.status_code == 303


def test_other_names_without_a_password_are_refused(database: Path) -> None:
    with pytest.raises(ValueError, match="not served under other names without a password"):
        webapp.create_app(database, names=["finanze.home"])
    with pytest.raises(auth.MalformedDigestError):
        webapp.create_app(database, password=WORDS)  # the words themselves, not their digest


def test_without_a_password_there_is_nothing_to_sign_in_to(database: Path) -> None:
    client = TestClient(webapp.create_app(database), base_url=SITE, follow_redirects=False)
    assert client.get("/").status_code == 200
    assert "Sign out" not in client.get("/months").text
    assert client.get("/login").status_code == 404
    good = {"token": client.app.state.token}  # type: ignore[attr-defined]  # the Starlette app
    assert client.post("/login", data=good).status_code == 404
    assert client.post("/logout", data=good).status_code == 404
