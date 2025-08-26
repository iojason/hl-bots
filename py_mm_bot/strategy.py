# py_mm_bot/strategy.py
import time
import math
import datetime
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, List

from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext
import os

from .db import (
    insert_bot,
    insert_fill,
    insert_lifecycle,
    upsert_minute_metrics,
    insert_coin_config_version,
    get_current_coin_config_version,
    insert_book_snapshot,
    insert_autotune_event,
)

# ample precision; we clamp to <= 8 dp before sending to SDK
getcontext().prec = 28

# ---------- numeric helpers ----------

def d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))

def as_float_8dp(x: Decimal) -> float:
    q = Decimal("0.00000001")
    return float(x.quantize(q))

def quantize_down(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return x
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step

def quantize_up(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return x
    return (x / step).to_integral_value(rounding=ROUND_UP) * step

def round_up_units(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.ceil(x / step) * step

# ---------- state ----------

@dataclass
class CoinState:
    pos: float = 0.0
    avg_entry: float = 0.0


class MarketMaker:
    """
    Live maker strategy with pro behaviors:
    - Quotes at the touch (ALO), optionally improves by 1 tick when touch is stale
    - Quantizes price/size to tick & size steps (avoids float_to_wire rounding errors)
    - Min-spread gate + inventory skew
    - STRICT cancel/replace: max 1 resting bid + 1 resting ask per coin
    - Throttled replacement: only if price moves >= N ticks or after min_replace_ms
    - Housekeeping cancels any stray orders every N loops
    - Optional IOC flatten when exposure > cap * 1.2 and optional flatten_on_start
    """

    def __init__(self, db, client, cfg: Dict[str, Any], logger=print):
        self.db = db
        self.client = client
        self.cfg = cfg
        self.bot_id = cfg["bot_id"]
        self.wallet = cfg["wallet_address"]

        self.state: Dict[str, CoinState] = {}
        self.last_min: Optional[int] = None

        # per-minute metrics accumulators
        self.maker_fills_min = 0
        self.taker_fills_min = 0
        self.realized_min = 0.0
        self.net_fees_min = 0.0

        # touch tracking (for improve logic)
        self.touch_px: Dict[Tuple[str, str], float] = {}   # (coin, side) -> last observed touch
        self.stale_count: Dict[Tuple[str, str], int] = {}  # (coin, side) -> loops at same touch

        self.loop_i = 0
        self.log = logger
        self._last_fc_logged = None

        # default leverage for margin aware sizing
        self.cfg.setdefault("assumed_leverage", 10.0)
        self.cfg.setdefault("margin_cap_fraction", 0.5)   # fraction of FC allocated to resting quotes
        self.cfg.setdefault("fc_hint_usd", 0.0)           # fallback FC if API returns 0/None
        self.cfg.setdefault("margin_cap_mode", "auto")    # auto|off|strict

        # dynamic min-spread controls (runtime, per-coin)
        self.cfg.setdefault("dynamic_min_spread_enabled", True)
        self.cfg.setdefault("dynamic_min_spread_percentile", 0.6)           # 60th percentile of recent spreads
        self.cfg.setdefault("dynamic_min_spread_lookback_loops", 300)       # ~60s at 200ms loop
        self.cfg.setdefault("dynamic_min_spread_update_every_loops", 20)    # update ~every 4s
        self.cfg.setdefault("dynamic_min_spread_hysteresis_bps", 0.5)       # avoid twitchy changes
        self.cfg.setdefault("dynamic_min_spread_exclude", ["PENGU", "FARTCOIN"])  # keep microcaps aggressive

        # --- auto-tuner defaults ---
        self.cfg.setdefault("autotune_enabled", True)
        self.cfg.setdefault("autotune_window_minutes", 5)
        self.cfg.setdefault("autotune_min_maker_share", 0.7)   # 70%+ maker implies selection risk if PnL <= 0
        self.cfg.setdefault("autotune_neg_pnl_usd", 0.0)       # trigger raise when sum(total_pnl) <= this
        self.cfg.setdefault("autotune_raise_percentile_step", 0.05)  # +5%ile per adjustment
        self.cfg.setdefault("autotune_lower_percentile_step", 0.05)  # -5%ile per adjustment
        self.cfg.setdefault("autotune_percentile_min", 0.40)
        self.cfg.setdefault("autotune_percentile_max", 0.90)
        self.cfg.setdefault("autotune_raise_guard_buffer_bps", 0.5)
        self.cfg.setdefault("autotune_lower_guard_buffer_bps", 0.5)
        self.cfg.setdefault("autotune_guard_buffer_min_bps", 1.5)
        self.cfg.setdefault("autotune_guard_buffer_max_bps", 4.0)
        self.cfg.setdefault("autotune_target_maker_fills_per_min", 2)

        # rolling minute window for auto-tune decisions
        self._minute_hist = []  # list of dicts: {maker_fills, taker_fills, maker_share, realized_pnl, net_fees, total_pnl}

        # Telemetry cadence for dynamic min-spread
        self.cfg.setdefault("dynamic_min_spread_telemetry_every_loops", 50)  # ~10s at 200ms loop

        # --- auto-tune cadence & per-coin accumulators ---
        self.cfg.setdefault("autotune_cooldown_minutes", 2)  # minimum minutes between per-coin adjustments
        self.cfg.setdefault("autotune_exclude", [])         # coins to skip in autotune

        # --- Telemetry & logging ---
        self.cfg.setdefault("telemetry_enabled", False)
        self.cfg.setdefault("telemetry_console", False)
        self.cfg.setdefault("telemetry_db", False)
        self.cfg.setdefault("fills_log_enabled", False)
        self.cfg.setdefault("lifecycle_log_enabled", False)
        self.cfg.setdefault("ss_market_aware_log_sample_s", 0.0)
        self.cfg.setdefault("fc_probe_log", False)

        self._last_ss_log_ts = {}
        self._last_ss_choice = {}

        # per-coin minute accumulators
        self.maker_fills_min_by_coin: Dict[str, int] = {}
        self.taker_fills_min_by_coin: Dict[str, int] = {}
        self.realized_min_by_coin: Dict[str, float] = {}
        self.net_fees_min_by_coin: Dict[str, float] = {}

        # per-coin minute histories and last-adjust times
        self._minute_hist_by_coin: Dict[str, list] = {}
        self._autotune_last_min_by_coin: Dict[str, int] = {}

        # runtime buffers for dynamic min-spread
        self._spread_hist: Dict[str, list] = {}
        self._dyn_min_spread: Dict[str, float] = {}

        # --- single-sided quoting config & state ---
        # single_sided_mode: "off" | "bid" | "ask" | "auto"
        self.cfg.setdefault("single_sided_mode", "off")
        # Flip cooldown to avoid thrashing. Measured in strategy loops.
        self.cfg.setdefault("single_sided_flip_cooldown_loops", 50)

        # Runtime state for single-sided
        self._single_side_choice: Dict[str, str] = {}      # coin -> "B" or "A"
        self._single_side_last_loop: Dict[str, int] = {}   # coin -> loop_i when side last changed
        self._last_mid_seen: Dict[str, float] = {}         # coin -> last mid we observed

        # --- market-aware single-sided settings (global; per-coin via _c) ---
        self.cfg.setdefault("single_sided_market_aware", True)
        self.cfg.setdefault("ss_ma_half_life_sec", 30.0)                 # decay half-life for flow counters
        self.cfg.setdefault("ss_ma_spread_excess_floor_bps", 0.5)        # require spread above dyn floor by this many bps
        self.cfg.setdefault("ss_ma_min_maker_share", 0.55)               # require maker share over this to quote
        self.cfg.setdefault("ss_ma_side_bias_ratio", 1.15)               # mb/ms or ms/mb must exceed this to bias
        self.cfg.setdefault("ss_ma_no_quote_when_tight", True)           # allow pausing when spread too tight

        # Runtime flow stats (exponential decay)
        # coin -> {"mb": maker-buys (sells hit our bid), "ms": maker-sells (buyers lift our ask),
        #          "maker": total maker fills, "taker": total taker fills, "ts": last update epoch}
        self._ma_flow: Dict[str, Dict[str, float]] = {}
        
        # Order book flow analysis
        # coin -> {"bid_volume": float, "ask_volume": float, "bid_orders": int, "ask_orders": int,
        #          "large_orders": list, "imbalance": float, "pressure": float, "ts": float}
        self._order_book_flow: Dict[str, Dict[str, Any]] = {}
        
        # Order book depth tracking
        # coin -> {"bids": [(price, size), ...], "asks": [(price, size), ...], "ts": float}
        self._order_book_depth: Dict[str, Dict[str, Any]] = {}
        
        # Flow imbalance detection
        # coin -> {"bid_imbalance": float, "ask_imbalance": float, "net_imbalance": float, "ts": float}
        self._flow_imbalance: Dict[str, Dict[str, float]] = {}
        
        # Flow analysis timeframes and configuration
        self.cfg.setdefault("flow_analysis_short_window_s", 30)    # 30 seconds for short-term flow
        self.cfg.setdefault("flow_analysis_medium_window_s", 300)  # 5 minutes for medium-term flow
        self.cfg.setdefault("flow_analysis_long_window_s", 1800)   # 30 minutes for long-term flow
        self.cfg.setdefault("order_book_flow_update_interval_s", 1) # Update order book flow every 1 second
        self.cfg.setdefault("flow_imbalance_update_interval_s", 5)  # Update flow imbalance every 5 seconds
        
        # Flow analysis update tracking
        self._last_order_book_flow_update: Dict[str, float] = {}
        self._last_flow_imbalance_update: Dict[str, float] = {}

        # --- directional bias & bailout policy (global; per-coin via _c) ---
        # Bias state machine: accumulate (toward target) vs distribute (work out at a rebate)
        self.cfg.setdefault("bias_enabled", True)
        self.cfg.setdefault("bias_direction_cooldown_min", 20)   # min minutes between long/short bias flips
        self.cfg.setdefault("target_inventory_usd_default", 0.0)
        self.cfg.setdefault("inv_band_pct_default", 0.25)        # +/-25% band around target before mode switch

        # Bailout knobs (maker-first, taker-second)
        self.cfg.setdefault("bail_partial_mae_bps", 30.0)        # partial reduce when >30 bps underwater
        self.cfg.setdefault("bail_full_mae_bps", 60.0)           # full exit when >60 bps underwater
        self.cfg.setdefault("bail_underwater_time_s", 180)       # or when >3 min underwater
        self.cfg.setdefault("bail_work_time_s", 90)              # allow N seconds to work passively before taker
        self.cfg.setdefault("portfolio_pnl_pause_pct", -0.8)     # (reserved) pause when 10m PnL < this % of equity
        self.cfg.setdefault("portfolio_pause_s", 180)

        # Runtime: bias timers, underwater timers
        self._bias_last_flip_min: Dict[str, int] = {}
        self._underwater_since: Dict[str, float] = {}   # coin -> epoch when MAE>0 started
        self._last_unrealized_bps: Dict[str, float] = {}

        # --- REST-friendly throttles ---
        self.cfg.setdefault("ioc_throttle_s", 0.5)            # per-coin gap between IOC reduces
        self.cfg.setdefault("open_orders_refresh_s", 5.0)      # min seconds between open_orders polls
        self._last_ioc_ts: Dict[str, float] = {}
        self._oo_cache = None
        self._oo_cache_ts = 0.0

        self._init_message_batching()

    # ---------- lifecycle ----------

    def _tlog(self, obj):
        # Only log non-error telemetry if enabled
        if not self.cfg.get("telemetry_enabled", True):
            return
        if not self.cfg.get("telemetry_console", True):
            return
        # Suppress noisy categories here if desired
        if isinstance(obj, dict) and obj.get("type") in ("info", "fill", "order", "startup") and not self.cfg.get("telemetry_console", True):
            return
        self.log(obj)

    def now_min(self) -> int:
        return int(time.time() // 60)

    def coin_state(self, coin: str) -> CoinState:
        if coin not in self.state:
            self.state[coin] = CoinState()
        return self.state[coin]

    def _percentile(self, values, q: float) -> Optional[float]:
        """Return the q-th percentile (0..1) of a list of numbers. None if empty."""
        try:
            if not values:
                return None
            xs = sorted(values)
            q = max(0.0, min(1.0, float(q)))
            k = (len(xs) - 1) * q
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return float(xs[int(k)])
            return float(xs[f] + (xs[c] - xs[f]) * (k - f))
        except Exception:
            return None

    def _maybe_autotune(self):
        """Auto-tune dynamic percentile and guard buffer using recent minute metrics.
        Rules:
          - If avg maker_share >= threshold AND window total_pnl <= 0 → raise percentile and guard buffer (be pickier).
          - Else if window total_pnl > 0 AND maker fills too few → lower percentile slightly (get more flow).
        Only updates in-memory cfg; never lowers below fee guard or configured mins.
        """
        try:
            if not self.cfg.get("autotune_enabled", True):
                return
            win = int(self.cfg.get("autotune_window_minutes", 5))
            if win <= 0:
                return
            exclude = set(self.cfg.get("autotune_exclude", []))
            for coin in self.cfg.get("coins", []):
                if coin in exclude:
                    continue
                hist_all = self._minute_hist_by_coin.get(coin, [])
                if not hist_all:
                    continue
                hist = hist_all[-win:]
                if not hist:
                    continue

                maker_fills = sum(int(h.get("maker_fills", 0) or 0) for h in hist)
                taker_fills = sum(int(h.get("taker_fills", 0) or 0) for h in hist)
                total_pnl   = sum(float(h.get("total_pnl", 0.0) or 0.0) for h in hist)
                shares      = [float(h.get("maker_share")) for h in hist if h.get("maker_share") is not None]
                maker_share_avg = (sum(shares) / len(shares)) if shares else None

                # current knobs (per-coin aware)
                perc = float(self._cf(coin, "dynamic_min_spread_percentile", float(self.cfg.get("dynamic_min_spread_percentile", 0.6))))
                perc_min = float(self._cf(coin, "autotune_percentile_min", float(self.cfg.get("autotune_percentile_min", 0.4))))
                perc_max = float(self._cf(coin, "autotune_percentile_max", float(self.cfg.get("autotune_percentile_max", 0.9))))
                step_up   = float(self._cf(coin, "autotune_raise_percentile_step", float(self.cfg.get("autotune_raise_percentile_step", 0.05))))
                step_down = float(self._cf(coin, "autotune_lower_percentile_step", float(self.cfg.get("autotune_lower_percentile_step", 0.05))))

                guard_buf = float(self._cf(coin, "min_spread_guard_buffer_bps", float(self.cfg.get("min_spread_guard_buffer_bps", 2.0))))
                guard_min = float(self._cf(coin, "autotune_guard_buffer_min_bps", float(self.cfg.get("autotune_guard_buffer_min_bps", 1.5))))
                guard_max = float(self._cf(coin, "autotune_guard_buffer_max_bps", float(self.cfg.get("autotune_guard_buffer_max_bps", 4.0))))
                guard_up  = float(self._cf(coin, "autotune_raise_guard_buffer_bps", float(self.cfg.get("autotune_raise_guard_buffer_bps", 0.5))))
                guard_dn  = float(self._cf(coin, "autotune_lower_guard_buffer_bps", float(self.cfg.get("autotune_lower_guard_buffer_bps", 0.5))))

                maker_share_thr = float(self._cf(coin, "autotune_min_maker_share", float(self.cfg.get("autotune_min_maker_share", 0.7))))
                neg_pnl_trig    = float(self._cf(coin, "autotune_neg_pnl_usd", float(self.cfg.get("autotune_neg_pnl_usd", 0.0))))
                target_fills_pm = float(self._cf(coin, "autotune_target_maker_fills_per_min", float(self.cfg.get("autotune_target_maker_fills_per_min", 2))))

                cooldown_min = int(self._cf(coin, "autotune_cooldown_minutes", int(self.cfg.get("autotune_cooldown_minutes", 2))))
                last_adj_min = int(self._autotune_last_min_by_coin.get(coin, 0))
                if self.last_min is not None and (self.last_min - last_adj_min) < cooldown_min:
                    continue  # respect cooldown

                changed = False
                reason = None
                old_perc = perc
                old_guard = guard_buf

                # Condition A: lots of maker fills but losing → become pickier
                if (maker_share_avg is not None and maker_share_avg >= maker_share_thr and total_pnl <= neg_pnl_trig):
                    perc = min(perc_max, perc + step_up)
                    guard_buf = min(guard_max, guard_buf + guard_up)
                    changed = (perc != old_perc) or (guard_buf != old_guard)
                    reason = f"adverse_selection[{coin}]: maker_share_avg={maker_share_avg:.2f} total_pnl={total_pnl:.4f}"

                # Condition B: profitable but too few fills → loosen slightly
                elif total_pnl > 0:
                    mins = max(1, len(hist))
                    fills_per_min = maker_fills / mins
                    if fills_per_min < target_fills_pm:
                        perc = max(perc_min, perc - step_down)
                        guard_buf = max(guard_min, guard_buf - guard_dn)
                        changed = (perc != old_perc) or (guard_buf != old_guard)
                        reason = f"low_flow[{coin}]: fills_per_min={fills_per_min:.2f} < target={target_fills_pm:.2f}, pnl>0"

                if changed:
                    self.cfg.setdefault("per_coin", {}).setdefault(coin, {})
                    self.cfg["per_coin"][coin]["dynamic_min_spread_percentile"] = perc
                    self.cfg["per_coin"][coin]["min_spread_guard_buffer_bps"] = guard_buf
                    self._autotune_last_min_by_coin[coin] = int(self.last_min or time.time()//60)
                    self._tlog({
                        "type": "info",
                        "op": "autotune",
                        "coin": coin,
                        "msg": f"percentile {old_perc:.2f}→{perc:.2f}, guard_buffer {old_guard:.2f}→{guard_buf:.2f} | {reason}",
                        "window_min": len(hist),
                        "maker_fills": maker_fills,
                        "taker_fills": taker_fills,
                        "maker_share_avg": None if maker_share_avg is None else round(maker_share_avg, 3),
                        "total_pnl": round(total_pnl, 6),
                        "cooldown_min": cooldown_min
                    })
        except Exception as e:
            self.log({"type": "warn", "op": "autotune", "msg": str(e)})

    def _purge_open_orders_for_coin(self, coin: str):
        """Best-effort: cancel all open orders for `coin` on startup or housekeeping."""
        try:
            oo = self.client.info.open_orders(self.client.addr)
        except Exception as e:
            self.log({"type": "warn", "op": "open_orders", "msg": str(e)})
            return
        if not isinstance(oo, list):
            return
        for row in oo:
            try:
                c = row.get("coin") or row.get("name") or row.get("asset")
                if c != coin:
                    continue
                oid = row.get("oid") or row.get("orderId") or row.get("order_id")
                if oid is None:
                    continue
                self.client.cancel(coin, int(oid))
            except Exception:
                pass

    def start(self):
        insert_bot(
            self.db,
            {
                "bot_id": self.bot_id,
                "name": self.cfg.get("name") or self.bot_id,
                "wallet": self.wallet,
                "mode": self.cfg.get("mode", "testnet"),
                "config_path": self.cfg.get("__path"),
                "config_json": __import__("json").dumps(self.cfg),
                "config_version": self.cfg.get("config_version", "1.0.0"),
                "started_at": datetime.datetime.utcnow().isoformat(),
                "updated_at": datetime.datetime.utcnow().isoformat(),
            },
        )
        
        # Initialize config version tracking for each coin
        for coin in self.cfg.get("coins", []):
            try:
                # Determine desired version for this coin
                coin_config = self.cfg.get("per_coin", {}).get(coin, {})
                config_version = coin_config.get("config_version", self.cfg.get("config_version", "1.0.0"))

                # Check current stored version and only insert when changed/new
                try:
                    existing = get_current_coin_config_version(self.db, self.bot_id, coin)
                except Exception:
                    existing = None

                if existing == config_version:
                    self._tlog({"type": "info", "coin": coin, "config_version": config_version, "msg": "Config version unchanged"})
                    continue

                # Create and store snapshot
                config_snapshot = self._get_coin_config_snapshot(coin)
                insert_coin_config_version(
                    self.db,
                    self.bot_id,
                    coin,
                    config_version,
                    __import__("json").dumps(config_snapshot)
                )
                self._tlog({"type": "info", "coin": coin, "config_version": config_version, "msg": "Config version tracked"})
            except Exception as e:
                msg = str(e)
                if "UNIQUE constraint failed" in msg:
                    self._tlog({"type": "info", "coin": coin, "msg": "Config version already tracked"})
                else:
                    self.log({"type": "warn", "coin": coin, "msg": f"Failed to track config version: {e}"})
        
        self.client.connect()

        # Startup tick + BBO visibility per coin (helps catch bad ticks early)
        for coin in self.cfg.get("coins", []):
            try:
                # px_step from client (already tolerates null meta)
                step = self.client.px_step(coin)
            except Exception:
                step = None
            override = None
            try:
                override = self.cfg.get("per_coin", {}).get(coin, {}).get("px_decimals_override")
            except Exception:
                override = None
            try:
                bb, ba = self.client.best_bid_ask(coin)
            except Exception:
                bb, ba = (None, None)
            self._tlog({
                "type": "startup",
                "coin": coin,
                "px_step": step,
                "px_decimals_override": override,
                "bbo": [bb, ba]
            })

        # Init dynamic spread buffers per coin
        for coin in self.cfg.get("coins", []):
            self._spread_hist.setdefault(coin, [])
            # seed dynamic min with configured min
            base_min = float(self._c(coin, "min_spread_bps", float(self.cfg.get("min_spread_bps", 0.0))))
            self._dyn_min_spread.setdefault(coin, base_min)

        # Fee math one-liners per coin: breakeven and suggested min_spread_bps
        try:
            add_bps, cross_bps = self.client.get_fee_rates()
        except Exception:
            add_bps, cross_bps = 1.5, 4.5
        be_mm = round(2 * add_bps, 2)          # maker→maker breakeven (bps)
        be_mt = round(add_bps + cross_bps, 2)  # maker→taker breakeven (bps)
        be_tt = round(2 * cross_bps, 2)        # taker→taker breakeven (bps)
        # Cache M→M breakeven for telemetry
        self._be_mm_bps = float(be_mm)
        default_min = float(self.cfg.get("min_spread_bps", 0.0))
        for coin in self.cfg.get("coins", []):
            cur = float(self._c(coin, "min_spread_bps", default_min))
            suggest = max(cur, be_mm + 2.0)  # +2 bps safety buffer over M→M breakeven
            # One-line, human-friendly print
            self._tlog(f"[fee_math] {coin}: add={add_bps:.2f}bps cross={cross_bps:.2f}bps | BE M→M={be_mm:.2f}bps M→T={be_mt:.2f}bps T→T={be_tt:.2f}bps | suggest min_spread_bps≥{suggest:.2f}")

        # Guard: enforce a runtime floor for min_spread_bps so maker→maker roundtrips are net-positive
        guard_enabled = bool(self.cfg.get("min_spread_guard_enabled", True))
        guard_buffer = float(self.cfg.get("min_spread_guard_buffer_bps", 2.0))  # extra safety above M→M breakeven
        guard_exclude = set(self.cfg.get("min_spread_guard_exclude", []))
        if guard_enabled:
            self.cfg.setdefault("per_coin", {})
            for coin in self.cfg.get("coins", []):
                if coin in guard_exclude:
                    continue
                cur = float(self._c(coin, "min_spread_bps", default_min))
                guard_buf_coin = float(self._c(coin, "min_spread_guard_buffer_bps", guard_buffer))
                target = round(max(cur, be_mm + guard_buf_coin), 2)
                if target > cur + 1e-9:  # only ever raise, never lower
                    self.cfg["per_coin"].setdefault(coin, {})
                    self.cfg["per_coin"][coin]["min_spread_bps"] = target
                    self._tlog({
                        "type": "info",
                        "op": "min_spread_guard",
                        "coin": coin,
                        "msg": f"auto-raise min_spread_bps: {cur:.2f} → {target:.2f} (M→M BE {be_mm:.2f} + buffer {guard_buf_coin:.2f})"
                    })

        # Subscribe to user fills and backfill a small recent window so our DB is populated
        try:
            if hasattr(self.client, "on_user_fill"):
                self.client.on_user_fill(self._on_user_fill)
        except Exception as e:
            self.log({"type": "warn", "op": "on_user_fill", "msg": str(e)})
        try:
            recent = []
            if hasattr(self.client, "fetch_recent_fills"):
                recent = self.client.fetch_recent_fills(limit=50)
            for f in (recent or []):
                self._on_user_fill(f)
        except Exception as e:
            self.log({"type": "warn", "op": "backfill_fills", "msg": str(e)})

        # Subscribe to real-time market data updates for each coin
        try:
            if hasattr(self.client, "on_market_data_update"):
                for coin in self.cfg.get("coins", []):
                    self.client.on_market_data_update(coin, self._on_market_data_update)
                    self.log({"type": "info", "op": "market_data_subscription", "coin": coin, "msg": "Subscribed to real-time market data"})
        except Exception as e:
            self.log({"type": "warn", "op": "market_data_subscription", "msg": str(e)})

        # Check WebSocket connection status
        if self.client.use_websocket and hasattr(self.client, 'ws_market_data'):
            ws_connected = self.client.ws_market_data.is_connected()
            if not ws_connected:
                self.log({"type": "warn", "op": "websocket", "msg": "WebSocket not connected, will use REST fallback"})
            else:
                # Log detailed WebSocket status
                market_data_count = len(self.client.ws_market_data.market_data)
                coins_with_data = list(self.client.ws_market_data.market_data.keys())
                
                # Show real-time market data if available
                if market_data_count > 0:
                    market_data_summary = {}
                    for coin in coins_with_data[:3]:  # Show first 3 coins
                        data = self.client.ws_market_data.market_data.get(coin, {})
                        if data:
                            market_data_summary[coin] = {
                                "bid": round(data.get("best_bid", 0), 4),
                                "ask": round(data.get("best_ask", 0), 4),
                                "age_ms": round((time.time() - data.get("timestamp", 0)) * 1000, 0)
                            }
                    
                    self.log({
                        "type": "info", 
                        "op": "websocket", 
                        "msg": f"WebSocket connected and ready with {market_data_count} coins",
                        "coins": coins_with_data,
                        "market_data_sample": market_data_summary
                    })
                else:
                    self.log({
                        "type": "info", 
                        "op": "websocket", 
                        "msg": f"WebSocket connected but waiting for market data (ws_ready={self.client.ws_market_data.ws_ready})"
                    })

        # Optional: purge all open orders for configured coins to avoid stacking from prior runs
        if self.cfg.get("purge_on_start", True):
            for coin in self.cfg.get("coins", []):
                try:
                    if self.client.supports(coin):
                        self._purge_open_orders_for_coin(coin)
                except Exception:
                    pass

        # Enforce single-sided mode from startup: ensure blocked sides have no resting orders
        try:
            for coin in self.cfg.get("coins", []):
                chosen = self._get_single_side(coin)
                if chosen == "B":
                    self._cancel_side_for_coin(coin, "A")
                elif chosen == "A":
                    self._cancel_side_for_coin(coin, "B")
                elif chosen == "N":
                    # No-quote state: cancel both sides
                    self._cancel_side_for_coin(coin, "A")
                    self._cancel_side_for_coin(coin, "B")
        except Exception:
            pass

        # Backfill position data from exchange on startup
        try:
            u = self.client.info.user_state(self.client.addr)
            for ap in u.get("assetPositions", []):
                pos = ap.get("position", {})
                coin = pos.get("coin")
                if not coin or coin not in self.cfg.get("coins", []):
                    continue
                
                # Get position size
                szi = pos.get("szi")
                if szi is None:
                    sz = float(pos.get("sz", 0.0))
                    side = (pos.get("side", "").lower())
                    szi = sz if side.startswith("long") else (-sz if side.startswith("short") else 0.0)
                szi = float(szi)
                
                if abs(szi) < 1e-12:
                    continue
                
                # Get average entry price
                avg_entry = float(pos.get("avgEntry", 0.0) or 0.0)
                
                # Backfill position state
                st = self.coin_state(coin)
                st.pos = szi
                st.avg_entry = avg_entry
                
                self.log({
                    "type": "info",
                    "op": "position_backfill",
                    "coin": coin,
                    "position": szi,
                    "avg_entry": avg_entry,
                    "msg": f"Backfilled position: {szi} {coin} at ${avg_entry}"
                })
                
                # Optional: flatten on startup if user asked for it
                if self.cfg.get("flatten_on_start"):
                    mid = self.mark_mid(coin)
                    self._flatten_position_immediate(coin, szi, mid)
                    
        except Exception as e:
            self.log({"type": "warn", "op": "position_backfill", "msg": str(e)})

    # ---------- inventory + PnL helpers ----------

    def mark_mid(self, coin: str) -> float:
        """Mid from touch (best bid/ask)."""
        best_bid, best_ask = self.client.best_bid_ask(coin)
        return 0.5 * (best_bid + best_ask)
    
    # ---------- single-sided mode helpers ----------

    def _single_sided_mode(self, coin: str) -> str:
        """
        Resolve effective single-sided mode for a coin.
        Returns: "off" | "bid" | "ask" | "auto"
        """
        try:
            mode = str(self._c(coin, "single_sided_mode", self.cfg.get("single_sided_mode", "off")) or "off").lower()
            if mode in ("bid", "b"):
                return "bid"
            if mode in ("ask", "a", "sell"):
                return "ask"
            if mode == "auto":
                return "auto"
            return "off"
        except Exception:
            return "off"

    def _choose_auto_side(self, coin: str) -> str:
        """
        Enhanced auto selector with multiple signals and market regime awareness.
        """
        return self._enhanced_auto_side_selection(coin)

    def _get_single_side(self, coin: str) -> Optional[str]:
        """
        Returns "B" or "A" when single-sided is active for this coin, otherwise None.
        Enforces a flip cooldown in loops.
        """
        mode = self._single_sided_mode(coin)
        if mode == "off":
            return None

        if mode == "bid":
            return "B"
        if mode == "ask":
            return "A"

        # auto
        desired = self._choose_auto_side(coin)

        # If a directional bias is configured, prefer its accumulate/distribute side
        try:
            bias_side = self._apply_bias_desired_side(coin)
            if bias_side in ("A", "B"):
                desired = bias_side
        except Exception:
            pass

        # Run bailout checks each loop; may force a pause or reduction via IOC
        try:
            self._maybe_bail(coin)
        except Exception:
            pass
        # Market-aware adjustments (only in auto mode)
        try:
            if bool(self._c(coin, "single_sided_market_aware", self.cfg.get("ss_market_aware_log_sample_s", True))):
                # Spread gate vs dynamic floor
                spread_bps = self._current_spread_bps(coin)
                # _dyn_min_spread holds a bps floor already
                dyn_cur = float(self._dyn_min_spread.get(
                    coin, float(self._c(coin, "min_spread_bps", float(self.cfg.get("min_spread_bps", 0.0))))
                ))
                excess_floor = float(self._c(coin, "ss_ma_spread_excess_floor_bps", self.cfg.get("ss_ma_spread_excess_floor_bps", 0.5)))
                no_quote_when_tight = bool(self._c(coin, "ss_ma_no_quote_when_tight", self.cfg.get("ss_ma_no_quote_when_tight", True)))

                mb, ms, mk, tk = self._ma_get(coin)
                maker_total = mk
                taker_total = tk
                maker_share = maker_total / max(1e-9, maker_total + taker_total)
                maker_share_min = float(self._c(coin, "ss_ma_min_maker_share", self.cfg.get("ss_ma_min_maker_share", 0.55)))
                bias_ratio = float(self._c(coin, "ss_ma_side_bias_ratio", self.cfg.get("ss_ma_side_bias_ratio", 1.15)))

                # Pause quoting if spreads are too tight or maker share is weak
                if (no_quote_when_tight and (spread_bps < (dyn_cur + excess_floor))) or (maker_share < maker_share_min):
                    desired = "N"  # no-quote state
                else:
                    # Bias to the side that is passively getting fills more
                    if mb > ms * bias_ratio:
                        desired = "B"
                    elif ms > mb * bias_ratio:
                        desired = "A"
                # Telemetry with sampling
                try:
                    sample_s = float(self.cfg.get("ss_market_aware_log_sample_s", 1.0))
                    ok_to_log = False
                    now = time.time()
                    last = self._last_ss_log_ts.get(coin, 0.0)
                    choice_changed = (desired != self._last_ss_choice.get(coin))

                    if sample_s > 0:
                        ok_to_log = (now - last) >= sample_s or choice_changed

                    if ok_to_log and self.cfg.get("telemetry_enabled", True) and self.cfg.get("telemetry_console", True):
                        self._tlog({
                            "type": "info", "op": "ss_market_aware", "coin": coin,
                            "spread_bps": round(spread_bps, 3),
                            "dyn_floor_bps": round(dyn_cur, 3),
                            "excess_floor_bps": excess_floor,
                            "maker_share": round(maker_share, 3),
                            "mb": round(mb, 3), "ms": round(ms, 3),
                            "chosen": desired
                        })
                        self._last_ss_log_ts[coin] = now
                        self._last_ss_choice[coin] = desired
                except Exception:
                    pass
        except Exception:
            pass

        cooldown = int(self._c(coin, "single_sided_flip_cooldown_loops",
                            int(self.cfg.get("single_sided_flip_cooldown_loops", 50))))
        last_choice = self._single_side_choice.get(coin)
        last_loop = int(self._single_side_last_loop.get(coin, -10**9))
        cur_loop = int(getattr(self, "loop_i", 0) or 0)

        # Respect cooldown before flipping, except when entering "N" (pause immediately)
        if last_choice and desired != last_choice:
            if str(desired).upper() != "N" and (cur_loop - last_loop) < cooldown:
                return last_choice

        if desired != last_choice:
            self._single_side_choice[coin] = desired
            self._single_side_last_loop[coin] = cur_loop
            try:
                self._tlog({"type": "info", "op": "single_sided_flip", "coin": coin, "side": desired, "loop_i": cur_loop})
            except Exception:
                pass
        return desired
    
    def _single_sided_allowed(self, coin: str, side: str) -> bool:
        """
        Returns True if the given side is allowed under the effective single-sided mode.
        side: "B" for bid or "A" for ask.
        """
        chosen = self._get_single_side(coin)
        if chosen is None:
            return True
        if str(chosen).upper() == "N":
            # "No quote" state: block both sides
            return False
        return str(side).upper() == str(chosen).upper()

    def _get_optimal_single_side_price(self, coin: str, side: str, base_price: float, tick: float, best_bid: float, best_ask: float) -> float:
        """
        Use market-aware logic to determine the optimal price for single-sided mode.
        Returns the optimal price for the given side.
        """
        try:
            # Get market-aware metrics
            mb, ms, mk, tk = self._ma_get(coin)
            maker_total = mk
            taker_total = tk
            maker_share = maker_total / max(1e-9, maker_total + taker_total)
            
            # Get spread information
            spread_bps = self._current_spread_bps(coin)
            spread = best_ask - best_bid
            
            # Base price is already calculated with basic logic
            optimal_price = base_price
            
            # Adjust based on market conditions and fill probability
            if side == "B":  # Bid side
                if mb > ms * 1.5:  # Strong buyer flow
                    # Be more aggressive - improve by 1 tick
                    optimal_price = min(optimal_price + tick, best_ask - tick)
                elif mb < ms * 0.7:  # Weak buyer flow
                    # Be less aggressive - worsen by 1 tick
                    optimal_price = max(optimal_price - tick, best_bid)
                # Otherwise keep base price
                    
            elif side == "A":  # Ask side
                if ms > mb * 1.5:  # Strong seller flow
                    # Be more aggressive - improve by 1 tick
                    optimal_price = max(optimal_price - tick, best_bid + tick)
                elif ms < mb * 0.7:  # Weak seller flow
                    # Be less aggressive - worsen by 1 tick
                    optimal_price = optimal_price + tick
                # Otherwise keep base price
            
            # Ensure price is within reasonable bounds
            if side == "B":
                optimal_price = max(optimal_price, best_bid)
                optimal_price = min(optimal_price, best_ask - tick)
            else:  # side == "A"
                optimal_price = max(optimal_price, best_bid + tick)
                optimal_price = min(optimal_price, best_ask)
                
            return optimal_price
            
        except Exception as e:
            # Fallback to base price if market-aware logic fails
            self.log({"type": "warn", "op": "optimal_price", "coin": coin, "side": side, "msg": str(e)})
            return base_price
    

    def _cancel_side_for_coin(self, coin: str, side_blocked: str):
        """
        Best-effort cancel of any open orders on the blocked side for a coin.
        side_blocked: "B" or "A"
        Note: With IOC orders, this should rarely be needed but kept for safety.
        """
        # Cache open_orders for a few seconds to reduce REST pressure
        try:
            refresh_s = float(self.cfg.get("open_orders_refresh_s", 5.0))
        except Exception:
            refresh_s = 5.0
        now = time.time()
        if (getattr(self, "_oo_cache", None) is None) or ((now - getattr(self, "_oo_cache_ts", 0.0)) > refresh_s):
            try:
                self._oo_cache = self.client.info.open_orders(self.client.addr)
                self._oo_cache_ts = now
            except Exception as e:
                self.log({"type": "warn", "op": "open_orders", "msg": str(e)})
                return
        oo = self._oo_cache if isinstance(self._oo_cache, list) else []
        if not oo:
            return
        
        for row in oo:
            try:
                c = row.get("coin") or row.get("name") or row.get("asset")
                if c != coin:
                    continue
                # Normalize order side
                s = row.get("side")
                is_buy = row.get("is_buy")
                if s is None and is_buy is not None:
                    s_norm = "B" if bool(is_buy) else "A"
                else:
                    s_str = str(s or "").strip().upper()
                    if s_str in ("B", "BUY"):
                        s_norm = "B"
                    elif s_str in ("A", "SELL"):
                        s_norm = "A"
                    else:
                        continue
                if s_norm != side_blocked:
                    continue
                oid = row.get("oid") or row.get("orderId") or row.get("order_id")
                if oid is None:
                    continue
                self.client.cancel(coin, int(oid))
            except Exception:
                pass

    # ---------- market-aware flow helpers ----------

    def _ma_decay(self, coin: str):
        """Exponentially decay flow counters to favor recent information."""
        now = time.time()
        st = self._ma_flow.setdefault(coin, {"mb": 0.0, "ms": 0.0, "maker": 0.0, "taker": 0.0, "ts": now})
        last = float(st.get("ts", now))
        dt = max(0.0, now - last)
        hl = float(self._c(coin, "ss_ma_half_life_sec", float(self.cfg.get("ss_ma_half_life_sec", 30.0))))
        if hl <= 0:
            decay = 0.0
        else:
            decay = 0.5 ** (dt / hl)
        st["mb"] *= decay
        st["ms"] *= decay
        st["maker"] *= decay
        st["taker"] *= decay
        st["ts"] = now
        return st

    def _ma_record_fill(self, coin: str, is_maker: bool, side: str):
        """Update decayed counters based on a fill event."""
        st = self._ma_decay(coin)
        if is_maker:
            st["maker"] += 1.0
            if str(side).upper() == "B":
                # We bought passively -> sellers hit our bid
                st["mb"] += 1.0
            elif str(side).upper() == "A":
                # We sold passively -> buyers lifted our ask
                st["ms"] += 1.0
        else:
            st["taker"] += 1.0

    def _ma_get(self, coin: str):
        """Return a snapshot of decayed flow counters after applying decay."""
        st = self._ma_decay(coin)
        return float(st["mb"]), float(st["ms"]), float(st["maker"]), float(st["taker"])

    # ---------- Order Book Flow Analysis ----------

    def _analyze_order_book_flow(self, coin: str, order_book: Dict[str, Any]) -> Dict[str, Any]:
        """
        Comprehensive order book flow analysis.
        Analyzes bid/ask imbalance, large orders, and order book pressure.
        """
        try:
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])
            
            if not bids or not asks:
                return {}
            
            # Calculate basic metrics
            bid_volume = sum(float(bid[1]) for bid in bids)
            ask_volume = sum(float(ask[1]) for ask in asks)
            bid_orders = len(bids)
            ask_orders = len(asks)
            
            # Calculate imbalance
            total_volume = bid_volume + ask_volume
            imbalance = (bid_volume - ask_volume) / max(total_volume, 1e-12)
            
            # Detect large orders (orders > 2x average size)
            avg_bid_size = bid_volume / max(bid_orders, 1)
            avg_ask_size = ask_volume / max(ask_orders, 1)
            
            large_orders = []
            for side, orders in [("bid", bids), ("ask", asks)]:
                for price, size in orders:
                    size_float = float(size)
                    avg_size = avg_bid_size if side == "bid" else avg_ask_size
                    if size_float > avg_size * 2.0:
                        large_orders.append({
                            "side": side,
                            "price": float(price),
                            "size": size_float,
                            "ratio": size_float / avg_size
                        })
            
            # Calculate order book pressure
            # Pressure = weighted average of order sizes by price level
            bid_pressure = 0.0
            ask_pressure = 0.0
            
            for i, (price, size) in enumerate(bids[:5]):  # Top 5 levels
                weight = 1.0 / (i + 1)  # Higher weight for closer levels
                bid_pressure += float(size) * weight
                
            for i, (price, size) in enumerate(asks[:5]):  # Top 5 levels
                weight = 1.0 / (i + 1)  # Higher weight for closer levels
                ask_pressure += float(size) * weight
            
            net_pressure = bid_pressure - ask_pressure
            
            # Calculate spread and depth
            best_bid = float(bids[0][0]) if bids else 0.0
            best_ask = float(asks[0][0]) if asks else 0.0
            spread = best_ask - best_bid
            mid = 0.5 * (best_bid + best_ask)
            spread_bps = (spread / max(mid, 1e-12)) * 10000.0
            
            # Depth analysis (volume within X bps of mid)
            depth_bps = 10.0  # 10 bps depth
            depth_range = mid * depth_bps / 10000.0
            
            bid_depth = sum(float(bid[1]) for bid in bids if best_bid - float(bid[0]) <= depth_range)
            ask_depth = sum(float(ask[1]) for ask in asks if float(ask[0]) - best_ask <= depth_range)
            
            result = {
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
                "bid_orders": bid_orders,
                "ask_orders": ask_orders,
                "imbalance": imbalance,
                "large_orders": large_orders,
                "bid_pressure": bid_pressure,
                "ask_pressure": ask_pressure,
                "net_pressure": net_pressure,
                "spread_bps": spread_bps,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "depth_imbalance": (bid_depth - ask_depth) / max(bid_depth + ask_depth, 1e-12),
                "ts": time.time()
            }
            
            # Store for historical analysis
            self._order_book_flow[coin] = result
            return result
            
        except Exception as e:
            self.log({"type": "warn", "op": "order_book_flow_analysis", "coin": coin, "msg": str(e)})
            return {}

    def _detect_flow_imbalance(self, coin: str, fills: List[Dict[str, Any]], window_seconds: int = 300) -> Dict[str, float]:
        """
        Detect flow imbalance from recent fills within a time window.
        Returns imbalance metrics for bid/ask flow.
        """
        try:
            if not fills:
                return {"bid_imbalance": 0.0, "ask_imbalance": 0.0, "net_imbalance": 0.0}
            
            # Filter fills by time window
            cutoff_time = time.time() - window_seconds
            recent_fills = [fill for fill in fills if fill.get("timestamp", 0) >= cutoff_time]
            
            bid_volume = 0.0
            ask_volume = 0.0
            bid_count = 0
            ask_count = 0
            
            for fill in recent_fills:
                side = fill.get("side", "").upper()
                size = float(fill.get("size", 0))
                
                if side == "B":  # Buy
                    bid_volume += size
                    bid_count += 1
                elif side == "A":  # Sell
                    ask_volume += size
                    ask_count += 1
            
            total_volume = bid_volume + ask_volume
            total_count = bid_count + ask_count
            
            if total_volume == 0:
                return {"bid_imbalance": 0.0, "ask_imbalance": 0.0, "net_imbalance": 0.0}
            
            bid_imbalance = bid_volume / total_volume
            ask_imbalance = ask_volume / total_volume
            net_imbalance = bid_imbalance - ask_imbalance
            
            result = {
                "bid_imbalance": bid_imbalance,
                "ask_imbalance": ask_imbalance,
                "net_imbalance": net_imbalance,
                "bid_count": bid_count,
                "ask_count": ask_count,
                "total_volume": total_volume,
                "ts": time.time()
            }
            
            # Store for historical analysis
            self._flow_imbalance[coin] = result
            return result
            
        except Exception as e:
            self.log({"type": "warn", "op": "flow_imbalance_detection", "coin": coin, "msg": str(e)})
            return {"bid_imbalance": 0.0, "ask_imbalance": 0.0, "net_imbalance": 0.0}

    def _get_order_book_flow_signals(self, coin: str) -> Dict[str, Any]:
        """
        Get comprehensive order book flow signals for trading decisions using multiple timeframes.
        """
        try:
            # Check if we should update order book flow analysis
            if not self._should_update_flow_analysis(coin, "order_book"):
                # Return cached results if available
                cached = getattr(self, f"_cached_flow_signals_{coin}", None)
                if cached and (time.time() - cached.get("timestamp", 0)) < 10:  # Cache for 10 seconds
                    return cached.get("data", {})
            
            # Get current order book
            order_book = self.client.get_order_book(coin)
            if not order_book:
                return {}
            
            # Analyze order book flow
            flow_analysis = self._analyze_order_book_flow(coin, order_book)
            
            # Get recent fills for flow imbalance analysis
            recent_fills = []
            try:
                # Get fills from multiple time windows
                short_window = self.cfg.get("flow_analysis_short_window_s", 30)
                medium_window = self.cfg.get("flow_analysis_medium_window_s", 300)
                long_window = self.cfg.get("flow_analysis_long_window_s", 1800)
                
                # Get fills from database for the longest window
                fills = self.db.execute(
                    "SELECT * FROM fills WHERE coin = ? AND t_fill_ms >= ? ORDER BY t_fill_ms DESC",
                    (coin, int((time.time() - long_window) * 1000))
                ).fetchall()
                
                for fill in fills:
                    recent_fills.append({
                        "side": fill[3],  # side column
                        "size": fill[4],  # size column
                        "price": fill[5],  # price column
                        "timestamp": fill[8] / 1000.0  # Convert ms to seconds
                    })
            except Exception:
                pass
            
            # Analyze flow imbalance for multiple timeframes
            short_imbalance = self._detect_flow_imbalance(coin, recent_fills, short_window)
            medium_imbalance = self._detect_flow_imbalance(coin, recent_fills, medium_window)
            long_imbalance = self._detect_flow_imbalance(coin, recent_fills, long_window)
            
            # Combine signals with multi-timeframe analysis
            signals = {
                "order_book_imbalance": flow_analysis.get("imbalance", 0.0),
                "net_pressure": flow_analysis.get("net_pressure", 0.0),
                "depth_imbalance": flow_analysis.get("depth_imbalance", 0.0),
                "large_orders": len(flow_analysis.get("large_orders", [])),
                "spread_bps": flow_analysis.get("spread_bps", 0.0),
                "bid_volume": flow_analysis.get("bid_volume", 0.0),
                "ask_volume": flow_analysis.get("ask_volume", 0.0),
                # Multi-timeframe flow imbalances
                "flow_imbalance_short": short_imbalance.get("net_imbalance", 0.0),
                "flow_imbalance_medium": medium_imbalance.get("net_imbalance", 0.0),
                "flow_imbalance_long": long_imbalance.get("net_imbalance", 0.0),
                # Timeframe metadata
                "short_window_s": short_window,
                "medium_window_s": medium_window,
                "long_window_s": long_window
            }
            
            # Generate trading signals
            trading_signals = {
                "bid_strength": 0.0,
                "ask_strength": 0.0,
                "overall_bias": "neutral",
                "confidence": 0.0
            }
            
            # Calculate bid strength using multi-timeframe analysis (positive values favor bids)
            bid_strength = 0.0
            
            # Order book signals (real-time)
            bid_strength += flow_analysis.get("imbalance", 0.0) * 2.0  # Order book imbalance
            bid_strength += flow_analysis.get("net_pressure", 0.0) / max(flow_analysis.get("bid_volume", 1.0), 1.0)  # Pressure
            
            # Multi-timeframe flow signals (weighted by recency)
            bid_strength += short_imbalance.get("net_imbalance", 0.0) * 0.5   # Short-term (30s) - 50% weight
            bid_strength += medium_imbalance.get("net_imbalance", 0.0) * 0.3   # Medium-term (5m) - 30% weight
            bid_strength += long_imbalance.get("net_imbalance", 0.0) * 0.2     # Long-term (30m) - 20% weight
            
            # Calculate ask strength (positive values favor asks)
            ask_strength = -bid_strength  # Inverse relationship
            
            trading_signals["bid_strength"] = bid_strength
            trading_signals["ask_strength"] = ask_strength
            
            # Determine overall bias
            if bid_strength > 0.1:
                trading_signals["overall_bias"] = "bid"
                trading_signals["confidence"] = min(1.0, abs(bid_strength))
            elif ask_strength > 0.1:
                trading_signals["overall_bias"] = "ask"
                trading_signals["confidence"] = min(1.0, abs(ask_strength))
            else:
                trading_signals["overall_bias"] = "neutral"
                trading_signals["confidence"] = 0.0
            
            # Log flow analysis periodically with multi-timeframe data
            if self.loop_i % 60 == 0:  # Every minute
                self._tlog({
                    "type": "info",
                    "op": "order_book_flow",
                    "coin": coin,
                    "order_book_imbalance": round(flow_analysis.get("imbalance", 0.0), 3),
                    "net_pressure": round(flow_analysis.get("net_pressure", 0.0), 2),
                    "flow_imbalance_30s": round(short_imbalance.get("net_imbalance", 0.0), 3),
                    "flow_imbalance_5m": round(medium_imbalance.get("net_imbalance", 0.0), 3),
                    "flow_imbalance_30m": round(long_imbalance.get("net_imbalance", 0.0), 3),
                    "large_orders": len(flow_analysis.get("large_orders", [])),
                    "bias": trading_signals["overall_bias"],
                    "confidence": round(trading_signals["confidence"], 2),
                    "timeframes": f"{short_window}s/{medium_window}s/{long_window}s"
                })
            
            result = {**signals, **trading_signals}
            
            # Cache the results
            setattr(self, f"_cached_flow_signals_{coin}", {
                "data": result,
                "timestamp": time.time()
            })
            
            return result
            
        except Exception as e:
            self.log({"type": "warn", "op": "order_book_flow_signals", "coin": coin, "msg": str(e)})
            return {}

    def _should_update_flow_analysis(self, coin: str, analysis_type: str = "order_book") -> bool:
        """
        Check if flow analysis should be updated based on configured intervals.
        """
        try:
            now = time.time()
            
            if analysis_type == "order_book":
                interval = self.cfg.get("order_book_flow_update_interval_s", 1)
                last_update = self._last_order_book_flow_update.get(coin, 0.0)
            else:  # flow_imbalance
                interval = self.cfg.get("flow_imbalance_update_interval_s", 5)
                last_update = self._last_flow_imbalance_update.get(coin, 0.0)
            
            if (now - last_update) >= interval:
                # Update the timestamp
                if analysis_type == "order_book":
                    self._last_order_book_flow_update[coin] = now
                else:
                    self._last_flow_imbalance_update[coin] = now
                return True
            
            return False
            
        except Exception:
            return True  # Default to updating if check fails

    def _should_adjust_quotes_for_flow(self, coin: str, side: str, base_price: float, tick: float) -> float:
        """
        Adjust quote prices based on order book flow analysis.
        Returns adjusted price.
        """
        try:
            flow_signals = self._get_order_book_flow_signals(coin)
            if not flow_signals:
                return base_price
            
            # Get flow-based adjustments
            bid_strength = flow_signals.get("bid_strength", 0.0)
            ask_strength = flow_signals.get("ask_strength", 0.0)
            confidence = flow_signals.get("confidence", 0.0)
            
            # Only adjust if confidence is high enough
            if confidence < 0.3:
                return base_price
            
            # Calculate adjustment in ticks
            max_adjustment_ticks = 2  # Maximum 2 tick adjustment
            adjustment_ticks = confidence * max_adjustment_ticks
            
            if side == "B":  # Bid side
                if bid_strength > 0.1:  # Strong bid flow
                    # Be more aggressive (higher bid)
                    return base_price + (adjustment_ticks * tick)
                elif ask_strength > 0.1:  # Strong ask flow
                    # Be less aggressive (lower bid)
                    return base_price - (adjustment_ticks * tick)
                    
            elif side == "A":  # Ask side
                if ask_strength > 0.1:  # Strong ask flow
                    # Be more aggressive (lower ask)
                    return base_price - (adjustment_ticks * tick)
                elif bid_strength > 0.1:  # Strong bid flow
                    # Be less aggressive (higher ask)
                    return base_price + (adjustment_ticks * tick)
            
            return base_price
            
        except Exception as e:
            self.log({"type": "warn", "op": "flow_price_adjustment", "coin": coin, "side": side, "msg": str(e)})
            return base_price

    def _current_spread_bps(self, coin: str) -> float:
        """Compute current spread in basis points using freshest BBO."""
        try:
            bb, ba = self.client.best_bid_ask(coin)
            if bb > 0.0 and ba > 0.0:
                mid = 0.5 * (bb + ba)
                if mid > 0:
                    return (ba - bb) / mid * 10000.0
        except Exception:
            pass
        return 0.0


    # ---------- bias state machine helpers ----------

    def _bias_cfg(self, coin: str) -> Optional[str]:
        """Return configured bias side: 'long'|'short' or None if disabled/unset."""
        if not bool(self._c(coin, "bias_enabled", self.cfg.get("bias_enabled", True))):
            return None
        side = self._c(coin, "bias_side", None)
        if not side:
            return None
        s = str(side).lower()
        if s.startswith("l"):
            return "long"
        if s.startswith("s"):
            return "short"
        return None

    def _target_units(self, coin: str, mid: float) -> float:
        """Compute target position units from USD target; quantize to sz_step."""
        tgt_usd = float(self._c(coin, "target_inventory_usd", self.cfg.get("target_inventory_usd_default", 0.0)) or 0.0)
        if tgt_usd <= 0 or mid <= 0:
            return 0.0
        try:
            step = float(self.client.sz_step(coin))
        except Exception:
            step = 0.0
        units = tgt_usd / max(1e-12, mid)
        if step > 0:
            units = math.floor(units / step) * step
        return max(0.0, units)

    def _apply_bias_desired_side(self, coin: str) -> Optional[str]:
        """
        If bias is configured, pick the operate side based on accumulate vs distribute:
          - bias=long: accumulate with bids until target; then distribute with asks.
          - bias=short: accumulate with asks until target short; then distribute with bids.
        Returns 'B'|'A' or None to let other logic decide.
        """
        bias = self._bias_cfg(coin)
        if bias is None:
            return None
        try:
            mid = float(self.mark_mid(coin))
        except Exception:
            mid = 0.0
        tgt_units = self._target_units(coin, mid)
        st = self.coin_state(coin)
        if tgt_units <= 0:
            # If no target, prefer exit side for any existing exposure
            if st.pos > 0:
                return "A"
            if st.pos < 0:
                return "B"
            # no exposure and no target → defer
            return None

        band = float(self._c(coin, "inv_band_pct", self.cfg.get("inv_band_pct_default", 0.25)))

        if bias == "long":
            # want +tgt_units; if below (1-band)*tgt → accumulate with bids, else distribute with asks
            if float(st.pos) < tgt_units * (1.0 - band):
                return "B"  # build toward target
            else:
                return "A"  # work out inventory at a rebate
        else:
            # bias short → negative target; if above -(1-band)*tgt → accumulate with asks, else distribute with bids
            if float(st.pos) > -tgt_units * (1.0 - band):
                return "A"  # build short passively
            else:
                return "B"  # work bids to buy back at rebate

    # ---------- bailout helpers ----------

    def _unreal_bps(self, coin: str, mid: float) -> float:
        """Return MAE in bps relative to avg_entry, positive when underwater."""
        st = self.coin_state(coin)
        if mid <= 0 or abs(st.pos) <= 0:
            return 0.0
        avg = float(st.avg_entry or 0.0)
        if avg <= 0:
            return 0.0
        if st.pos > 0:
            # long underwater when mid < avg
            mae = max(0.0, (avg - mid) / avg * 10000.0)
        else:
            # short underwater when mid > avg
            mae = max(0.0, (mid - avg) / avg * 10000.0)
        return float(mae)

    def _reduce_position_ioc(self, coin: str, frac: float, slippage_bps: float = None) -> None:
        """Reduce position immediately using IOC (reduce-only)."""
        try:
            now = time.time()
            gap = float(self.cfg.get("ioc_throttle_s", 0.5))
            last = float(self._last_ioc_ts.get(coin, 0.0) or 0.0)
            if (now - last) < gap:
                return
            self._last_ioc_ts[coin] = now
        except Exception:
            pass

        try:
            st = self.coin_state(coin)
            if abs(st.pos) <= 0:
                return
            bb, ba = self.client.best_bid_ask(coin)
            mid = 0.5 * (bb + ba) if (bb > 0 and ba > 0) else None
            if mid is None or mid <= 0:
                return
            step = float(self.client.sz_step(coin))
            reduce_units = max(0.0, abs(st.pos) * max(0.0, min(1.0, float(frac))))
            if step > 0:
                reduce_units = math.floor(reduce_units / step) * step
            if reduce_units <= 0:
                return
            slip = float(slippage_bps if slippage_bps is not None else self.cfg.get("flatten_max_slippage_bps", 6))
            if st.pos > 0:
                # sell to reduce long
                px = bb * (1.0 - slip / 10000.0)
                try:
                    self.client.place_ioc(coin, is_buy=False, sz=reduce_units, px=px, reduce_only=True)
                except TypeError:
                    self.client.place_ioc(coin, is_buy=False, sz=reduce_units, px=px)
            else:
                # buy to reduce short
                px = ba * (1.0 + slip / 10000.0)
                try:
                    self.client.place_ioc(coin, is_buy=True, sz=reduce_units, px=px, reduce_only=True)
                except TypeError:
                    self.client.place_ioc(coin, is_buy=True, sz=reduce_units, px=px)
        except Exception as e:
            try:
                self.log({"type": "warn", "op": "reduce_position_ioc", "coin": coin, "msg": str(e)})
            except Exception:
                pass

    def _maybe_bail(self, coin: str) -> None:
        """Partial/Full bailout when MAE/time thresholds breach. Maker-first, taker-second."""
        try:
            bb, ba = self.client.best_bid_ask(coin)
            if not (bb and ba and bb > 0 and ba > 0):
                return
            mid = 0.5 * (bb + ba)
            st = self.coin_state(coin)
            if abs(st.pos) <= 0:
                # reset timers when flat
                self._underwater_since.pop(coin, None)
                return

            mae_bps = self._unreal_bps(coin, mid)
            self._last_unrealized_bps[coin] = mae_bps

            if mae_bps <= 0.0:
                self._underwater_since.pop(coin, None)
                return

            now = time.time()
            since = self._underwater_since.get(coin)
            if since is None:
                self._underwater_since[coin] = now
                return
            elapsed = now - since

            partial_bps = float(self._c(coin, "bail_partial_mae_bps", self.cfg.get("bail_partial_mae_bps", 30.0)))
            full_bps    = float(self._c(coin, "bail_full_mae_bps", self.cfg.get("bail_full_mae_bps", 60.0)))
            t_under     = float(self._c(coin, "bail_underwater_time_s", self.cfg.get("bail_underwater_time_s", 180)))
            work_time   = float(self._c(coin, "bail_work_time_s", self.cfg.get("bail_work_time_s", 90)))

            # If deeply underwater or for too long → full exit now
            if mae_bps >= full_bps or elapsed >= t_under:
                self._reduce_position_ioc(coin, frac=1.0)
                # pause quoting immediately
                self._single_side_choice[coin] = "N"
                self._single_side_last_loop[coin] = int(getattr(self, "loop_i", 0) or 0)
                return

            # Moderately underwater and not improving after work_time → partial reduce
            if mae_bps >= partial_bps and elapsed >= work_time:
                self._reduce_position_ioc(coin, frac=0.33)
        except Exception:
            pass


    def realized_on_close(self, state: CoinState, side: str, px: float, sz: float) -> float:
        """
        Update position/avg_entry and return realized PnL for the portion that closes.
        side: 'B' for buy, 'A' for sell
        """
        q0 = state.pos
        avg = state.avg_entry
        realized = 0.0

        if side == "A" and q0 > 0:  # selling long
            reduce = min(sz, q0)
            realized += reduce * (px - avg)
            state.pos = q0 - reduce
            if state.pos == 0:
                state.avg_entry = 0.0
        elif side == "B" and q0 < 0:  # buying back short
            reduce = min(sz, -q0)
            realized += reduce * (avg - px)
            state.pos = q0 + reduce
            if state.pos == 0:
                state.avg_entry = 0.0
        else:
            # opening / increasing exposure: update VWAP avg entry
            signed = sz if side == "B" else -sz
            new_pos = q0 + signed
            w = abs(q0) + sz
            new_avg = 0.0 if w == 0 else (abs(q0) * avg + sz * px) / w
            state.pos = new_pos
            state.avg_entry = new_avg
        return realized

    # ---------- margin and tick helpers ----------
    def _effective_tick(self, coin: str, best_bid: float, best_ask: float) -> float:
        """
        Choose a safe tick for order prices.
        - Prefer the *coarser* of (meta tick, observed decimal power-of-10 tick).
        - Allow a per-coin override via cfg[per_coin][coin]["px_decimals_override"].
        """
        try:
            tick_meta = d(self.client.px_step(coin))
        except Exception:
            tick_meta = d("0")

        def _pow10_tick(x: float) -> Decimal:
            try:
                # Convert number of decimal places to a 10^-n tick
                n = 0
                t = Decimal(str(x)).normalize().as_tuple().exponent
                n = abs(t) if t < 0 else 0
                # Guard rails: keep reasonable range [0, 8]
                n = max(0, min(8, n))
                return d(1).scaleb(-n)
            except Exception:
                return d("0")

        tick_obs_bid = _pow10_tick(best_bid)
        tick_obs_ask = _pow10_tick(best_ask)
        tick_obs = min(tick_obs_bid, tick_obs_ask) if (tick_obs_bid > 0 and tick_obs_ask > 0) else max(tick_obs_bid, tick_obs_ask)

        # Use the COARSER tick to avoid exchange rejections
        tick = tick_obs if tick_meta <= 0 else max(tick_meta, tick_obs)

        # Optional explicit override
        override_dec = self._c(coin, "px_decimals_override", None)
        if override_dec is not None:
            try:
                tick = d(1).scaleb(-int(override_dec))
            except Exception:
                pass

        if tick <= 0:
            tick = d("0.0001")  # final guard for tiny-price coins
        return float(tick)

    def _free_collateral(self) -> Optional[float]:
        """
        Try several likely fields to get free collateral from user_state.
        Return a float when available, otherwise None. Falls back to fc_hint_usd if set.
        Logs fc_probe only when value changes; can be disabled with cfg["fc_probe_log"] = False.
        """
        try:
            u = self.client.info.user_state(self.client.addr)
        except Exception as e:
            self.log({"type": "warn", "op": "user_state", "msg": str(e)})
            return None

        def _as_float(v):
            try:
                return float(v)
            except Exception:
                return None

        log_fc = self.cfg.get("fc_probe_log", True)

        # top level keys that may exist
        for key in ("freeCollateral", "withdrawable", "free_collateral", "availableBalance", "available_balance"):
            if key in u:
                val = _as_float(u.get(key))
                if val is not None:
                    if log_fc:
                        self._log_fc(key, val)
                    return val

        # nested containers we have seen
        for parent in ("marginSummary", "summary", "account"):
            dct = u.get(parent, {})
            if isinstance(dct, dict):
                for key in ("freeCollateral", "withdrawable", "availableBalance"):
                    if key in dct:
                        val = _as_float(dct.get(key))
                        if val is not None:
                            if log_fc:
                                self._log_fc(f"{parent}.{key}", val)
                            return val

        # fallback to hint if provided
        hint = _as_float(self.cfg.get("fc_hint_usd", 0.0))
        if hint and hint > 0.0:
            if log_fc:
                self._log_fc("fc_hint_usd", hint)
            return hint

        return None

    def _cap_size_by_margin(self, coin: str, mid: float, desired_units: float) -> float:
        """
        Margin-aware sizing. Returns units clipped to a share of free collateral (FC).
        Modes:
          - auto   : if FC is missing/zero on testnet, bypass cap; otherwise cap.
          - off    : never cap (use desired_units).
          - strict : if FC missing/zero, size to 0 to avoid rejections.
        Also supports fc_hint_usd to override FC when API lacks the field.
        """
        mode = str(self.cfg.get("mode", "testnet")).lower()
        cap_mode = str(self.cfg.get("margin_cap_mode", "auto")).lower()

        # size step
        try:
            step = float(self.client.sz_step(coin))
        except Exception:
            step = 0.0

        # short-circuit for explicit off
        if cap_mode == "off":
            return desired_units

        fc = self._free_collateral()
        # allow a hint override when API reports 0/None
        if fc is None or fc <= 0.0:
            hint = float(self.cfg.get("fc_hint_usd", 0.0))
            if hint > 0.0:
                fc = hint
            elif cap_mode == "auto" and mode == "testnet":
                # On testnet, let exchange risk checks be the backstop
                return desired_units
            elif cap_mode == "strict":
                # Refuse to size without FC info
                return 0.0
            else:
                # default strict-ish behavior on mainnet without hint
                return 0.0

        lev = float(self.cfg.get("assumed_leverage", 10.0))
        frac = float(self.cfg.get("margin_cap_fraction", 0.5))
        n_coins = max(1, len(self.cfg.get("coins", [])))
        max_orders = n_coins * 2  # one bid + one ask per coin

        # Budget part of FC across all resting orders
        budget = max(0.0, fc * max(0.0, min(1.0, frac)) / max_orders)
        max_notional = budget * lev

        cur_notional = desired_units * max(mid, 1e-12)
        if cur_notional <= max_notional:
            return desired_units

        scale = max_notional / max(1e-12, cur_notional)
        units = desired_units * scale

        # quantize DOWN to avoid exceeding cap post-quantization
        if step > 0:
            units = math.floor(units / step) * step
        return max(0.0, units)

    # ---------- price logic helpers ----------

    def _maybe_improve(self, side: str, coin: str, px: float,
                       best_bid: float, best_ask: float, tick: float) -> float:
        """If the touch is stale for N loops and we are joined, improve by 1 tick (still ALO)."""
        if not self._c(coin, "improve_one_tick", True):
            return px

        key = (coin, side)
        touch = best_bid if side == "B" else best_ask
        last_touch = self.touch_px.get(key)

        if last_touch is None or abs(touch - last_touch) > 1e-12:
            self.touch_px[key] = touch
            self.stale_count[key] = 0
            return px

        self.stale_count[key] = self.stale_count.get(key, 0) + 1
        thresh = int(self._c(coin, "improve_stale_ticks", 6))
        if self.stale_count[key] < thresh:
            return px

        joined = abs(px - touch) <= (tick * 1.1)
        if not joined:
            return px

        spread = best_ask - best_bid
        if spread < 2.0 * tick:  # one-tick spread: improving would cross
            return px

        if side == "B":
            return min(best_ask - tick, max(best_bid + tick, px + tick))
        else:
            return max(best_bid + tick, min(best_ask - tick, px - tick))

    # ---------- placing + logging ----------

    def _log_lifecycle(self, coin: str, side: str, size: float, price: float, status: str, order_id: str = ""):
        insert_lifecycle(
            self.db,
            {
                "wallet": self.wallet,
                "order_id": order_id,
                "coin": coin,
                "side": side,
                "size": float(size),
                "price": float(price),
                "order_type": "LIMIT",
                "status": status,
                "timestamp": int(time.time() * 1000),
                "client_id": None,
                "inserted_at": datetime.datetime.utcnow().isoformat(),
                "bot_id": self.bot_id,
            },
        )
    
    def _log_fc(self, source: str, val: float):
        if not self.cfg.get("telemetry_enabled", True) or not self.cfg.get("telemetry_console", True):
            return
        if not self.cfg.get("fc_probe_log", True):
            return
        try:
            prev = getattr(self, "_last_fc_logged", None)
            if prev is None or abs(float(val) - float(prev)) / max(float(prev or 1.0), 1.0) >= 0.01:
                self._last_fc_logged = float(val)
                self.log({"type": "fc_probe", "source": source, "val": float(val)})
        except Exception:
            pass

    def _process_fill(self, fill_data: Dict[str, Any]):
        """Process a fill and log maker/taker information to console."""
        try:
            # Extract fill information
            coin = fill_data.get("coin", "")
            side = fill_data.get("side", "")
            price = fill_data.get("price", 0)
            size = fill_data.get("size", 0)
            is_maker = fill_data.get("is_maker", 0)
            fee = fill_data.get("fee", 0)
            notional = price * size if price and size else 0
            
            # Determine fill type
            fill_type = "MAKER" if is_maker == 1 else "TAKER"
            
            # Log to console with maker/taker info
            self._tlog({
                "type": "fill",
                "coin": coin,
                "side": side,
                "fill_type": fill_type,
                "price": price,
                "size": size,
                "notional_usd": round(notional, 2),
                "fee": fee,
                "fee_bps": round((fee / notional * 10000), 2) if notional > 0 else 0
            })
            
            # Update minute counters
            if is_maker == 1:
                self.maker_fills_min += 1
            else:
                self.taker_fills_min += 1

            # Update decayed flow metrics for market-aware selector
            try:
                self._ma_record_fill(coin, is_maker == 1, side)
            except Exception:
                pass

            # Update realized PnL and fees
            if fill_data.get("realized_pnl") is not None:
                self.realized_min += float(fill_data.get("realized_pnl", 0))
            if fee is not None:
                self.net_fees_min += float(fee)

            # Per-coin minute accumulators
            try:
                coin_key = str(coin)
                self.maker_fills_min_by_coin[coin_key] = self.maker_fills_min_by_coin.get(coin_key, 0) + (1 if is_maker == 1 else 0)
                self.taker_fills_min_by_coin[coin_key] = self.taker_fills_min_by_coin.get(coin_key, 0) + (0 if is_maker == 1 else 1)
                self.realized_min_by_coin[coin_key] = float(self.realized_min_by_coin.get(coin_key, 0.0)) + float(fill_data.get("realized_pnl", 0.0) or 0.0)
                self.net_fees_min_by_coin[coin_key] = float(self.net_fees_min_by_coin.get(coin_key, 0.0)) + float(fee or 0.0)
            except Exception:
                pass
                
            # Store in database with console logging enabled
            if self.cfg.get("telemetry_db", True) and self.cfg.get("fills_log_enabled", True):
                insert_fill(self.db, fill_data, log_to_console=False)
            
        except Exception as e:
            self.log({"type": "error", "msg": f"Error processing fill: {e}"})

    def log_fill(self, coin: str, side: str, price: float, size: float, is_maker: bool, fee: float = 0.0, **kwargs):
        """Convenience method to log a fill with maker/taker info."""
        fill_data = {
            "wallet": self.wallet,
            "coin": coin,
            "side": side,
            "price": price,
            "size": size,
            "is_maker": 1 if is_maker else 0,
            "fee": fee,
            "t_fill_ms": int(time.time() * 1000),
            "inserted_at": datetime.datetime.utcnow().isoformat(),
            "bot_id": self.bot_id,
            **kwargs
        }
        self._process_fill(fill_data)

    def _on_user_fill(self, f: Dict[str, Any]):
        """Translate a WsFill dict to our internal fill record and persist it."""
        try:
            coin = f.get("coin")
            if not coin:
                return
            # Parse numerics
            def _to_float(x, default=0.0):
                try:
                    return float(x)
                except Exception:
                    return float(default)
            px = _to_float(f.get("px"))
            sz = _to_float(f.get("sz"))
            fee = _to_float(f.get("fee"))
            closed_pnl = _to_float(f.get("closedPnl"))
            t_ms = int(f.get("time", int(time.time() * 1000)))

            # Side normalization: accept "B"/"A" or "Buy"/"Sell"
            side_raw = str(f.get("side", "")).strip().upper()
            if side_raw in ("B", "BUY"):
                side = "B"
            elif side_raw in ("A", "SELL"):
                side = "A"
            else:
                side = "B" if sz >= 0 else "A"

            crossed = bool(f.get("crossed", False))
            is_maker = not crossed

            # Delegate to our common path (adds wallet/bot_id and persists)
            self.log_fill(
                coin=coin,
                side=side,
                price=px,
                size=abs(sz),
                is_maker=is_maker,
                fee=fee,
                realized_pnl=closed_pnl,
                crossed=crossed,
                oid=f.get("oid"),
                tid=f.get("tid"),
                dir=f.get("dir"),
                fee_token=f.get("feeToken")
            )
        except Exception as e:
            self.log({"type": "error", "msg": f"Error in _on_user_fill: {e}"})

    def _on_market_data_update(self, market_data: Dict[str, Any]):
        """Real-time market data callback - place orders immediately when data arrives."""
        try:
            # Extract coin from market data (now included by WebSocket callback)
            coin = market_data.get("coin")
            if not coin or coin not in self.cfg.get("coins", []):
                return
            
            best_bid = market_data.get("best_bid", 0.0)
            best_ask = market_data.get("best_ask", 0.0)
            
            if best_bid <= 0 or best_ask <= 0:
                return
            
            # Place orders immediately for this coin
            self._place_orders_for_coin_realtime(coin, best_bid, best_ask)
            
        except Exception as e:
            self.log({"type": "error", "op": "market_data_callback", "msg": f"Error in market data callback: {e}"})
            
    
    def _c(self, coin: Optional[str], key: str, default=None):
        """Config lookup with per-coin override: per_coin[coin][key] > cfg[key] > default."""
        try:
            if coin:
                per = self.cfg.get("per_coin", {})
                if isinstance(per, dict):
                    ov = per.get(coin, {})
                    if isinstance(ov, dict) and key in ov:
                        return ov.get(key, default)
        except Exception:
            pass
        return self.cfg.get(key, default)

    def _cf(self, coin: Optional[str], key: str, default: float = 0.0) -> float:
        v = self._c(coin, key, default)
        try:
            return float(v)
        except Exception:
            return float(default)

    def _get_coin_config_snapshot(self, coin: str) -> Dict[str, Any]:
        """Get a complete config snapshot for a specific coin."""
        # Get global config (excluding per_coin section)
        global_config = {k: v for k, v in self.cfg.items() 
                        if k not in ["per_coin", "config_version"]}
        
        # Get per-coin config
        per_coin = self.cfg.get("per_coin", {})
        coin_config = per_coin.get(coin, {})
        
        # Combine global and coin-specific config
        snapshot = {**global_config, **coin_config}
        snapshot["config_version"] = coin_config.get("config_version", self.cfg.get("config_version", "1.0.0"))
        snapshot["coin"] = coin
        
        return snapshot

    def _place_post_only_quantized(self, side: str, coin: str, price: float, size: float,
                                   best_bid: float, best_ask: float, adaptive_tick: float = None):
        """
        Quantize price/size to steps; ensure ALO won't cross; send order.
        Records oid for cancel/replace.
        """
        # Single-sided gate: skip placing orders on the blocked side and cancel any strays
        try:
            if not self._single_sided_allowed(coin, side):
                # Proactively cancel any resting orders on this blocked side
                self._cancel_side_for_coin(coin, side)
                # Lifecycle log for visibility
                try:
                    self._log_lifecycle(coin, side, float(size), float(price), "SKIP:SINGLE_SIDED_BLOCK")
                except Exception:
                    pass
                return
        except Exception:
            # If gating logic fails, fall back to normal behavior
            pass

        # Quantize price and size to exchange steps
        tick = self._effective_tick(coin, best_bid, best_ask) if adaptive_tick is None else adaptive_tick
        step = float(self.client.sz_step(coin))
        
        # Quantize price to tick size
        if side == "B":  # Bid - quantize down
            px_q = quantize_down(d(price), d(tick))
        else:  # Ask - quantize up
            px_q = quantize_up(d(price), d(tick))
        
        # Quantize size to size step
        sz_q = quantize_down(d(size), d(step))
        
        # Safety checks
        if sz_q <= 0 or px_q <= 0:
            return
        
        # Convert to float for API
        px_f = as_float_8dp(px_q)
        sz_f = as_float_8dp(sz_q)
        
        # Determine buy/sell
        is_buy = (side == "B")
        
        try:
            # Place the order using IOC
            result = self.client.place_ioc(coin, is_buy, sz_f, px_f, reduce_only=False)
            
            # Log the order placement
            self._log_lifecycle(coin, side, sz_f, px_f, "IOC_SENT")
            
            # Check for rate limiting
            if result == "RATE_LIMITED":
                self.log({"type": "warn", "op": "order_rate_limited", "coin": coin, "side": side})
                return
            
            # Check for other errors
            if isinstance(result, str) and "error" in result.lower():
                self.log({"type": "error", "op": "order_error", "coin": coin, "side": side, "msg": result})
                return
            
            # Log successful placement
            print(f"🚀 ORDER PLACED: {coin} {side} {sz_f} @ {px_f}")
            self._tlog({
                "type": "order_placed",
                "coin": coin,
                "side": side,
                "price": px_f,
                "size": sz_f,
                "result": result
            })
            
        except Exception as e:
            self.log({"type": "error", "op": "order_exception", "coin": coin, "side": side, "msg": str(e)})

    def _place_orders_for_coin_realtime(self, coin: str, best_bid: float, best_ask: float):
        """Real-time order placement for a single coin - called immediately when market data arrives."""
        try:
            # Skip if market data is invalid
            if best_bid <= 0 or best_ask <= 0:
                print(f"❌ Invalid market data for {coin}: bid={best_bid}, ask={best_ask}")
                return
            
            spread = best_ask - best_bid
            if spread <= 0:
                return
            
            tick = self._effective_tick(coin, best_bid, best_ask)
            step = float(self.client.sz_step(coin))
            mid = 0.5 * (best_bid + best_ask)
            spread_bps_live = (spread / max(mid, 1e-12)) * 10_000.0
            
            # min-spread gate (bps) with dynamic floor
            base_min_spread_bps = self._cf(coin, "min_spread_bps", 0.0)
            eff_min_spread_bps = base_min_spread_bps
            dyn = self._dyn_min_spread.get(coin)
            if dyn is not None:
                eff_min_spread_bps = max(eff_min_spread_bps, float(dyn))
            if eff_min_spread_bps > 0.0 and spread_bps_live < eff_min_spread_bps:
                print(f"❌ Spread too tight for {coin}: {spread_bps_live:.1f}bps < {eff_min_spread_bps}bps")
                return  # skip this coin this update
            
            # desired size (USD -> units), then round UP to size step
            target_notional = self._cf(coin, "size_notional_usd", 100.0)
            raw_units = max(1e-12, target_notional / max(mid, 1e-12))
            size_units = round_up_units(raw_units, step)
            
            # margin cap
            size_units = self._cap_size_by_margin(coin, mid, size_units)
            if size_units <= 0 or size_units < step:
                print(f"❌ Size too small for {coin}: {size_units} <= {step}")
                return
            
            # enforce exchange minimum notional (e.g., $10)
            min_usd = self._cf(coin, "exchange_min_order_usd", 10.0)
            if mid * size_units < min_usd:
                bump_units = round_up_units(min_usd / max(mid, 1e-12), step)
                bumped = self._cap_size_by_margin(coin, mid, bump_units)
                if bumped >= step and (bumped * mid) >= min_usd:
                    size_units = bumped
                else:
                    return
            
            # current per-coin notional & position management
            st = self.coin_state(coin)
            notional = abs(st.pos) * mid
            
            # risk caps
            max_coin_cap = float(self.cfg.get("max_per_coin_notional", 300.0))
            max_gross_cap = float(self.cfg.get("max_gross_notional", 600.0))
            
            # Calculate current gross exposure
            gross = 0.0
            try:
                for c, s in self.state.items():
                    gross += abs(s.pos) * self.mark_mid(c)
            except Exception:
                pass
            
            # Take profit on large profitable positions first
            if self._maybe_take_profit(coin):
                print(f"💰 Taking profit on {coin}, skipping new orders")
                return  # Position was closed, skip placing new orders
            
            # Safety flatten if position too large
            self.flatten_if_needed(coin, mid)
            
            # Enhanced bailout check for underwater positions
            if self._enhanced_bailout_check(coin):
                print(f"🚨 Bailing out {coin}, skipping new orders")
                return  # Position was bailed out, skip placing new orders
            
            # Join or improve inside the spread with a 1-tick cushion when possible.
            if spread >= 2.0 * tick:
                # Improve by 1 tick but never cross the opposite touch
                bid_px = min(best_ask - tick, best_bid + tick)
                ask_px = max(best_bid + tick, best_ask - tick)
            else:
                # One-tick spread: just join at the touch to stay ALO-safe
                bid_px = max(best_bid, 0.0001)
                ask_px = best_ask
            
            # Additional safety: ensure prices are reasonable
            if bid_px <= 0 or ask_px <= 0:
                return
            
            # Sanity check: ensure prices are within reasonable bounds of market
            if bid_px > best_ask or ask_px < best_bid:
                return
            
            # inventory skew
            inv_skew_ticks = int(self._c(coin, "inventory_skew_ticks", 0))
            if inv_skew_ticks > 0 and coin not in ['PENGU', 'BIO']:
                if st.pos > 0:  # long -> worsen bid
                    bid_px = max(bid_px - inv_skew_ticks * tick, 0.0001)
                elif st.pos < 0:  # short -> worsen ask
                    ask_px = ask_px + inv_skew_ticks * tick
            
            # join-then-improve if touch is stale (use adaptive tick size)
            if coin not in ['PENGU', 'BIO']:
                bid_px = self._maybe_improve("B", coin, bid_px, best_bid, best_ask, tick)
                ask_px = self._maybe_improve("A", coin, ask_px, best_bid, best_ask, tick)
            
            # Adjust prices based on order book flow analysis
            bid_px = self._should_adjust_quotes_for_flow(coin, "B", bid_px, tick)
            ask_px = self._should_adjust_quotes_for_flow(coin, "A", ask_px, tick)
            
            # Check single-sided mode and get optimal prices
            allowed_side = self._get_single_side(coin)
            
            # Place orders based on single-sided mode with optimal pricing
            if allowed_side == "B" and notional < max_coin_cap and gross < max_gross_cap:
                # Use market-aware logic for optimal bid price
                optimal_bid_px = self._get_optimal_single_side_price(coin, "B", bid_px, tick, best_bid, best_ask)
                self._place_single_order_realtime(coin, "B", optimal_bid_px, size_units, best_bid, best_ask)
                
            elif allowed_side == "A" and notional < max_coin_cap and gross < max_gross_cap:
                # Use market-aware logic for optimal ask price
                optimal_ask_px = self._get_optimal_single_side_price(coin, "A", ask_px, tick, best_bid, best_ask)
                self._place_single_order_realtime(coin, "A", optimal_ask_px, size_units, best_bid, best_ask)
                
            elif allowed_side == "N":
                # No quote state - don't place any orders
                pass
                
            elif allowed_side is None:
                # Single-sided mode is off - place both sides
                if notional < max_coin_cap and gross < max_gross_cap:
                    print(f"📈 Placing orders for {coin}: bid={bid_px:.4f}, ask={ask_px:.4f}, size={size_units}")
                    self._place_single_order_realtime(coin, "B", bid_px, size_units, best_bid, best_ask)
                    self._place_single_order_realtime(coin, "A", ask_px, size_units, best_bid, best_ask)
                else:
                    print(f"❌ Caps exceeded for {coin}: notional={notional:.1f}/{max_coin_cap}, gross={gross:.1f}/{max_gross_cap}")
                    
        except Exception as e:
            self.log({"type": "error", "op": "realtime_order_placement", "coin": coin, "msg": str(e)})

    def _place_single_order_realtime(self, coin: str, side: str, price: float, size: float, best_bid: float, best_ask: float):
        """Place a single order in real-time with minimal latency."""
        try:
            # Place the order immediately using IOC
            self._place_post_only_quantized(side, coin, price, size, best_bid, best_ask)
            
            # Log successful placement
            self._tlog({
                "type": "order", 
                "coin": coin, 
                "side": side, 
                "price": price, 
                "size": size, 
                "status": "placed_realtime",
                "latency": "sub_ms"
            })
            
        except Exception as e:
            self.log({"type": "error", "op": "single_order_realtime", "coin": coin, "side": side, "msg": str(e)})

    def place(self, side: str, coin: str, price: float, size: float, best_bid: float, best_ask: float, adaptive_tick: float = None):
        # Place the new quote (quantized, ALO-safe) - IOC orders don't need cancellation
        self._place_post_only_quantized(side, coin, price, size, best_bid, best_ask, adaptive_tick)

    # ---------- flatten logic (IOC, reduce-only) ----------

    def _flatten_position_immediate(self, coin: str, signed_pos: float, mid: float):
        """
        Reduce-only IOC to take position to zero, with quantization and safety guards.
        """
        if abs(signed_pos) < 1e-12:
            return

        best_bid, best_ask = self.client.best_bid_ask(coin)
        tick = d(self._effective_tick(coin, best_bid, best_ask))
        step = d(self.client.sz_step(coin))

        # Guards
        spread = best_ask - best_bid
        mid_live = 0.5 * (best_bid + best_ask)
        if best_bid <= 0 or best_ask <= 0 or spread <= 0:
            self.log({"type": "warn", "op": "flatten", "coin": coin, "msg": f"skip: bad book bid={best_bid} ask={best_ask}"})
            return

        max_spread_bps = self._cf(coin, "flatten_max_spread_bps", 15.0)
        live_spread_bps = (spread / max(mid_live, 1e-12)) * 10_000.0
        if live_spread_bps > max_spread_bps:
            self.log({"type":"warn","op":"flatten","coin":coin,"msg":f"skip: spread {live_spread_bps:.2f}bps > {max_spread_bps}bps"})
            return

        # Bounded slippage from mid
        max_slip_bps = self._cf(coin, "flatten_max_slippage_bps", 10.0)
        down_guard = d(mid_live) * (d(1) - d(max_slip_bps) / d(10_000))  # worst sell price
        up_guard   = d(mid_live) * (d(1) + d(max_slip_bps) / d(10_000))  # worst buy price

        # Direction & price bounds (IOC will walk the book but not past our guard)
        szi = d(signed_pos).copy_abs()
        if signed_pos > 0:  # long -> SELL
            limit_px = quantize_down(max(d(best_bid), down_guard), tick)   # never worse than guard
            is_buy = False
            side = "A"
        else:               # short -> BUY
            limit_px = quantize_up(min(d(best_ask), up_guard), tick)       # never worse than guard
            is_buy = True
            side = "B"

        # Chunking to avoid sweeping too deep
        chunk_usd = self._cf(coin, "flatten_chunk_usd", 1000.0)
        min_usd = max(10.0, self._cf(coin, "exchange_min_order_usd", 10.0))
        chunk_usd = max(min_usd, chunk_usd)
        chunks = []
        remaining = szi
        while remaining > 0:
            units_for_chunk = d(chunk_usd) / max(d(mid_live), d("1e-12"))
            units_for_chunk = quantize_down(units_for_chunk, step)
            if units_for_chunk <= 0:
                break
            use = min(remaining, units_for_chunk)
            chunks.append(use)
            remaining -= use

        if not chunks:
            return

        for use in chunks:
            px_q = limit_px
            sz_q = quantize_down(use, step)  # reduce-only sizing: quantize DOWN

            if sz_q <= 0:
                continue

            px_f = as_float_8dp(px_q)
            sz_f = as_float_8dp(sz_q)

            try:
                if self._c(coin, "flatten_reduce_only", True):
                    try:
                        # Primary path: pass reduce_only if the SDK supports it
                        _ = self.client.place_ioc(coin, is_buy, sz_f, px_f, reduce_only=True)
                    except TypeError:
                        # SDK does not accept reduce_only; retry without it
                        _ = self.client.place_ioc(coin, is_buy, sz_f, px_f)
                else:
                    _ = self.client.place_ioc(coin, is_buy, sz_f, px_f)
                self._log_lifecycle(coin, side, sz_f, px_f, "IOC_SENT")
            except Exception as e:
                self._log_lifecycle(coin, side, float(use), float(mid_live), f"IOC_ERROR:{e}")
                self.log({"type": "error", "op": "flatten", "coin": coin, "msg": str(e)})
                break

    def flatten_if_needed(self, coin: str, mid: float):
        max_coin = self._cf(coin, "max_per_coin_notional", 100.0)
        state = self.coin_state(coin)
        notional = abs(state.pos) * mid
        if notional <= max_coin * 1.2 or state.pos == 0:
            return
        self._flatten_position_immediate(coin, state.pos, mid)

    # ---------- replacement policy / housekeeping ----------

    def _place_batch_orders(self, orders: list) -> list:
        """
        Place multiple orders in a batch to reduce API calls.
        orders = [{"side": "B", "coin": "ETH", "price": 2500.0, "size": 0.01}, ...]
        """
        if not orders:
            return []
            
        # Convert to client batch format
        batch_orders = []
        for order in orders:
            batch_orders.append({
                "coin": order["coin"],
                "is_buy": order["side"] == "B",
                "sz": order["size"],
                "px": order["price"],
                "reduce_only": False
            })
        
        # Use client batch ordering
        try:
            results = self.client.place_batch_orders(batch_orders)
            return results
        except Exception as e:
            self.log({"type": "error", "op": "batch_order", "msg": str(e)})
            return [{"error": str(e)}] * len(orders)
    
    def _batch_cancel_existing_orders(self):
        """Cancel all existing orders in batch to reduce API calls.
        Note: With IOC orders, this method is no longer used but kept for potential future use.
        """
        # This method is deprecated since IOC orders don't rest
        pass

    def _cancel_all_orders_for_coin(self, coin: str):
        """Cancel all existing orders for a coin efficiently.
        Note: With IOC orders, this method is no longer used but kept for potential future use.
        """
        # This method is deprecated since IOC orders don't rest
        pass

    def _should_replace(self, side: str, coin: str, target_px: float, tick: float) -> bool:
        """Always place IOC orders - no replacement logic needed since they don't rest."""
        return True



    # ---------- main loop ----------

    def step(self):
        self.loop_i += 1

        # Portfolio risk check - do this first before any trading
        if self._check_portfolio_risk():
            self.log({"type": "error", "op": "step", "msg": "Portfolio risk threshold exceeded - pausing trading"})
            # Emergency flatten all positions
            self._emergency_flatten_all()
            # Skip this trading cycle
            return

        # Rate limit check - skip trading if limits are critical
        if self._should_skip_trading_cycle():
            return

        # flush minute metrics
        if self.last_min is None:
            self.last_min = self.now_min()
        cur_min = self.now_min()
        if cur_min != self.last_min:
            # inventory valuation at mark
            try:
                inv = 0.0
                for c, s in self.state.items():
                    inv += abs(s.pos) * self.mark_mid(c)
            except Exception:
                inv = None

            total_fills = self.maker_fills_min + self.taker_fills_min
            maker_share = (self.maker_fills_min / total_fills) if total_fills > 0 else None

            if self.cfg.get("telemetry_db", True) and self.cfg.get("telemetry_enabled", True):
                upsert_minute_metrics(
                    self.db,
                    {
                        "ts_min": self.last_min,
                        "bot_id": self.bot_id,
                        "coin": None,
                        "maker_fills": self.maker_fills_min,
                        "taker_fills": self.taker_fills_min,
                        "realized_pnl": self.realized_min,
                        "net_fees": self.net_fees_min,
                        "total_pnl": (self.realized_min + self.net_fees_min)
                                    if (self.realized_min is not None and self.net_fees_min is not None)
                                    else None,
                        "inventory": inv,
                        "maker_share": maker_share,
                        "avg_latency_ms": self.client.avg_latency_ms(),
                    },
                )

            # ---- per-coin minute snapshot → histories for auto-tune ----
            try:
                for c in self.cfg.get("coins", []):
                    m = int(self.maker_fills_min_by_coin.get(c, 0) or 0)
                    t = int(self.taker_fills_min_by_coin.get(c, 0) or 0)
                    tot = m + t
                    ms = (m / tot) if tot > 0 else None
                    rpnl = float(self.realized_min_by_coin.get(c, 0.0) or 0.0)
                    nfee = float(self.net_fees_min_by_coin.get(c, 0.0) or 0.0)
                    ent = {"maker_fills": m, "taker_fills": t, "maker_share": ms,
                           "realized_pnl": rpnl, "net_fees": nfee, "total_pnl": rpnl + nfee}
                    hist = self._minute_hist_by_coin.setdefault(c, [])
                    hist.append(ent)
                    if len(hist) > 180:
                        del hist[0:len(hist)-180]
            except Exception:
                pass

            # run auto-tuner (per-coin with cooldown)
            self._maybe_autotune()

            # reset per-coin minute accumulators
            self.maker_fills_min_by_coin.clear()
            self.taker_fills_min_by_coin.clear()
            self.realized_min_by_coin.clear()
            self.net_fees_min_by_coin.clear()



            # push into in-memory history for auto-tuner and run it
            self._minute_hist.append({
                "maker_fills": self.maker_fills_min,
                "taker_fills": self.taker_fills_min,
                "maker_share": maker_share,
                "realized_pnl": self.realized_min,
                "net_fees": self.net_fees_min,
                "total_pnl": (self.realized_min + self.net_fees_min)
                              if (self.realized_min is not None and self.net_fees_min is not None)
                              else None,
            })
            if len(self._minute_hist) > 120:
                del self._minute_hist[0:len(self._minute_hist)-120]
            self._maybe_autotune()
            # reset accumulators
            self.maker_fills_min = 0
            self.taker_fills_min = 0
            self.realized_min = 0.0
            self.net_fees_min = 0.0
            self.last_min = cur_min

        # Manage WebSocket subscriptions based on coin activity
        if self.loop_i % 60 == 0:  # Every minute
            self._manage_websocket_subscriptions()
        
        # Check if we should send batched messages
        if self._should_send_batch():
            self._send_batched_messages()

        # risk caps
        max_coin_cap = float(self.cfg.get("max_per_coin_notional", 300.0))
        max_gross_cap = float(self.cfg.get("max_gross_notional", 600.0))

        # current gross
        gross = 0.0
        try:
            for c, s in self.state.items():
                gross += abs(s.pos) * self.mark_mid(c)
        except Exception:
            pass

        # Real-time order placement is now handled via WebSocket callbacks
        # This loop now only handles housekeeping and telemetry
        
        # Update spread history for dynamic min_spread calculations
        if self.cfg.get("dynamic_min_spread_enabled", True):
            for coin in self.cfg.get("coins", []):
                try:
                    if not self.client.supports(coin):
                        continue
                    
                    # Get current spread from WebSocket data
                    if (self.client.use_websocket and 
                        hasattr(self.client, 'ws_market_data') and 
                        coin in self.client.ws_market_data.market_data):
                        
                        data = self.client.ws_market_data.market_data[coin]
                        best_bid = data.get("best_bid", 0.0)
                        best_ask = data.get("best_ask", 0.0)
                        
                        if best_bid > 0 and best_ask > 0:
                            spread = best_ask - best_bid
                            mid = 0.5 * (best_bid + best_ask)
                            spread_bps_live = (spread / max(mid, 1e-12)) * 10_000.0
                            
                            # Update spread history
                            if coin not in set(self.cfg.get("dynamic_min_spread_exclude", [])):
                                hist = self._spread_hist.setdefault(coin, [])
                                hist.append(spread_bps_live)
                                lookback = int(self.cfg.get("dynamic_min_spread_lookback_loops", 300))
                                if len(hist) > lookback:
                                    del hist[0:len(hist) - lookback]
                                
                                # Update dynamic min_spread periodically
                                upd_every = int(self.cfg.get("dynamic_min_spread_update_every_loops", 20))
                                if upd_every > 0 and (self.loop_i % upd_every == 0) and len(hist) >= max(10, int(0.2 * lookback)):
                                    p = float(self._cf(coin, "dynamic_min_spread_percentile", float(self.cfg.get("dynamic_min_spread_percentile", 0.6))))
                                    target_pctl = self._percentile(hist, p)
                                    base_min = float(self._c(coin, "min_spread_bps", float(self.cfg.get("min_spread_bps", 0.0))))
                                    target = max(base_min, round(target_pctl, 2) if target_pctl is not None else base_min)
                                    prev = float(self._dyn_min_spread.get(coin, base_min))
                                    hyst = float(self.cfg.get("dynamic_min_spread_hysteresis_bps", 0.5))
                                    if target > prev + hyst or target < prev - hyst:
                                        self._dyn_min_spread[coin] = target
                                        self._tlog({
                                            "type": "info",
                                            "op": "dynamic_min_spread",
                                            "coin": coin,
                                            "msg": f"min_spread_bps updated: {prev:.2f} → {target:.2f} (pctl {p*100:.0f} over {len(hist)} samples)"
                                        })
                            
                            # Telemetry at low cadence
                            tel_every = int(self.cfg.get("dynamic_min_spread_telemetry_every_loops", 50))
                            if tel_every > 0 and (self.loop_i % tel_every == 0):
                                hist = self._spread_hist.get(coin, [])
                                p50 = self._percentile(hist, 0.5) if hist else None
                                p90 = self._percentile(hist, 0.9) if hist else None
                                base_min_spread_bps = self._cf(coin, "min_spread_bps", 0.0)
                                dyn_floor = float(self._dyn_min_spread.get(coin, base_min_spread_bps))
                                eff_min_spread_bps = max(base_min_spread_bps, dyn_floor)
                                be_mm_cache = getattr(self, "_be_mm_bps", None)
                                guard_buf = float(self._cf(coin, "min_spread_guard_buffer_bps", float(self.cfg.get("min_spread_guard_buffer_bps", 2.0))))
                                guard_floor = (be_mm_cache + guard_buf) if isinstance(be_mm_cache, (int, float)) else None
                                self._tlog({
                                    "type": "telemetry",
                                    "op": "spread",
                                    "coin": coin,
                                    "live_bps": round(spread_bps_live, 2),
                                    "p50_bps": None if p50 is None else round(p50, 2),
                                    "p90_bps": None if p90 is None else round(p90, 2),
                                    "eff_min_spread_bps": round(eff_min_spread_bps, 2),
                                    "guard_floor_bps": None if guard_floor is None else round(guard_floor, 2)
                                })
                except Exception as e:
                    self.log({"type": "warn", "op": "spread_history_update", "coin": coin, "msg": str(e)})

    # ---------- portfolio risk management ----------

    def _check_portfolio_risk(self) -> bool:
        """
        Check portfolio-level risk and return True if we should pause trading.
        Implements emergency stop loss and portfolio PnL monitoring.
        """
        try:
            # Get current portfolio state
            u = self.client.info.user_state(self.client.addr)
            
            # Calculate total portfolio PnL
            total_pnl = 0.0
            total_equity = 0.0
            
            # Sum up all position PnL
            for ap in u.get("assetPositions", []):
                pos = ap.get("position", {})
                if pos:
                    unrealized_pnl = float(pos.get("unrealizedPnl", 0.0) or 0.0)
                    total_pnl += unrealized_pnl
            
            # Get account equity
            fc = self._free_collateral()
            if fc is not None:
                total_equity = fc
            
            # Emergency stop loss: if portfolio PnL < -X% of equity
            stop_loss_pct = float(self.cfg.get("emergency_stop_loss_pct", -0.15))  # -15% default
            if total_equity > 0 and total_pnl < (total_equity * stop_loss_pct):
                self.log({
                    "type": "error", 
                    "op": "emergency_stop", 
                    "msg": f"EMERGENCY STOP: Portfolio PnL {total_pnl:.2f} < {stop_loss_pct*100:.1f}% of equity {total_equity:.2f}"
                })
                return True
            
            # Portfolio pause threshold: if portfolio PnL < -X% of equity
            pause_threshold_pct = float(self.cfg.get("portfolio_pause_threshold_pct", -0.08))  # -8% default
            if total_equity > 0 and total_pnl < (total_equity * pause_threshold_pct):
                self.log({
                    "type": "warn", 
                    "op": "portfolio_pause", 
                    "msg": f"Portfolio pause: PnL {total_pnl:.2f} < {pause_threshold_pct*100:.1f}% of equity {total_equity:.2f}"
                })
                return True
                
        except Exception as e:
            self.log({"type": "warn", "op": "portfolio_risk_check", "msg": str(e)})
        
        return False

    def _emergency_flatten_all(self):
        """
        Emergency function to flatten all positions immediately.
        Called when emergency stop loss is triggered.
        """
        try:
            u = self.client.info.user_state(self.client.addr)
            for ap in u.get("assetPositions", []):
                pos = ap.get("position", {})
                coin = pos.get("coin")
                if not coin or coin not in self.cfg.get("coins", []):
                    continue
                
                szi = pos.get("szi")
                if szi is None:
                    sz = float(pos.get("sz", 0.0))
                    side = (pos.get("side", "").lower())
                    szi = sz if side.startswith("long") else (-sz if side.startswith("short") else 0.0)
                
                szi = float(szi)
                if abs(szi) < 1e-12:
                    continue
                
                # Get current mid price
                try:
                    mid = self.mark_mid(coin)
                    if mid > 0:
                        self._flatten_position_immediate(coin, szi, mid)
                        self.log({
                            "type": "info", 
                            "op": "emergency_flatten", 
                            "coin": coin, 
                            "size": szi, 
                            "price": mid
                        })
                except Exception as e:
                    self.log({"type": "error", "op": "emergency_flatten", "coin": coin, "msg": str(e)})
                    
        except Exception as e:
            self.log({"type": "error", "op": "emergency_flatten_all", "msg": str(e)})

    def _enhanced_bailout_check(self, coin: str) -> bool:
        """
        Enhanced bailout check that works regardless of single-sided mode.
        Returns True if position should be bailed out.
        """
        try:
            bb, ba = self.client.best_bid_ask(coin)
            if not (bb and ba and bb > 0 and ba > 0):
                return False
                
            mid = 0.5 * (bb + ba)
            st = self.coin_state(coin)
            if abs(st.pos) <= 0:
                return False

            mae_bps = self._unreal_bps(coin, mid)
            if mae_bps <= 0.0:
                return False

            now = time.time()
            since = self._underwater_since.get(coin)
            if since is None:
                self._underwater_since[coin] = now
                return False
            elapsed = now - since

            # Use configurable thresholds
            partial_bps = float(self._c(coin, "bailout_partial_mae_bps", self.cfg.get("bailout_partial_mae_bps", 30.0)))
            full_bps = float(self._c(coin, "bailout_full_mae_bps", self.cfg.get("bailout_full_mae_bps", 60.0)))
            t_under = float(self._c(coin, "bailout_full_max_seconds", self.cfg.get("bailout_full_max_seconds", 180)))
            work_time = float(self._c(coin, "bailout_partial_min_seconds", self.cfg.get("bailout_partial_min_seconds", 90)))

            # Full bailout conditions
            if mae_bps >= full_bps or elapsed >= t_under:
                self._reduce_position_ioc(coin, frac=1.0)
                self.log({
                    "type": "warn", 
                    "op": "full_bailout", 
                    "coin": coin, 
                    "mae_bps": mae_bps, 
                    "elapsed_s": elapsed
                })
                return True

            # Partial bailout conditions
            if mae_bps >= partial_bps and elapsed >= work_time:
                reduce_frac = float(self._c(coin, "bailout_partial_reduce_fraction", self.cfg.get("bailout_partial_reduce_fraction", 0.33)))
                self._reduce_position_ioc(coin, frac=reduce_frac)
                self.log({
                    "type": "info", 
                    "op": "partial_bailout", 
                    "coin": coin, 
                    "mae_bps": mae_bps, 
                    "elapsed_s": elapsed,
                    "reduce_frac": reduce_frac
                })
                return True
                
        except Exception as e:
            self.log({"type": "warn", "op": "enhanced_bailout", "coin": coin, "msg": str(e)})
        
        return False

    def _enhanced_momentum_analysis(self, coin: str) -> dict:
        """
        Enhanced momentum analysis with multiple timeframes and trend strength.
        Returns dict with momentum signals and confidence levels.
        """
        try:
            # Get recent price history (last 60 seconds)
            price_history = getattr(self, f"_price_history_{coin}", [])
            if len(price_history) < 10:
                return {"signal": None, "confidence": 0.0, "trend_strength": 0.0}
            
            # Calculate multiple timeframe momentum
            short_term = price_history[-5:] if len(price_history) >= 5 else price_history
            medium_term = price_history[-15:] if len(price_history) >= 15 else price_history
            long_term = price_history[-30:] if len(price_history) >= 30 else price_history
            
            def calc_momentum(prices):
                if len(prices) < 2:
                    return 0.0
                start_price = float(prices[0])
                end_price = float(prices[-1])
                return ((end_price - start_price) / start_price) * 10000  # in bps
            
            # Calculate momentum for each timeframe
            short_mom = calc_momentum(short_term)
            medium_mom = calc_momentum(medium_term)
            long_mom = calc_momentum(long_term)
            
            # Weight the signals (short term = 0.5, medium = 0.3, long = 0.2)
            weighted_momentum = (short_mom * 0.5) + (medium_mom * 0.3) + (long_mom * 0.2)
            
            # Calculate trend strength (consistency of direction)
            trend_strength = 0.0
            if len(price_history) >= 10:
                positive_moves = sum(1 for i in range(1, len(price_history)) 
                                   if float(price_history[i]) > float(price_history[i-1]))
                trend_strength = positive_moves / (len(price_history) - 1)
            
            # Determine signal and confidence
            signal = None
            confidence = 0.0
            
            if abs(weighted_momentum) > 5.0:  # 5 bps threshold
                signal = "A" if weighted_momentum > 0 else "B"
                confidence = min(1.0, abs(weighted_momentum) / 20.0)  # max confidence at 20 bps
                
                # Boost confidence if trend is consistent
                if trend_strength > 0.7 or trend_strength < 0.3:
                    confidence *= 1.2
                    
            return {
                "signal": signal,
                "confidence": min(1.0, confidence),
                "trend_strength": trend_strength,
                "weighted_momentum": weighted_momentum
            }
            
        except Exception as e:
            self.log({"type": "warn", "op": "momentum_analysis", "coin": coin, "msg": str(e)})
            return {"signal": None, "confidence": 0.0, "trend_strength": 0.0}

    def _market_regime_detection(self, coin: str) -> str:
        """
        Detect market regime: trending, choppy, volatile, or stable.
        """
        try:
            price_history = getattr(self, f"_price_history_{coin}", [])
            if len(price_history) < 20:
                return "unknown"
            
            # Calculate volatility (standard deviation of returns)
            returns = []
            for i in range(1, len(price_history)):
                ret = (float(price_history[i]) - float(price_history[i-1])) / float(price_history[i-1])
                returns.append(ret)
            
            if not returns:
                return "unknown"
                
            import statistics
            volatility = statistics.stdev(returns) * 10000  # in bps
            
            # Calculate trend consistency
            positive_moves = sum(1 for r in returns if r > 0)
            trend_consistency = positive_moves / len(returns)
            
            # Classify regime
            if volatility > 50:  # High volatility
                return "volatile"
            elif volatility < 10:  # Low volatility
                return "stable"
            elif trend_consistency > 0.7 or trend_consistency < 0.3:  # Strong trend
                return "trending"
            else:  # Mixed signals
                return "choppy"
                
        except Exception:
            return "unknown"

    def _update_price_history(self, coin: str, mid_price: float):
        """
        Update price history for momentum analysis.
        """
        try:
            history_key = f"_price_history_{coin}"
            if not hasattr(self, history_key):
                setattr(self, history_key, [])
            
            history = getattr(self, history_key)
            history.append(mid_price)
            
            # Keep last 60 price points (about 60 seconds at 1-second intervals)
            if len(history) > 60:
                history.pop(0)
                
        except Exception:
            pass

    def _enhanced_auto_side_selection(self, coin: str) -> str:
        """
        Enhanced auto side selection with multiple signals and confidence weighting.
        """
        try:
            # Get current mid price and update history
            mid = float(self.mark_mid(coin))
            self._update_price_history(coin, mid)
            
            # Get enhanced momentum analysis
            momentum_data = self._enhanced_momentum_analysis(coin)
            momentum_signal = momentum_data.get("signal")
            momentum_confidence = momentum_data.get("confidence", 0.0)
            
            # Get market regime
            regime = self._market_regime_detection(coin)
            
            # Get inventory bias
            st = self.coin_state(coin)
            inv_bias = "A" if st.pos > 0 else ("B" if st.pos < 0 else None)
            
            # Get flow bias from maker/taker fills
            mb, ms, mk, tk = self._ma_get(coin)
            bias_ratio = float(self._c(coin, "ss_ma_side_bias_ratio", self.cfg.get("ss_ma_side_bias_ratio", 1.15)))
            flow_bias = None
            if mb > ms * bias_ratio:
                flow_bias = "B"
            elif ms > mb * bias_ratio:
                flow_bias = "A"
            
            # Get order book flow bias
            order_book_flow = self._get_order_book_flow_signals(coin)
            order_book_bias = None
            if order_book_flow:
                overall_bias = order_book_flow.get("overall_bias", "neutral")
                confidence = order_book_flow.get("confidence", 0.0)
                if confidence > 0.3:  # Only use if confident
                    if overall_bias == "bid":
                        order_book_bias = "B"
                    elif overall_bias == "ask":
                        order_book_bias = "A"
            
            # Weight the signals based on market regime and confidence
            signals = []
            weights = []
            
            # Momentum signal (weight depends on confidence and regime)
            if momentum_signal:
                momentum_weight = momentum_confidence
                if regime == "trending":
                    momentum_weight *= 1.5  # Boost momentum in trending markets
                elif regime == "choppy":
                    momentum_weight *= 0.5  # Reduce momentum in choppy markets
                signals.append(momentum_signal)
                weights.append(momentum_weight)
            
            # Inventory bias (always important)
            if inv_bias:
                signals.append(inv_bias)
                weights.append(1.0)
            
            # Flow bias (important but can be overridden)
            if flow_bias:
                signals.append(flow_bias)
                weights.append(0.8)
            
            # Order book flow bias (high confidence signals)
            if order_book_bias:
                signals.append(order_book_bias)
                weights.append(1.2)  # Higher weight for order book flow
            
            # Default to momentum if no other signals
            if not signals and momentum_signal:
                signals.append(momentum_signal)
                weights.append(0.5)
            
            # Calculate weighted decision
            if signals:
                # Count weighted votes
                bid_votes = sum(weights[i] for i, s in enumerate(signals) if s == "B")
                ask_votes = sum(weights[i] for i, s in enumerate(signals) if s == "A")
                
                if bid_votes > ask_votes * 1.1:  # 10% threshold
                    return "B"
                elif ask_votes > bid_votes * 1.1:
                    return "A"
                else:
                    # Close call - use inventory bias as tiebreaker
                    return inv_bias or "B"
            
            # No clear signal - use inventory bias or default to bid
            return inv_bias or "B"
            
        except Exception as e:
            self.log({"type": "warn", "op": "enhanced_auto_side", "coin": coin, "msg": str(e)})
            # Fallback to simple inventory bias
            st = self.coin_state(coin)
            return "A" if st.pos > 0 else ("B" if st.pos < 0 else "B")

    def _is_coin_active(self, coin: str) -> bool:
        """
        Determine if a coin is actively trading and should be subscribed.
        """
        try:
            # Check last 5 minutes of activity
            now = time.time()
            five_minutes_ago = now - 300
            
            # Check for recent fills
            recent_fills = 0
            try:
                # Count fills in last 5 minutes
                fills = self.db.execute(
                    "SELECT COUNT(*) FROM fills WHERE coin = ? AND t_fill_ms >= ?",
                    (coin, int(five_minutes_ago * 1000))
                ).fetchone()
                recent_fills = fills[0] if fills else 0
            except Exception:
                pass
            
            # Check for recent orders
            recent_orders = 0
            try:
                # Count order placements in last 5 minutes
                orders = self.db.execute(
                    "SELECT COUNT(*) FROM lifecycle WHERE coin = ? AND timestamp >= ?",
                    (coin, int(five_minutes_ago * 1000))
                ).fetchone()
                recent_orders = orders[0] if orders else 0
            except Exception:
                pass
            
            # Check for recent spread opportunities
            spread_checks = 0
            try:
                # Count spread checks in last 5 minutes (from telemetry)
                checks = self.db.execute(
                    "SELECT COUNT(*) FROM minute_metrics WHERE coin = ? AND ts_min >= ?",
                    (coin, int(five_minutes_ago // 60))
                ).fetchone()
                spread_checks = checks[0] if checks else 0
            except Exception:
                pass
            
            # Check current spread width
            try:
                bb, ba = self.client.best_bid_ask(coin)
                if bb > 0 and ba > 0:
                    mid = 0.5 * (bb + ba)
                    spread_bps = ((ba - bb) / mid) * 10000
                    wide_spread = spread_bps > 5.0  # 5 bps minimum
                else:
                    wide_spread = False
            except Exception:
                wide_spread = False
            
            # Coin is active if:
            # 1. Has recent fills, OR
            # 2. Has recent orders, OR  
            # 3. Has wide spreads (trading opportunity), OR
            # 4. Has significant spread checks (bot is interested)
            is_active = (recent_fills > 0 or 
                        recent_orders > 0 or 
                        wide_spread or 
                        spread_checks > 5)
            
            # Log activity status periodically
            if self.loop_i % 300 == 0:  # Every 5 minutes
                self._tlog({
                    "type": "info",
                    "op": "coin_activity",
                    "coin": coin,
                    "recent_fills": recent_fills,
                    "recent_orders": recent_orders,
                    "spread_checks": spread_checks,
                    "wide_spread": wide_spread,
                    "is_active": is_active
                })
            
            return is_active
            
        except Exception as e:
            self.log({"type": "warn", "op": "coin_activity_check", "coin": coin, "msg": str(e)})
            return True  # Default to active if check fails

    def _manage_websocket_subscriptions(self):
        """
        Dynamically manage WebSocket subscriptions based on coin activity.
        """
        try:
            if not hasattr(self.client, 'ws_market_data') or not self.client.ws_market_data:
                return
                
            # Check which coins should be active
            active_coins = []
            for coin in self.cfg.get("coins", []):
                if self._is_coin_active(coin):
                    active_coins.append(coin)
            
            # Get currently subscribed coins
            current_subscriptions = getattr(self.client.ws_market_data, '_subscribed_coins', set())
            
            # Coins to subscribe (newly active)
            to_subscribe = [coin for coin in active_coins if coin not in current_subscriptions]
            
            # Coins to unsubscribe (no longer active)
            to_unsubscribe = [coin for coin in current_subscriptions if coin not in active_coins]
            
            # Subscribe to newly active coins
            for coin in to_subscribe:
                try:
                    if hasattr(self.client.ws_market_data, 'subscribe_to_coin'):
                        self.client.ws_market_data.subscribe_to_coin(coin)
                        self._tlog({
                            "type": "info",
                            "op": "websocket_subscribe",
                            "coin": coin,
                            "reason": "coin_became_active"
                        })
                except Exception as e:
                    self.log({"type": "warn", "op": "websocket_subscribe", "coin": coin, "msg": str(e)})
            
            # Unsubscribe from inactive coins
            for coin in to_unsubscribe:
                try:
                    if hasattr(self.client.ws_market_data, 'unsubscribe_from_coin'):
                        self.client.ws_market_data.unsubscribe_from_coin(coin)
                        self._tlog({
                            "type": "info", 
                            "op": "websocket_unsubscribe",
                            "coin": coin,
                            "reason": "coin_became_inactive"
                        })
                except Exception as e:
                    self.log({"type": "warn", "op": "websocket_unsubscribe", "coin": coin, "msg": str(e)})
            
            # Update subscription tracking
            if hasattr(self.client.ws_market_data, '_subscribed_coins'):
                self.client.ws_market_data._subscribed_coins = set(active_coins)
            
            # Log subscription status periodically
            if self.loop_i % 600 == 0:  # Every 10 minutes
                self._tlog({
                    "type": "info",
                    "op": "subscription_status",
                    "active_coins": active_coins,
                    "total_coins": len(self.cfg.get("coins", [])),
                    "subscription_ratio": len(active_coins) / max(1, len(self.cfg.get("coins", [])))
                })
                
        except Exception as e:
            self.log({"type": "warn", "op": "subscription_management", "msg": str(e)})

    def _init_message_batching(self):
        """
        Initialize message batching system.
        """
        self._message_batch = {}
        self._batch_start_time = time.time()
        self._batch_interval = 0.5  # 500ms batching window
        self._max_batch_size = 50   # Maximum messages per batch

    def _is_critical_message(self, message_type: str, data: dict) -> bool:
        """
        Determine if a message is critical and should bypass batching.
        """
        try:
            # Critical message types that need immediate execution
            critical_types = {
                "order_placement",      # New orders
                "order_cancel",         # Cancellations
                "emergency_stop",       # Risk management
                "position_flatten",     # Risk management
                "bailout_action"        # Risk management
            }
            
            if message_type in critical_types:
                return True
            
            # Check for critical conditions in data
            if message_type == "market_data":
                # Check for extreme spread changes
                if "spread_bps" in data:
                    spread_bps = float(data.get("spread_bps", 0))
                    if spread_bps > 20.0:  # Very wide spread - opportunity
                        return True
                
                # Check for large price movements
                if "price_change_bps" in data:
                    price_change = abs(float(data.get("price_change_bps", 0)))
                    if price_change > 50.0:  # Large price move
                        return True
            
            return False
            
        except Exception:
            return False  # Default to non-critical if check fails

    def _add_to_batch(self, coin: str, message_type: str, data: dict):
        """
        Add a message to the current batch, or send immediately if critical.
        """
        try:
            # Check if this is a critical message
            if self._is_critical_message(message_type, data):
                # Send immediately for critical messages
                self._send_immediate_message(coin, message_type, data)
                return
            
            # Non-critical messages go to batch
            if coin not in self._message_batch:
                self._message_batch[coin] = {}
            
            if message_type not in self._message_batch[coin]:
                self._message_batch[coin][message_type] = []
            
            # Add timestamp to data
            data['timestamp'] = int(time.time() * 1000)
            self._message_batch[coin][message_type].append(data)
            
            # Limit batch size per coin/message_type
            if len(self._message_batch[coin][message_type]) > 10:
                self._message_batch[coin][message_type] = self._message_batch[coin][message_type][-10:]
                
        except Exception as e:
            self.log({"type": "warn", "op": "add_to_batch", "msg": str(e)})

    def _send_immediate_message(self, coin: str, message_type: str, data: dict):
        """
        Send a message immediately, bypassing batching.
        """
        try:
            immediate_message = {
                "coin": coin,
                "type": message_type,
                "data": data,
                "timestamp": int(time.time() * 1000),
                "immediate": True
            }
            
            # Send via WebSocket if available
            if (hasattr(self.client, 'ws_market_data') and 
                self.client.ws_market_data and 
                hasattr(self.client.ws_market_data, 'send_message')):
                
                try:
                    self.client.ws_market_data.send_message(immediate_message)
                    
                    # Log immediate sends for monitoring
                    if message_type in ["order_placement", "order_cancel", "emergency_stop"]:
                        self._tlog({
                            "type": "info",
                            "op": "immediate_send",
                            "coin": coin,
                            "message_type": message_type,
                            "reason": "critical_message"
                        })
                        
                except Exception as e:
                    self.log({"type": "warn", "op": "immediate_send", "msg": str(e)})
                    
        except Exception as e:
            self.log({"type": "warn", "op": "send_immediate_message", "msg": str(e)})

    def _should_send_batch(self) -> bool:
        """
        Determine if we should send the current batch.
        """
        try:
            current_time = time.time()
            time_elapsed = current_time - self._batch_start_time
            
            # Send if:
            # 1. Time interval reached, OR
            # 2. Batch is getting too large, OR
            # 3. Critical message (emergency stop, etc.)
            
            total_messages = sum(
                len(messages) 
                for coin_data in self._message_batch.values() 
                for messages in coin_data.values()
            )
            
            # Reduce batch interval for faster response
            batch_interval = 0.2  # 200ms instead of 500ms for faster response
            
            return (time_elapsed >= batch_interval or 
                   total_messages >= self._max_batch_size)
                   
        except Exception:
            return True  # Default to sending if check fails

    def _send_batched_messages(self):
        """
        Send the current batch of messages and reset.
        """
        try:
            if not self._message_batch:
                return
            
            # Prepare batched message
            batched_message = {
                "type": "batch_update",
                "timestamp": int(time.time() * 1000),
                "batch_id": f"batch_{int(time.time())}",
                "data": self._message_batch.copy()
            }
            
            # Send via WebSocket if available
            if (hasattr(self.client, 'ws_market_data') and 
                self.client.ws_market_data and 
                hasattr(self.client.ws_market_data, 'send_batch')):
                
                try:
                    self.client.ws_market_data.send_batch(batched_message)
                    
                    # Log batch statistics
                    total_messages = sum(
                        len(messages) 
                        for coin_data in self._message_batch.values() 
                        for messages in coin_data.values()
                    )
                    
                    if total_messages > 1:  # Only log if we actually batched
                        self._tlog({
                            "type": "info",
                            "op": "batch_sent",
                            "total_messages": total_messages,
                            "batch_size": len(self._message_batch),
                            "compression_ratio": total_messages
                        })
                        
                except Exception as e:
                    self.log({"type": "warn", "op": "send_batch", "msg": str(e)})
                    # Fallback to individual messages
                    self._send_individual_messages()
            else:
                # Fallback to individual messages
                self._send_individual_messages()
            
            # Reset batch
            self._message_batch = {}
            self._batch_start_time = time.time()
            
        except Exception as e:
            self.log({"type": "warn", "op": "send_batched_messages", "msg": str(e)})

    def _send_individual_messages(self):
        """
        Fallback: send individual messages if batching fails.
        """
        try:
            for coin, coin_data in self._message_batch.items():
                for message_type, messages in coin_data.items():
                    for message in messages:
                        # Send individual message
                        if (hasattr(self.client, 'ws_market_data') and 
                            self.client.ws_market_data and 
                            hasattr(self.client.ws_market_data, 'send_message')):
                            
                            try:
                                self.client.ws_market_data.send_message({
                                    "coin": coin,
                                    "type": message_type,
                                    "data": message
                                })
                            except Exception:
                                pass  # Skip failed individual messages
                                
        except Exception as e:
            self.log({"type": "warn", "op": "send_individual_messages", "msg": str(e)})

    def _batch_market_data_update(self, coin: str, market_data: dict):
        """
        Add market data update to batch.
        """
        self._add_to_batch(coin, "market_data", market_data)

    def _batch_order_update(self, coin: str, order_data: dict):
        """
        Add order update to batch (critical messages bypass batching).
        """
        self._add_to_batch(coin, "order_placement", order_data)

    def _batch_fill_update(self, coin: str, fill_data: dict):
        """
        Add fill update to batch.
        """
        self._add_to_batch(coin, "fill", fill_data)

    def _batch_telemetry_update(self, coin: str, telemetry_data: dict):
        """
        Add telemetry update to batch.
        """
        self._add_to_batch(coin, "telemetry", telemetry_data)

    def _check_rate_limits(self) -> dict:
        """
        Check current rate limit usage and return status.
        """
        try:
            if hasattr(self.client, '_dual_rl'):
                ws_tokens = self.client._dual_rl.get_ws_tokens()
                rest_tokens = self.client._dual_rl.get_rest_tokens()
                
                # Calculate usage percentages
                ws_capacity = float(os.environ.get("HL_WS_CAPACITY_PER_MIN", "1800")) / 60.0
                rest_capacity = float(os.environ.get("HL_REST_CAPACITY_PER_MIN", "800")) / 60.0
                
                ws_usage_pct = max(0.0, (ws_capacity - ws_tokens) / ws_capacity * 100.0)
                rest_usage_pct = max(0.0, (rest_capacity - rest_tokens) / rest_capacity * 100.0)
                
                return {
                    "ws_tokens": ws_tokens,
                    "rest_tokens": rest_tokens,
                    "ws_usage_pct": ws_usage_pct,
                    "rest_usage_pct": rest_usage_pct,
                    "ws_critical": ws_usage_pct > 80.0,
                    "rest_critical": rest_usage_pct > 80.0
                }
        except Exception as e:
            self.log({"type": "warn", "op": "rate_limit_check", "msg": str(e)})
        
        return {
            "ws_tokens": 0.0,
            "rest_tokens": 0.0,
            "ws_usage_pct": 0.0,
            "rest_usage_pct": 0.0,
            "ws_critical": False,
            "rest_critical": False
        }

    def _should_skip_trading_cycle(self) -> bool:
        """
        Determine if we should skip this trading cycle due to rate limits.
        """
        try:
            rate_status = self._check_rate_limits()
            
            # Skip if either rate limit is critical
            if rate_status.get("ws_critical", False) or rate_status.get("rest_critical", False):
                self.log({
                    "type": "warn",
                    "op": "rate_limit_skip",
                    "ws_usage_pct": rate_status.get("ws_usage_pct", 0.0),
                    "rest_usage_pct": rate_status.get("rest_usage_pct", 0.0),
                    "msg": "Skipping trading cycle due to rate limit pressure"
                })
                return True
            
            # Log rate limit status periodically
            if self.loop_i % 100 == 0:  # Every 100 loops
                self._tlog({
                    "type": "info",
                    "op": "rate_limit_status",
                    "ws_usage_pct": round(rate_status.get("ws_usage_pct", 0.0), 1),
                    "rest_usage_pct": round(rate_status.get("rest_usage_pct", 0.0), 1),
                    "ws_tokens": round(rate_status.get("ws_tokens", 0.0), 1),
                    "rest_tokens": round(rate_status.get("rest_tokens", 0.0), 1)
                })
            
            return False
            
        except Exception as e:
            self.log({"type": "warn", "op": "rate_limit_check", "msg": str(e)})
            return False

    def _maybe_take_profit(self, coin: str) -> bool:
        """
        Enhanced take profit with gradual price reduction and market order fallback.
        Returns True if position was closed, False otherwise.
        """
        try:
            st = self.coin_state(coin)
            if abs(st.pos) <= 0:
                return False
                
            bb, ba = self.client.best_bid_ask(coin)
            if not (bb and ba and bb > 0 and ba > 0):
                return False
                
            mid = 0.5 * (bb + ba)
            
            # Calculate unrealized PnL in bps and USD
            avg_entry = float(st.avg_entry or 0.0)
            if avg_entry <= 0:
                return False
                
            if st.pos > 0:  # long position
                # Profitable when mid > avg_entry
                pnl_bps = ((mid - avg_entry) / avg_entry) * 10000.0
                unrealized_pnl_usd = st.pos * (mid - avg_entry)  # Unrealized profit in USD terms
            else:  # short position
                # Profitable when mid < avg_entry  
                pnl_bps = ((avg_entry - mid) / avg_entry) * 10000.0
                unrealized_pnl_usd = abs(st.pos) * (avg_entry - mid)  # Unrealized profit in USD terms
            
            # Get current funding (if available)
            funding_usd = 0.0
            try:
                # Try to get funding from user state
                u = self.client.info.user_state(self.client.addr)
                for ap in u.get("assetPositions", []):
                    pos = ap.get("position", {})
                    if pos.get("coin") == coin:
                        funding_usd = float(pos.get("funding", 0.0) or 0.0)
                        break
            except Exception:
                # If we can't get funding, assume 0
                funding_usd = 0.0
            
            # Total profit = unrealized + funding
            total_pnl_usd = unrealized_pnl_usd + funding_usd
            
            # Take profit thresholds (configurable)
            min_profit_bps = float(self._c(coin, "take_profit_min_bps", self.cfg.get("take_profit_min_bps", 30.0)))  # 30 bps = 0.3%
            min_profit_usd = float(self._c(coin, "take_profit_min_usd", self.cfg.get("take_profit_min_usd", 25.0)))  # $25 minimum profit
            
            # Take profit conditions:
            # 1. Position is profitable above percentage threshold (bps)
            # 2. Total profit in USD (unrealized + funding) is above minimum threshold
            should_take_profit = (
                pnl_bps >= min_profit_bps and 
                total_pnl_usd >= min_profit_usd
            )
            
            if should_take_profit:
                self.log({
                    "type": "info",
                    "op": "take_profit",
                    "coin": coin,
                    "msg": f"Taking profit: {pnl_bps:.1f}bps profit, ${total_pnl_usd:.0f} total profit (${unrealized_pnl_usd:.0f} unrealized + ${funding_usd:.0f} funding)",
                    "pnl_bps": round(pnl_bps, 1),
                    "unrealized_pnl_usd": round(unrealized_pnl_usd, 0),
                    "funding_usd": round(funding_usd, 0),
                    "total_pnl_usd": round(total_pnl_usd, 0),
                    "position_size": st.pos
                })
                
                # Use enhanced take profit with gradual price reduction
                return self._enhanced_take_profit(coin, st.pos, mid, bb, ba)
                
        except Exception as e:
            self.log({"type": "warn", "op": "take_profit", "coin": coin, "msg": str(e)})
        
        return False

    def _enhanced_take_profit(self, coin: str, signed_pos: float, mid: float, best_bid: float, best_ask: float) -> bool:
        """
        Enhanced take profit using market orders and IOC orders to ensure position closure.
        Prioritizes market orders to guarantee execution when taking profit.
        """
        if abs(signed_pos) < 1e-12:
            return True

        step = d(self.client.sz_step(coin))
        szi = d(signed_pos).copy_abs()
        
        if signed_pos > 0:  # long -> SELL
            is_buy = False
            side = "A"
        else:  # short -> BUY
            is_buy = True
            side = "B"

        sz_f = as_float_8dp(quantize_down(szi, step))
        if sz_f <= 0:
            return False

        self.log({
            "type": "info",
            "op": "enhanced_take_profit",
            "coin": coin,
            "msg": f"Starting enhanced take profit for {sz_f} units",
            "position_size": signed_pos,
            "side": side
        })

        # Strategy 1: Try IOC market order (IOC with no price = market order)
        try:
            self.log({
                "type": "info",
                "op": "take_profit_ioc_market",
                "coin": coin,
                "msg": "Attempting IOC market order"
            })
            
            # IOC order with no price limit = market order
            res = self.client.ex.order(coin, is_buy, sz_f, 0.0, {"limit": {"tif": "Ioc"}}, True)
            
            if res.get("status") == "ok":
                self._log_lifecycle(coin, side, sz_f, 0.0, "TAKE_PROFIT_IOC_MARKET_SENT")
                self.log({
                    "type": "success",
                    "op": "take_profit",
                    "coin": coin,
                    "msg": "Successfully closed position with IOC market order"
                })
                return True
            else:
                self.log({
                    "type": "warn",
                    "op": "take_profit_ioc_market",
                    "coin": coin,
                    "response": res
                })
                
        except Exception as e:
            self.log({
                "type": "warn",
                "op": "take_profit_ioc_market",
                "coin": coin,
                "error": str(e)
            })

        # Strategy 2: Try pure market order
        try:
            self.log({
                "type": "info",
                "op": "take_profit_pure_market",
                "coin": coin,
                "msg": "Attempting pure market order"
            })
            
            res = self.client.ex.order(coin, is_buy, sz_f, 0.0, {"market": {}}, True)
            
            if res.get("status") == "ok":
                self._log_lifecycle(coin, side, sz_f, 0.0, "TAKE_PROFIT_PURE_MARKET_SENT")
                self.log({
                    "type": "success",
                    "op": "take_profit",
                    "coin": coin,
                    "msg": "Successfully closed position with pure market order"
                })
                return True
            else:
                self.log({
                    "type": "warn",
                    "op": "take_profit_pure_market",
                    "coin": coin,
                    "response": res
                })
                
        except Exception as e:
            self.log({
                "type": "warn",
                "op": "take_profit_pure_market",
                "coin": coin,
                "error": str(e)
            })

        # Strategy 3: Try GTC market order (GTC with no price = market order)
        try:
            self.log({
                "type": "info",
                "op": "take_profit_gtc_market",
                "coin": coin,
                "msg": "Attempting GTC market order"
            })
            
            res = self.client.ex.order(coin, is_buy, sz_f, 0.0, {"limit": {"tif": "Gtc"}}, True)
            
            if res.get("status") == "ok":
                self._log_lifecycle(coin, side, sz_f, 0.0, "TAKE_PROFIT_GTC_MARKET_SENT")
                self.log({
                    "type": "success",
                    "op": "take_profit",
                    "coin": coin,
                    "msg": "Successfully closed position with GTC market order"
                })
                return True
            else:
                self.log({
                    "type": "warn",
                    "op": "take_profit_gtc_market",
                    "coin": coin,
                    "response": res
                })
                
        except Exception as e:
            self.log({
                "type": "warn",
                "op": "take_profit_gtc_market",
                "coin": coin,
                "error": str(e)
            })

        # Strategy 4: Fallback to aggressive limit order near mid price
        try:
            self.log({
                "type": "info",
                "op": "take_profit_aggressive_limit",
                "coin": coin,
                "msg": "Attempting aggressive limit order near mid price"
            })
            
            # Use mid price as a fallback
            tick = d(self._effective_tick(coin, best_bid, best_ask))
            if is_buy:
                limit_px = quantize_up(d(mid), tick)
            else:
                limit_px = quantize_down(d(mid), tick)
            
            px_f = as_float_8dp(limit_px)
            
            # Try IOC limit order
            if self._c(coin, "flatten_reduce_only", True):
                try:
                    _ = self.client.place_ioc(coin, is_buy, sz_f, px_f, reduce_only=True)
                except TypeError:
                    _ = self.client.place_ioc(coin, is_buy, sz_f, px_f)
            else:
                _ = self.client.place_ioc(coin, is_buy, sz_f, px_f)
            
            self._log_lifecycle(coin, side, sz_f, px_f, "TAKE_PROFIT_AGGRESSIVE_LIMIT_SENT")
            self.log({
                "type": "success",
                "op": "take_profit",
                "coin": coin,
                "msg": f"Successfully closed position with aggressive limit order at {px_f}"
            })
            return True
            
        except Exception as e:
            self.log({
                "type": "warn",
                "op": "take_profit_aggressive_limit",
                "coin": coin,
                "error": str(e)
            })

        # If all attempts failed, log and return False
        self.log({
            "type": "error",
            "op": "take_profit",
            "coin": coin,
            "msg": "All take profit attempts failed - manual intervention may be required"
        })
        return False

    def flatten_if_needed(self, coin: str, mid: float):
        try:
            max_coin = self._cf(coin, "max_per_coin_notional", 300.0)
            state = self.coin_state(coin)
            notional = abs(state.pos) * mid
            if notional <= max_coin * 1.2 or state.pos == 0:
                return
            self._flatten_position_immediate(coin, state.pos, mid)
        except Exception as e:
            self.log({"type": "warn", "op": "flatten_if_needed", "coin": coin, "msg": str(e)})
