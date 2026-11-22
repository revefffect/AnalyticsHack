(() => {
  const host = document.getElementById('weather-chart');
  const payload = document.getElementById('weather-chart-data');
  if (!host || !payload || !window.echarts) return;
  const data = JSON.parse(payload.textContent);
  const hint = document.querySelector('[data-chart-hint]');
  const green = '#117666';
  const orange = '#bd732c';
  const available = data.metric === 'wind_dir' ? data.directions.length : data.periods.length;
  if (!available) return;

  host.hidden = false;
  const chart = echarts.init(host, null, { renderer: 'canvas' });
  const numericLabel = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

  if (data.metric === 'wind_dir') {
    chart.setOption({
      angleAxis: { type: 'category', data: data.directions.map(sector => String(sector[0]) + '°'), startAngle: 90 },
      radiusAxis: { axisLabel: { formatter: '{value} %' } },
      polar: {},
      tooltip: { renderMode: 'richText', valueFormatter: value => numericLabel.format(value) + ' %' },
      series: [{ type: 'bar', coordinateSystem: 'polar', data: data.directions.map(sector => sector[1]), itemStyle: { color: green } }],
      aria: { enabled: true },
    });
    hint.textContent = 'Роза ветров · доля наблюдений по направлениям.';
  } else {
    const dates = data.periods.map(period => period.start);
    const byDate = new Map(data.periods.map(period => [period.start, period]));
    const coverage = period => Math.round(1000 * period.metric_days / period.period_days) / 10;
    chart.setOption({
      animation: false,
      color: [green, green, orange],
      aria: { enabled: true },
      grid: [
        { left: 62, right: 22, top: 20, bottom: '38%' },
        { left: 62, right: 22, top: '70%', bottom: 72 },
      ],
      xAxis: [
        { type: 'category', data: dates, axisLabel: { hideOverlap: true } },
        { type: 'category', gridIndex: 1, data: dates, axisLabel: { show: false } },
      ],
      yAxis: [
        { type: 'value', name: data.unit, scale: true },
        { type: 'value', name: '%', gridIndex: 1, min: 0, max: 100, splitNumber: 2 },
      ],
      tooltip: {
        trigger: 'axis',
        renderMode: 'richText',
        formatter: entries => {
          const period = byDate.get(entries[0]?.axisValue);
          if (!period) return '';
          const value = period.mean === null ? 'Среднее отсутствует' : numericLabel.format(period.mean) + ' ' + data.unit;
          return [period.start, value, 'Пригодно ' + period.metric_days + ' из ' + period.period_days + ' дней', period.state].join('\n');
        },
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1] },
        { type: 'slider', xAxisIndex: [0, 1], bottom: 17, height: 23 },
      ],
      series: [
        { name: 'Среднее', type: 'line', data: data.periods.map(period => period.mean), connectNulls: false, symbolSize: 7 },
        { name: 'Пригодные', type: 'bar', stack: 'coverage', data: data.periods.map(coverage), xAxisIndex: 1, yAxisIndex: 1 },
        { name: 'Неполные', type: 'bar', stack: 'coverage', data: data.periods.map(period => 100 - coverage(period)), xAxisIndex: 1, yAxisIndex: 1 },
      ],
    });
    hint.textContent = 'Нижняя полоса показывает покрытие. Нажмите на период, чтобы открыть его подробности.';
    chart.on('click', event => {
      const period = data.periods[event.dataIndex];
      if (!period || event.componentType !== 'series') return;
      const query = new URLSearchParams(data.query);
      query.set('metric', data.metric);
      query.set('step', 'day');
      if (data.step === 'day') {
        query.set('start', data.start);
        query.set('end', data.end);
        query.set('detail', period.start);
        window.location.assign(data.report + '?' + query + '#qc');
      } else {
        query.set('start', period.start > data.start ? period.start : data.start);
        query.set('end', period.end < data.end ? period.end : data.end);
        query.delete('detail');
        window.location.assign(data.report + '?' + query + '#results');
      }
    });
  }
  new ResizeObserver(() => chart.resize()).observe(host);
})();
