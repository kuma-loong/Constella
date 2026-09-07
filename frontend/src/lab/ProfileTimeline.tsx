import { useEffect, useRef, useState } from "preact/hooks";
import { Icon } from "./ProfileIcon";
import { fmtDuration } from "../format";
import { dateLabel, hours, type Activity, type ActivityJob } from "./profile-data";

const level = (count: number) => Math.min(3, count >= 4 ? 3 : count >= 2 ? 2 : 1);
const geometry = (start: number, end: number, midnight: number) => ({
  "--start": `${Math.max(0, (start - midnight) / 864)}%`,
  "--length": `${Math.max(.12, (Math.min(end, midnight + 86400) - Math.max(start, midnight)) / 864)}%`,
});
const time = (at: number) => new Date(at * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit" });

export function ProfileTimeline({ data, date, model, onDate, onJob, onInspect }: {
  data: Activity; date: string; model: string; onDate: (date: string) => void; onJob: (job: ActivityJob) => void; onInspect: (span: { start: number; end: number } | null) => void;
}) {
  const strip = useRef<HTMLDivElement>(null);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => setShowAll(false), [date, model, data.days.length]);
  const mobileDate = date || data.days.at(-1)?.date;
  useEffect(() => {
    const active = strip.current?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (active && strip.current) strip.current.scrollLeft = active.offsetLeft - strip.current.offsetLeft - strip.current.clientWidth / 2;
  }, [mobileDate]);
  return <>
    <div class="mobile-days" ref={strip} aria-label="Choose a day">{data.days.map(day => <button key={day.date} class={`mobile-day ${day.active_days ? "has-activity" : ""}`} aria-label={day.date} aria-pressed={mobileDate === day.date} onClick={() => onDate(day.date)}><span>{dateLabel(day.date, true)}</span><strong>{Number(day.date.slice(-2))}</strong><i /></button>)}</div>
    <div class="timeline-header"><span>Date</span><div class="time-axis">{Array.from({ length: 13 }, (_, i) => <span style={{ "--position": `${i / 12 * 100}%` }}>{String(i * 2).padStart(2, "0")}</span>)}</div><span>GPU·h</span></div>
    <div class={`activity-days ${data.days.length > 7 ? "month-view" : ""}`} onKeyDown={event => {
      if (!(event.target instanceof HTMLElement) || !event.target.matches(".day-select") || !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>(".day-select")];
      const i = buttons.indexOf(event.target as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : Math.max(0, Math.min(buttons.length - 1, i + (event.key === "ArrowDown" ? 1 : -1)));
      event.preventDefault(); buttons[next]?.focus();
    }}>{data.days.map(day => {
      const expanded = date === day.date;
      const tracks = expanded ? data.day_jobs.items : [];
      const visibleTracks = showAll ? tracks : tracks.slice(0, 10);
      return <article key={day.date} class={`activity-day ${expanded ? "is-expanded" : ""} ${day.date === mobileDate ? "is-mobile-day" : ""}`}>
        <button class="day-select" aria-label={`${day.date}, ${hours(day.gpu_hours)} GPU hours. Explore jobs.`} aria-expanded={expanded} aria-controls={`lanes-${day.date}`} onClick={() => onDate(expanded && !matchMedia("(max-width:759px)").matches ? "" : day.date)}>
          <span class="day-label"><Icon name="chevron-right" /><strong>{dateLabel(day.date, true)}</strong><span>{dateLabel(day.date)}</span></span>
          <span class="day-track">{day.segments.map(s => <span class={`activity-segment level-${level(s.count)}`} style={geometry(s.start, s.end, day.start)} onPointerEnter={() => onInspect(s)} onPointerLeave={() => onInspect(null)} title={`${time(s.start)}–${s.end === day.start + 86400 ? "24:00" : time(s.end)} · ${s.count} GPUs`} />)}
            {day.end < day.start + 86400 && <><span class="future-span" style={geometry(day.end, day.start + 86400, day.start)} /><span class="now-marker" style={{ "--position": `${(day.end - day.start) / 864}%` }} title={`As of ${time(day.end)}`} /></>}
            {!day.segments.length && <span class="idle-label">{model ? "No matching activity" : "No recorded activity"}</span>}</span>
          <span class="day-total">{day.gpu_hours ? hours(day.gpu_hours) : "—"}</span>
        </button>
        <div class="day-expansion" id={`lanes-${day.date}`} inert={!expanded}><div class="expansion-inner"><div class="day-lanes">
          {visibleTracks.map(job => <button class="job-track" key={job.job_key} onClick={() => onJob(job)} title={`${job.task_name} · ${job.node_id}`}>
            <span class="lane-label">{job.task_name}<small>{job.models.join(" / ")}</small></span>
            <span class="lane-track">{(job.segments || []).filter(r => r.start < day.end && r.end > day.start).map(r => <span class={`task-span level-${level(r.count)}`} style={geometry(r.start, r.end, day.start)} />)}</span>
            <span class="lane-end">{job.status === "running" ? "Running" : fmtDuration(Math.max(0, Math.min(job.last_seen_at, day.end) - Math.max(job.started_at, day.start)))}</span>
          </button>)}
          {tracks.length > 10 && <button class="quiet day-lanes-toggle" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>{showAll ? "Show first 10" : `Show all ${tracks.length} jobs`}</button>}
          <p class="day-detail-caption">{tracks.length ? `${data.day_jobs.total} jobs · ${hours(day.active_hours)} h with active jobs` : "No jobs recorded on this day."}{data.day_jobs.total > visibleTracks.length ? ` · Showing ${visibleTracks.length}${showAll ? "; use Recent jobs to explore more" : ""}.` : ""}</p>
        </div></div></div>
      </article>;
    })}</div>
  </>;
}
