# Notes

Running log of what we tried, what broke, and what we changed (with before/after numbers
where there are any). Newest first.

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
