"""Gemini Developer API adapter for explicitly unbilled projects."""

import json
import time

import requests

from .summaries import PROMPT, SCHEMA, ProviderUnavailable, SummaryError

MODEL = "gemini-3.1-flash-lite"
TIMEOUT = (5, 15)


class GeminiProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.cache_namespace = f"gemini:{model}"
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def summarize(self, headline: str, description: str) -> object:
        payload = {
            "systemInstruction": {"parts": [{"text": PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(
                {"headline": headline, "description": description}, ensure_ascii=False
            )}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": SCHEMA,
                "maxOutputTokens": 1024,
                "thinkingConfig": {"thinkingLevel": "MINIMAL"},
            },
        }
        for attempt in range(2):
            try:
                response = requests.post(
                    self.endpoint, headers={"x-goog-api-key": self.api_key},
                    json=payload, timeout=TIMEOUT, allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt:
                    raise ProviderUnavailable("Gemini network failure") from None
            except requests.RequestException:
                raise ProviderUnavailable("Gemini request failure") from None
            else:
                status = response.status_code
                if status == 200:
                    return read_candidate(response)
                if status == 400 and location_unsupported(response):
                    raise ProviderUnavailable("Gemini HTTP 400: free-tier API access unavailable from this location")
                if status not in {500, 502, 503, 504} or attempt:
                    raise ProviderUnavailable(f"Gemini HTTP {status}")
            time.sleep(1)
        raise ProviderUnavailable("Gemini retry limit reached")


def location_unsupported(response: requests.Response) -> bool:
    try:
        error = response.json()["error"]
        message = error["message"]
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(message, str) and "location is not supported" in message.casefold()


def read_candidate(response: requests.Response) -> object:
    try:
        candidate = response.json()["candidates"][0]
        if candidate["finishReason"] != "STOP":
            raise ValueError
        parts = candidate["content"]["parts"]
        text = "".join(part["text"] for part in parts if not part.get("thought"))
        return json.loads(text)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise SummaryError("invalid Gemini response") from None
