# Pine Script Conventions (v6)

This repo's strategies are written for TradingView's Pine Script v6.

## Hard rules

1. **Never use multi-line chained ternaries.** Pine v6 produces
   `Syntax error at input 'end of line without line continuation'` even when
   continuation lines are properly indented. Always rewrite chained
   conditionals as `if / else if / else` blocks. Single-line ternaries are
   fine.

   Bad:
   ```
   x = a ? 1 :
       b ? 2 :
       c ? 3 : 0
   ```
   Good:
   ```
   x = 0
   if a
       x := 1
   else if b
       x := 2
   else if c
       x := 3
   ```

2. **No-repaint HTF data pattern.** Use
   `request.security(sym, HTF, expr[1], lookahead=barmerge.lookahead_on)`
   to pull the previously-closed HTF bar's value without lookahead bias.

3. **Persistent state.** Use `var` for state machines (e.g., the weekly
   fakeout latch). Reset state on a `ta.change(time("W"))` / `time("D")`
   boundary.

4. **Strategy declarations** must include `process_orders_on_close=true`
   for deterministic backtests, plus generous `max_labels_count`,
   `max_lines_count`, `max_boxes_count` so visuals don't get clipped on
   long histories.

5. **Use `else if` not nested `if`** for mutually exclusive label
   placement so we don't stack multiple labels on the same bar.
