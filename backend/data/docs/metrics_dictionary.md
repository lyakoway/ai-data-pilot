# Словарь метрик RideGo

## Utilization
Utilization (утилизация) — доля активного флота.
Формула: `sh_active / sh_available`.
В витрине аналитики сырые поля не хранятся как utilization;
для демо-дашборда можно аппроксимировать спрос как
`rides_count / population * 1000`.

Источник истины в проде (пример): `iceberg.bi.ss_wide_rlp_total`.

## Active subscription user
Пользователь с `fact_subscriptions.status = 'active'` на дату отчёта.

## Partner trip
Поездка с `fact_rides.is_partner = 1`. В стандартных отчётах исключается.
