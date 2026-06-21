"""laduje odczyty czujnikow (data/readings.csv) do hurtowni analitycznej PostgreSQL.

dane to skonsolidowany wynik strumienia z lab11 (telemetria iot): pola
reading_time, sensor_id, zone, value, state. ladujemy do tabeli `readings`.
"""

import sys

import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://bi:bi@localhost:5432/sensors"
CSV_PATH = "data/readings.csv"
TABLE = "readings"


def main() -> int:
    engine = create_engine(DB_URL)

    df = pd.read_csv(CSV_PATH, parse_dates=["reading_time"])
    df["value"] = df["value"].astype(float)
    df.to_sql(TABLE, engine, if_exists="replace", index=False)

    with engine.connect() as conn:
        n = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()

    print(f"Zaladowano wierszy: {len(df)} (w bazie: {n})")
    print("Zakres czasu:", df["reading_time"].min(), "->", df["reading_time"].max())
    print("Strefy:", ", ".join(sorted(df["zone"].unique())))
    print("Statusy:", df["state"].value_counts().to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
