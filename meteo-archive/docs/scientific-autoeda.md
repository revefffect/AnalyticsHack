# AutoEDA в научных проектах

**Для Метеоархива ближайший научный образец — CMS AutoDQM плюс правила IOOS QARTOD.** Первый показывает, как учёный разбирает отклонение относительно эталона. Второй даёт реализованные проверки для сенсорных временных рядов. Для быстрого обзора CSV подходит FG Data Profiling, а для ручного исследования — уже знакомый PyGWalker. Эти компоненты решают разные этапы работы с данными. [Работа CMS по AutoDQM](https://cds.cern.ch/record/2957148) · [IOOS QC](https://github.com/ioos/ioos_qc) · [FG Data Profiling](https://github.com/Data-Centric-AI-Community/fg-data-profiling) · [PyGWalker](https://github.com/Kanaries/pygwalker)

## Как искали

Поиск GitHub начался с проектов CMS и IRIS-HEP, затем прошёл по зависимостям их аналитических пакетов, пакетам визуализации и проектам контроля качества. Имена репозиториев и звёзды сверены по страницам GitHub 22 ноября 2026 года. Звёзды показывают популярность, а использование в научном процессе подтверждают материалы эксперимента, рабочий пример или код интеграции.

1. **CMS → ROOT и Coffea.** CMS показывает две ветки анализа NanoAOD. ROOT даёт RDataFrame для фильтрации, гистограмм и параллельных расчётов. Coffea использует Uproot для чтения ROOT, Awkward Array для вложенных событий и Hist для распределений. В манифесте Coffea есть опциональные Dask, dask-awkward и dask-histogram. Манифест Hist ведёт дальше к boost-histogram. В IRIS-HEP Analysis Grand Challenge есть исполняемый пример Coffea и dask-awkward. [CMS Analysis Frameworks](https://cds.cern.ch/record/2902863/files/DP2024_040.pdf) · [ROOT](https://github.com/root-project/root) · [Coffea pyproject](https://github.com/scikit-hep/coffea/blob/master/pyproject.toml) · [Hist pyproject](https://github.com/scikit-hep/hist/blob/main/pyproject.toml) · [AGC](https://github.com/iris-hep/analysis-grand-challenge) · [Пример AGC](https://github.com/iris-hep/calver-coffea-agc-demo)
2. **CMS DQM → AutoDQM и Run Registry.** Сервис AutoDQM сравнивает гистограммы текущего запуска с эталонными и выделяет участки для проверки экспертом. Run Registry фиксирует результат сертификации — откуда возникло решение и какие данные годятся для анализа. ШАД также опубликовала отдельный эксперимент на открытых данных CMS с подсказками о причине аномалии. Это исследовательский репозиторий команды, применение в штатной эксплуатации CMS из его README не следует. [Статья авторов AutoDQM](https://cds.cern.ch/record/2957148) · [AutoDQM](https://github.com/AutoDQM/AutoDQM) · [Run Registry](https://github.com/cms-DQM/runregistry) · [Эксперимент ШАД](https://github.com/yandexdataschool/cms-dqm)
3. **Наблюдения Земли → IOOS QARTOD и xarray.** У приёмника океанографических данных IOOS есть рабочий путь «температура → диапазон, скачок, скорость изменения, зависание → отдельные флаги». Ocean Observatories Initiative хранит конфигурации проверок для каждого класса прибора и местоположения. Для пространственно-временных сеток репозиторий xarray ведёт к Dask, NetCDF и Zarr. Это ветка для сеточного ERA5; нынешние станционные файлы — таблицы. [Интеграция в IOOS](https://github.com/ioos/glider-dac/issues/277) · [Конфигурации OOI](https://github.com/oceanobservatories/qc-lookup) · [Зависимости xarray](https://github.com/pydata/xarray/blob/main/doc/getting-started-guide/installing.rst)
4. **Интерактивный EDA → PyGWalker, Graphic Walker и DuckDB.** PyGWalker открывает таблицу в интерактивном конструкторе. Graphic Walker — редактор визуализаций. Манифест PyGWalker содержит зависимость DuckDB, которую также можно оценить отдельно для запросов к большим CSV и Parquet на одном сервере. [PyGWalker](https://github.com/Kanaries/pygwalker) · [Зависимости PyGWalker](https://github.com/Kanaries/pygwalker/blob/main/pyproject.toml) · [Graphic Walker](https://github.com/Kanaries/graphic-walker) · [DuckDB](https://github.com/duckdb/duckdb)

## Короткий список

| Проект | Звёзды GitHub | Реальная роль |
|---|---:|---|
| [ROOT / RDataFrame](https://github.com/root-project/root) | ≈3,4 тыс. | CERN. Высокопроизводительный анализ больших файлов событий |
| [Coffea](https://github.com/scikit-hep/coffea) · [Uproot](https://github.com/scikit-hep/uproot) · [Awkward](https://github.com/scikit-hep/awkward) · [Hist](https://github.com/scikit-hep/hist) | 146 · 274 · 989 · 140 | Python-стек анализа событий CMS, подтверждён в материалах CMS и IRIS-HEP |
| [Dask](https://github.com/dask/dask) | ≈14 тыс. | Движок параллельных и распределённых вычислений, используемый в ветке Coffea |
| [AutoDQM](https://github.com/AutoDQM/AutoDQM) · [cms-dqm ШАД](https://github.com/yandexdataschool/cms-dqm) | 4 · 11 | Научные ориентиры для автоматического контроля качества и разбора аномалий |
| [IOOS QC](https://github.com/ioos/ioos_qc) | 70 | Ближайший готовый набор правил качества рядов датчиков |
| [FG Data Profiling](https://github.com/Data-Centric-AI-Community/fg-data-profiling) | ≈13,8 тыс. | Автоматический обзор типов, пропусков, дублей, распределений и зависимостей. Бывший ydata-profiling |
| [Pandera](https://github.com/unionai-oss/pandera) | ≈4,5 тыс. | Контракт таблицы. Удобен при добавлении разных форматов датчиков |
| [PyGWalker](https://github.com/Kanaries/pygwalker) | ≈16 тыс. | Исследователь вручную строит графики и проверяет гипотезы |
| [xarray](https://github.com/pydata/xarray) · [DuckDB](https://github.com/duckdb/duckdb) | ≈4,2 тыс. · ≈43,2 тыс. | Сетки NetCDF/Zarr и быстрые локальные запросы к таблицам соответственно |

## Что берём для хакатона

1. **Автоматический паспорт качества после импорта.** Первыми показать долю пригодных дней, длительность пропусков, причины флагов и исходные записи. Подход CMS переносится как сравнение одной станции и показателя с сопоставимым эталоном. Для температуры это тот же сезон и час. Для разных станций потребуется один и тот же период и общее покрытие. Подозрительная аномалия остаётся видна сотруднику с причиной, а правило кейса определяет, входит ли значение в сводку.
2. **Проверки датчика из QARTOD.** Заимствовать разделение «подозрительно», «ошибка» и «проверка невозможна» для скачка, зависания и скорости изменения. Порог океанографического датчика нельзя переносить на метеостанцию. Его нужно обосновать единицами, моделью, частотой и фактическими наблюдениями.
3. **FG Data Profiling и PyGWalker оставить в исследовательском режиме.** FG строит HTML-профиль выбранной станции и периода. PyGWalker позволяет быстро проверить странный ряд точками или сравнить две переменные. Официальный отчёт строится из общего ядра правил кейса. Для перехода на DuckDB, Dask или xarray есть конкретные причины: рост стоимости чтения больших табличных архивов, необходимость распределения нагрузки, либо появление сеточных NetCDF/Zarr соответственно.

Демонстрация для жюри начинается с вопроса «Почему в апреле по Кр1 нет надёжного среднего давления?». Проверка автоматически показывает 2 пригодных дня из 30, флаг и исходную строку. Этот сценарий объясняет, зачем здесь AutoEDA: исследователь сужает область проверки и видит основание решения, прежде чем отправить сводку. [Сценарий защиты](platform-release.md)

В Colab можно открыть автоматический профиль того же DataFrame `april`, который использовался в PyGWalker. HTML лучше формировать для выбранной станции и периода. Для анализа рядов полезно сначала убрать из набора технические идентификаторы, сохранив отметку времени и явные признаки качества. [Установка и быстрый старт FG Data Profiling](https://github.com/Data-Centric-AI-Community/fg-data-profiling#-quickstart)

```python
%pip install -q fg-data-profiling
from data_profiling import ProfileReport

profile = ProfileReport(april, title="Кр1 · апрель 2024", minimal=True)
profile.to_file("kr1-april-profile.html")
```
