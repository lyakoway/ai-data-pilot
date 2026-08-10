# Backend logic (фейковая документация для Ксюши)

## Reset errors в админке ТС
Кнопка «Сбросить ошибки» вызывает `TransportCardService.ResetCriticalErrors`.
Критические флаги сбрасываются только если SIM-доступ активен.
Если SIM disabled — API возвращает 409 Conflict.

## Anti-fraud battery serial
Сервис `opsactions.api`, файл `AntifraudBatterySerialNumberService.cs`.
Суть: не даём накручивать счётчик замен АКБ одним и тем же serial.
Используется Redis TTL whitelist `SkippableSerialNumber`.

## PositionCodes
Справочник кодов позиций ТС в `location.api`.
Коды 1–10 — штатные, 11+ — сервисные/ремонт.
При переводе города в inactive поле location не очищается,
но перестаёт попадать в публичный geo-feed.
