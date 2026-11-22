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

## Extension points

- `techpulse.sources` turns source responses into `Article` records.
- `techpulse.articles` categorizes and ranks articles. A future summarizer can consume the selected records before rendering.
- `techpulse.digest` renders the preview and Telegram messages. `techpulse.telegram` handles delivery.
- A future GitHub Actions workflow can install dependencies on Python 3.12 and invoke `python -m techpulse --send` once a day with both Telegram values supplied through repository secrets. Phase 1 runs on demand.
