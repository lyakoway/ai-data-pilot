"""Business schema catalog injected into Analyst Oleg's prompt."""

SCHEMA_CATALOG = """
# RideGo analytics schema (SQLite)

## dim_city
Cities where RideGo operates.
- city_id INTEGER PK
- city_name TEXT  -- Москва, Санкт-Петербург, Казань, Екатеринбург, Новосибирск, Уфа, Краснодар, Сочи
- country TEXT    -- Россия
- population INTEGER
- is_inhouse INTEGER  -- 1 = собственные города, 0 = франшиза
- region TEXT     -- Центр, Северо-Запад, Поволжье, Урал, Сибирь, Юг

## dim_user
- user_id INTEGER PK
- registered_at DATE
- city_id INTEGER FK → dim_city
- phone_country TEXT  -- RU / OTHER
- app_version TEXT

## fact_rides
Daily ride facts.
- ride_id INTEGER PK
- ride_date DATE
- city_id INTEGER FK → dim_city
- user_id INTEGER FK → dim_user
- distance_km REAL
- revenue_rub REAL
- duration_min INTEGER
- is_partner INTEGER  -- 1 = партнёрская поездка (обычно исключаем)
- vehicle_type TEXT   -- scooter / bike

## fact_subscriptions
- sub_id INTEGER PK
- user_id INTEGER FK → dim_user
- city_id INTEGER FK → dim_city
- brand TEXT  -- ridego_plus / mts_prime / boost
- status TEXT -- active / cancelled
- started_at DATE
- cancelled_at DATE NULL
- cancel_reason TEXT NULL  -- price / unused / competitor / other

## Metrics (business definitions)
- utilization is NOT in tables; approximate active demand as rides / population * 1000
- "уникальные пользователи" = COUNT(DISTINCT user_id)
- "InHouse города" = is_inhouse = 1
- "Россия" = country = 'Россия'
- Exclude partner trips unless asked: is_partner = 0
"""


SEED_SCENARIOS = [
    {
        "id": "sales-by-region",
        "name": "Выручка по регионам за 30 дней",
        "agent": "oleg",
        "description": "Сумма revenue_rub по регионам за последний месяц",
        "prompt": (
            "Покажи выручку по регионам за последние 30 дней от максимальной даты в данных. "
            "Исключи партнёрские поездки. Отсортируй по убыванию выручки. Нужен bar chart."
        ),
        "chart_type": "bar",
    },
    {
        "id": "top-cities-rides",
        "name": "Топ-10 городов по поездкам",
        "agent": "oleg",
        "description": "Количество поездок и уникальных пользователей",
        "prompt": (
            "Топ-10 городов по числу поездок за всё время. "
            "Покажи city_name, rides_count, unique_users. Исключи партнёрские поездки."
        ),
        "chart_type": "bar",
    },
    {
        "id": "subscription-penetration",
        "name": "Проникновение подписок (InHouse)",
        "agent": "oleg",
        "description": "Доля активных подписчиков среди пользователей города",
        "prompt": (
            "Для InHouse городов посчитай долю пользователей с активной подпиской "
            "от всех зарегистрированных пользователей города. Покажи худшие 8 городов "
            "(наименьшая доля). Нужен bar chart."
        ),
        "chart_type": "bar",
    },
    {
        "id": "cancel-reasons",
        "name": "Причины отмен подписок Boost",
        "agent": "oleg",
        "description": "Распределение cancel_reason для brand=boost",
        "prompt": (
            "Построй распределение причин отмены подписки brand = 'boost'. "
            "Нужен pie chart."
        ),
        "chart_type": "pie",
    },
    {
        "id": "ksyusha-schema",
        "name": "Где хранится utilization?",
        "agent": "ksyusha",
        "description": "Вопрос к документации (Ксюша)",
        "prompt": "Где хранится utilization и как она считается?",
        "chart_type": None,
    },
]
