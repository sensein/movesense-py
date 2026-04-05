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
      content.innerHTML = '<div class="empty">No data collected yet. Run <code>movensense fetch</code> first.</div>';
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

let channelViewer = null;
window.channelViewer = null;

async function showChannels(serial, date, logId) {
  subtitle.textContent = `Log ${logId}`;
  setBreadcrumb([
    { label: 'Devices', action: 'showDevices()' },
    { label: serial, action: `showDates('${serial}')` },
    { label: date, action: `showSessions('${serial}', '${date}')` },
    { label: `Log ${logId}` }
  ]);

  content.innerHTML = `
    <div class="cv-layout">
      <div class="cv-sidebar" id="cv-selector"></div>
      <div class="cv-charts" id="cv-charts"></div>
      <div class="cv-stats" id="cv-stats"></div>
    </div>`;

  if (typeof ChannelViewer !== 'undefined') {
    channelViewer = new ChannelViewer('cv-selector', 'cv-charts', 'cv-stats');
    window.channelViewer = channelViewer;
    channelViewer.load(serial, date, logId);
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
