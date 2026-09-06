// Time-based activity view. Aggregate segments and individual jobs share one clock.
(() => {
  const D = window.ProfileData;
  const level = (count) => count >= 4 ? 3 : count >= 2 ? 2 : 1;
  const geometry = (start, end) => `--start:${start / 24 * 100}%;--length:${(end - start) / 24 * 100}%`;
  class ActivityTimeline {
    constructor({ onSelect, onJob, onInspect }) {
      this.onSelect = onSelect;
      this.onJob = onJob;
      this.onInspect = onInspect;
      this.host = document.querySelector('#activity-days');
      this.mobile = matchMedia('(max-width: 759px)');
      this.mobileDate = '2026-09-06';
      this.signature = '';
      this.mobile.addEventListener('change', () => {
        // Wait for the viewport to settle before changing the selected date.
        clearTimeout(this.resizeTimer);
        this.resizeTimer = setTimeout(() => {
          if (this.mobile.matches && !this.selected) this.onSelect(this.mobileDate);
          else this.updateSelection();
        }, 120);
      });
      document.querySelector('#time-axis').innerHTML = Array.from({ length: 13 }, (_, i) => `<span style="--position:${i / 12 * 100}%">${String(i * 2).padStart(2, '0')}</span>`).join('');
      this.host.addEventListener('click', (event) => {
        const job = event.target.closest('[data-open-job]');
        if (job) return this.onJob(D.jobs.find((item) => item.id === job.dataset.openJob));
        const day = event.target.closest('[data-day]');
        if (day) this.onSelect(this.selected === day.dataset.day && !this.mobile.matches ? '' : day.dataset.day);
      });
      this.host.addEventListener('keydown', (event) => {
        if (!event.target.matches('.day-select') || !['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
        const buttons = [...this.host.querySelectorAll('.day-select')];
        const index = buttons.indexOf(event.target);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)));
        event.preventDefault();
        buttons[next].focus();
      });
      this.host.addEventListener('pointerover', (event) => {
        const segment = event.target.closest('.activity-segment');
        if (segment) this.onInspect(segment.dataset.jobs?.split(' ') || [], segment.title);
      });
      this.host.addEventListener('pointerout', (event) => {
        if (event.target.closest('.activity-segment')) this.onInspect([], 'Select a day to explore your jobs.');
      });
      this.host.addEventListener('pointerleave', () => this.onInspect([], 'Select a day to explore your jobs.'));
      document.querySelector('#mobile-days').addEventListener('click', (event) => {
        const button = event.target.closest('[data-mobile-day]');
        if (button) this.onSelect(button.dataset.mobileDay);
      });
    }
    render({ days, model, selected, partial }) {
      this.days = days;
      this.selected = selected;
      this.model = model;
      this.partial = partial;
      const signature = `${days[0]}:${model}:${partial}`;
      if (signature !== this.signature) {
        this.signature = signature;
        this.host.classList.toggle('month-view', days.length > 7);
        this.host.innerHTML = days.map((date) => this.day(date)).join('');
        document.querySelector('#mobile-days').innerHTML = days.map((date) => {
          const active = D.summary([date], model, partial).hours > 0;
          return `<button class="mobile-day ${active ? 'has-activity' : ''}" data-mobile-day="${date}" aria-label="${D.dateLabel(date)}"><span>${D.weekday(date).slice(0, 1)}</span><strong>${Number(date.slice(-2))}</strong><i></i></button>`;
        }).join('');
      }
      if (selected) this.mobileDate = selected;
      this.updateSelection();
      this.renderPeak();
    }
    day(date) {
      const spans = D.segments(date, this.model, this.partial);
      const totals = D.summary([date], this.model, this.partial);
      const jobs = D.dayJobs(date, this.model);
      const today = date === '2026-09-06';
      const bars = spans.filter((span) => span.count || span.missing).map((span) => `<span class="activity-segment ${span.missing ? 'missing-span' : `level-${level(span.count)}`}" style="${geometry(span.start, span.end)}" data-jobs="${span.jobs.map((job) => job.id).join(' ')}" title="${D.dateLabel(date)} · ${D.clock(span.start)}–${D.clock(span.end)} · ${span.missing ? 'Not recorded' : `${span.count} GPU${span.count === 1 ? '' : 's'}`}"></span>`).join('');
      const now = today ? '<span class="now-marker" style="--position:69.5833%"><span>Now</span></span><span class="future-span" style="--start:69.5833%;--length:30.4167%"></span>' : '';
      return `<article class="activity-day" data-date="${date}">
        <button class="day-select" data-day="${date}" aria-expanded="false" aria-controls="lanes-${date}" aria-label="${D.dateLabel(date)}, ${totals.hours.toFixed(1)} GPU hours, ${jobs.length} jobs. Expand jobs.">
          <span class="day-label"><i data-lucide="chevron-right"></i><strong>${D.weekday(date)}</strong><span>${D.dateLabel(date)}</span></span>
          <span class="day-track">${bars}${now}${!totals.hours ? `<span class="idle-label">${this.model ? 'No matching activity' : 'No activity'}</span>` : ''}</span>
          <span class="day-total">${totals.hours ? totals.hours.toFixed(1) : '—'}</span>
        </button>
        <div class="day-expansion" id="lanes-${date}" inert><div class="expansion-inner"><div class="day-lanes">${jobs.length ? jobs.map((job) => this.lane(job, date)).join('') : '<p class="idle-detail">No jobs recorded on this day.</p>'}<p class="day-detail-caption">${D.dateLabel(date)} · ${jobs.length} job${jobs.length === 1 ? '' : 's'} · ${D.duration(totals.active)} with active jobs</p></div></div></div>
      </article>`;
    }
    lane(job, date) {
      const start = Math.max(0, (job.startMs - D.midnight(date)) / D.HOUR);
      const end = Math.min(24, (job.endMs - D.midnight(date)) / D.HOUR);
      const continued = job.startMs < D.midnight(date);
      const gap = this.partial && date === '2026-09-04' && start < 17 && end > 13
        ? `<span class="task-span missing-span" style="${geometry(Math.max(13, start), Math.min(17, end))}" title="Not recorded, 13:00–17:00"></span>` : '';
      return `<button class="job-track" data-open-job="${job.id}" title="${job.name} · ${job.gpu} · ${D.clock(start)}–${D.clock(end)}" aria-label="View ${job.name}, ${job.gpu}, ${D.clock(start)} to ${D.clock(end)}${continued ? ', continued from previous day' : ''}">
        <span class="lane-label">${job.name}<small>${job.gpu}</small></span>
        <span class="lane-track"><span class="task-span level-${level(job.count)}" style="${geometry(start, end)}">${continued ? '<span class="continued">←</span>' : ''}</span>${gap}</span>
        <span class="lane-end">${job.state === 'running' ? '<span class="dot"></span>Live' : D.duration(end - start)}</span>
      </button>`;
    }
    updateSelection() {
      this.host.querySelectorAll('.activity-day').forEach((row) => {
        const date = row.dataset.date;
        const mobileDay = date === this.mobileDate;
        const open = this.mobile.matches ? mobileDay : date === this.selected;
        row.classList.toggle('is-expanded', open);
        row.classList.toggle('is-mobile-day', mobileDay);
        row.querySelector('.day-select').setAttribute('aria-expanded', String(open));
        row.querySelector('.day-expansion').inert = !open;
      });
      document.querySelectorAll('[data-mobile-day]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.mobileDay === this.mobileDate)));
      if (this.mobile.matches) {
        const strip = document.querySelector('#mobile-days');
        const active = strip.querySelector('[aria-pressed=true]');
        if (active) {
          const left = active.offsetLeft - strip.offsetLeft;
          if (left < strip.scrollLeft || left + active.offsetWidth > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = left - strip.clientWidth / 2 + active.offsetWidth / 2;
        }
      }
    }
    renderPeak() {
      const values = Array.from({ length: 24 }, () => ({ hours: 0, coverage: 0 }));
      this.days.forEach((date) => D.segments(date, this.model, this.partial).forEach((span) => {
        if (span.missing) return;
        values.forEach((value, hour) => {
          const overlap = Math.max(0, Math.min(hour + 1, span.end) - Math.max(hour, span.start));
          value.hours += overlap * span.count;
          value.coverage += overlap;
        });
      }));
      const averages = values.map((value) => value.coverage ? value.hours / value.coverage : 0);
      // A contiguous three-hour window, calculated from the same observed activity.
      const windows = averages.slice(0, 22).map((_, hour) => averages.slice(hour, hour + 3).reduce((a, b) => a + b, 0));
      const peak = windows.indexOf(Math.max(...windows));
      const max = Math.max(...averages);
      document.querySelector('#peak-label').textContent = max ? `${D.clock(peak)}–${D.clock(peak + 3)}` : 'No activity';

    }
  }
  window.ActivityTimeline = ActivityTimeline;
})();
