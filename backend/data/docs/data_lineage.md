# Data lineage и хранилища

## fact_rides
Инкрементальная витрина поездок. Источник: `rides.api` → Kafka → DWH.
Поля `distance_km`, `revenue_rub`, `duration_min` считаются на этапе ETL.

## dim_city
Справочник городов. `is_inhouse = 1` — собственные города RideGo,
`0` — франшиза. Франшизные города обычно исключают из unit-экономики HQ.

## MongoDB customer_subscription
Операционный документ подписки (не аналитическая витрина).
Поля: `brand`, `status`, `startedAt`, `cancelledAt`, `cancelReason`.
Аналитика читает уже нормализованную `fact_subscriptions`.

## Redis pricing cache
Кэш тарифов в Redis, TTL 15 минут.
Ключ: `pricing:{cityId}:{vehicleType}`.
При изменении цены в админке ключ инвалидируется; до инвалидации
могут отдаваться старые диапазоны.
