# Mortgage Servicing Live Agent Team

A voice agent for Canadian mortgage servicing calls. Homeowners can change their payment amount, date or frequency, make a lump-sum prepayment, request a payout, or ask about property tax, insurance and their account. The agent talks and listens, verifies the caller, reads documents held up to the camera, and draws a before-and-after payment chart while the conversation continues. It's built with Gemini 3.8 Live, with an optional live avatar.

A background agent team extracts the request, applies servicing rules (prepayment allowances, payment-increase limits, arrears, maturity) plus security and hardship checks, and prepares a downloadable request packet for a servicing representative.

Adapted from [insurance_claim_live_agent_team](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team) in [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps) by Shubham Saboo, licensed under Apache 2.0. The upstream commit is in `.upstream-commit`.

## Features

* **Live conversation:** speak or type, with live transcripts.
* **Identity first:** no account details are shared until the name, mortgage number and property postal code match.
* **Payment scenario chart:** current vs. proposed balance over time, the new payment, years saved and interest saved. Calculated in code with Canadian semi-annual compounding for fixed rates, never by an image model.
* **Document capture:** void cheques, insurance pages, tax bills and lender letters, with long numbers redacted to the last four digits.
* **Servicing notebook:** request details, account summary, rule notes, and open questions.
* **Routing:** ready to process, needs info, specialist review, hardship support, or security review.
* **Packet download:** ZIP with the Markdown request, a document manifest, captured images and the payment scenario.

## Setup

Requires Python 3.12 and a Google API key with access to the configured models.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `GOOGLE_API_KEY` in `.env`, then start the app:

```bash
python -m uvicorn live_demo.server:app --reload --host 127.0.0.1 --port 4177
```

Open [localhost:4177](http://127.0.0.1:4177/).

Call data is kept in memory only. Nothing is sent to a lender and no mortgage is changed.

## Try a call

With the camera off, say or type:

> Hi, I'm Maya Singh, mortgage MTG-40117, postal code M4C 1B5. I'd like to switch to accelerated bi-weekly payments starting November 1st.

The agent verifies the account, notes the change, and pins a chart showing roughly 3 years and $37,000 of interest saved.

### Mock mortgages

| Mortgage | Borrowers | Postal code | Product | Try |
| --- | --- | --- | --- | --- |
| MTG-40117 | Maya Singh, Arjun Singh | M4C 1B5 | 5-yr fixed 4.79%, monthly | Frequency change, prepayment, skip a payment |
| MTG-52290 | Jordan Lee | T2N 1N4 | 5-yr variable 4.05%, bi-weekly | $25,000 prepayment within allowance |
| MTG-61845 | Priya Shah | V5K 0A1 | 3-yr fixed 5.29%, monthly | $20,000 prepayment over allowance → specialist |
| MTG-70032 | Sam Rivera | B3H 4R2 | 5-yr fixed 5.49%, in arrears | Hardship support |
| MTG-88410 | Alex Chen | K1N 6N5 | 5-yr fixed 4.59%, contact changed 3 days ago | New bank account + prepayment → security review |
| MTG-55408 | Joe Smith | L5B 3C2 | 5-yr fixed 4.39%, home insurance expires 2026-09-30 | Show the renewed insurance on camera + $15,000 prepayment |
| MTG-93006 | Chris Park, Dana Park | H2X 1Y4 | 5-yr fixed 2.39%, matures 2026-12-01 | Payout for a sale, renewal note |

When a mortgage's home insurance expires within 30 days, an updated declaration page becomes a required document. Hold it up to the camera: the capture reads the policy expiry date, the agent reads it back, and the date goes into the notes and request packet.

A printable sample page for this call is in [`test_documents/joe-smith-insurance-declaration.pdf`](test_documents/joe-smith-insurance-declaration.pdf) (fictional, marked as a sample).

All people, addresses and accounts are fictional. `examples.py` has a typed prompt for each.

## Routing

Routes are decided in this order. The first one that applies wins.

| Route | When |
| --- | --- |
| Security review | Name or postal code doesn't match, the caller isn't a borrower, a bank-account change comes with money movement, contact details changed in the last 30 days before a money movement, or the caller mentions a suspicious message |
| Hardship support | Job loss, illness, bereavement, separation, missed payments, or a legal notice (urgent) |
| Specialist review | Prepayment above the annual allowance, payment increase above the allowance or any decrease, no skip-a-payment on the product, rate/term changes, life events |
| Needs info | Required details or documents are missing |
| Ready to process | Everything is captured and within the rules |

## Architecture

| Component | Responsibility |
| --- | --- |
| `agent.py` | ADK graph: extraction, classification, validation, rules, gate, packet |
| `servicing_rules.py` | Servicing rules, documents, security and hardship gates, packet Markdown |
| `schemas.py` | Structured data contracts |
| `mortgage_directory.py` | Mock mortgage records and identity verification |
| `payment_math.py` | Canadian mortgage payment and amortization math |
| `live_demo/live_tools.py` | Live agent prompt and tool declarations |
| `live_demo/server.py` | Sessions, WebSocket transport, tools, downloads |
| `live_demo/app.js` | Conversation, camera input, notebook and chart rendering |

* `gemini-3.8-live` handles the conversation, camera input and optional avatar.
* `gemini-3.8-flash` extracts the request and captions captured documents.
* Background tools: `lookup_mortgage`, `sync_service_request`, `pin_document_photo`, `show_payment_scenario`.

To change the rules, edit `servicing_rules.py`. To change what the agent says, edit `SYSTEM_INSTRUCTION` in `live_demo/live_tools.py`. To add accounts, add entries to `_RAW_RECORDS` in `mortgage_directory.py`.

## Configuration

Restart the server after changing `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MORTGAGE_GEMINI_LIVE_MODEL` | `gemini-3.8-live` | Live conversation and avatar |
| `MORTGAGE_VOICE` | `Kore` | Voice-only responses |
| `MORTGAGE_REQUEST_MODEL` | `gemini-3.8-flash` | Background request team and document captions; switch to `gemini-flash-latest` if you see 503 "high demand" errors |
| `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | `False`, empty, empty | Set to `True`, your project ID and `global` to run the request team and document captions on a Google Cloud project (billed per use) instead of the AI Studio key's free daily limit. Needs `gcloud auth application-default login`. |
| `MORTGAGE_TRANSCRIPTION_VOCABULARY` | Empty | Comma-separated speech-recognition hints |
| `MORTGAGE_AVATAR_NAME` | Empty | Prebuilt avatar; empty disables it unless an image is set |
| `MORTGAGE_AVATAR_PROJECT` | Empty | Avatar Cloud project |
| `MORTGAGE_AVATAR_LOCATION` | `us-central1` | Avatar API region |
| `MORTGAGE_AVATAR_VOICE` | `Kore` | Avatar voice |
| `MORTGAGE_AVATAR_IMAGE` | Empty | Custom portrait path (PNG under 5 MB, at least 704 × 1280) |

The optional avatar needs `gcloud auth application-default login` and a Cloud project with Gemini Live access.

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py'
node tests/client-regressions.cjs
node tests/avatar-player.cjs
```

The JavaScript tests need Node.js. Model calls and devices are mocked; check live microphone and camera behaviour separately.

## License

Apache License 2.0; see [LICENSE](LICENSE). This project modifies the upstream insurance claim demo: the domain was changed to Canadian mortgage servicing (data models, rules, mock data, prompts, tools), a payment scenario calculator and chart replaced incident sketches, and the UI was redesigned. All people, accounts, and documents in it are fictional.
