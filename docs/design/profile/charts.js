// Shared, dependency-free traces for the design prototype.
// Curves pass through each daily value without exceeding adjacent values.
class ProfileTrace {
  constructor(container, options = {}) {
    this.container = container;
    this.options = options;
    this.values = [];
    this.activeIndex = 0;
    this.observer = new ResizeObserver(() => {
      if (this.container.clientWidth !== this.drawnWidth) this.draw(this.drawnWidth == null);
    });
    this.observer.observe(container);
  }

  setData(values, options = {}) {
    const changed = this.values.join(',') !== values.join(',');
    this.values = values;
    this.options = { ...this.options, ...options };
    this.peakIndex = values.indexOf(Math.max(...values));
    this.activeIndex = this.options.selectedIndex >= 0 ? this.options.selectedIndex : this.peakIndex;
    this.draw(changed);
  }

  setActive(index) {
    this.activeIndex = index >= 0 ? index : this.peakIndex;
    this.updateGuide();
  }

  draw(animate) {
    const width = this.container.clientWidth;
    if (!width || !this.values.length) return;
    this.drawnWidth = width;
    const { axes, interactive, labels: suppliedLabels = [], highlight, hourly } = this.options;
    const hourStep = width >= 320 ? 2 : 3;
    const labels = hourly
      ? this.values.map((_, index) => ({ index, text: String(index).padStart(2, '0') })).filter(({ index }) => index % hourStep === 0)
      : suppliedLabels;
    const height = axes ? 174 : hourly ? 140 : labels.length ? 112 : 100;
    const left = axes ? 30 : 3;
    const right = width - (axes ? 10 : 3);
    const top = axes ? 28 : hourly ? 30 : 12;
    const bottom = height - (labels.length ? 25 : 8);
    const maxValue = Math.max(1, ...this.values);
    const ceiling = axes ? Math.ceil(maxValue / 10) * 10 : maxValue * 1.12;
    this.geometry = { width, left, right, top, bottom };
    this.points = this.values.map((value, index) => ({
      x: left + index / Math.max(1, this.values.length - 1) * (right - left),
      y: bottom - value / ceiling * (bottom - top),
    }));
    const path = ProfileTrace.path(this.points);
    const ticks = axes ? [0, ceiling / 2, ceiling].map((value) => {
      const y = bottom - value / ceiling * (bottom - top);
      return `<line class="trace-grid" x1="${left}" x2="${right}" y1="${y}" y2="${y}"/><text x="${left - 9}" y="${y + 4}" text-anchor="end">${value}</text>`;
    }).join('') : '';
    const dates = labels.map(({ index, text }) => {
      const point = this.points[index];
      const anchor = index === 0 ? 'start' : index === this.values.length - 1 ? 'end' : 'middle';
      return `<text class="trace-axis-label" x="${point.x}" y="${height - 3}" text-anchor="${anchor}">${text}</text>`;
    }).join('');
    const hourTicks = hourly ? this.points.map((point, index) => `<line class="trace-hour-tick" x1="${point.x}" x2="${point.x}" y1="${bottom}" y2="${bottom + (index % hourStep === 0 ? 5 : 3)}"/>`).join('') : '';
    let band = '';
    let peakLine = '';
    if (highlight) {
      const start = this.points[highlight[0]].x;
      const end = this.points[highlight[1]].x;
      const peakPath = ProfileTrace.path(this.points.slice(highlight[0], highlight[1] + 1));
      band = `<rect class="trace-window" x="${start}" y="${top}" width="${end - start}" height="${bottom - top}"/><path class="trace-window-edge" d="M${start},${bottom} V${top} H${end} V${bottom}"/><text class="trace-peak-label" x="${(start + end) / 2}" y="${top - 10}" text-anchor="middle">Peak</text>`;
      peakLine = `<path class="trace-peak-line" d="${peakPath}"/>`;
    }
    const dots = axes && this.values.length <= 7 ? this.points.map((point) => `<circle class="trace-sample" cx="${point.x}" cy="${point.y}" r="2.5"/>`).join('') : '';
    this.container.innerHTML = `<svg class="activity-trace ${animate ? 'trace-enter' : ''}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${this.options.description || 'GPU activity'}"><title>${this.options.description || 'GPU activity'}</title>${ticks}${band}<path class="trace-area" d="${path} L${right},${bottom} L${left},${bottom} Z"/><path class="trace-line" pathLength="1000" d="${path}"/>${peakLine}${dots}${interactive ? '<line class="trace-guide"/><circle class="trace-halo" r="8"/><circle class="trace-dot" r="3.5"/>' : ''}${hourTicks}${dates}</svg>`;
    if (interactive) {
      const hit = document.createElement('button');
      hit.className = 'trace-hit';
      hit.type = 'button';
      hit.style.left = `${left}px`;
      hit.style.right = `${width - right}px`;
      hit.style.bottom = `${height - bottom}px`;
      hit.onpointermove = (event) => this.inspect(this.indexAt(event.clientX));
      hit.onpointerleave = () => this.inspect(this.options.selectedIndex >= 0 ? this.options.selectedIndex : this.peakIndex);
      hit.onclick = (event) => {
        if (event.detail) this.inspect(this.indexAt(event.clientX));
        this.options.onSelect?.(this.activeIndex);
      };
      hit.onkeydown = (event) => {
        const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? this.values.length - 1 : this.activeIndex + (event.key === 'ArrowRight' ? 1 : -1);
        this.inspect(Math.max(0, Math.min(this.values.length - 1, next)));
      };
      this.container.append(hit);
      this.updateGuide();
    }
  }

  indexAt(clientX) {
    const { left, right } = this.geometry;
    const x = clientX - this.container.getBoundingClientRect().left;
    return Math.round(Math.max(0, Math.min(1, (x - left) / (right - left))) * (this.values.length - 1));
  }

  inspect(index) {
    if (index === this.activeIndex) return;
    this.setActive(index);
    this.options.onInspect?.(index);
  }

  updateGuide() {
    if (!this.options.interactive || !this.points) return;
    const point = this.points[this.activeIndex];
    if (!point) return;
    const line = this.container.querySelector('.trace-guide');
    if (!line) return;
    for (const [attribute, value] of Object.entries({ x1: point.x, x2: point.x, y1: this.geometry.top, y2: this.geometry.bottom })) line.setAttribute(attribute, String(value));
    this.container.querySelectorAll('.trace-dot, .trace-halo').forEach((dot) => {
      dot.setAttribute('cx', String(point.x));
      dot.setAttribute('cy', String(point.y));
    });
    const label = this.options.valueLabel?.(this.activeIndex) || String(this.values[this.activeIndex]);
    this.container.querySelector('.trace-hit').setAttribute('aria-label', `${label}. Use left and right arrows to choose a day, then Enter to view jobs.`);
  }

  static path(points) {
    return points.map((point, index) => {
      if (!index) return `M${point.x},${point.y}`;
      const previous = points[index - 1];
      const middle = (previous.x + point.x) / 2;
      return `C${middle},${previous.y} ${middle},${point.y} ${point.x},${point.y}`;
    }).join(' ');
  }
}
window.ProfileTrace = ProfileTrace;
