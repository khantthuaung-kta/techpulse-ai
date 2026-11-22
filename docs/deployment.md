# Deploy and operate on GitHub Actions

## Before deployment

1. Run the checks in [CONTRIBUTING.md](../CONTRIBUTING.md) on Python 3.12. Verify that `.env`, SQLite state, and any real chat screenshots stay outside Git.
2. Enable GitHub Actions in the target repository. Confirm that your plan has enough included Actions usage and keep paid overages disabled for a $0 budget. Preserve the `techpulse-delivery-state` branch and allow the delivery job to create or update it. Keep protections on the default branch.
3. In **Settings → Secrets and variables → Actions**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as repository secrets. Confirm the bot can post to your chat or channel. Paste values alone and keep them out of issues and logs.
4. Optionally configure Groq or Gemini using the [AI setup](../README.md#optional-burmese-summaries-with-gemini). Confirm the provider account is on its Free tier before setting the confirmation variable. You can leave AI disabled and deliver news with original headlines and links.
5. Merge the workflow onto the default branch. The scheduled run is at **07:00 Asia/Yangon**. GitHub can delay a run or skip it during high load. [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## Verify deployment

1. Open **Actions → Daily TechPulse AI digest → Run workflow** on the default branch. Leave the live-send checkbox off. The preview should show collector results and a digest or an empty-news notice.
2. On a day without an existing digest, run it again with the live-send checkbox on. Verify the receipt in the target chat and the confirmed-parts count in the send logs. Verify the day's record on `techpulse-delivery-state` has status `sent`.
3. Rerun the same delivery job on the same Yangon date. Expect a duplicate skip and no new Telegram message. A preview never creates a claim.
4. Observe the first successful **schedule** event at the next eligible Yangon delivery date. Record its run link, confirmed-part count, and actual arrival time. A manual send on that date will consume that day's delivery slot.
5. Enable failed-run web or email notifications in GitHub for the account associated with the schedule. A skipped schedule has no failed-run notification, so inspect the Actions history if a digest is missing. [GitHub workflow notifications](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)

Use one sending environment per destination. The local SQLite ledger does not share state with the GitHub ledger. The interactive bot is optional and runs in the foreground locally, independently of the scheduled job.

## Recover a failed delivery

- **Missing secrets or HTTP 401** — Check the GitHub secret names and the token's validity. Rotate any exposed credential through the issuing provider and update its secret before testing again.
- **HTTP 400 or 403** — Verify the destination and bot posting permissions.
- **Rate limit** — A short explicit Telegram retry is automatic. For a longer rate limit, check the chat and the date's claim before making any manual recovery.
- **Source outage** — Review per-source warnings. Partial source results are accepted. If every source fails, the workflow fails before claiming a digest. If no unsent stories qualify, it exits without claiming or sending.
- **GitHub state write failure, timeout, cancellation, or partial delivery** — Read the date's ledger record and verify which messages are present in Telegram. A missing receipt does not establish that the message was never delivered. Leave the claim if anything arrived or the outcome is uncertain. Only clear that day's claim when no worker is active and the chat confirms nothing was delivered. See [ledger details](../README.md#delivery-ledger-and-recovery).

To inspect an already-deployed ledger:

```sh
git fetch origin techpulse-delivery-state
git show FETCH_HEAD:delivery/YYYY-MM-DD.json
```

## Roll back safely

Disable the daily workflow from the Actions tab if delivery is unsafe. Keep the state branch and do not switch to a previous sender that bypasses the ledger. Re-enable after fixing the issue and follow the preview and receipt checks above. If credentials were exposed, rotate them before restarting. Rotating the bot token preserves the daily slot but changes the scope of article-history and local interactive-reader fingerprints.
