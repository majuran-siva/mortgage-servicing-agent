# Building Kira: a live AI mortgage servicing agent you can video call

*How I turned an open-source insurance demo into a voice and video agent for Canadian mortgage servicing: the process, the architecture, the test cases, what broke, and where this could go next.*

---

## TL;DR

I built a working prototype of an AI mortgage servicing agent called **Kira**. A borrower can start an audio or video call with her, verify their identity, change how they pay, make a prepayment, and show a renewed home-insurance policy on camera (or upload it as a PDF). While the conversation happens, a background team of AI agents and deterministic rules writes up the request, checks it against lender-style servicing rules, flags fraud or hardship for a human, and produces a ready-to-process packet for a servicing representative.

Nothing is approved automatically. The agent prepares the work and a person confirms it. All the data is fictional.

- **Live demo:** a 45-second video call (embedded below)
- **Code:** [github.com/majuran-siva/mortgage-servicing-agent](https://github.com/majuran-siva/mortgage-servicing-agent)
- **Stack:** Gemini Live (voice plus a live video avatar), Google ADK, Python/FastAPI, vanilla JS
- **Tests:** 97 automated tests, plus end-to-end checks against the real models

---

## Why mortgage servicing?

Most calls to a mortgage servicer aren't complex. They're a handful of routine requests:

- "Can I move my payment date to line up with payday?"
- "Can I switch to accelerated bi-weekly?"
- "I got a bonus. Can I put $15,000 down without a penalty?"
- "My home insurance renewed. Here's the new policy."
- "I'm selling. Can I get a payout statement?"

Each one usually means hold time, a representative looking up the account, manual notes, a follow-up email asking for a document, and a second person checking the rules. The information needed is almost always available during the first call. It just isn't captured in a structured way.

I wanted to see whether a real-time agent could handle the *first contact* properly: understand the request, verify the caller, collect every missing detail and document, check the rules, and hand over a complete package, all while staying warm and conversational.

---

## Where I started

I didn't start from scratch. Shubham Saboo's [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps) repository includes an **Insurance Claim Live Agent Team**: a voice agent that takes first notice of loss for an insurance claim, looks at damage through your camera, sketches the incident, and writes up a claim packet for an adjuster.

That project had exactly the architecture I wanted to explore:

1. **A live agent** you talk to, which calls tools in the background without pausing the conversation.
2. **A background agent team** (Google ADK) that extracts structured facts from the transcript.
3. **Deterministic rules** that decide routing, so the language model never makes the final call.

So the question became: what does it take to move a design like this into a different, more regulated domain?

---

## How I went about it

### 1. Understand before changing anything

I read the whole codebase first, about 5,000 lines across the agent graph, the rules engine, the FastAPI server, the live-session transport and the browser UI. I wanted to know which parts were domain-specific (claim types, adjuster routing, sketches) and which were reusable infrastructure (the live transport, tool scheduling, session handling, evidence capture).

### 2. Map the domain: why do people actually call?

Before writing code, I brainstormed every reason a homeowner calls their lender and grouped them by what happens next:

| Group | Examples | Typical outcome |
|---|---|---|
| Change how they pay | Amount, date, frequency, bank account, skip a payment | Usually self-serve within the rules |
| Pay it down or off | Lump-sum prepayment, prepayment charge quote, payout statement | Rules-based; specialist if over the allowance |
| Rate and term | Renewal, early renewal, blend-and-extend, porting, refinance | Specialist |
| Life events | Add or remove a borrower, separation, death of a borrower | Specialist, documents needed |
| Financial hardship | Job loss, illness, missed payments, legal notice | Dedicated hardship team |
| Tax, insurance, info | Property tax, proof of insurance, statements | Mostly answerable directly |
| Security red flags | Caller isn't a borrower, bank change plus urgent payout | Security review |

That table became the scope. **Version 1** covered payment changes, prepayments and payouts, and tax and insurance, plus the security and hardship gates, because those are the most common calls and they exercise every routing outcome.

### 3. Get the domain right: Canadian mortgages have their own rules

I chose Canada, which meant getting the details right:

- **Semi-annual compounding** for fixed-rate mortgages (required by the Interest Act), monthly for variable.
- **Annual prepayment allowances** (for example 15% of the original principal as a lump sum), with a prepayment charge on anything above it.
- **Payment-increase allowances**, and **accelerated bi-weekly** payments (half the monthly payment every two weeks, which adds up to one extra monthly payment a year).
- **Interest adjustments** when the payment date or frequency changes.

I checked the payment math against a known value: a $400,000 mortgage at 5% over 25 years is **$2,326.42 a month**. Switching to accelerated bi-weekly pays it off in about 21.5 years and saves about $48,000 in interest. Those numbers became a unit test.

### 4. Design the system

```
 Borrower (browser: mic, webcam, uploads)
        │  WebSocket
        ▼
 ┌─────────────────────────────┐        ┌───────────────────────────────┐
 │  Live agent "Kira"          │ tools  │  Background request team       │
 │  Gemini Live + live avatar  │───────▶│  1. Extract + classify (LLM)   │
 │  talks, listens, sees       │        │  2. Validate fields (rules)    │
 └─────────────────────────────┘        │  3. Servicing rules (rules)    │
        │  lookup_mortgage              │  4. Document checklist (rules) │
        │  show_payment_scenario        │  5. Security + hardship gate   │
        │  pin_document_photo           │  6. Request packet             │
        ▼                               └───────────────────────────────┘
 Mock mortgage directory · Payment math · Document reader (vision)
```

Four tools connect the live conversation to the back office:

- **`lookup_mortgage`**: identity check against the mock servicing system. No balance, rate or payment amount is shared until the name, mortgage number and property postal code all match.
- **`sync_service_request`**: sends the conversation to the background team and returns the checklist of what's still missing.
- **`pin_document_photo`**: captures the current camera frame. A *separate* model call reads it, classifies the document, extracts an insurance expiry date, and redacts long account numbers to the last four digits.
- **`show_payment_scenario`**: draws a before-and-after chart of the balance over time, with the new payment, years saved and interest saved.

### 5. Design principles I held to

- **Identity first.** Nothing about the account is shared before verification, and the agent never hints at which detail was wrong.
- **Rules decide, the model assists.** Language models extract and converse; deterministic code decides routing. A prepayment is "within allowance" because arithmetic says so, not because a model thinks so.
- **Humans approve.** Every packet says nothing has changed on the mortgage. Specialist, hardship and security routes all go to people.
- **Security beats everything.** A bank-account change combined with an urgent prepayment, or contact details changed three days ago plus a money movement, goes to security review even if the rest of the request looks routine.
- **Calculated, not generated.** The original project used an image model to sketch incidents. For payment scenarios I replaced that with code, because an image model can make up numbers and a borrower hearing the wrong interest savings is a real problem. Every chart is labelled as an estimate.
- **Privacy by default.** Long numbers in document captions are redacted, the agent never asks for government ID or bank cards, and all test data is fictional.

---

## Testing: what I tested and how

### Mock mortgages as test scenarios

I created fictional mortgages that each exercise a specific path:

| Mortgage | Scenario | Expected outcome |
|---|---|---|
| **MTG-40117** (Maya Singh, Toronto) | Switch to accelerated bi-weekly | Ready to process; chart shows about $36.9k interest saved |
| **MTG-52290** (Jordan Lee, Calgary) | $25,000 prepayment | Within allowance, no charge |
| **MTG-61845** (Priya Shah, Vancouver) | $20,000 prepayment when most of the allowance is used | Specialist review, with an estimated charge |
| **MTG-70032** (Sam Rivera, Halifax) | Lost job, two missed payments | Hardship support |
| **MTG-88410** (Alex Chen, Ottawa) | Email asked them to move payments to a new account and prepay | Security review |
| **MTG-93006** (Chris Park, Montréal) | Selling the house, needs a payout | Needs documents (signed request, purchase agreement); renewal note |
| **MTG-55408** (Joe Smith, Mississauga) | Home insurance expiring; shows renewed policy on camera plus $15,000 prepayment | Insurance page required until captured, then ready to process |

For the Joe Smith scenario I also made a printable, clearly watermarked **sample insurance declaration page**, so I could test the camera and upload flows with a real piece of paper.

### Automated tests (97)

The test suite mocks the models and devices so it runs in a fraction of a second:

- **Payment math:** known-value checks, accelerated bi-weekly payoff, payments that never cover the interest, prepayments larger than the balance.
- **Identity:** details are hidden until verified, co-borrowers can verify, postal codes are never exposed, and a later correction to a different mortgage is never overwritten.
- **Servicing rules:** allowances, payment increases and decreases, skip-a-payment eligibility, arrears, renewal windows, insurance expiring within 30 days.
- **Security and hardship gates:** third-party callers, bank change plus money movement, recent contact changes, suspicious messages, legal notices marked urgent, and security always winning over hardship.
- **Documents:** the model can't mark a document as "received" on its own; a capture only satisfies its own document type; expiry dates are only read from insurance documents; uploads are checked by file type and signature (a PDF renamed to `.png` is rejected).
- **Live transport:** transcript ordering, interruptions, the spoken greeting streaming into the existing greeting turn, and slow background work never blocking audio or video.
- **Failure handling:** quota and "service unavailable" errors are recognized and turned into clear messages.

### Testing against the real models

Mocks can't tell you whether the real model behaves, so I also ran end-to-end checks:

- Streamed a synthesized "Hello, can you hear me?" (macOS text-to-speech, converted to 16 kHz PCM) into the live avatar session to confirm spoken input works without a microphone.
- Rendered the sample insurance PDF and ran it through the real document reader. It correctly identified a home insurance declaration page and extracted the expiry date **2027-09-30**.
- Ran full typed calls through the browser and checked the notebook, checklist, chart and routing badge.

---

## What broke (and what I learned)

Real systems fail in ways unit tests don't show. The most useful part of the project was finding and fixing these:

1. **A schema the model couldn't accept.** I'd used a "greater than zero" constraint on amount fields. Gemini's structured-output format doesn't support it, so every background update was rejected. The tests passed because they mocked the model. *Fix:* use a supported constraint, and add a test that runs the SDK's real schema conversion so this class of bug can't come back.

2. **Free-tier limits.** The background writer hit the free tier's 20-requests-a-day limit within about ten caller turns, and the checklist silently stopped updating. *Fixes:* merged extraction and classification into **one** model call (halving usage), showed a clear "daily limit reached" message once instead of a red error every turn, and eventually moved the background work onto a Google Cloud project with billing.

3. **The checklist depended entirely on the background writer.** When the writer was paused, the agent had no checklist, so it stopped asking for a contact number and wrapped up the call. *Fix:* identity details verified by `lookup_mortgage` and facts the live agent passes along now fill the checklist even when the writer is down, and the agent has a standing rule to collect a contact and a clear request before ending.

4. **"Service unavailable" mid-call.** Twice, Google closed the live avatar session (`1011`), and the call went silent. *Fix:* detect it and tell the borrower plainly that the service dropped, their notes are kept, and how to reconnect.

5. **The greeting was never spoken.** It was only written into the transcript, so after clicking Talk there was silence. *Fix:* on a new call, Kira speaks the greeting, streamed into the same transcript line so it doesn't appear twice.

6. **Regional models.** Text models were available in Google Cloud's `global` region but the live model only in `us-central1`, so voice-only fallback calls would have failed. *Fix:* route live calls to the avatar project's region.

**Lesson:** tests that mock the model prove your logic, not your integration. You need both, and a few real end-to-end runs before calling anything done.

---

## Designing the experience

I iterated on the interface more than I expected. I tried design systems inspired by Revolut, Binance, Apple, Coinbase and Stripe (using [awesome-design-md](https://github.com/voltagent/awesome-design-md) as a reference), measured each one at laptop, desktop and phone sizes, and settled on a Stripe-like look: calm navy and indigo, thin display type, and a warm cream card for the payment chart.

The layout went through several rounds too:

- **Left:** the live call (Kira's video, call controls) and the transcript, staying in view while you scroll.
- **Right:** the call notes, the servicing team's activity, and the "still needed" checklist.
- **Call controls:** **Audio call** (your mic, Kira's video), **Video call** (FaceTime-style, with your webcam as a small corner tile), **Show document** (camera just for a document), **Upload document** (photo or PDF), and **End call**.

Small details mattered: a large webcam overlay covered Kira's face, and a long transcript pushed the Talk button off-screen. The column now sizes itself so everything fits on a laptop screen.

---

## How I built it

I built this with an AI coding assistant (Claude Code) as a pair programmer. I set the direction, made the product and domain decisions (which call types, which rules, where humans stay in control, how the layout should work) and tested it as a user. The assistant read the codebase, wrote and refactored code, ran tests, debugged the failures above from the server logs, and checked the UI at different screen sizes.

What made that work well:

- **Being specific about intent** ("Audio call means Kira's video but no webcam") and correcting course quickly.
- **Asking "why" when something broke** instead of accepting a workaround: the silent checklist turned out to be three separate problems.
- **Committing in small, described steps**, so every change is traceable in the Git history.

---

## What the future could look like

This is a prototype on fictional data. Turning it into something a lender could run would mean:

**Integrations**
- Connect to the real servicing core (balances, payment schedules, allowances) instead of mock records.
- Write approved changes back through the lender's existing workflow, still with human confirmation.
- Warm transfer to a live representative, with the packet already on their screen.

**Security and compliance**
- Stronger authentication: one-time passcodes or in-app authentication instead of name and postal code alone.
- Alignment with Canadian regulation and guidance (FCAC consumer protection, OSFI expectations for model risk, PIPEDA and provincial privacy law), including consent to record and retention rules.
- Audit logs, model monitoring, and a documented model-risk process.

**Better conversations**
- French and other languages (the agent can already switch).
- More request types: renewals, blend-and-extend quotes, porting, adding or removing a borrower.
- Proactive outreach: renewal reminders, insurance-expiry nudges, payment-shock warnings for variable-rate borrowers.

**Quality and operations**
- An evaluation harness: recorded conversations scored for correct routing, missed questions, tone and compliance phrases.
- Cost and latency dashboards per call.
- Accessibility: captions, keyboard control, and a text-only mode.

The bigger idea is **first-contact resolution with a human in the loop**: the borrower gets a fast, friendly, face-to-face experience at any hour, and the servicing team gets complete, rule-checked work instead of half-finished notes.

---

## Try it yourself

The code is open source under Apache 2.0: **[github.com/majuran-siva/mortgage-servicing-agent](https://github.com/majuran-siva/mortgage-servicing-agent)**. The README covers setup, the mock mortgages and the sample documents. You'll need a Google API key or Google Cloud project for the models, and a Cloud project for the live avatar.

*Credit: this project builds on Shubham Saboo's open-source Insurance Claim Live Agent Team in [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps). All people, accounts and documents in the demo are fictional.*

---

*If you work in lending, servicing or AI in financial services, I'd love to hear where you think agents like this help, and where they shouldn't be used.*
