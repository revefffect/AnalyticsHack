# Запуск Метеоархива

Исследователь добавляет город или территорию, создаёт станцию и импортирует ZIP, CSV, XLSX или JSON с подтверждением колонок. Созданная версия доступна владельцу, пока он не выдаст доступ читателю или не опубликует её в каталоге команды. Читатель формирует и сохраняет отчёт в кабинете бизнеса.

В стартовом каталоге три станции, привязанные к урочищу «Красное» в Тверской области. Сотрудник самостоятельно добавляет города и территории, если поступают данные из других мест.

## Локальный запуск

Из корня проекта создайте окружение с Python 3.12 или 3.13. Пароль администратора вводится в Терминале.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r portal/requirements.txt
export METEO_DEBUG=1
python manage.py migrate
python manage.py seed_catalog
python manage.py createsuperuser
python scripts/install_dashboard_assets.py
python manage.py collectstatic --noinput
python manage.py runserver
```

Откройте каталог и для проверки загрузите `kr1_2024.csv` в карточку **Сокол М1 Кр1 · Поляна**. Альтернативно укажите полный ZIP организаторов в `ARCHIVE` и выполните импорт одной командой. Идентичное содержимое и сопоставление для той же станции и учётной записи используют существующую версию. [Локальный каталог](http://127.0.0.1:8000)

```bash
python manage.py import_case_archive "$ARCHIVE" --user ИМЯ_ИССЛЕДОВАТЕЛЯ
python manage.py test catalog
METEO_ARCHIVE="$ARCHIVE" python -m unittest discover -s tests -v
python scripts/gap_benchmark.py "$ARCHIVE" --out output/gap-benchmark
```

Для воспроизведения заведите получателя в группе **Читатели**. Команда создаст отдельный испытательный поток и отправит три реальные строки архива через вход станции. Параметр `--measure-panel` измеряет время получения HTML панели получателя. Для устаревания и доставки запустите `python manage.py monitor_weather` в отдельном процессе.

```bash
python manage.py replay_case "$ARCHIVE" --owner ИМЯ_ИССЛЕДОВАТЕЛЯ --recipient ИМЯ_ЧИТАТЕЛЯ \
  --url http://127.0.0.1:8000 --measure-panel
```

## Карта, отчёты, сравнение и мониторинг

Каталог служит точкой входа. На карте выбирают станцию, в отчёте проверяют один показатель за нужный период, в Superset сравнивают архивные версии, в Grafana следят за входящими наблюдениями. Отчёт и витрина строятся из одних расчётов контроля качества.

1. **MapLibre.** Каталог показывает точки с подтверждённой широтой и долготой. Фильтр по городу или территории действует и на список, и на карту. По клику открывается ссылка на карточку станции. Станции без координат доступны в списке. `METEO_MAP_STYLE_URL` позволяет подключить базовую карту. При пустом значении точки отображаются на нейтральном фоне без сетевых запросов к провайдеру карты.

2. **ECharts.** Откройте версию набора в карточке станции и выберите показатель, даты и шаг. График сохраняет пропуски. Нижняя полоса показывает долю пригодных дней. Нажмите на месяц или декаду, чтобы перейти к дням. Нажмите на день, чтобы открыть проверенные и исключённые исходные строки. Те же переходы доступны из таблицы.

3. **Superset.** Создайте витрину из выбранных версий. Идентификаторы версий есть в URL отчётов. Порядок `--compare` задаёт знак разницы. Для расчёта Кр1 − Кр2 укажите Кр1 первой.

   ```bash
   python -m pip install 'duckdb>=1.4,<2'
   python manage.py build_dashboard_mart output/analytics/dashboard-meteo.duckdb \
     --dataset "$AME60_VERSION" --dataset "$KR1_VERSION" --dataset "$KR2_VERSION" \
     --compare "$KR1_VERSION" "$KR2_VERSION" --year 2025
   ```

   Скопируйте `integrations/superset/.env.example` в `integrations/superset/.env`, заполните два секрета и ограничьте права файла командой `chmod 600 integrations/superset/.env`. Контейнер монтирует DuckDB только для чтения и сам создаёт дашборд `meteo-archive`.

   ```bash
   docker compose --env-file integrations/superset/.env -f integrations/superset/compose.yaml up --build -d
   ```

   Установите `METEO_SUPERSET_URL=http://127.0.0.1:8088/superset/dashboard/meteo-archive/` в окружении портала, чтобы добавить ссылку в карточки. Дашборд показывает график и покрытие с фильтрами станции, показателя, шага и дат. Панели сравнения показывают фиксированную пару и год из команды, разницу по месяцам и часам и число общих пар. Доступ в Superset выдаётся отдельно сотрудникам, которым разрешены все вошедшие в витрину версии. Для обновления укажите новый файл через `METEO_DASHBOARD_MART_FILE` и пересоздайте контейнер. [Локальный дашборд Superset](http://127.0.0.1:8088/superset/dashboard/meteo-archive/)

4. **Grafana.** Откройте **«Мониторинг»** в карточке станции. Входящие наблюдения идут через `POST /api/streams/КЛЮЧ/observations/`, синхронизируются с InfluxDB, и при готовности привязки владелец видит **«Открыть поток в Grafana»**. Сохраняйте отдельно время измерения и время приёма. Для защиты запускайте `replay_case` и оставляйте подпись «Воспроизведение архива». Для архивного экрана маршрут `/datasets/ВЕРСИЯ/grafana/` также принимает `metric`, `step`, `start` и `end`. [Переменные в ссылках Grafana](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/create-dashboard-url-variables/)

Проверьте итог на Кр1 за апрель 2024. В отчёте и Superset у давления должны быть 28 пригодных дней станции, 2 пригодных дня показателя и пустое месячное среднее. В данных сравнения за 2025 год для 13-го часа должно быть около +0,86 °C и 351 пара при расчёте Кр1 − Кр2.

### Локальный стенд с Grafana

Заполните `deploy/catalog/.env` по примеру, указав `METEO_PUBLIC_HOST=127.0.0.1` и адрес локального Superset. Конфигурация публикует портал вместе с Grafana на `127.0.0.1:8060`.

```bash
docker compose --env-file deploy/catalog/.env -f deploy/catalog/compose.yaml -f deploy/catalog/compose.local.yaml up --build -d
docker compose --env-file deploy/catalog/.env -f deploy/catalog/compose.yaml -f deploy/catalog/compose.local.yaml exec app python manage.py createsuperuser
```

## Развёртывание на виртуальной машине

Используйте Linux-ВМ в командном каталоге Yandex Cloud с активным платёжным аккаунтом или действующую Linux-ВМ команды. Для небольшого пилота достаточно одного экземпляра приложения с постоянным диском. На ВМ нужны Docker и Docker Compose, доменное имя с A-записью на адрес ВМ и доступ к портам 80 и 443. SSH-доступ ограничивают адресами администраторов. [ВМ в Yandex Compute Cloud](https://yandex.cloud/ru/docs/compute/quickstart/quick-create-linux) · [Автоматический HTTPS Caddy](https://caddyserver.com/docs/automatic-https)

1. Доставьте код на сервер и создайте `deploy/catalog/.env` по примеру `.env.example`. Укажите в `METEO_PUBLIC_HOST` доменное имя без схемы. Для каждого из пяти секретов, отмеченных в примере, сгенерируйте отдельное значение, например `openssl rand -hex 32`. Ограничьте права файла командой `chmod 600 deploy/catalog/.env`. Для вебхука укажите адрес получателя в `METEO_WEBHOOK_URL`. Для коммерческого Open-Meteo задайте ключ в `METEO_OPEN_METEO_KEY`.
2. Из корня проекта запустите конфигурацию. Приложение применит миграции и создаст каталог и группы. Caddy получит сертификат, Grafana и InfluxDB будут доступны из сети контейнеров.

   ```bash
   docker compose --env-file deploy/catalog/.env -f deploy/catalog/compose.yaml up --build -d
   docker compose --env-file deploy/catalog/.env -f deploy/catalog/compose.yaml exec app python manage.py createsuperuser
   ```

3. Откройте адрес по HTTPS. В `/admin/` назначьте сотрудникам группу **Исследователи** или **Читатели**. У первой группы есть права создавать место, станцию и набор. Вторая работает с выданными версиями.
4. Проверьте `/health/`, вход, импорт и выгрузку. После импорта и появления ссылки Grafana откройте дашборд из отчёта. Проверьте одну попытку чтения того же источника через Grafana под пользователем без доступа, затем выдайте и отзовите доступ и повторите запрос. Эта проверка требуется на запущенных Grafana и InfluxDB.
5. При необходимости скопируйте архив в контейнер через `docker compose cp` и вызовите `import_case_archive` или `replay_case` через `docker compose exec app python manage.py`. Для воспроизведения используйте URL своего портала. Параметр `--webhook` добавляет отправку через настроенный адрес.

### Контракт входа станции

Владелец станции создаёт поток в карточке станции. Страница показывает адрес и токен входа. В HTTPS POST передаётся JSON с обязательными полями `id`, `datetime` в ISO 8601 с зоной и `values` в канонических единицах. Выборочная запись может содержать только интересующий показатель. Пример полезной нагрузки:

```json
{"id":"sample-20241122-001","datetime":"2024-11-22T10:15:00+03:00","values":{"t_air":-2.6,"pressure_hpa":1004.5}}
```

Аутентификация — `Authorization: Bearer <токен>`. Код 201 сохраняет наблюдение, в том числе опоздавшее. Код 200 обозначает повтор события. Код 409 обозначает повтор идентификатора с изменившимся содержимым. Хранятся исходная отметка времени, значения и флаги. Вебхук использует `Idempotency-Key` для повторных отправок.

### Хранение и резервные копии

ZIP, CSV, XLSX и JSON до 10 МиБ сохраняются в SQLite на постоянном томе `catalog-data`. Резервная копия включает исходники, версии, права и поток. Для полного восстановления идентификаторов дашбордов также сохраните тома Grafana и InfluxDB.

```bash
docker compose --env-file deploy/catalog/.env -f deploy/catalog/compose.yaml exec app python manage.py backup_catalog /data/backups/catalog-2026-11-22.sqlite3
docker compose --env-file deploy/catalog/.env -f deploy/catalog/compose.yaml cp app:/data/backups/catalog-2026-11-22.sqlite3 ./catalog-2026-11-22.sqlite3
```

Храните копию отдельно от сервера с ограничением доступа. Для проверки восстановления создайте отдельный `METEO_DATA_DIR`, положите копию как `catalog.sqlite3` и запустите приложение с этой настройкой. Проверьте, что видны станции и скачивается CSV. Для эксплуатации назначьте периодичность резервного копирования в команде сопровождения.

Когда понадобятся несколько экземпляров приложения или частые параллельные загрузки больших файлов, запланируйте PostgreSQL и приватное объектное хранилище. Текущая конфигурация рассчитана на пилот с одним экземпляром. [Проверки перед запуском Django](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/) · [Доступ к приватному бакету](https://yandex.cloud/ru/docs/storage/operations/buckets/iam-access)
