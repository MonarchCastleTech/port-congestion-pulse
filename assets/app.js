const COMPONENTS = {
  port_flow: ['Port flow', '35%', 'AIS port-call distortion'],
  chokepoint_flow: ['Chokepoint flow', '25%', 'Passage traffic distortion'],
  weather_forecast: ['Weather forecast', '25%', 'Wind and precipitation'],
  hazard_proximity: ['Hazard proximity', '15%', 'GDACS events near ports'],
};

const ui = {
  data: null,

  async init() {
    try {
      const response = await fetch('data/output.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.data = await response.json();
      this.render();
    } catch (error) {
      this.error(error);
    }
  },

  render() {
    const { meta, warning, components } = this.data;
    this.text('mode', meta.mode === 'live' ? 'Live · 6h refresh' : 'Retained snapshot');
    this.text('generated', `Updated ${this.date(meta.generated)}`);
    this.text('score', Number(warning.score).toFixed(1));
    this.text('status', warning.status);
    this.text('headline', warning.headline);
    this.text('interpretation', warning.interpretation);
    this.text('confidence', meta.confidence);
    this.text('coverage', meta.coverage);
    this.text('bonus', warning.concurrence_bonus ? `+${warning.concurrence_bonus.toFixed(0)}` : '+0');
    document.body.dataset.status = warning.status.toLowerCase();
    document.getElementById('score-ring').style.setProperty('--score', `${warning.score * 3.6}deg`);

    const notes = meta.source_notes || [];
    this.text('status-strip', notes.length
      ? `${meta.coverage} components available. ${notes.join(' ')}`
      : `${meta.coverage} components live · ${meta.confidence.toLowerCase()} confidence · next automated refresh within 6 hours.`);

    this.components(components);
    this.flowTable('port-rows', components.port_flow);
    this.flowTable('choke-rows', components.chokepoint_flow);
    this.markers(components);
    this.weather(components.weather_forecast);
    this.hazards(components.hazard_proximity);
    this.history(this.data.history || []);
    this.sources(this.data.sources || []);
  },

  components(components) {
    const root = document.getElementById('components');
    root.replaceChildren(...Object.entries(COMPONENTS).map(([key, labels]) => {
      const component = components[key] || {};
      const card = document.createElement('article');
      card.className = 'component-card';
      const top = document.createElement('div');
      top.className = 'component-top';
      const name = document.createElement('span');
      name.textContent = labels[0];
      const weight = document.createElement('b');
      weight.textContent = labels[1];
      top.append(name, weight);
      const value = document.createElement('strong');
      value.textContent = component.available ? Number(component.score).toFixed(1) : '—';
      const bar = document.createElement('div');
      bar.className = 'meter';
      const fill = document.createElement('i');
      fill.style.width = `${component.score || 0}%`;
      bar.append(fill);
      const copy = document.createElement('small');
      copy.textContent = `${labels[2]} · ${component.status || 'unavailable'}${component.retained ? ' · retained' : ''}`;
      card.append(top, value, bar, copy);
      return card;
    }));
  },

  flowTable(id, component) {
    const body = document.getElementById(id);
    const rows = (component && component.evidence) || [];
    if (!rows.length) return this.emptyRow(body, 'No validated series available.');
    body.replaceChildren(...rows.slice(0, 8).map(item => {
      const row = document.createElement('tr');
      const name = document.createElement('td');
      name.innerHTML = `<b></b><small></small>`;
      name.querySelector('b').textContent = item.name || 'Unknown';
      name.querySelector('small').textContent = item.country || `${item.observations || 0} observations`;
      const direction = document.createElement('td');
      direction.innerHTML = `<span class="direction ${item.direction}"></span>`;
      direction.firstChild.textContent = item.direction;
      const change = document.createElement('td');
      change.className = item.change_pct >= 0 ? 'positive' : 'negative';
      change.textContent = `${item.change_pct >= 0 ? '+' : ''}${Number(item.change_pct).toFixed(1)}%`;
      const pressure = document.createElement('td');
      pressure.innerHTML = `<div class="inline-score"><span></span><i><em></em></i></div>`;
      pressure.querySelector('span').textContent = Number(item.pressure).toFixed(1);
      pressure.querySelector('em').style.width = `${item.pressure}%`;
      row.append(name, direction, change, pressure);
      return row;
    }));
  },

  markers(components) {
    const root = document.getElementById('markers');
    const items = Object.entries(COMPONENTS).map(([key, label]) => ({ key, label: label[0], ...(components[key] || {}) }));
    root.replaceChildren(...items.map(item => {
      const row = document.createElement('div');
      row.className = 'marker';
      const dot = document.createElement('i');
      dot.style.setProperty('--level', `${item.score || 0}%`);
      const copy = document.createElement('div');
      const title = document.createElement('b');
      title.textContent = item.label;
      const meta = document.createElement('span');
      meta.textContent = `${item.available ? Number(item.score).toFixed(1) : '—'} · ${item.coverage || 0} observations/sites`;
      copy.append(title, meta);
      const state = document.createElement('strong');
      state.textContent = item.status || 'UNAVAILABLE';
      row.append(dot, copy, state);
      return row;
    }));
  },

  weather(component) {
    const root = document.getElementById('weather-grid');
    const rows = (component && component.evidence) || [];
    if (!rows.length) return this.emptyBlock(root, 'Forecast unavailable.');
    root.replaceChildren(...rows.slice(0, 6).map(item => {
      const card = document.createElement('article');
      card.className = 'weather-card';
      const title = document.createElement('div');
      title.innerHTML = '<b></b><small></small>';
      title.querySelector('b').textContent = item.name;
      title.querySelector('small').textContent = item.country;
      const metrics = document.createElement('div');
      metrics.className = 'weather-metrics';
      metrics.innerHTML = '<span><b></b> m/s<small>max wind</small></span><span><b></b> mm<small>max daily rain</small></span>';
      metrics.children[0].querySelector('b').textContent = Number(item.max_wind_ms).toFixed(1);
      metrics.children[1].querySelector('b').textContent = Number(item.max_precip_24h_mm).toFixed(1);
      const risk = document.createElement('em');
      risk.textContent = `${Number(item.pressure).toFixed(1)} pressure`;
      card.append(title, metrics, risk);
      return card;
    }));
  },

  hazards(component) {
    const root = document.getElementById('hazards');
    const rows = (component && component.evidence) || [];
    if (!rows.length) return this.emptyBlock(root, 'No current GDACS event inside calibrated port proximity bands.');
    root.replaceChildren(...rows.slice(0, 6).map(item => {
      const link = document.createElement(item.url ? 'a' : 'div');
      link.className = 'hazard';
      if (item.url) { link.href = item.url; link.target = '_blank'; link.rel = 'noopener'; }
      const top = document.createElement('div');
      const type = document.createElement('span');
      type.textContent = item.type;
      const alert = document.createElement('strong');
      alert.textContent = item.alert;
      top.append(type, alert);
      const name = document.createElement('b');
      name.textContent = item.name;
      const meta = document.createElement('small');
      meta.textContent = `${item.nearest_port} · ${item.distance_km} km · ${Number(item.pressure).toFixed(1)}`;
      link.append(top, name, meta);
      return link;
    }));
  },

  history(items) {
    const root = document.getElementById('history-chart');
    if (!items.length) return this.emptyBlock(root, 'History begins with this validated run.');
    const width = 1000, height = 250, pad = 34;
    const values = items.map(item => Number(item.score)).filter(Number.isFinite);
    const points = values.map((value, index) => {
      const x = values.length === 1 ? width / 2 : pad + index * (width - pad * 2) / (values.length - 1);
      const y = height - pad - value / 100 * (height - pad * 2);
      return [x, y, value];
    });
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('role', 'img');
    [25, 45, 65, 80].forEach(level => {
      const y = height - pad - level / 100 * (height - pad * 2);
      const line = document.createElementNS(svg.namespaceURI, 'line');
      line.setAttribute('x1', pad); line.setAttribute('x2', width - pad); line.setAttribute('y1', y); line.setAttribute('y2', y); line.setAttribute('class', 'gridline');
      svg.append(line);
    });
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', points.map((point, i) => `${i ? 'L' : 'M'}${point[0]},${point[1]}`).join(' '));
    path.setAttribute('class', 'history-line');
    svg.append(path);
    points.forEach(point => {
      const circle = document.createElementNS(svg.namespaceURI, 'circle');
      circle.setAttribute('cx', point[0]); circle.setAttribute('cy', point[1]); circle.setAttribute('r', 5); circle.setAttribute('class', 'history-point');
      const title = document.createElementNS(svg.namespaceURI, 'title');
      title.textContent = point[2].toFixed(1); circle.append(title); svg.append(circle);
    });
    root.replaceChildren(svg);
  },

  sources(items) {
    const root = document.getElementById('source-grid');
    root.replaceChildren(...items.map(item => {
      const link = document.createElement('a');
      link.href = item.url; link.target = '_blank'; link.rel = 'noopener';
      const name = document.createElement('b'); name.textContent = item.name;
      const role = document.createElement('span'); role.textContent = item.role;
      link.append(name, role); return link;
    }));
  },

  emptyRow(body, message) {
    const row = document.createElement('tr');
    const cell = document.createElement('td'); cell.colSpan = 4; cell.className = 'empty'; cell.textContent = message; row.append(cell); body.replaceChildren(row);
  },
  emptyBlock(root, message) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = message; root.replaceChildren(p); },
  text(id, value) { const el = document.getElementById(id); if (el) el.textContent = value; },
  date(value) { const d = new Date(value); return Number.isFinite(d.getTime()) ? new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short' }).format(d) : '—'; },
  error(error) {
    document.body.dataset.status = 'severe';
    this.text('mode', 'Unavailable');
    this.text('status-strip', `Dashboard data unavailable: ${error.message}. No stale value is presented as live.`);
    this.text('headline', 'Current warning could not be validated.');
  },
};

ui.init();
