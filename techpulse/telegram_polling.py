"""Bounded Bot API reads used by the local long poller."""

import re

import requests

POLL_SECONDS = 20
POLL_LIMIT = 20
MAX_WAIT = 120


class PollError(Exception):
    """A sanitized polling error, optionally safe to retry."""

    def __init__(self, message: str, *, retry: bool = False, delay: int = 0) -> None:
        super().__init__(message)
        self.retry = retry
        self.delay = delay


def bot_api_read(token: str, method: str, payload: dict, read_timeout: int) -> object:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload, timeout=(5, read_timeout), allow_redirects=False,
        )
    except requests.RequestException as error:
        raise PollError(f"Telegram polling connection failed ({type(error).__name__}).", retry=True) from None

    if response.status_code == 409:
        raise PollError("Telegram polling conflict. Check for another poller or an active webhook.")
    if response.status_code in {401, 403}:
        raise PollError(f"Telegram denied polling (HTTP {response.status_code}). Check the bot credentials.")
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code == 429:
        parameters = data.get("parameters") if isinstance(data, dict) else None
        delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if type(delay) is int and delay > MAX_WAIT:
            raise PollError("Telegram requests a longer polling pause. Stop and retry later.")
        raise PollError("Telegram rate limited polling.", retry=True,
                        delay=delay if type(delay) is int and delay > 0 else 0)
    if response.status_code != 200:
        raise PollError(f"Telegram polling returned HTTP {response.status_code}.",
                        retry=response.status_code >= 500)
    if not isinstance(data, dict) or data.get("ok") is not True or "result" not in data:
        raise PollError("Telegram polling returned an invalid response.", retry=True)
    return data["result"]


def get_username(token: str) -> str:
    result = bot_api_read(token, "getMe", {}, 10)
    username = result.get("username") if isinstance(result, dict) else None
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        raise PollError("Telegram did not return a valid bot username.")
    return username.casefold()


def get_updates(token: str, offset: int) -> list[dict]:
    result = bot_api_read(token, "getUpdates", {
        "offset": offset, "timeout": POLL_SECONDS, "limit": POLL_LIMIT,
        "allowed_updates": ["message"],
    }, POLL_SECONDS + 5)
    if not isinstance(result, list) or len(result) > POLL_LIMIT or any(
        not isinstance(update, dict) or type(update.get("update_id")) is not int
        or not 0 <= update["update_id"] < 2**52
        for update in result
    ):
        raise PollError("Telegram returned an invalid update batch.", retry=True)
    return sorted(result, key=lambda update: update["update_id"])
