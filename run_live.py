"""
Run Live Trading
=================
Entry point for the OB + FVG trading bot.
"""

from live_bot import LiveBot
import config


def main():
    print("\n" + "!" * 60)
    print("  OB + FVG TRADING BOT - XAUUSD")
    print("!" * 60)
    print(f"\nSymbol:         {config.SYMBOL}")
    print(f"OB Timeframe:   {config.OB_TIMEFRAME}")
    print(f"FVG Timeframes: {', '.join(config.FVG_TIMEFRAMES)}")
    print(
        f"SL:             {config.SL_PIPS} pips ({config.pips_to_points(config.SL_PIPS)} points)"
    )
    print(f"TP1:            {config.TP1_PIPS} pips (close {config.TP1_CLOSE_PERCENT}%)")
    print(f"TP2:            {config.TP2_PIPS} pips")
    print(f"Account Mode:   {config.ACCOUNT_MODE}")
    print()

    if config.ACCOUNT_MODE == "prop":
        print("PROP FIRM MODE: 0.5% risk, 1.5% max daily loss")
    else:
        print("Risk tier will be auto-detected from balance")

    print()
    print("!" * 60)
    print("  WARNING: This will place REAL trades on your MT5 account!")
    print("!" * 60)

    confirm = input("\nType 'YES' to start: ")
    if confirm != "YES":
        print("Cancelled.")
        return

    bot = LiveBot()
    bot.start()


if __name__ == "__main__":
    main()
