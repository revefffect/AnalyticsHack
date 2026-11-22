"""Private dashboard settings and schema-validated DeepSeek suggestions."""
from datetime import date
import json
import os
from typing import Annotated, Literal
from urllib.error import URLError
from urllib.request import Request, urlopen

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from meteo.analytics import PERIODS
from meteo.importer import PARAMETERS

NOTE = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
POINT_KEY = Annotated[str, StringConstraints(pattern=r'^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?\|\d{1,5}$')]
FINITE_VALUE = Annotated[float, Field(ge=-1e9, le=1e9)]
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'
YANDEX_URL = 'https://ai.api.cloud.yandex.net/v1/chat/completions'
PANEL_LABELS = {
    'chart': 'график', 'metrics': 'показатели', 'table': 'таблица', 'quality': 'качество данных',
    'insights': 'анализ', 'annotations': 'разметка',
}
Panel = Literal['chart', 'metrics', 'table', 'quality', 'insights', 'annotations']


def unique_panels(items):
    if len(set(items)) != len(items):
        raise ValueError('Каждая панель может быть выбрана один раз')
    return items


Panels = Annotated[list[Panel], Field(min_length=1, max_length=len(PANEL_LABELS)), AfterValidator(unique_panels)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ChartPatch(StrictModel):
    panels: Panels | None = None
    kind: Literal['line', 'bar', 'scatter', 'histogram'] | None = None
    method: Literal['mad', 'zscore', 'off'] | None = None
    window: int | None = Field(default=None, ge=3, le=90)
    threshold: float | None = Field(default=None, ge=0.5, le=12)
    reference: bool | None = None


class ChartSettings(ChartPatch):
    panels: Panels = Field(default_factory=lambda: list(PANEL_LABELS))
    kind: Literal['line', 'bar', 'scatter', 'histogram'] = 'line'
    method: Literal['mad', 'zscore', 'off'] = 'mad'
    window: int = Field(default=14, ge=3, le=90)
    threshold: float = Field(default=3.5, ge=0.5, le=12)
    reference: bool = False


class ChartSelection(StrictModel):
    station: str = Field(min_length=1, max_length=100)
    parameter: str
    start: date
    end: date
    step: str = 'month'

    @model_validator(mode='after')
    def check_range(self):
        if (self.parameter not in PARAMETERS or self.step not in PERIODS
                or not 1800 <= self.start.year <= self.end.year <= 2200 or self.start > self.end):
            raise ValueError('Проверьте показатель, агрегацию и даты')
        return self


class PointEdit(StrictModel):
    action: Literal['keep', 'exclude', 'replace']
    original: FINITE_VALUE
    value: FINITE_VALUE | None = None
    note: NOTE

    @model_validator(mode='after')
    def check_replacement(self):
        if (self.action == 'replace') != (self.value is not None):
            raise ValueError('Укажите численную оценку для замены')
        return self


class ChartDraft(StrictModel):
    settings: ChartSettings = Field(default_factory=ChartSettings)
    edits: dict[POINT_KEY, PointEdit] = Field(default_factory=dict, max_length=200)


class SaveChart(ChartDraft):
    selection: ChartSelection


class ChatTurn(StrictModel):
    role: Literal['user', 'assistant']
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class AssistantRequest(StrictModel):
    selection: ChartSelection
    settings: ChartSettings
    messages: list[ChatTurn] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def check_last_turn(self):
        if self.messages[-1].role != 'user':
            raise ValueError('Введите вопрос или цель визуализации')
        return self


class AssistantReply(StrictModel):
    reply: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    questions: list[NOTE] = Field(default_factory=list, max_length=3)
    patch: ChartPatch = Field(default_factory=ChartPatch)


def local_guide(prompt, settings):
    target = prompt.casefold()
    kind = next((kind for keyword, kind in (
        ('гистограмм', 'histogram'), ('распределен', 'histogram'), ('столб', 'bar'),
        ('точечн', 'scatter'), ('точками', 'scatter'), ('лини', 'line'), ('динамик', 'line'),
    ) if keyword in target), None)
    stems = {'график': 'chart', 'показател': 'metrics', 'метрик': 'metrics',
             'таблиц': 'table', 'качеств': 'quality', 'анализ': 'insights', 'разметк': 'annotations'}
    mentions = sorted((target.index(stem), panel) for stem, panel in stems.items() if stem in target)
    named = list(dict.fromkeys(panel for _, panel in mentions))
    if named and any(word in target for word in ('только', 'оставь', 'скрой', 'убери', 'добавь')):
        if 'скрой' in target or 'убери' in target:
            panels = [panel for panel in settings.panels if panel not in named]
        elif 'добавь' in target:
            panels = list(dict.fromkeys([*settings.panels, *named]))
        else:
            panels = named
        if not panels:
            return AssistantReply(reply='Нужна хотя бы одна панель.', questions=['Какую панель оставить?'])
        layout = 'Порядок панелей: ' + ', '.join(PANEL_LABELS[p] for p in panels) + '.'
        chart_names = {'line': 'Линейный график', 'bar': 'Столбчатая диаграмма',
                       'scatter': 'Точечный график', 'histogram': 'Гистограмма'}
        reply = chart_names[kind] + '. ' + layout if kind else layout
        return AssistantReply(reply=reply, patch=ChartPatch(kind=kind, panels=panels))
    if kind is None:
        return AssistantReply(reply='Могу подобрать график по цели.', questions=[
            'Что вы хотите увидеть: динамику, отдельные измерения или распределение?',
            'За какой период сравнивать значение с предыдущими точками?',
        ])
    return AssistantReply(reply='Предлагаю изменить вид графика. Проверьте его сразу после применения.',
                          questions=['Показать локальный ориентир или оставить только значения?'] if kind != 'histogram' else [],
                          patch=ChartPatch(kind=kind))


SYSTEM_PROMPT = """Ты помогаешь исследователю выбрать настройки визуализации.
Отвечай на русском. Если цель неясна, задай до трёх коротких уточняющих вопросов.
Верни один JSON-объект: reply (текст до 2000 символов), questions (массив строк), patch (объект).
В patch включай только нужные изменения из списка: kind (line, bar, scatter, histogram),
method (mad: медиана и 1.4826×MAD, zscore: среднее и выборочное стандартное отклонение, off),
window (3–90 предыдущих периодов), threshold (0.5–12), reference (true/false).
panels — полный список видимых панелей по порядку: chart (график), metrics (показатели),
table (таблица), quality (качество), insights (анализ), annotations (разметка).
В списке от одной до шести панелей, без повторов. Сохраняй состав панелей, если о нём не просили.
Пример ответа: {"reply":"Оставим график и таблицу.","questions":[],"patch":{"panels":["chart","table"]}}.
Оба метода сравнивают точку только с предшествующими значениями. Для направления ветра метод off.
Измерения тебе недоступны: не выдумывай значения, даты выбросов или физические причины.
Правки к точкам делает исследователь. Если менять настройки рано, верни пустой patch."""


class AssistantUnavailable(RuntimeError):
    pass


def chart_assistant(request, fetch=None):
    direct_key = os.getenv('DEEPSEEK_API_KEY', '').strip()
    yandex_key = os.getenv('YANDEX_API_KEY', '').strip()
    folder = os.getenv('YANDEX_FOLDER_ID', '').strip()
    if not direct_key and not (yandex_key and folder):
        source, answer = 'local', local_guide(request.messages[-1].content, request.settings)
    else:
        if direct_key:
            source, url = 'deepseek', DEEPSEEK_URL
            model = os.getenv('DEEPSEEK_MODEL', '').strip() or 'deepseek-flash'
            headers = {'Authorization': 'Bearer ' + direct_key}
        else:
            source, url = 'yandex', YANDEX_URL
            slug = os.getenv('YANDEX_DEEPSEEK_MODEL', '').strip() or 'deepseek-v4.1-flash'
            model = f'gpt://{folder}/{slug}'
            headers = {'Authorization': 'Api-Key ' + yandex_key, 'OpenAI-Project': folder}
        selection = request.selection
        context = {
            'parameter': PARAMETERS[selection.parameter][0], 'unit': PARAMETERS[selection.parameter][1],
            'step': selection.step, 'settings': request.settings.model_dump(),
        }
        body = {
            'model': model, 'max_tokens': 2048,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT + '\nКонтекст: ' + json.dumps(context, ensure_ascii=False)},
                *(turn.model_dump() for turn in request.messages),
            ],
        }
        upstream = Request(url, data=json.dumps(body).encode(), headers={**headers, 'Content-Type': 'application/json'})
        try:
            with (fetch or urlopen)(upstream, timeout=15) as response:
                payload = json.loads(response.read(128 * 1024))
            answer = AssistantReply.model_validate_json(payload['choices'][0]['message']['content'])
        except (URLError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
            label = 'DeepSeek API' if source == 'deepseek' else 'Yandex AI Studio'
            raise AssistantUnavailable(f'{label} не вернул допустимые настройки. Повторите запрос.') from error
    patch = answer.patch.model_dump(exclude_none=True)
    if request.selection.parameter == 'wind_dir':
        for option in ('method', 'window', 'threshold', 'reference'):
            patch.pop(option, None)
    patch = {key: value for key, value in patch.items() if getattr(request.settings, key) != value}
    return {'source': source, 'reply': answer.reply, 'questions': answer.questions, 'patch': patch}
