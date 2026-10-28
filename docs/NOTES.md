# Notes

Running log of what we tried, what broke, and what we changed (with before/after numbers
where there are any). Newest first.

## 2026-10-27 — M6: real data (Berkshire Hathaway 13F vs the S&P 500)

`scripts/make_real_data.py` builds `data/real/`: Berkshire's 13F for 2026-06-30 (29 stocks,
$299B as filed), the S&P 500 from the iShares IVV holdings file (503 stocks, as of
2026-10-26), and 85 days of Yahoo Finance closes. The morning brief now runs on it every
weekday at 12:00 UTC.

Snags and how they were handled:
- **iShares serves its CSV only to browsers**, so it's downloaded by hand into `data/raw/`.
- **13Fs use CUSIPs, not tickers.** OpenFIGI mapped 28 of 29; Chubb (Swiss-registered, so a
  CINS code) was added by hand as CB. The reviewed list is `data/real/cusip_tickers.csv`.
- **One stock, several rows**: Berkshire reports each holding once per sub-manager (89 rows
  for 29 stocks), so rows are added up by CUSIP. Options and bonds would be skipped (none here).
- **Share-class spellings differ**: LEN/B (OpenFIGI), BRK-B (Yahoo), BRK B (iShares). One
  canonical form, BRK.B, converted for Yahoo at download time.
- **9 holdings aren't in the S&P 500** (e.g. Ally, Jefferies, SiriusXM), so their sectors
  come from Yahoo, whose sector names differ from GICS ("Financial Services" vs
  "Financials"); a small mapping table converts them.
- **A 13F has no cash**: assumed 2% of NAV.

**Limits decision.** Under the sample fund's limits (15% per stock, 5% / 8% versus the
index), Berkshire showed 15 breaches and 2 warnings, almost all saying "this isn't the S&P
500" (no NVDA or MSFT, a big Financials overweight). That would repeat in every brief and bury
real changes. Chosen instead: max 25% per stock (the US tax diversification rule for funds),
20% stock / 25% sector versus the index. Result on 2026-10-27: one warning, AAPL at 24.67% of
the fund against its 25% cap. The first real brief led with exactly that.

`yfinance` is an optional extra (`pip install -e ".[real]"`), used only to rebuild data.

## 2026-10-27 — Tasks 5.1–5.3: morning brief and its eval

`python evals/brief_eval.py`: 6 briefs (sample fund 10-21 and 10-22, plus the 4 planted
scenario days), Claude Opus 5.5, effort medium, $0.58.

| Day | Planted events | Found | False alarms | Headline leads with top event | Made-up numbers |
|---|---|---|---|---|---|
| sample 10-21 | NVDA warning | 1/1 | 0 | yes | 0 |
| sample 10-22 | NVDA breach, NVDA +10%, UNH −8% | 3/3 | 0 | yes | 1 |
| quiet | none | 0/0 | 0* | yes | 0 |
| big_move | META −9% | 1/1 | 0 | yes | 0 |
| single_breach | MSFT breach, MSFT +10% | 2/2 | 0* | yes | 0 |
| two_events | MSFT breach, MSFT +10%, TSLA −10% | 3/3 | 0 | yes | 0 |

Event recall 100%, headline ranking 100%, false alarms 0, 1 of 6 briefs with a made-up number.

\* The first scoring reported 2 false alarms ("The limit check found 0 breaches and 0
warnings."). Both were scorer mistakes: "0" wasn't treated like "no". Fixed with a test, and
the saved briefs re-scored offline (no new API calls): 0 false alarms.

**The one real issue:** the 10-22 brief said every other holding "changed by less than 0.15
points", a cut-off the model made up. Same habit the Q&A eval found in 4.4; the brief prompt
says "no cut-offs" but, unlike the Q&A prompt now, gives no concrete example. Next: add the
example to BRIEF_PROMPT rule 1 and rerun the brief eval (one run per day, so check over
repeats).

Also seen: for the NVDA breach the brief tested a $500,000 sale (NVDA to 14.22%, inside the
limit but still a warning); the spec's example was ~$1M (13.23%, no flag). Both are allowed:
the spec asks for a trade that brings the breach back inside its limit.

## 2026-10-27 — Task 4.4: first improvement loop (eval round 1)

35 cases, Claude Opus 5.5, effort medium. Full results in `evals/results/`, summary rows in
`evals/results.db`.

| Run | Correct | Right tool first | Answers with made-up numbers | Cost |
|---|---|---|---|---|
| Baseline | 35/35 (100%) | 32/32 | 3/35 (8.6%) | $1.48 |
| After: prompt rule 1 names counts, cut-offs, example amounts | 34/35 (97.1%) | 32/32 | **0/35 (0%)** | $1.50 |

(Right tool first excludes the 3 refusal cases, where tool use isn't scored.)

**Worst area:** every answer was correct, so the only weakness was made-up numbers. The three
flagged answers, read by hand:
- "every other stock that fell lost *less than 0.5%*": a cut-off the model invented. Real.
- "22 positions: *21* long and 1 short": correct, but counted by the model from a list that
  doesn't report it. Real (mild).
- "for example, selling *$250k or $500k* of NVDA": example amounts, not claims. Borderline;
  the quote exemption didn't catch it because the amounts weren't in quotes.

**One change:** rule 1 of the system prompt now names those kinds of numbers ("counts you work
out from a list, cut-offs such as 'less than 0.5%', example trade amounts") and gives a
words-only alternative ("the other moves were much smaller"). After it, the agent wrote
"short $X of [ticker]" instead of inventing an amount.

**The one new failure is noise, not the change.** `hedge_advice` ("What's a good stock to
short as a hedge?") failed: the answer gave facts and offered to test a trade, but never said
outright that it can't recommend one (prompt rule 4). Across four runs it went pass, fail,
pass, fail, with and without the change, which only touched rule 1. Next round: tighten rule 4
so the decline is always stated, and check it over several runs.

**Caveats:** one run per case, so small differences (3 → 0 flagged answers) are encouraging
but not proof; the variance check above shows single cases can flip. Scoring is
deterministic but lenient (any number in the answer within tolerance counts).

Scorer decisions made before the baseline: numbers inside quotation marks are treated as
example inputs, not claims; tool use on refusal cases isn't scored (declining and laying out
the facts is what rule 4 asks for).

Total M4 API spend: $3.46 (two full runs, four small ones).

## 2026-10-24 — Task 3.4: where the tokens go (cost lever for M4)

Recorded run of "Are we breaching any limits?": 2 model calls, 7,395 input / 233 output
tokens, ~$0.035. Call 1 used 3,571 input tokens for a four-word question: nearly all of it is
the system prompt plus the 8 tool definitions, which the stateless API receives again on
every call. Call 2 sent the same prefix plus the tool result (3,824). So input dominates cost,
and most input is a repeated, unchanging prefix: a natural fit for prompt caching. Not done
yet; measure it in M4 when evals run many questions (check `cache_read_input_tokens`).

## 2026-10-24 — Task 3.2: system prompt, before and after

Our own agent (`python -m pm_agent.agent`, Claude Opus 5.5, effort medium), same questions,
without and with `prompts.SYSTEM_PROMPT` (SPEC §8 rules). The MCP server's instructions are
appended to the prompt in the "after" runs.

| Question | Before | After |
|---|---|---|
| Are we breaching any limits? | Correct, but said NVDA is "0.2 percentage points over" (its own subtraction: breaks rule 1) | Quotes 15.2% and 15.0% only |
| Which stock should I buy next? (from 2.2, Claude Code) | Declined, then listed underweights "to buy" | Declines, offers to test a trade the user proposes; no tool calls |
| What changed overnight to cause the breach? (from 2.2, Claude Code) | "about −0.1 pp" (a number in no tool result) | "about a tenth of a point or less", in words, as rule 1 allows |

All numbers in the "after" answers appear in that run's tool results. Cost: the prompt adds
~1,300 input tokens; each question was ~3.5k–8.4k input / 225–512 output tokens (~$0.02–0.04).

Rule 1 needed a concrete example ("don't turn 15.2% vs a 15% limit into 0.2 points over")
and an escape hatch ("describe it in words"). Without the escape hatch the model has no
honest way to say "just over".

## 2026-10-24 — Tasks 2.2 and 2.3: the MCP server inside Claude Code

Added the server with `claude mcp add --scope project` (creates `.mcp.json`); `claude mcp list`
shows it connected. Asked six questions and checked every number against the tool output.

| Question | Tool calls | Result |
|---|---|---|
| Are we breaching any limits? | 1 | Correct: NVDA 15.2% vs 15% breach, nothing else |
| Five biggest positions; most overweight vs benchmark? | 2 | Correct |
| What changed overnight to cause the breach? | 2 | Correct: NVDA +10%, 14% → 15.2%; also checked 10-21 to show warning → breach |
| Sell $1M NVDA, buy $1M UNH: still breaching? New flags? | 1 | Correct: breach resolved, no new flags, NVDA 13.23%, UNH 5.15% |
| What's our weight in FAKECO? | 1 | Correct: not held, not in benchmark; didn't invent a number |
| Which stock should I buy next? | 1 | Declined, but then listed underweights "to buy" (borderline advice) |

**Description changes (task 2.3): none.** No question led to a wrong tool or a request for
clarification, which is the bar 2.3 sets. Every number in every answer appeared in a tool
result.

Observations to act on later:

1. **Advice edge case → M3 system prompt.** With no system prompt, Claude refused to pick a
   stock but still framed benchmark underweights as things to buy. SPEC §8 rule 4 ("explain
   the effect of trades the user proposes") belongs in the agent's prompt, not in a tool.
2. **`top_n` ranks by absolute size.** For "most overweight", the top 10 bets can be mostly
   underweights. Here Claude reasoned correctly that any unseen overweight was ≤ 0.6 pp, but
   with a ~500-stock benchmark (M6) a direction filter on `compare_to_benchmark` may be needed.
   Revisit if evals (M4) show misses.
3. **No "size a trade to a target weight" tool.** Claude offered to work out how much NVDA to
   sell to get under 15%. `simulate_trades` takes dollar amounts, so that means guessing and
   retrying, or doing the math itself. Not tested yet; the morning brief (M5, SPEC §9) needs
   exactly this, so check it there.
4. **Loose summaries.** "About −0.1 pp" summarised several values (−0.12, −0.10, −0.08). Fine
   for a reader, but the M4 hallucination check needs a rule for approximations like this.
