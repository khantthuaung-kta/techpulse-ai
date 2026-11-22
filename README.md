# TechPulse AI

TechPulse AI collects recent technology news from Hacker News and trusted RSS or Atom feeds and sends a daily Telegram digest. Run the CLI once per digest.

## Run locally

From the repository root, with Python 3.12:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
python -m techpulse --dry-run
```

The tests use local fixtures and mocked HTTP responses. The dry run fetches live news, prints the digest with original URLs, and requires internet access.

To test Telegram delivery, create a bot with [BotFather](https://t.me/BotFather), start a chat with it or give it posting permission in your target group or channel, and copy the sample environment file.

```sh
cp .env.example .env
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, then run:

```sh
python -m src.main --test-telegram
```

The bot sends “✅ TechPulse AI is connected successfully!” to the configured chat. This command only contacts Telegram and can run without a feed configuration. API errors and timeouts return a nonzero exit status. Check the chat before retrying a timeout because Telegram may already have delivered the message.

To send a live news digest, run:

```sh
python -m techpulse --send
```

Use the numeric chat ID for a private chat or group, or `@channelusername` for a public channel where the bot can post. For a private chat or group, send a message or command the bot can receive and inspect the `message.chat.id` field from [Telegram's getUpdates method](https://core.telegram.org/bots/api#getupdates). Existing environment variables take precedence over `.env`. Each `--send` invocation can post a new digest, so check Telegram before retrying an ambiguous timeout.

## Configure feeds and selection

Edit `feeds.toml` or pass `--feeds path/to/feeds.toml`. Every `[[feeds]]` entry has a display `name`, an HTTP(S) `url`, and a default `category`. Supported categories are AI & LLM, Programming, Developer Tools, Japan IT, Open Source, and Trending Tech. Title keywords can override a feed's default category. To run with Hacker News alone, use a config containing `feeds = []`.

The collector reads up to 40 IDs each from Hacker News top and new stories. Hacker News entries need an external story link and a technology-related title. Their displayed time is the Hacker News submission time. Feeds use the publisher's publication timestamp or the Atom update timestamp if publication time is absent. Times are displayed in UTC.

Selection considers the last 24 hours. It ranks by freshness, specific topic matches, and capped Hacker News popularity, then deduplicates normalized links and titles and keeps up to 10 articles. A failed source is logged and the remaining sources can supply the digest. If every source fails, the CLI exits with status 1. A successful collection with zero matches produces an empty-digest notice. Telegram messages split between articles when needed.

When a free-tier AI provider is configured, the three highest-ranked distinct stories keep their positions. For the remaining spots, usable publisher text from a configured feed adds one ranking point, equivalent to 12 hours of freshness within the 24-hour window. Headline repeats, bare links, and short boilerplate earn no bonus and are skipped by the summarizer. Stories without publisher text can still rank on freshness, topic and popularity. With AI off or incomplete configuration, the original ranking applies.

## Extension points

- `techpulse.sources` turns source responses into `Article` records.
- `techpulse.articles` categorizes and ranks articles. The summarizer consumes selected records before rendering.
- `techpulse.digest` renders the preview and Telegram messages. `techpulse.telegram` handles delivery.

## Optional Burmese summaries with Gemini

Gemini is disabled by default. For a $0 budget, create a key in a dedicated [Google AI Studio project](https://aistudio.google.com/projects) that has **no Cloud Billing account linked**. Keep billing unlinked. The app requires explicit confirmation before making inference calls because the key alone cannot verify the project's billing tier. Use Gemini 3.1 Flash-Lite, which Google lists for free-tier text input and output. The adapter uses text generation only. [Model pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-lite) · [Billing tiers](https://ai.google.dev/gemini-api/docs/billing)

Set these values in your ignored local `.env`:

```dotenv
AI_PROVIDER=gemini
AI_MODEL=gemini-3.1-flash-lite
GEMINI_FREE_TIER_CONFIRMED=true
GEMINI_API_KEY=
AI_SUMMARY_CACHE=.techpulse-summaries.sqlite3
```

Fill in `GEMINI_API_KEY` locally. The SQLite cache is optional. Use a persistent local path to reuse validated summaries across invocations. Entries are keyed by article URL and evidence, model, and prompt version. The hosted workflow leaves the cache unset because the runner is ephemeral.

Run `python -m src.main --dry-run` to preview with the configured AI provider. It can consume free Gemini quota and prints a Burmese headline, factual summary, career relevance, and the original article URL when adequate publisher text exists. Publisher feed descriptions or inline feed text are labeled **Headline/description-based**. Hacker News entries often contain only a headline. Headline-only items and failed or rejected summaries keep the original headline and URL. The collector uses feed-supplied text and does not request or bypass access controls on linked pages. Source quotes and numbers are checked against the supplied evidence, but an accepted model response can still misstate the source.

For GitHub Actions, add `GEMINI_API_KEY` as an **Actions secret**. Add `AI_PROVIDER=gemini`, `AI_MODEL=gemini-3.1-flash-lite`, and `GEMINI_FREE_TIER_CONFIRMED=true` under **Actions variables**. Try the preview before enabling a live manual send. The date in the digest heading uses Yangon time. Individual article timestamps remain UTC.

Expect one serial request per eligible uncached item, with at most 10 first attempts per digest. A timeout or transient server error allows one retry after a delay. Quota and authentication failures stop further Gemini requests for that digest. If free-tier access or quota runs out, leave billing disabled and use the original-headline fallback.

If the preview selects only headline-only items, it can report zero summaries without contacting Gemini. If Gemini reports that free-tier API access is unavailable from the current location, the digest falls back to original headlines. Check [Google's available regions](https://ai.google.dev/gemini-api/docs/available-regions) and keep billing disabled for a $0 budget.

### GroqCloud alternative

For a $0 budget, confirm that your Groq organization is on the **Free** plan before creating a key. The app uses GPT-OSS 20B with structured text output, one request at a time, and stops further attempts for the digest on quota or access errors. The confirmation flag represents the account owner's check of the plan. The app cannot verify the plan from the key. [Groq billing FAQ](https://console.groq.com/docs/billing-faqs) · [Rate limits](https://console.groq.com/docs/rate-limits)

Add the key directly to your ignored `.env` and update the AI settings:

```dotenv
AI_PROVIDER=groq
AI_MODEL=openai/gpt-oss-20b
GROQ_API_KEY=
GROQ_FREE_TIER_CONFIRMED=true
```

Run `python -m src.main --dry-run` from the repository root. When an eligible story has publisher text, a successful preview includes a Burmese summary and the original link. Headline-only stories and failed inference use the original headline. Switch back to Gemini by restoring its provider and model settings. The app calls only the selected provider.

For GitHub Actions, set `AI_PROVIDER`, `AI_MODEL`, and `GROQ_FREE_TIER_CONFIRMED` as Actions variables and `GROQ_API_KEY` as an Actions secret. Verify in preview mode before enabling a live Telegram send.

Groq selects a publisher excerpt by ID. The app resolves that ID to the original text and still rejects summaries with unsupported numbers or invalid Burmese fields.

## GitHub Actions daily delivery

The [daily workflow](.github/workflows/daily-news.yml) runs `python -m src.main --send` on Python 3.12 at 07:00 every day in `Asia/Yangon`. The workflow must be on the repository's default branch for scheduled delivery. GitHub can delay or drop scheduled runs during high load. [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

1. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` using the same values as your local `.env`. Keep `.env` local and ignored by Git.
2. Open **Actions → Daily TechPulse AI digest → Run workflow**, select the default branch, and leave **Send a live Telegram digest** unchecked for a preview. Open **Preview the news digest** in the run logs to see the selected articles.
3. To test delivery, use **Run workflow** again with **Send a live Telegram digest** checked. This sends a real digest to the configured chat. Check the Telegram chat and the **Send the news digest to Telegram** step for confirmation.

If the send step reports missing secrets, confirm both names and values under the repository's **Actions** secrets. For HTTP 401, check that the token belongs to the active bot. For HTTP 400 or 403, verify the chat ID, start the bot in a private chat, or confirm it can post in the target group or channel. If the job fails during collection, read the source warnings in the failed step. If a scheduled run is missing, check that Actions is enabled and the workflow is on the default branch. For timeouts, cancellation, or partial delivery, check the chat before rerunning.

### Duplicate delivery across runners

The workflow's concurrency group prevents overlapping runs. A later manual run or rerun can still deliver the same digest. Until a durable delivery ledger is deployed, check the target chat before repeating a send for the same day.

For durable protection, use a shared store with atomic conditional writes, such as DynamoDB. Key each record by a stable destination identifier and the intended Yangon digest date. Pass the original date through on retries so a late rerun keeps its original key. Before the first Telegram request, atomically create a claim and skip delivery if a claim already exists. Record confirmed message IDs and mark the record sent only after every part succeeds. Retain records for the full period in which reruns are permitted. After a timeout, runner crash, or partial send, keep the claim and reconcile against the chat before clearing it or resuming unsent parts. Telegram delivery and the ledger write can fail independently, so the claim prevents blind retries but cannot guarantee exactly-once delivery.
