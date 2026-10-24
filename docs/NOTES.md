# Notes

Running log of what we tried, what broke, and what we changed (with before/after numbers
where there are any). Newest first.

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
