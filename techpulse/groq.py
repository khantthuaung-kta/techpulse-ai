"""GroqCloud text completion adapter for the Free plan."""

import json
import time

import requests

from .summaries import PROMPT, SCHEMA, ProviderUnavailable, SummaryError

MODEL = "openai/gpt-oss-20b"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = (5, 15)
TRANSIENT_STATUS = {500, 502, 503, 504}


class GroqProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.cache_namespace = f"groq:{model}"

    def summarize(self, headline: str, description: str) -> object:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(
                    {"headline": headline, "description": description}, ensure_ascii=False
                )},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "burmese_news_summary", "strict": True, "schema": SCHEMA,
            }},
            "reasoning_effort": "low",
            "max_completion_tokens": 1536,
        }
        for attempt in range(2):
            try:
                response = requests.post(
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=TIMEOUT,
                    allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt:
                    raise ProviderUnavailable("Groq network failure") from None
            except requests.RequestException:
                raise ProviderUnavailable("Groq request failure") from None
            else:
                status = response.status_code
                if status == 200:
                    return read_completion(response)
                if status not in TRANSIENT_STATUS or attempt:
                    raise ProviderUnavailable(f"Groq HTTP {status}")
            time.sleep(1)
        raise ProviderUnavailable("Groq retry limit reached")


def read_completion(response: requests.Response) -> object:
    try:
        choice = response.json()["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError
        return json.loads(choice["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise SummaryError("invalid Groq response") from None
