"""
chart_generator.py — Setup chart generation.

Produces a side-by-side PNG chart per trade:
  Left panel  (Before): Price context, swept swing level, OB zone,
                         FVG zone, and planned entry/SL/TP lines.
  Right panel (After):  Same context extended with trade outcome —
                         exit marker and result label.

Charts are saved to config.CHART_OUTPUT_DIR and can be downloaded.
"""

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import matplotlib
matplotlib.use("Agg")   # Non-interactive backend for server/headless use
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch

import config


# ─────────────────────────────────────────────────────────────────────────────
# Trade result data class  (passed in from trade_manager.py)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeResult:
    setup_id:      str
    direction:     str              # "BUY" or "SELL"
    trade_type:    str              # "INTRADAY" or "SWING"
    entry:         float
    sl:            float
    tp1:           float
    tp2:           Optional[float]
    ob_low:        float
    ob_high:       float
    fvg_low:       float
    fvg_high:      float
    swept_level:   float            # Price of the swept swing high/low
    entry_time:    pd.Timestamp
    outcome:       str              # "TP1_HIT", "TP2_HIT", "SL_HIT", "OPEN"
    partial_closed: bool = False    # True if TP1 was hit before TP2/SL
    exit_time:     Optional[pd.Timestamp] = None
    exit_price:    Optional[float] = None
    pnl_pips:      Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Colour palette
# ─────────────────────────────────────────────────────────────────────────────

COLORS = {
    "background":  "#0d1117",
    "panel_bg":    "#161b22",
    "grid":        "#21262d",
    "bull_candle": "#26a69a",
    "bear_candle": "#ef5350",
    "swept_level": "#ff9800",
    "ob_zone":     "#4fc3f7",
    "fvg_zone":    "#ce93d8",
    "entry":       "#ffffff",
    "sl":          "#ef5350",
    "tp1":         "#66bb6a",
    "tp2":         "#a5d6a7",
    "exit_win":    "#66bb6a",
    "exit_loss":   "#ef5350",
    "text":        "#e6edf3",
    "subtext":     "#8b949e",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_setup_chart(
    candles:   pd.DataFrame,
    result:    TradeResult,
    timeframe: str = config.CHART_TIMEFRAME,
) -> str:
    """
    Generate and save a before/after setup chart as a PNG.

    Args:
        candles:   OHLCV DataFrame (used for candlestick display).
        result:    TradeResult with all setup and outcome data.
        timeframe: Label shown in the chart title.

    Returns:
        Absolute path to the saved PNG file.
    """
    os.makedirs(config.CHART_OUTPUT_DIR, exist_ok=True)

    entry_idx   = _find_index(candles, result.entry_time)
    before_start = max(0, entry_idx - config.CHART_CANDLES_BEFORE)
    after_end    = min(len(candles), entry_idx + config.CHART_CANDLES_AFTER + 1)

    before_df = candles.iloc[before_start : entry_idx + 1].reset_index(drop=True)
    after_df  = candles.iloc[before_start : after_end].reset_index(drop=True)

    # Recalculate entry index within the sliced DataFrames
    entry_idx_before = len(before_df) - 1
    entry_idx_after  = entry_idx - before_start

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2,
        figsize=(20, 9),
        sharey=True,
        gridspec_kw={"wspace": 0.03},
    )
    fig.patch.set_facecolor(COLORS["background"])

    direction_label = "▲ BUY" if result.direction == "BUY" else "▼ SELL"
    pnl_text = f"  {result.pnl_pips:+.1f} pips" if result.pnl_pips is not None else ""
    fig.suptitle(
        f"XAUUSD  ·  {result.trade_type}  ·  {direction_label}{pnl_text}  ·  {result.setup_id}",
        color=COLORS["text"], fontsize=13, fontweight="bold", y=0.99,
    )

    _draw_panel(
        ax=ax_left, df=before_df, result=result,
        entry_idx=entry_idx_before, title=f"SETUP  [{timeframe}]",
        show_outcome=False,
    )
    _draw_panel(
        ax=ax_right, df=after_df, result=result,
        entry_idx=entry_idx_after, title=f"OUTCOME  ·  {result.outcome}",
        show_outcome=True,
    )

    filepath = os.path.join(config.CHART_OUTPUT_DIR, f"{result.setup_id}.png")
    plt.savefig(filepath, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return os.path.abspath(filepath)


# ─────────────────────────────────────────────────────────────────────────────
# Panel drawing
# ─────────────────────────────────────────────────────────────────────────────

def _draw_panel(
    ax:           plt.Axes,
    df:           pd.DataFrame,
    result:       TradeResult,
    entry_idx:    int,
    title:        str,
    show_outcome: bool,
):
    """Draw a single chart panel: candlesticks + all overlay annotations."""
    ax.set_facecolor(COLORS["panel_bg"])
    ax.set_title(title, color=COLORS["text"], fontsize=11, pad=8, fontweight="bold")

    for spine in ax.spines.values():
        spine.set_edgecolor(COLORS["grid"])

    ax.tick_params(colors=COLORS["subtext"])
    ax.yaxis.tick_right()

    # ── Candlesticks ──────────────────────────────────────────────────────
    for x, (_, row) in enumerate(df.iterrows()):
        _draw_candle(ax, x, row)

    # ── Swept level ───────────────────────────────────────────────────────
    ax.axhline(result.swept_level, color=COLORS["swept_level"],
               linewidth=1.0, linestyle="--", alpha=0.85, zorder=2)
    ax.text(0.01, result.swept_level, "Swept Level",
            color=COLORS["swept_level"], fontsize=7, va="bottom",
            transform=ax.get_yaxis_transform(), alpha=0.85)

    # ── OB zone ───────────────────────────────────────────────────────────
    ax.axhspan(result.ob_low, result.ob_high,
               alpha=0.12, color=COLORS["ob_zone"], zorder=1)
    ax.axhline(result.ob_low,  color=COLORS["ob_zone"], linewidth=0.6, linestyle=":", alpha=0.7)
    ax.axhline(result.ob_high, color=COLORS["ob_zone"], linewidth=0.6, linestyle=":", alpha=0.7)
    ax.text(0.01, (result.ob_low + result.ob_high) / 2, "OB",
            color=COLORS["ob_zone"], fontsize=7, va="center",
            transform=ax.get_yaxis_transform(), alpha=0.9)

    # ── FVG zone ──────────────────────────────────────────────────────────
    ax.axhspan(result.fvg_low, result.fvg_high,
               alpha=0.22, color=COLORS["fvg_zone"], zorder=1)
    ax.text(0.01, (result.fvg_low + result.fvg_high) / 2, "FVG",
            color=COLORS["fvg_zone"], fontsize=7, va="center",
            transform=ax.get_yaxis_transform(), alpha=0.9)

    # ── Entry / SL / TP lines ─────────────────────────────────────────────
    _hline_label(ax, result.entry, COLORS["entry"],  f"Entry  {result.entry:.2f}", "-",  1.2)
    _hline_label(ax, result.sl,    COLORS["sl"],     f"SL  {result.sl:.2f}",       "-.", 1.0)
    _hline_label(ax, result.tp1,   COLORS["tp1"],    f"TP1  {result.tp1:.2f}",     "-.", 1.0)
    if result.tp2:
        _hline_label(ax, result.tp2, COLORS["tp2"],  f"TP2  {result.tp2:.2f}",     "--", 1.0)

    # ── Entry marker ──────────────────────────────────────────────────────
    if 0 <= entry_idx < len(df):
        entry_y = result.entry
        marker  = "^" if result.direction == "BUY" else "v"
        ax.plot(entry_idx, entry_y, marker=marker, markersize=11,
                color=COLORS["entry"], zorder=6)

    # ── Outcome marker (after panel only) ─────────────────────────────────
    if show_outcome and result.exit_time and result.exit_price:
        exit_x = _find_index(df, result.exit_time)
        is_win  = "TP" in result.outcome
        color   = COLORS["exit_win"] if is_win else COLORS["exit_loss"]
        label   = "✓ TP" if is_win else "✗ SL"
        ax.plot(exit_x, result.exit_price, marker="X", markersize=13,
                color=color, zorder=7)
        ax.text(exit_x + 0.3, result.exit_price, label,
                color=color, fontsize=8, va="center", fontweight="bold")

    # ── X-axis date labels ────────────────────────────────────────────────
    _set_date_ticks(ax, df)


def _draw_candle(ax: plt.Axes, x: int, row: pd.Series):
    """Draw a single candlestick (wick + body)."""
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    is_bull    = c >= o
    color      = COLORS["bull_candle"] if is_bull else COLORS["bear_candle"]
    body_bot   = min(o, c)
    body_top   = max(o, c)

    # Wick
    ax.plot([x, x], [l, h], color=color, linewidth=0.8, zorder=3)

    # Body (use a thin line if open == close)
    if abs(body_top - body_bot) < 0.001:
        ax.plot([x - 0.4, x + 0.4], [body_bot, body_bot],
                color=color, linewidth=1.0, zorder=4)
    else:
        rect = plt.Rectangle(
            (x - 0.4, body_bot), 0.8, body_top - body_bot,
            facecolor=color, edgecolor=color, linewidth=0.4, zorder=4,
        )
        ax.add_patch(rect)


def _hline_label(
    ax: plt.Axes, price: float, color: str, label: str,
    linestyle: str, linewidth: float,
):
    """Draw a horizontal price line with a right-side label."""
    ax.axhline(price, color=color, linewidth=linewidth,
               linestyle=linestyle, alpha=0.85, zorder=2)
    ax.text(0.99, price, label, color=color, fontsize=7,
            ha="right", va="bottom",
            transform=ax.get_yaxis_transform(), alpha=0.9)


def _set_date_ticks(ax: plt.Axes, df: pd.DataFrame):
    """Place readable date labels on the x-axis."""
    n = len(df)
    if n == 0:
        return
    step = max(1, n // 6)
    positions = list(range(0, n, step))
    labels    = [str(df.iloc[i]["time"])[:10] for i in positions if i < n]
    ax.set_xticks(positions[:len(labels)])
    ax.set_xticklabels(labels, rotation=30, ha="right",
                       fontsize=7, color=COLORS["subtext"])


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _find_index(df: pd.DataFrame, target_time: pd.Timestamp) -> int:
    """Return the row index closest to target_time."""
    if df.empty or target_time is None:
        return 0
    diffs = (df["time"] - target_time).abs()
    return int(diffs.argmin())
