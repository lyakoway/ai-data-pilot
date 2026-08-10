"""Create RideGo analytics DB and seed synthetic demo data."""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import get_settings

CITIES = [
    # city_name, country, population, is_inhouse, region
    ("Москва", "Россия", 12_600_000, 1, "Центр"),
    ("Санкт-Петербург", "Россия", 5_600_000, 1, "Северо-Запад"),
    ("Казань", "Россия", 1_300_000, 1, "Поволжье"),
    ("Екатеринбург", "Россия", 1_500_000, 1, "Урал"),
    ("Новосибирск", "Россия", 1_600_000, 1, "Сибирь"),
    ("Уфа", "Россия", 1_100_000, 1, "Урал"),
    ("Краснодар", "Россия", 1_000_000, 0, "Юг"),
    ("Сочи", "Россия", 450_000, 0, "Юг"),
]

DDL = """
CREATE TABLE IF NOT EXISTS dim_city (
  city_id INTEGER PRIMARY KEY,
  city_name TEXT NOT NULL,
  country TEXT NOT NULL,
  population INTEGER NOT NULL,
  is_inhouse INTEGER NOT NULL,
  region TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_user (
  user_id INTEGER PRIMARY KEY,
  registered_at DATE NOT NULL,
  city_id INTEGER NOT NULL REFERENCES dim_city(city_id),
  phone_country TEXT NOT NULL,
  app_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_rides (
  ride_id INTEGER PRIMARY KEY,
  ride_date DATE NOT NULL,
  city_id INTEGER NOT NULL REFERENCES dim_city(city_id),
  user_id INTEGER NOT NULL REFERENCES dim_user(user_id),
  distance_km REAL NOT NULL,
  revenue_rub REAL NOT NULL,
  duration_min INTEGER NOT NULL,
  is_partner INTEGER NOT NULL,
  vehicle_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_subscriptions (
  sub_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES dim_user(user_id),
  city_id INTEGER NOT NULL REFERENCES dim_city(city_id),
  brand TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at DATE NOT NULL,
  cancelled_at DATE,
  cancel_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_rides_date ON fact_rides(ride_date);
CREATE INDEX IF NOT EXISTS idx_rides_city ON fact_rides(city_id);
CREATE INDEX IF NOT EXISTS idx_subs_brand ON fact_subscriptions(brand);
"""


def seed_analytics_db(force: bool = False) -> Path:
    settings = get_settings()
    db_path = Path(settings.database_url.replace("sqlite:///", ""))
    if db_path.exists() and not force:
        # Already seeded
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM fact_rides")).scalar()
            if n and n > 0:
                return db_path

    if db_path.exists():
        db_path.unlink()

    engine = create_engine(settings.database_url)
    rng = random.Random(42)
    start = date(2026, 1, 1)
    end = date(2026, 7, 15)
    days = (end - start).days

    with engine.begin() as conn:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))

        for i, (name, country, pop, inhouse, region) in enumerate(CITIES, start=1):
            conn.execute(
                text(
                    "INSERT INTO dim_city VALUES "
                    "(:id, :name, :country, :pop, :inhouse, :region)"
                ),
                {
                    "id": i,
                    "name": name,
                    "country": country,
                    "pop": pop,
                    "inhouse": inhouse,
                    "region": region,
                },
            )

        users: list[tuple[int, int]] = []
        user_id = 1
        for city_id in range(1, len(CITIES) + 1):
            n_users = 800 if city_id <= 2 else 350 if city_id <= 5 else 180
            for _ in range(n_users):
                reg = start + timedelta(days=rng.randint(0, days))
                phone = "RU" if rng.random() > 0.08 else "OTHER"
                ver = rng.choice(["1.9.0", "2.0.0", "2.1.0", "2.2.0"])
                conn.execute(
                    text(
                        "INSERT INTO dim_user VALUES "
                        "(:id, :reg, :city, :phone, :ver)"
                    ),
                    {
                        "id": user_id,
                        "reg": reg.isoformat(),
                        "city": city_id,
                        "phone": phone,
                        "ver": ver,
                    },
                )
                users.append((user_id, city_id))
                user_id += 1

        ride_id = 1
        batch: list[dict] = []
        for day_offset in range(days + 1):
            d = start + timedelta(days=day_offset)
            # Seasonality: more rides in warmer months
            season = 0.7 + 0.6 * (d.month / 7)
            for city_id in range(1, len(CITIES) + 1):
                base = {1: 220, 2: 160, 3: 90, 4: 85, 5: 70, 6: 55, 7: 40, 8: 35}[city_id]
                n_rides = int(base * season * rng.uniform(0.85, 1.15))
                city_users = [u for u, c in users if c == city_id]
                for _ in range(n_rides):
                    uid = rng.choice(city_users)
                    dist = round(rng.uniform(0.8, 12.0), 2)
                    rev = round(dist * rng.uniform(12, 28), 2)
                    dur = int(dist * rng.uniform(3, 6))
                    partner = 1 if rng.random() < 0.06 else 0
                    vtype = "scooter" if rng.random() > 0.25 else "bike"
                    batch.append(
                        {
                            "id": ride_id,
                            "d": d.isoformat(),
                            "city": city_id,
                            "uid": uid,
                            "dist": dist,
                            "rev": rev,
                            "dur": dur,
                            "partner": partner,
                            "vtype": vtype,
                        }
                    )
                    ride_id += 1
                    if len(batch) >= 800:
                        conn.execute(
                            text(
                                "INSERT INTO fact_rides VALUES "
                                "(:id, :d, :city, :uid, :dist, :rev, :dur, :partner, :vtype)"
                            ),
                            batch,
                        )
                        batch.clear()
        if batch:
            conn.execute(
                text(
                    "INSERT INTO fact_rides VALUES "
                    "(:id, :d, :city, :uid, :dist, :rev, :dur, :partner, :vtype)"
                ),
                batch,
            )

        brands = ["ridego_plus", "mts_prime", "boost"]
        reasons = ["price", "unused", "competitor", "other"]
        sub_id = 1
        for uid, city_id in users:
            if rng.random() > 0.22:
                continue
            brand = rng.choice(brands)
            started = start + timedelta(days=rng.randint(0, days - 10))
            active = rng.random() > 0.35
            cancelled = None
            reason = None
            status = "active"
            if not active:
                status = "cancelled"
                cancelled = (started + timedelta(days=rng.randint(5, 60))).isoformat()
                reason = rng.choice(reasons)
            conn.execute(
                text(
                    "INSERT INTO fact_subscriptions VALUES "
                    "(:id, :uid, :city, :brand, :status, :started, :cancelled, :reason)"
                ),
                {
                    "id": sub_id,
                    "uid": uid,
                    "city": city_id,
                    "brand": brand,
                    "status": status,
                    "started": started.isoformat(),
                    "cancelled": cancelled,
                    "reason": reason,
                },
            )
            sub_id += 1

    return db_path


def get_engine():
    settings = get_settings()
    seed_analytics_db()
    return create_engine(settings.database_url)
