// Calendar view for data coverage
const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];

class CalendarView {
  constructor(containerId, apiFetch, onDayClick) {
    this.container = document.getElementById(containerId);
    this.apiFetch = apiFetch;
    this.onDayClick = onDayClick;
    this.year = new Date().getFullYear();
    this.month = new Date().getMonth() + 1;
    this.serial = null;
    this.data = null;
  }

  setDevice(serial) {
    this.serial = serial;
    this.render();
  }

  prevMonth() {
    this.month--;
    if (this.month < 1) { this.month = 12; this.year--; }
    this.render();
  }

  nextMonth() {
    this.month++;
    if (this.month > 12) { this.month = 1; this.year++; }
    this.render();
  }

  async render() {
    if (!this.serial) {
      this.container.innerHTML = '<div class="empty">Select a device to view calendar.</div>';
      return;
    }

    try {
      this.data = await this.apiFetch(`/devices/${this.serial}/coverage/${this.year}/${this.month}`);
    } catch (e) {
      this.container.innerHTML = `<div class="error">${e.message}</div>`;
      return;
    }

    const days = this.data.days || [];
    const summary = this.data.summary || {};
    const dayMap = {};
    for (const d of days) dayMap[d.date] = d;

    // Build calendar grid
    const firstDay = new Date(this.year, this.month - 1, 1).getDay();
    const daysInMonth = new Date(this.year, this.month, 0).getDate();

    let html = `
      <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;">
        <button onclick="calendarView.prevMonth()">◀</button>
        <h3 style="margin:0;min-width:180px;text-align:center">${MONTHS[this.month-1]} ${this.year}</h3>
        <button onclick="calendarView.nextMonth()">▶</button>
      </div>
      <div class="cal-grid">
        <div class="cal-header">Sun</div><div class="cal-header">Mon</div><div class="cal-header">Tue</div>
        <div class="cal-header">Wed</div><div class="cal-header">Thu</div><div class="cal-header">Fri</div><div class="cal-header">Sat</div>
    `;

    // Empty cells before first day
    for (let i = 0; i < firstDay; i++) html += '<div class="cal-cell empty"></div>';

    for (let day = 1; day <= daysInMonth; day++) {
      const dateStr = `${this.year}-${String(this.month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
      const info = dayMap[dateStr];
      let cls = 'cal-cell';
      let title = 'No data';

      if (info) {
        cls += ` cal-${info.level}`;
        const hrs = (info.total_duration_s / 3600).toFixed(1);
        title = `${info.session_count} session(s), ${hrs}h\\n${info.channels.join(', ')}`;
      }

      const clickable = info ? `onclick="calendarView.onDayClick('${this.serial}','${dateStr}')"` : '';
      html += `<div class="${cls}" title="${title}" ${clickable}><span>${day}</span></div>`;
    }

    html += '</div>';

    // Summary stats
    if (summary.days_with_data > 0) {
      html += `
        <div class="cal-summary">
          <div><strong>${summary.days_with_data}</strong> days with data</div>
          <div><strong>${summary.total_hours}</strong> total hours</div>
          <div><strong>${summary.avg_daily_hours}</strong> avg hours/day</div>
          <div><strong>${summary.longest_gap_days}</strong> day longest gap</div>
        </div>
      `;
    } else {
      html += '<div class="empty" style="margin-top:1rem">No data collected this month.</div>';
    }

    this.container.innerHTML = html;
  }
}

window.CalendarView = CalendarView;
