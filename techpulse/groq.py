"""GroqCloud text completion adapter for the Free plan."""

import json
import re
import time

import requests

from .summaries import (
    PROMPT, SCHEMA, SCHEMA_PROPERTIES, TEXT_LIMITS, ProviderUnavailable, SummaryError, validate_summary,
)

MODEL = "openai/gpt-oss-20b"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = (5, 15)
TRANSIENT_STATUS = {500, 502, 503, 504}
CLAIM_HEADLINE = re.compile(r"主張|\bclaims?\s+(?:to|that)\b", re.IGNORECASE)
ATTRIBUTION = re.compile(r"ဟု\s*(?:ဆို|ပြော|ပြောဆို)")
DIGIT = re.compile(r"\d")
VERSION_OR_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?:[vV]?\d+(?:\.\d+)+|"
    r"(?:[A-Za-z]|\d+[A-Za-z])[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*)(?![A-Za-z0-9])"
)
PROMPT_QUANTITY = re.compile(
    VERSION_OR_IDENTIFIER.pattern + r"|(?P<quantity>\d+(?:[.,]\d+)*(?:[億万千百]?行)?)"
)
KATAKANA_PERSON_NAME = re.compile(r"[ァ-ヺー]+(?:[・･][ァ-ヺー]+)+氏")
GROQ_PROMPT = PROMPT.replace(
    "Include an exact contiguous quote from the description supporting the summary in evidence.",
    "In evidence, return the string ID of the publisher excerpt that supports the summary.",
) + """
Write all three display fields in Burmese (မြန်မာဘာသာ), even if the article
is in Japanese or English. Keep names in their original spelling. Use two
short sentences for the summary and one short sentence for relevance. Aim
for at most 120 characters in the headline, 250 in the summary and 100 in
relevance. Avoid listing the reader's whole tech stack. Omit quantities,
dates, and counts. Keep only dotted software versions and product identifiers
that occur verbatim in the source, such as Python 3.12. Preserve
attribution and uncertainty, especially in claims of scientific results.
When a source reports someone's claim, attribute it in both headline and
summary. Use ဟုဆိုသည် or ဟုဆိုထားသည်. Do not state the claimed result as
established fact. Omit personal names unless needed and present verbatim
in the source. Use the person's role when their name is transliterated.
"""
CORRECTION = (
    "\nA previous response was rejected. Regenerate from the supplied evidence alone. "
    "Return only the schema fields and an offered evidence ID. Use shorter Burmese text, "
    "omit optional names and numbers, and explicitly attribute claims in both display fields."
)


class GroqProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.cache_namespace = f"groq:{model}:verbatim-evidence-v8"

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
                **SCHEMA_PROPERTIES,
                "evidence": {"type": "string", "enum": ["", *excerpts]},
            },
        }
        user_message = {"role": "user", "content": json.dumps(
            {
                "headline": prompt_excerpt(headline),
                "excerpts": {key: prompt_excerpt(text) for key, text in excerpts.items()},
            },
            ensure_ascii=False,
        )}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": GROQ_PROMPT},
                user_message,
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
                if status == 200 or (status == 400 and generated_output_rejected(response)):
                    try:
                        if status == 400:
                            raise SummaryError("Groq rejected its generated output")
                        return validated_completion(response, headline, description, excerpts)
                    except SummaryError:
                        if attempt:
                            raise
                        # Keep the same evidence and schema. Never feed rejected output back.
                        payload = {
                            **payload,
                            "messages": [
                                {"role": "system", "content": GROQ_PROMPT + CORRECTION},
                                user_message,
                            ],
                        }
                elif status not in TRANSIENT_STATUS or attempt:
                    raise ProviderUnavailable(f"Groq HTTP {status}")
            time.sleep(1)
        raise ProviderUnavailable("Groq retry limit reached")


def prompt_excerpt(text: str) -> str:
    """Keep optional quantities and transliterated personal names out of the prompt."""
    text = KATAKANA_PERSON_NAME.sub("[person's name omitted]", text)
    return PROMPT_QUANTITY.sub(
        lambda match: "[quantity omitted]" if match.group("quantity") else match.group(), text
    )


def validated_completion(
    response: requests.Response, headline: str, description: str, excerpts: dict[str, str]
) -> dict[str, str]:
    result = read_completion(response)
    if not isinstance(result, dict) or not isinstance(result.get("evidence"), str):
        raise SummaryError("invalid Groq evidence selection")
    evidence = excerpts.get(result["evidence"])
    if evidence is None:
        raise SummaryError("invalid Groq evidence selection")
    result = {**result, "evidence": evidence}
    summary = validate_summary(result, headline, description)
    source_tokens = set(VERSION_OR_IDENTIFIER.findall(f"{headline} {description}"))
    for value in (summary.headline, summary.summary, summary.relevance):
        remainder = VERSION_OR_IDENTIFIER.sub(
            lambda match: "" if match.group() in source_tokens else match.group(), value
        )
        if DIGIT.search(remainder):
            raise SummaryError("Groq included a quantity or changed a numbered identifier")
    if CLAIM_HEADLINE.search(headline) and not all(
        ATTRIBUTION.search(value) for value in (summary.headline, summary.summary)
    ):
        raise SummaryError("Groq omitted an attribution marker from a claimed result")
    return result


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
