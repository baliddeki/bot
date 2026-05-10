# test_data.py
from deriv_client import ping, fetch_candles

print("Ping:", ping())

df = fetch_candles("frxXAUUSD", granularity=3600, count=10)
print(df)
