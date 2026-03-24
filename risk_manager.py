"""
Risk Manager
=============
Handles position sizing and daily loss limits.

Auto-detects account tier from balance:
  < $1000  → 6% risk, 12% max daily loss
  >= $1000 → 2% risk, 4% max daily loss
  Prop     → 0.5% risk, 1.5% max daily loss
"""

from datetime import datetime, date
import MetaTrader5 as mt5
import config


class RiskManager:
    def __init__(self):
        self.daily_start_balance = None
        self.daily_loss_today = 0.0
        self.current_date = None

    def reset_daily_tracking(self, balance):
        """Reset daily loss tracking. Call at start of each day."""
        today = date.today()
        if self.current_date != today:
            self.current_date = today
            self.daily_start_balance = balance
            self.daily_loss_today = 0.0

    def calculate_lot_size(self, balance, sl_pips=None):
        """
        Calculate lot size based on account balance and risk tier.

        Args:
            balance: Current account balance
            sl_pips: Stop loss in pips (defaults to config.SL_PIPS)

        Returns:
            (lot_size, risk_amount, tier_info)
        """
        if sl_pips is None:
            sl_pips = config.SL_PIPS

        tier = config.get_risk_tier(balance)
        risk_percent = tier["risk_per_trade"]
        risk_amount = balance * (risk_percent / 100.0)

        # lot_size = risk_amount / (sl_pips * pip_value_per_lot)
        cost_per_lot = sl_pips * config.PIP_VALUE_PER_LOT
        if cost_per_lot <= 0:
            return 0.0, 0.0, tier

        lot_size = risk_amount / cost_per_lot

        # Clamp to broker limits
        lot_size = max(config.MIN_LOT, min(config.MAX_LOT, lot_size))

        # Round to 2 decimal places
        lot_size = round(lot_size, 2)

        return lot_size, risk_amount, tier

    def can_trade(self, balance):
        """
        Check if we're allowed to trade (daily loss limit not hit).

        Returns:
            (allowed, reason)
        """
        self.reset_daily_tracking(balance)
        tier = config.get_risk_tier(balance)
        max_daily_loss_pct = tier["max_daily_loss"]

        if self.daily_start_balance is None or self.daily_start_balance <= 0:
            return True, "ok"

        # Calculate today's loss as percentage of starting balance
        current_loss_pct = (self.daily_loss_today / self.daily_start_balance) * 100.0

        if current_loss_pct >= max_daily_loss_pct:
            return False, (
                f"Daily loss limit hit: {current_loss_pct:.1f}% "
                f"(max {max_daily_loss_pct:.1f}%)"
            )

        # Check if one more trade could breach the limit
        risk_per_trade = tier["risk_per_trade"]
        if current_loss_pct + risk_per_trade > max_daily_loss_pct * 1.5:
            return False, (
                f"Close to daily limit: {current_loss_pct:.1f}% loss, "
                f"next trade risks {risk_per_trade:.1f}% more"
            )

        return True, "ok"

    def record_loss(self, loss_amount):
        """Record a loss for daily tracking. Pass positive number."""
        self.daily_loss_today += abs(loss_amount)

    def update_daily_pnl_from_mt5(self, connection):
        """
        Update daily P&L from MT5 trade history.
        Call this periodically to stay in sync.
        """
        balance = connection.get_balance()
        self.reset_daily_tracking(balance)

        closed_today = connection.get_todays_closed_trades()
        total_pnl = sum(d.profit for d in closed_today)

        if total_pnl < 0:
            self.daily_loss_today = abs(total_pnl)
        else:
            self.daily_loss_today = 0.0

    def get_status(self, balance):
        """Get a status summary string."""
        tier = config.get_risk_tier(balance)
        lot, risk_amt, _ = self.calculate_lot_size(balance)

        self.reset_daily_tracking(balance)
        loss_pct = 0.0
        if self.daily_start_balance and self.daily_start_balance > 0:
            loss_pct = (self.daily_loss_today / self.daily_start_balance) * 100.0

        return (
            f"Tier: {tier['description']} | "
            f"Risk: {tier['risk_per_trade']}% (${risk_amt:.2f}) | "
            f"Lot: {lot} | "
            f"Daily loss: {loss_pct:.1f}% / {tier['max_daily_loss']}%"
        )
