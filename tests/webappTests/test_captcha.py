"""Captcha behaviour with ENABLE_CAPTCHA on (captcha_client fixture) and off
(plain client fixture)."""

import io
import re

from flask import session

from digital_converter_webapp.captcha import MAX_LIVE_CAPTCHAS, generate_captcha

_QUESTION = re.compile(r"What is (\d+) \+ (\d+)\?")


def _hidden_field(html: str, name: str) -> str:
    match = re.search(f'name="{name}" value="([^"]*)"', html)
    assert match, f"Hidden field {name!r} not found in page"
    return match.group(1)


def _captcha_form(client) -> tuple[str, str, int]:
    """Load the index page and return (captcha_id, csrf_token, correct answer)."""
    html = client.get("/").get_data(as_text=True)
    match = _QUESTION.search(html)
    assert match, "Captcha question not found in page"
    answer = int(match.group(1)) + int(match.group(2))
    return _hidden_field(html, "captcha_id"), _hidden_field(html, "csrf_token"), answer


def _upload(client, **form_fields):
    """POST a minimal valid upload; captcha fields supplied by the caller."""
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(b"content"), "test.xlsx"), **form_fields},
        content_type="multipart/form-data",
    )


def _submit_captcha(client, captcha_id: str, csrf_token: str, answer: int):
    return _upload(
        client, captcha=str(answer), captcha_id=captcha_id, csrf_token=csrf_token
    )


class TestGenerateCaptcha:
    def test_question_matches_stored_answer(self, app):
        with app.test_request_context("/"):
            captcha_id, question = generate_captcha()
            num1, num2 = map(int, _QUESTION.search(question).groups())
            assert session["captcha_answers"][captcha_id] == num1 + num2

    def test_concurrent_captchas_all_kept(self, app):
        with app.test_request_context("/"):
            ids = [generate_captcha()[0] for _ in range(5)]
            answers = session["captcha_answers"]
            assert all(captcha_id in answers for captcha_id in ids)

    def test_oldest_evicted_beyond_cap(self, app):
        with app.test_request_context("/"):
            ids = [generate_captcha()[0] for _ in range(MAX_LIVE_CAPTCHAS + 5)]
            answers = session["captcha_answers"]
            assert len(answers) == MAX_LIVE_CAPTCHAS
            assert ids[0] not in answers
            assert ids[-1] in answers


class TestCaptchaEnabled:
    def test_form_includes_question_and_hidden_fields(self, captcha_client):
        html = captcha_client.get("/").get_data(as_text=True)
        assert _QUESTION.search(html)
        assert _hidden_field(html, "captcha_id")
        assert _hidden_field(html, "csrf_token")

    def test_correct_answer_accepted(self, captcha_client):
        captcha_id, csrf_token, answer = _captcha_form(captcha_client)
        resp = _submit_captcha(captcha_client, captcha_id, csrf_token, answer)
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]

    def test_wrong_answer_rejected_with_flash(self, captcha_client):
        captcha_id, csrf_token, answer = _captcha_form(captcha_client)
        resp = _submit_captcha(captcha_client, captcha_id, csrf_token, answer + 1)
        assert resp.status_code == 302
        html = captcha_client.get("/").get_data(as_text=True)
        assert "Invalid captcha" in html

    def test_answer_is_single_use(self, captcha_client):
        captcha_id, csrf_token, answer = _captcha_form(captcha_client)
        first = _submit_captcha(captcha_client, captcha_id, csrf_token, answer)
        assert first.status_code == 303
        replay = _submit_captcha(captcha_client, captcha_id, csrf_token, answer)
        assert replay.status_code == 302

    def test_concurrently_open_forms_each_validate(self, captcha_client):
        # Two page loads (e.g. two browser tabs); the older form must still work
        older = _captcha_form(captcha_client)
        newer = _captcha_form(captcha_client)
        assert _submit_captcha(captcha_client, *older).status_code == 303
        assert _submit_captcha(captcha_client, *newer).status_code == 303

    def test_missing_captcha_fields_rejected(self, captcha_client):
        captcha_client.get("/")
        resp = _upload(captcha_client)
        assert resp.status_code == 302


class TestCaptchaDisabled:
    def test_form_has_no_captcha_fields(self, client):
        html = client.get("/").get_data(as_text=True)
        for name in ("captcha", "captcha_id", "csrf_token"):
            assert f'name="{name}"' not in html
        assert not _QUESTION.search(html)
        assert 'value="None"' not in html

    def test_upload_succeeds_without_captcha_fields(self, client):
        resp = _upload(client)
        assert resp.status_code == 303
        assert "/conversions/" in resp.headers["Location"]
