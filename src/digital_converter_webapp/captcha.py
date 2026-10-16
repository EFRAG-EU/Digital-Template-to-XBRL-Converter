"""The upload form's captcha and CSRF checks."""

from __future__ import annotations

from random import randint
from secrets import token_hex

from flask import Response, flash, make_response, redirect, request, session, url_for

MAX_LIVE_CAPTCHAS = 20  # answers kept per session (multiple tabs/reloads)


def generate_captcha() -> tuple[str, str]:
    """Generate a simple math captcha and stash its answer in the session.

    Answers are kept in an id-keyed map (capped at MAX_LIVE_CAPTCHAS, oldest
    evicted first) so concurrently open forms (multiple tabs, reloads) each
    validate against their own question.
    """
    num1 = randint(1, 10)
    num2 = randint(1, 10)
    captcha_id = token_hex(8)
    answers = session.setdefault("captcha_answers", {})
    answers[captcha_id] = num1 + num2
    while len(answers) > MAX_LIVE_CAPTCHAS:
        answers.pop(next(iter(answers)))
    session.modified = True
    return captcha_id, f"What is {num1} + {num2}?"


def validateCaptchaAndCsrf() -> Response | None:
    """Check the submitted captcha answer and CSRF token.

    Returns a redirect back to the form (with a flashed error) if either is
    wrong, or None if the upload may proceed. The captcha answer is single use:
    it is removed on the first attempt.
    """
    captcha_input = request.form.get("captcha", type=int)
    captcha_id = request.form.get("captcha_id", "")
    captcha_answer = session.get("captcha_answers", {}).pop(captcha_id, None)
    session.modified = True
    if captcha_answer is None or captcha_input != captcha_answer:
        flash(
            message="Invalid captcha. Please confirm you are human by calculating the correct result and try again.",
            category="error",
        )
        return make_response(redirect(url_for("basic.index")))
    csrf_token = request.form.get("csrf_token")
    if not csrf_token or csrf_token != session.get("csrf_token"):
        flash(message="Invalid CSRF token. Please try again.", category="error")
        return make_response(redirect(url_for("basic.index")))
    return None
