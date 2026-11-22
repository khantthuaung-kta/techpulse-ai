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

Use the numeric chat ID for a private chat or group, or `@channelusername` for a public channel where the bot can post. For a private chat or group, send a message or command the bot can receive and inspect the `message.chat.id` field from [Telegram's getUpdates method](https://core.telegram.org/bots/api#getupdates). Existing environment variables take precedence over `.env`. Local `--send` uses `.techpulse-delivery.sqlite3` in the project directory. To share local state across invocations, keep that file or set `TECHPULSE_DELIVERY_DB` to a fixed path. GitHub Actions uses a separate ledger, so use a single sending environment for any given Telegram destination.

## Configure feeds and selection

Edit `feeds.toml` or pass `--feeds path/to/feeds.toml`. Every `[[feeds]]` entry has a display `name`, an HTTP(S) `url`, and a default `category`. A `[[github_releases]]` entry uses an `owner/repo` value in `repository` in place of a URL. Set `reliability` between 0 and 1 to express a source preference. These are editorial weights. Supported categories are AI & LLM, Programming, Developer Tools, Japan IT, Open Source, and Trending Tech. Title keywords can override a feed's default category. To run with Hacker News alone, use a config containing `feeds = []`.

The collector reads up to 40 IDs each from Hacker News top and new stories. Hacker News entries need an external story link and a technology-related title. Their displayed time is the Hacker News submission time. Feeds use the publisher's publication timestamp or the Atom update timestamp if publication time is absent. GitHub uses the release's publication time and original release URL, reads up to five releases per configured repository with one unauthenticated request, and skips drafts and prereleases. Times are displayed in UTC. Logs report the collector's result count per source and warn when a source fails.

The default registry includes Python Insider, the engineering blogs from LY Corporation and Visual Studio Code, ITmedia, and releases from FastAPI, React, and Odoo Owl. Other sources include Hacker News, GitHub Blog, Hugging Face, Ars Technica IT, and TechCrunch. [Python Insider feed announcement](https://blog.python.org/2026/03/the-python-insider-blog-has-moved/) · [LY Corporation feed announcement](https://engineering.linecorp.com/en/blog/LY-Corporation-Tech-Blog) · [GitHub Releases API](https://docs.github.com/en/rest/releases/releases#list-releases)

A failed source is logged and the remaining sources can supply the digest. If every source fails, the CLI exits with status 1. A successful collection with zero matches produces an empty-digest notice. Telegram messages split between articles when needed.

### Personalized ranking

Edit `preferences.toml` or pass `--preferences path/to/preferences.toml`. Interests cover AI, Python, FastAPI, React/TypeScript, Odoo, developer tools, open source, and Japan IT. Weights range from 0 to 2, and 0 disables an interest. Ranking combines freshness, category, matched career interests, source reliability, capped Hacker News engagement, and title novelty within the current digest. When free-tier AI is ready, usable publisher text adds one point after the first three picks. Headline repeats, bare links, and short boilerplate earn no bonus.

Cross-source deduplication uses normalized URLs and titles, with a conservative title-overlap check that keeps differently numbered releases distinct. The checked-in preferences cap the digest at three stories per source and four per category. It can be shorter when coverage is sparse. It fills from the last 24 hours first and extends the lookback to 36 hours only if fewer than six recent stories qualify. Set `fallback_hours` to 24 to disable the extension, or choose a value up to 72 and adjust `min_recent`.

Japan IT entries distinguish engineering coverage, jobs, and visa or regulatory updates. A policy headline qualifies only from an explicitly configured Japanese government feed with an HTTPS `.go.jp` feed and article link. For such a feed set `category = "Japan IT"` and `official_policy = true`. Job and policy entries show the original headline, publisher, and timestamp with their link.

```sh
AI_PROVIDER=off python -m src.main --dry-run --explain --learn-tip
```

The preview lists each selected story and its scoring factors. `--learn-tip` adds one reading exercise beside a selected publisher link when suitable source text exists. It also works with `--send`. The exercise uses no AI requests.

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

Groq selects a publisher excerpt by ID. The app resolves that ID to the original text and rejects invalid Burmese fields and missing attribution for explicitly claimed results. The prompt masks standalone quantities and Katakana personal names marked with a Japanese honorific to reduce unit and name mistranslations. The validator still checks the original excerpt and rejects generated quantities. Dotted versions and numbered product identifiers can pass when copied from the source. If Groq rejects its generated JSON or the output fails validation, the app can use one corrective attempt with the same excerpts and schema. The two-request limit per item still applies. If the retry fails, the original headline and link remain in the digest.

## GitHub Actions daily delivery

The [daily workflow](.github/workflows/daily-news.yml) runs `python -m src.main --send` on Python 3.12 at 07:00 every day in `Asia/Yangon`. The workflow must be on the repository's default branch for scheduled delivery. GitHub can delay or drop scheduled runs during high load. [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

1. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` using the same values as your local `.env`. Keep `.env` local and ignored by Git.
2. Open **Actions → Daily TechPulse AI digest → Run workflow**, select the default branch, and leave **Send a live Telegram digest** unchecked for a preview. Open **Preview the news digest** in the run logs to see the selected articles.
3. To test delivery, use **Run workflow** again with **Send a live Telegram digest** checked on a day that has no completed digest. Check the Telegram chat and the send step for the confirmed-parts log. Rerun that same job to confirm it reports the duplicate skip without posting again.

If the send step reports missing secrets, confirm both names and values under the repository's **Actions** secrets. For HTTP 401, check that the token belongs to the active bot. For HTTP 400 or 403, verify the chat ID, start the bot in a private chat, or confirm it can post in the target group or channel. If the job fails during collection, read the source warnings in the failed step. If a scheduled run is missing, check that Actions is enabled and the workflow is on the default branch. For timeouts, cancellation, or partial delivery, check the chat before rerunning.

### Delivery ledger and recovery

GitHub Actions stores one record per Yangon date under `delivery/YYYY-MM-DD.json` on the `techpulse-delivery-state` branch. The delivery job creates the branch automatically and uses the built-in token with content write access and run metadata read access. Keep the branch writable by that workflow and retain its history. Preview runs use read-only repository permissions and do not access the ledger. [GitHub Contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)

Each repository reserves one delivery slot per day. The record contains an opaque destination fingerprint, a digest fingerprint, and confirmed Telegram message IDs with article fingerprints. The slot claim is atomic and is written before any Telegram request. After each acknowledged message, the workflow records its receipt. A completed slot is skipped. An incomplete claim stops subsequent same-day sends for review. Reruns use the original GitHub run creation time converted to Yangon. Runs from previous Yangon dates skip delivery. Confirmed articles from the previous seven days are suppressed using their canonical URL or normalized title. Rotating the bot token resets that destination's article history while retaining the same-day slot.

- **No eligible stories** means no Telegram message or daily claim. A later same-day run can still deliver fresh results.
- **Timeout, 5xx, cancellation, or partial delivery** means Telegram may have accepted a message before the runner could save its receipt. Check the chat and the date's record on the state branch. Leave the claim in place if any part was received or delivery is uncertain. If you verify that nothing arrived and no run is active, remove only that date's file in a normal commit on the state branch before redispatching the same day. For local runs, preserve the SQLite file and reconcile locally. Telegram acknowledgments and GitHub writes are separate operations, so delivery cannot be guaranteed exactly once.
- **HTTP 429** gets one retry of the rejected message when Telegram specifies a wait of at most 30 seconds. A timeout or ambiguous server response is never retried automatically.
- **GitHub ledger errors** stop delivery. Check workflow token permissions and rules for creating or updating the state branch. Inspect a record with `git fetch origin techpulse-delivery-state` and `git show FETCH_HEAD:delivery/YYYY-MM-DD.json`.

For alerts, enable failure-only **GitHub Actions** email or web notifications for the repository account responsible for the schedule. GitHub routes scheduled workflow notifications to the account associated with that schedule. The workflow also writes a failure annotation and job summary. Alerts do not call Telegram. A missing scheduled run will not generate a failure notification, so confirm a real **schedule** event and its Telegram receipt after deployment. [GitHub Actions notifications](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)

The ledger uses repository storage and the existing GitHub Actions runner. No external database, billing account, or new secret is needed.
