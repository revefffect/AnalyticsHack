"""Exploratory analysis and one-day backtesting on an existing QC-filtered slice."""
from datetime import date, timedelta
from math import isfinite
from statistics import mean, median

from meteo.importer import PARAMETERS


DAY = timedelta(days=1)
METHODS = {
    'persistence': 'Последнее значение',
    'median_3': 'Медиана трёх суток',
    'linear_3': 'Линейная регрессия по трём суткам',
}


def _shown(value, unit):
    return f'{value:.2f}'.replace('.', ',') + (' ' + unit if unit else '')


def _predictions(history, metric):
    # Least squares with x = [-1, 0, 1], extrapolated to x = 2.
    first, _, last = history
    linear = mean(history) + (last - first)
    lower, upper = PARAMETERS[metric][2:]
    linear = max(lower, linear) if lower is not None else linear
    linear = min(upper, linear) if upper is not None else linear
    return {'persistence': last, 'median_3': median(history), 'linear_3': linear}


def _previous_days(series, target, length):
    values = [series.get(target - offset * DAY) for offset in range(length, 0, -1)]
    return values if None not in values else []


def _model(snapshot, series):
    metric = snapshot['parameter']
    result = {'status': 'abstained', 'reason': '', 'selected': None, 'candidates': [], 'forecast': None}
    if snapshot['step'] != 'day':
        result['reason'] = 'Для прогноза на следующие сутки выберите суточный шаг.'
    elif metric == 'wind_dir':
        result['reason'] = 'Для направления ветра нужна модель угловых величин.'
    elif snapshot.get('source') in {'forecast', 'reanalysis'}:
        result['reason'] = 'Для проверки прогноза требуются наблюдения станции. Выбран ряд внешней модели.'
    if result['reason']:
        return result

    # Each candidate reads the same three *calendar* days preceding each target.
    errors = []
    for target, actual in sorted(series.items()):
        history = _previous_days(series, target, 3)
        if history:
            predictions = _predictions(history, metric)
            errors.append((target, {name: abs(value - actual) for name, value in predictions.items()}))
    if len(errors) < 6:
        result['reason'] = (f'Пригодных проверочных дат {len(errors)} из необходимых 6. '
                            'Каждой дате должны предшествовать трое пригодных суток подряд.')
        return result

    # Reserve the latest third, at least two dates, for a score independent of model choice.
    split = len(errors) - max(2, len(errors) // 3)
    selection, holdout = errors[:split], errors[split:]
    scores = [{'method': name, 'label': label,
               'selection_mae': mean(error[name] for _, error in selection), 'selection_n': len(selection),
               'mae': mean(error[name] for _, error in holdout), 'n': len(holdout)}
              for name, label in METHODS.items()]
    winner = min(scores, key=lambda candidate: candidate['selection_mae'])
    result.update(candidates=scores, selected=winner['method'],
                  selection_start=str(selection[0][0]), selection_end=str(selection[-1][0]),
                  holdout_start=str(holdout[0][0]), holdout_end=str(holdout[-1][0]))
    end = date.fromisoformat(snapshot['end'])
    target = end + DAY
    history = _previous_days(series, target, 3)
    if not history:
        result['reason'] = 'В конце выбранного периода нет трёх последовательных пригодных суток.'
        return result
    model = winner['method']
    result.update(status='forecast', reason='Модель выбрана по минимальной MAE на ранних датах. '
                  'Итоговая MAE рассчитана по следующим отложенным датам.',
                  forecast={'date': str(target), 'training_end': str(end), 'kind': 'forecast',
                            'value': _predictions(history, metric)[model], 'method': model,
                            'label': METHODS[model]})
    return result


def _daily_anomalies(series, unit):
    """Flag retrospective review candidates relative to seven earlier complete QC days."""
    candidates = []
    for target, actual in sorted(series.items()):
        history = _previous_days(series, target, 7)
        if not history:
            continue
        center = median(history)
        mad = median(abs(value - center) for value in history)
        if mad > 0 and abs(actual - center) > 3 * 1.4826 * mad:
            candidates.append({
                'date': str(target), 'value': actual,
                'explanation': (f'Отклонение от медианы семи предыдущих суток '
                                f'({_shown(center, unit)}) превышает 3 × 1,4826 × MAD. '
                                'Точка требует экспертной проверки.'),
            })
    return candidates


def decision_insights(snapshot):
    """Accept a slice_data result or immutable report snapshot without re-reading observations."""
    metric, unit = snapshot['parameter'], snapshot['unit']
    usable = [point for point in snapshot['points']
              if point.get('eligible', True) and point['value'] is not None and isfinite(point['value'])]
    coverage = dict(snapshot['coverage'])
    scalar = metric != 'wind_dir'
    values = [point['value'] for point in usable] if scalar else []
    distribution = {'count': len(usable), 'min': min(values, default=None),
                    'max': max(values, default=None), 'median': median(values) if values else None}
    trend = {'change': None, 'description': 'Для сравнения нужно шесть пригодных точек.'}
    if not scalar:
        trend['description'] = 'Направление ветра задаётся углом. Для него нужен круговой анализ.'
    elif len(values) >= 6:
        before, after = median(values[:3]), median(values[-3:])
        change = after - before
        trend.update(change=change, description=(
            f'Медиана первых трёх пригодных точек — {_shown(before, unit)}, '
            f'последних трёх — {_shown(after, unit)}. Изменение {_shown(change, unit)}.'))

    daily = {date.fromisoformat(point['start']): point['value'] for point in usable
             if snapshot['step'] == 'day' and scalar}
    outliers = _daily_anomalies(daily, unit)
    automl = _model(snapshot, daily)
    management = [{
        'title': 'Покрытие исходных данных',
        'detail': (f"Контроль качества пройден для {coverage['valid_days']} из {coverage['days']} суток. "
                   f"Значение доступно в {coverage['valid_periods']} из {coverage['periods']} периодов."),
    }]
    if not usable:
        management.append({'title': 'Что запросить перед решением',
                           'detail': 'Для выбранного показателя нет пригодного значения на этом шаге. Проверьте исходные сроки и флаги качества. Для сопоставления запросите измерения соседней станции с датой и источником.'})
    if outliers:
        latest = outliers[-1]
        management.append({'title': 'Экспертная проверка',
                           'detail': (f"Дат для проверки: {len(outliers)}. Последняя — {latest['date']}, "
                                      f"{_shown(latest['value'], unit)}. Сверьте исходные сроки, соседнюю станцию и полевой журнал.")})
    if metric in {'t_air', 'rh', 'precipitation', 'wind_speed', 'wind_gust'}:
        management.append({'title': 'Пожарная готовность',
                           'detail': 'Для оценки условий пожара сопоставьте температуру, влажность, осадки и ветер с влажностью горючих материалов и региональным прогнозом опасности.'})
    if metric in {'t_air', 'soil_05', 'soil_10', 'soil_20', 'precipitation'}:
        management.append({'title': 'Весенняя доступность маршрутов',
                           'detail': 'При планировании весенних маршрутов проверьте снегозапас, промерзание почвы, прогноз потепления и уровни ближайших рек.'})
    if automl['forecast']:
        prediction = automl['forecast']
        management.append({'title': 'Горизонт планирования',
                           'detail': f"На следующие за выбранным периодом сутки ({prediction['date']}) оценка ряда составляет {_shown(prediction['value'], unit)}. Для оперативного плана сверьте дату и актуальный прогноз."})
    return {'coverage': coverage, 'distribution': distribution, 'trend': trend,
            'anomalies': outliers[-20:], 'anomaly_count': len(outliers),
            'anomaly_method': 'Эвристика для суточных величин: семь предшествующих суток подряд, ненулевой MAD и порог 3 × 1,4826 × MAD. Показаны до 20 последних дат.',
            'automl': automl, 'management': management, 'unit': unit}
