import csv
import os
import sys
from datetime import datetime, timezone

import redis

# Chei TimeSeries → coloana CSV corespunzătoare
METRICS = {
    "forecast:temperature_C":    "temperature_C",
    "forecast:humidity_pct":     "humidity_pct",
    "forecast:pressure_hPa":     "pressure_hPa",
    "forecast:rain_probability":  "rain_probability",
}


def push_csv_to_redis() -> int:
    r = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        db=int(os.environ.get("REDIS_DB", 0)),
        password=None,
        decode_responses=True,
        socket_connect_timeout=5,
    )
    r.ping()
    ts = r.ts()

    # Creează cheile TS dacă nu există (ignora dacă există deja)
    for key, col in METRICS.items():
        try:
            ts.create(key, labels={"metric": col, "source": "forecast"})
        except redis.exceptions.ResponseError:
            pass

    csv_path = os.environ.get("CSV_PATH", "forecast_5h.csv")
    rows = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    for row in rows:
        ts_ms = int(
            datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M")
            .replace(tzinfo=timezone.utc)
            .timestamp() * 1000
        )
        for key, col in METRICS.items():
            ts.add(key, ts_ms, float(row[col]), duplicate_policy="last")

    print(
        f"[redis-ts] Stored {len(rows)} points × {len(METRICS)} metrics "
        f"→ keys: {list(METRICS.keys())}",
        flush=True,
    )
    return len(rows)


if __name__ == "__main__":
    try:
        push_csv_to_redis()
    except Exception as e:
        print(f"[redis-ts] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
