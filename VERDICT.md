# Verdict: chart/candlestick pattern strategy

**Decision: NO-GO. Stop the pattern strategy.** Date: 2026-11-05. Evidence from the research run of 2026-10-06.

None of 32 frozen-rule patterns beat a matched random-entry control on held-out data. The two that looked best on large caps (bull flag, cup and handle) went to zero or negative on a wider universe. Nothing here justifies more build time or any capital.

## Methodology

- **Universe:** 291 liquid US mid/small caps (price >= $10, 20-day avg dollar volume $20M-$300M), drawn at random with a fixed seed from 1,591 qualifying names, not picked by performance. 692,827 daily bars.
- **Eras:** development 2016-2022, held-out test 2023-2026. Nothing was tuned; the textbook parameters were frozen before the run.
- **Rules:** pivot-based detection with no lookahead (a pivot is usable only after its confirming bars). Signal on the close of the breakout bar, entry at the next open, stop at the far side of the pattern, target at the measured move (min 1R). 30-day time stop (10 days for candlesticks and islands). 2 bps slippage each side.
- **Control:** for every trade, 12 random dates from the same symbol and era, with the same stop and target distances in percent. The score is the trade's R minus the control's mean R. This strips out market drift and the payoff shape of the stop/target.
- **Pre-registered gate:** PASS only if the pattern beats the control in BOTH eras AND pooled z >= 2.5. The high bar is because 32 patterns were tested; by chance alone the best of 32 noise results lands near z = 2.
- **Code (branch `claude/elegant-cannon-pcffjk`):** `bull_brain/patterns.py`, `scripts/run_patterns.py`.

## Results (291 mid/small caps)

R = profit in units of initial risk. "vs ctl" = average R minus the random-date control, ± one standard error.

| Pattern | Side | Dev n | Dev avg R | Dev vs ctl | Test n | Test avg R | Test vs ctl | Pooled z | Gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| double_bottom | L | 347 | +0.08 | +0.02±0.06 | 238 | -0.01 | -0.11±0.08 | -0.73 | fail |
| double_top | S | 350 | -0.13 | -0.04±0.05 | 269 | -0.09 | -0.04±0.06 | -0.96 | fail |
| head_shoulders | S | 381 | -0.19 | -0.08±0.06 | 248 | -0.10 | +0.04±0.08 | -0.63 | fail |
| inverse_head_shoulders | L | 445 | +0.01 | -0.01±0.07 | 313 | +0.04 | -0.02±0.08 | -0.32 | fail |
| ascending_triangle | L | 177 | +0.08 | -0.02±0.10 | 82 | +0.09 | -0.00±0.22 | -0.15 | fail |
| descending_triangle | S | 77 | -0.06 | -0.11±0.16 | 60 | +0.11 | +0.23±0.17 | +0.36 | fail |
| bull_flag | L | 2659 | -0.00 | -0.05±0.03 | 2132 | +0.15 | +0.04±0.04 | -0.27 | fail |
| bear_flag | S | 1877 | -0.21 | +0.64±0.58 | 1525 | -0.17 | +0.10±0.14 | +1.21 | fail |
| **cup_handle** | L | 1129 | -0.11 | **-0.19±0.04** | 933 | +0.03 | **-0.08±0.05** | **-4.37** | fail |
| triple_bottom | L | 76 | +0.21 | +0.04±0.14 | 66 | -0.01 | -0.29±0.16 | -1.13 | fail |
| triple_top | S | 95 | -0.01 | -0.13±0.22 | 74 | -0.19 | -0.02±0.12 | -0.63 | fail |
| falling_wedge | L | 637 | +0.06 | +0.05±0.08 | 396 | +0.02 | -0.03±0.11 | +0.33 | fail |
| rising_wedge | S | 801 | -0.15 | -0.04±0.08 | 555 | -0.11 | +0.23±0.12 | +1.08 | fail |
| symmetrical_triangle_up | L | 187 | +0.32 | +0.30±0.15 | 90 | +0.24 | +0.07±0.21 | +1.85 | fail |
| symmetrical_triangle_down | S | 156 | -0.18 | +1.50±1.63 | 107 | +0.22 | +0.32±0.19 | +1.05 | fail |
| rectangle_up | L | 20 | +0.24 | +0.22±0.20 | 11 | +0.55 | +0.15±0.45 | +0.95 | fail |
| rectangle_down | S | 11 | -0.40 | -0.25±0.21 | 10 | -0.07 | +0.11±0.30 | -0.43 | fail |
| bull_pennant | L | 3536 | -0.05 | -0.10±0.02 | 2709 | +0.06 | -0.04±0.03 | -4.07 | fail |
| bear_pennant | S | 2411 | -0.22 | +0.72±0.40 | 1892 | -0.16 | -0.04±0.03 | +1.70 | fail |
| rounding_bottom | L | 1010 | -0.08 | -0.14±0.03 | 652 | +0.12 | +0.03±0.04 | -3.01 | fail |
| bullish_engulfing | L | 2749 | +0.03 | +0.04±0.03 | 2156 | +0.03 | +0.00±0.03 | +1.13 | fail |
| bearish_engulfing | S | 4009 | -0.03 | +0.03±0.03 | 2740 | -0.03 | +0.08±0.04 | +2.29 | fail (closest) |
| hammer | L | 3516 | +0.04 | +0.12±0.10 | 2284 | +0.11 | +0.12±0.09 | +1.67 | fail |
| shooting_star | S | 3671 | -0.07 | -0.03±0.04 | 2886 | -0.12 | -0.01±0.04 | -0.83 | fail |
| morning_star | L | 1153 | +0.08 | +0.08±0.06 | 693 | -0.01 | -0.03±0.05 | +1.00 | fail |
| evening_star | S | 1089 | +0.02 | +0.04±0.04 | 882 | -0.15 | -0.06±0.07 | -0.08 | fail |
| inverse_cup_handle | S | 945 | -0.10 | +1.10±0.77 | 646 | -0.16 | -0.05±0.05 | +1.38 | fail |
| rounding_top | S | 812 | -0.08 | -0.01±0.04 | 643 | -0.05 | +0.03±0.04 | +0.41 | fail |
| island_reversal_bullish | L | 545 | +0.06 | +0.03±0.05 | 268 | +0.07 | +0.01±0.07 | +0.50 | fail |
| island_reversal_bearish | S | 516 | -0.01 | +0.05±0.05 | 310 | -0.07 | +0.07±0.07 | +1.37 | fail |
| megaphone_up / megaphone_down | - | 0 | - | - | 0 | - | - | - | no signals |

**Passed: none.**

How to read it:
- **Cup and handle is worse than random** (-0.19R in dev, -0.08R in test, z -4.4). Bull pennant (z -4.1) and rounding bottom (z -3.0) are also reliably worse than random entries. The textbook long breakouts lose to a coin flip with the same stop and target.
- **Bull flag**, the large-cap "winner", is about zero against the control.
- **Bearish engulfing** is the only pattern positive in both eras, at z +2.29, under the gate. With 32 tries, a best result near z 2 is what pure noise produces. Its edge would be about +0.05R per trade before short-borrow costs, which would eat most of it.
- Several short-side controls have huge error bars (for example +1.50±1.63). A few outlier control draws dominate them; they are noise, not signal.

## Same result everywhere else

Every other approach tested in this repo also failed a held-out + random-control gate: opening-range breakout and fade, gap go/fade, crypto session breakouts, the event-day signal (volume >= 2.5x and |move| >= 4%; the large-cap short did not replicate on the 291 names), and 12 news-headline categories on 20 large caps. Price-only and public-headline signals on liquid US stocks look priced in.

## Caveats

Survivorship bias (symbols listed today), daily bars only, 2 bps slippage and no commissions, no short-borrow cost. These biases mostly flatter the strategy. Fixing them would not turn a null result into an edge.

## Recommendation

1. **Stop.** Keep the daily paper routines disabled. Do not put money behind any pattern, and do not build more detectors or tune parameters. Tuning after seeing these numbers is how a fake edge gets made.
2. **Opportunity cost.** The hours this would take are worth more in Harbinger, or in the one active side project (Poly Copy Trader paper run).
3. **The one follow-up worth testing, optional and capped.** Run bearish engulfing (short) with zero code or parameter changes on a fresh universe: 300 new random liquid mid/small caps that exclude the 291 above, same eras, same gate.
   - **Cost:** one session of about an hour, no capital, no new code.
   - **Kill criterion:** if it does not beat the control in both eras with pooled z >= 2.5, **or** if the edge is under +0.10R after a 1%/year borrow-cost haircut, the pattern research is closed for good and this file is final.
   - **Deadline:** 2026-11-30. If it is not run by then, treat the answer as "stop".
   - **Even a pass** earns only a paper forward test, never live money.

Not on the list: defined-risk options income (put spreads, iron condors). It is a separate project with a new data source and new payoff math, not a follow-up to this one. It should go through the same replacement / outcome / deadline check before any time goes into it.
