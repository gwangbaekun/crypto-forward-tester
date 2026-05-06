# AI & DEVELOPER GUIDE: How to Create or Update Strategies

**PLEASE READ THIS ENTIRE DOCUMENT BEFORE MODIFYING OR CREATING ANY STRATEGIES.**

This project employs a **Twin Architecture** where historical backtesting (`btc_backtest`) and live real-time trading (`btc_forwardtest`) share the exact same logical foundation but execute in fundamentally different ways. 

To maintain 100% compatibility, the file structures in the strategy folders (e.g., `cvd_explosion`, `atr_breakout`) are deliberately kept identical.

Here is the exact rulebook for copying, pasting, and modifying strategy files between the backtest and forwardtest environments:

## 1. `signal.py` (The Core Logic)
- **Status:** 🟢 **100% Identical (Copy & Paste is Safe)**
- **Rule:** The `compute_signal()` function must be exactly the same in both environments. It receives a unified data contract (`sweep_by_tf` and `magnets`) so it doesn't care if the data came from a PostgreSQL database or a live Binance WebSocket.
- **Action:** If you improve the scoring or entry logic in `btc_backtest/.../signal.py`, you MUST copy-paste it directly to `btc_forwardtest/.../signal.py`.

## 2. `exit_check.py` (TP / SL Resolution)
- **Status:** 🟡 **Similar Logic, Different Parameters (DO NOT Copy & Paste directly)**
- **Rule:** 
  - **Backtest (`btc_backtest`)**: Historical candles hide intra-bar price trajectories. Did the price hit Take Profit (TP) or Stop Loss (SL) first within that 1-hour candle? To simulate this, the backtest `check_exit()` takes `bar_high` and `bar_low` to resolve intra-bar conflicts conservatively.
  - **Forward Test (`btc_forwardtest`)**: Live environments process tick-by-tick data in real-time. The forwardtest `check_exit()` accepts `bar_high`/`bar_low` (for the **bar-close tick** path → backtest-faithful) AND an extra `intrabar: bool = False` flag.
- **Action:** If you update how TP/SL advances (e.g., changing the ratchet logic), you must apply the conceptual changes to both files, but **keep their respective method signatures intact**.

### 2-a. ⚠️ Intrabar Ratchet Rule (Forward Test ONLY)
**Critical:** Backtest only ratchets at bar close (only OHLCV available). Live exit ticks fire every 1s with the WS price, so naïvely passing `bar_high = bar_low = ws_price` makes ratchet fire on a single price spike, then any oscillation triggers the ratcheted SL → spurious closes.

The fix: live exit ticks (`strategy_loop` fast tick when `has_open_pos=True`) pass `state["intrabar"] = True`. In that mode, `check_exit`:
1. Skips the entire ratchet / TP advance loop.
2. Only checks the **currently-held SL** against `current_price` directly.
3. Defers TP touches to the next bar-close tick (where `bar_high` is the bar's actual high → ratchet runs as in backtest).
4. Engines also skip the **entry** branch when `intrabar=True` (entry only happens on bar-close / pre-entry ticks).

When you add a new strategy with ratchet logic (`magnet_rr` / `magnet_tp_rr`), `check_exit` MUST accept `intrabar` and short-circuit ratchet when `intrabar=True`. The engine MUST read `state.get("intrabar", False)` and forward it.

## 3. `engine.py` (The Execution Runner)
- **Status:** 🔴 **Completely Different Execution Models (NEVER Copy & Paste)**
- **Rule:**
  - **Backtest (`btc_backtest`)**: Contains a `run()` function. It sweeps through years of historical data in a massive `for` loop, injecting pre-compiled Liquidation Map `.pkl` caches at every step.
  - **Forward Test (`btc_forwardtest`)**: Contains a class extending `BaseForwardTest`. It runs a `tick()` function every X seconds, maintaining the `self._position` state in memory, and executing real Binance orders / sending Telegram alerts.
- **Action:** These files share the same name (`engine.py`) to align their *roles* as runners, but their code is entirely specific to their environment.

## 4. `config.yaml` & `config_loader.py`
- **Status:** 🟢 **Nearly Identical**
- **Rule:** Contains signal thresholds, window sizes, and TP/SL modes. 
- **Action:** Keep the parameter names identical so the Backtest Dashboard's `SAVE & APPLY` button can seamlessly sync changes to the live Forward Test server.

---

### Summary Checklist for AIs
When the user says "I updated the CVD strategy in backtest, apply it to forward test":
1. **Copy** `signal.py` over exactly.
2. **Carefully transplant** any TP/SL logic changes into `exit_check.py`, respecting the `bar_high`/`bar_low` vs `current_price` difference.
3. **Check** if any new parameters were added to `config.yaml` and update both files.
4. **Leave** `engine.py` alone unless the strategy requires a new state tracking mechanism in the live environment.
