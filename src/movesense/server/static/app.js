// Movensense Data Browser — minimal SPA
// TOKEN and apiFetch are defined in the global init block (index.html)
const $ = (id) => document.getElementById(id);
const content = $('content');
const breadcrumb = $('breadcrumb');
const subtitle = $('subtitle');

function setBreadcrumb(items) {
  breadcrumb.innerHTML = items.map((item, i) =>
    i < items.length - 1
      ? `<a onclick="${item.action}">${item.label}</a> /`
      : `<span>${item.label}</span>`
  ).join(' ');
}

// --- Views ---

async function showDevices() {
  subtitle.textContent = 'Devices';
  setBreadcrumb([{ label: 'Devices' }]);
  try {
    const data = await apiFetch('/devices');
    if (!data.devices.length) {
      content.innerHTML = '<div class="empty">No data collected yet. Run <code>movesense fetch</code> first.</div>';
      return;
    }
    content.innerHTML = data.devices.map(d => `
      <div class="card" onclick="showDates('${d.serial}')">
        <h3>${d.serial}</h3>
        <div class="meta">${d.date_count} collection date${d.date_count !== 1 ? 's' : ''}</div>
      </div>
    `).join('');
  } catch (e) { if (e.message !== '401') content.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function showDates(serial) {
  subtitle.textContent = serial;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial }
  ]);
  try {
    const data = await apiFetch(`/devices/${serial}/dates`);
    content.innerHTML = data.dates.map(d => `
      <div class="card" onclick="showSessions('${serial}', '${d}')">
        <h3>${d}</h3>
      </div>
    `).join('');
  } catch (e) { content.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function showSessions(serial, date) {
  subtitle.textContent = `${serial} / ${date}`;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial, action: `showDates('${serial}')` },
    { label: date }
  ]);
  try {
    const data = await apiFetch(`/devices/${serial}/dates/${date}/sessions`);
    content.innerHTML = data.sessions.map(s => `
      <div class="card" onclick="showChannels('${serial}', '${date}', ${s.log_id})">
        <h3>Log ${s.log_id}</h3>
        <div class="meta">${s.channels.map(c => `<span class="badge">${c}</span>`).join('')}</div>
      </div>
    `).join('');
  } catch (e) { content.innerHTML = `<div class="error">${e.message}</div>`; }
}

let dataChart = null;
let channelPicker = null;

async function showChannels(serial, date, logId) {
  subtitle.textContent = `Log ${logId}`;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial, action: `showDates('${serial}')` },
    { label: date, action: `showSessions('${serial}', '${date}')` },
    { label: `Log ${logId}` }
  ]);

  content.innerHTML = `
    <div style="display:flex;gap:1rem;align-items:flex-start;">
      <div id="data-picker" style="min-width:160px;"></div>
      <div style="flex:1;">
        <div style="margin-bottom:0.5rem;">
          <button onclick="if(dataChart&&dataChart._plot)dataChart._plot.setScale('x',{min:dataChart._data[0][0],max:dataChart._data[0][dataChart._data[0].length-1]})" style="font-size:0.75rem;">Reset Zoom</button>
          <button onclick="dataChart&&dataChart.captureScreenshot()" style="font-size:0.75rem;">📷</button>
        </div>
        <div id="data-chart"></div>
      </div>
    </div>`;

  // Load channel metadata
  try {
    const meta = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels`);
    const channels = meta.channels || [];

    // Setup channel picker
    if (typeof ChannelPicker !== 'undefined') {
      channelPicker = new ChannelPicker('data-picker', {
        onToggle: (ch, enabled) => loadSessionData(serial, date, logId),
      });
      window.channelPicker = channelPicker;
      channelPicker.setChannels(channels.map(c => ({
        name: c.name, rate_hz: c.sampling_rate_hz, unit: c.unit || '',
      })));
    }

    // Setup chart and load data
    if (typeof UnifiedChart !== 'undefined') {
      dataChart = new UnifiedChart('data-chart', { mode: 'static' });
      await loadSessionData(serial, date, logId);
    }
  } catch (e) { content.innerHTML = `<div class="error">${e.message}</div>`; }
}

async function loadSessionData(serial, date, logId) {
  if (!dataChart) return;
  const selected = channelPicker ? channelPicker.getSelected() : [];
  if (!selected.length) { dataChart.clear(); return; }

  dataChart.clear();

  // Load each channel via downsample API
  for (const chName of selected) {
    try {
      const ds = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels/${chName}/downsample?buckets=2000`);
      if (ds.data && ds.data.time) {
        dataChart.loadSegments([{
          session_index: logId, channel: chName,
          data: ds.data, rate_hz: ds.sampling_rate_hz,
        }], [chName]);
      }
    } catch (e) { /* skip failed channels */ }
  }
}

async function showChannelData(serial, date, logId, channel) {
  subtitle.textContent = channel;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial, action: `showDates('${serial}')` },
    { label: date, action: `showSessions('${serial}', '${date}')` },
    { label: `Log ${logId}`, action: `showChannels('${serial}','${date}',${logId})` },
    { label: channel }
  ]);
  try {
    const data = await apiFetch(`/devices/${serial}/dates/${date}/sessions/${logId}/channels/${channel}/data?limit=1000`);
    const values = Array.isArray(data.data[0]) ? data.data.map(r => r[0]) : data.data;
    const min = Math.min(...values).toFixed(4);
    const max = Math.max(...values).toFixed(4);
    const mean = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(4);
    const duration = data.sampling_rate_hz ? (data.total_samples / data.sampling_rate_hz).toFixed(1) + 's' : '-';

    content.innerHTML = `
      <table>
        <tr><th>Total samples</th><td>${data.total_samples}</td></tr>
        <tr><th>Sampling rate</th><td>${data.sampling_rate_hz || '-'} Hz</td></tr>
        <tr><th>Duration</th><td>${duration}</td></tr>
        <tr><th>Unit</th><td>${data.unit || '-'}</td></tr>
        <tr><th>Min (first 1000)</th><td>${min}</td></tr>
        <tr><th>Max (first 1000)</th><td>${max}</td></tr>
        <tr><th>Mean (first 1000)</th><td>${mean}</td></tr>
        <tr><th>Showing</th><td>${data.offset}–${data.offset + values.length} of ${data.total_samples}</td></tr>
      </table>
    `;
  } catch (e) { content.innerHTML = `<div class="error">${e.message}</div>`; }
}

// --- Init ---
if (!TOKEN) {
  content.innerHTML = '<div class="error">No token provided. Start the server and use the URL shown in the terminal.</div>';
  subtitle.textContent = 'Authentication required';
} else {
  showDevices();
}
