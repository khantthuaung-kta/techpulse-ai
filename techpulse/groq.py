"""GroqCloud text completion adapter for the Free plan."""

import json
import re
import time

import requests

from .summaries import PROMPT, SCHEMA, TEXT_LIMITS, ProviderUnavailable, SummaryError

MODEL = "openai/gpt-oss-20b"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = (5, 15)
TRANSIENT_STATUS = {500, 502, 503, 504}
CLAIM_HEADLINE = re.compile(r"主張|\bclaims?\s+(?:to|that)\b", re.IGNORECASE)
ATTRIBUTION = re.compile(r"ဟု\s*(?:ဆို|ပြော|ပြောဆို)")
GROQ_PROMPT = PROMPT.replace(
    "Include an exact contiguous quote from the description supporting the summary in evidence.",
    "In evidence, return the string ID of the publisher excerpt that supports the summary.",
) + """
Write all three display fields in Burmese (မြန်မာဘာသာ), even if the article
is in Japanese or English. Keep names in their original spelling. Use two
short sentences for the summary and one short sentence for relevance. Aim
for at most 120 characters in the headline, 250 in the summary and 100 in
relevance. Avoid listing the reader's whole tech stack. Omit numerical
details unless they are essential and the source gives the same value and unit. Preserve
attribution and uncertainty, especially in claims of scientific results.
When a source reports someone's claim, attribute it in both headline and
summary. Use ဟုဆိုသည် or ဟုဆိုထားသည်. Do not state the claimed result as
established fact. Omit a person's name if the source gives it only in a
different script and the original spelling is unavailable.
"""


class GroqProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.cache_namespace = f"groq:{model}:verbatim-evidence-v5"

    def summarize(self, headline: str, description: str) -> object:
        limit = TEXT_LIMITS["evidence"]
        excerpts = {}
        for start in range(0, len(description), 250):
            excerpt = description[start:start + limit].strip()
            if len(excerpt) >= 12:
                excerpts[str(start)] = excerpt
            if start + limit >= len(description):
                break
        if not excerpts:
            raise SummaryError("no usable Groq evidence excerpt")
        schema = {
            **SCHEMA,
            "properties": {
                **SCHEMA["properties"],
                "evidence": {"type": "string", "enum": ["", *excerpts]},
            },
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": GROQ_PROMPT},
                {"role": "user", "content": json.dumps(
                    {"headline": headline, "excerpts": excerpts},
                    ensure_ascii=False,
                )},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "burmese_news_summary", "strict": True, "schema": schema,
            }},
            "reasoning_effort": "medium",
            "temperature": 0.2,
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
                    result = read_completion(response)
                    if not isinstance(result, dict) or not isinstance(result.get("evidence"), str):
                        raise SummaryError("invalid Groq evidence selection")
                    evidence = excerpts.get(result["evidence"])
                    if evidence is None:
                        raise SummaryError("invalid Groq evidence selection")
                    for field in ("headline", "summary"):
                        value = result.get(field)
                        if not isinstance(value, str):
                            raise SummaryError("invalid Groq display field")
                        if CLAIM_HEADLINE.search(headline) and not ATTRIBUTION.search(value):
                            raise SummaryError("Groq omitted an attribution marker from a claimed result")
                    return {**result, "evidence": evidence}
                if status == 400 and generated_output_rejected(response):
                    raise SummaryError("Groq rejected its generated output")
                if status not in TRANSIENT_STATUS or attempt:
                    raise ProviderUnavailable(f"Groq HTTP {status}")
            time.sleep(1)
        raise ProviderUnavailable("Groq retry limit reached")


def generated_output_rejected(response: requests.Response) -> bool:
    try:
        return response.json()["error"]["code"] == "json_validate_failed"
    except (ValueError, KeyError, TypeError):
        return False


def read_completion(response: requests.Response) -> object:
    try:
        choice = response.json()["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError
        return json.loads(choice["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise SummaryError("invalid Groq response") from None
