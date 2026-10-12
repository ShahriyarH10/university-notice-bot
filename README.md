# University Notice Bot

[![CI](https://github.com/ShahriyarH10/university-notice-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/ShahriyarH10/university-notice-bot/actions/workflows/ci.yml)

A Telegram bot that watches the [AIUB notice board](https://www.aiub.edu/category/notices) and
sends every new notice to a chat, with the date, the full title, a short excerpt and a link.
It runs hourly on GitHub Actions, so there is no server to host.

Each message looks like this:

```
🗂️ Registration  ·  📅 27 Sep 2026

Adding Dropping for Fall 2026-27

▎ ADDING & DROPPING
▎ [NO SECTION CHANGING]
▎ Date: Thursday, October 1 and Sunday, October 4, 2026. …      (tap to expand)

[ 🔗 Read full notice ]
```

- A **category tag** is guessed from the title: 🎓 Admission, 📝 Exams, 💰 Scholarship & Aid, 💳 Payments, 🗂️ Registration, 💼 Internship & Thesis, 🏖️ Holiday, 🏆 Events, 🛠️ System, or 📢 General. The rules live in `notice_bot/categories.py`.
- The excerpt is a **collapsible quote**, and the link is a **button**, so messages stay compact.

## How it works

1. Download the notice listing and parse out each notice (title, link, date).
2. Compare the links with `last_notices.json`, the list of notices already delivered.
3. For each new notice, oldest first, fetch its page for the full title and body and send it to Telegram.
4. Record each delivered notice in `last_notices.json`. The workflow commits that file back to the repo, which is how the bot remembers state between runs.

`last_notices.json` records each notice the bot has handled:

```json
{
  "url": "https://www.aiub.edu/adding-dropping-for-fall-2026-27",
  "title": "Adding Dropping for Fall 2026-27",
  "date": "27 Sep 2026",
  "seen_at": "2026-10-12T16:14:33+06:00"
}
```

`date` is the date shown on the notice board; `seen_at` is when the bot sent it, in Bangladesh time (UTC+6). Entries from
before this was added are bare `{"url": …}` objects (or plain URL strings, which are still read).

Behaviour worth knowing:

- **First run is silent.** If `last_notices.json` doesn't exist, the bot records what is on the board without sending anything, so you don't get dozens of old notices.
- **Failures are loud.** If the board can't be read, parses to zero notices (the site layout changed, or Cloudflare blocked the request) or a message can't be delivered, the run exits non-zero and the workflow sends a "Scraper Failed" message to the chat. A notice that failed to send is not recorded, so the next run retries it.
- **A corrupt state file stops the run** instead of being treated as empty, which would resend everything.
- **Long titles are restored.** The listing truncates titles with "…", so the bot uses the full title from the notice page.

## Setup

### 1. Create the Telegram bot

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Send any message to your new bot (or add it to a group or channel).
3. Find your chat ID by opening `https://api.telegram.org/bot<TOKEN>/getUpdates` and reading `message.chat.id`.

### 2. Deploy on GitHub Actions

1. Fork or clone this repository.
2. Under **Settings → Secrets and variables → Actions**, add:

   | Secret               | Value                       |
   | -------------------- | --------------------------- |
   | `TELEGRAM_BOT_TOKEN` | the token from BotFather    |
   | `TELEGRAM_CHAT_ID`   | the chat ID from step 1.3   |

3. Under **Settings → Actions → General → Workflow permissions**, choose *Read and write permissions* so the workflow can commit the state file.
4. If you are starting fresh, delete `last_notices.json` so the first run records the current board silently. Then trigger **Hourly Notice Scraper** from the Actions tab, or wait for the next hour.

## Local development

Requires Python 3.11 or newer.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                       # run the tests
ruff check . && ruff format --check .

python -m notice_bot --dry-run              # print what would be sent; sends nothing, changes nothing
python -m notice_bot --dry-run --state-file empty.json   # with `[]` in empty.json: preview every notice on the board
```

To send for real from your machine, export `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` and run
`python -m notice_bot`. Use `-v` for debug logging.

## Project layout

```
notice_bot/
  cli.py        argument parsing and wiring
  app.py        one check cycle: fetch → diff → send → record
  parser.py     HTML → data (pure functions, no network)
  http.py       Cloudflare-aware session with retries
  telegram.py   message layout and the Telegram API client
  categories.py title → category tag (emoji + label)
  state.py      the "already sent" store (atomic JSON file)
  config.py     settings
tests/          pytest suite, with HTML fixtures modelled on the real pages
.github/workflows/
  schedule.yml  hourly run + state commit + failure alert
  ci.yml        lint, format check and tests on every push and PR
```

## Troubleshooting

| Symptom | Likely cause |
| ------- | ------------ |
| "No notices found … page structure may have changed" | aiub.edu changed its markup. Update `parse_notice_list` in `notice_bot/parser.py` and add a fixture. |
| Repeated fetch failures in Actions | Cloudflare is challenging the runner's IP. Re-running usually helps; otherwise see `notice_bot/http.py`. |
| "Telegram rejected the message: chat not found" | Wrong `TELEGRAM_CHAT_ID`, or you haven't messaged the bot yet. |
| The workflow can't push the state file | Enable read and write workflow permissions (setup step 2.3). |
