import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { Icon } from "./ProfileIcon";
import { fmtDuration, formatTime } from "../format";
import type { ClusterSnapshot } from "../types";
import { LabApiError, labRequest } from "./api";
import { ProfileJobDrawer } from "./ProfileJobDrawer";
import { ProfileTimeline } from "./ProfileTimeline";
import { clockHour, dateLabel, hours, liveJobs, mergeLiveJobs, statusLabel, type Activity, type ActivityJob } from "./profile-data";
import type { LabUser } from "./types";

export function ProfilePage({ user, snapshot }: { user: LabUser; snapshot: ClusterSnapshot | null }) {
  const [period, setPeriod] = useState("7d");
  const [date, setDate] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<Activity | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<ActivityJob | null>(null);
  const [hovered, setHovered] = useState<{ start: number; end: number } | null>(null);
  const [definition, setDefinition] = useState(false);
  const reading = useRef(false);
  reading.current = selected !== null || (data?.jobs?.items.length || 0) > 20;
  const generation = useRef(0);
  const pageRequest = useRef<AbortController | null>(null);
  const jobsHeading = useRef<HTMLHeadingElement>(null);
  const params = new URLSearchParams({ range: period, status, q: query, ...(date ? { date } : {}), ...(model ? { gpu_model: model } : {}) }).toString();
  const bindingKey = JSON.stringify(user.bindings);

  useEffect(() => { const timer = window.setTimeout(() => setQuery(search.trim()), 250); return () => clearTimeout(timer); }, [search]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden && !reading.current) setRevision(v => v + 1); }, 60_000);
    return () => { clearInterval(timer); pageRequest.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    pageRequest.current?.abort();
    const request = ++generation.current;
    setBusy(true); setError("");
    void labRequest<Activity>(`/api/lab/me/activity?${params}`, { signal: controller.signal }).then(value => {
      if (request === generation.current) setData(value);
    }).catch(caught => { if (!controller.signal.aborted) setError(caught instanceof LabApiError && caught.status === 503 ? "Activity is temporarily unavailable. Please try again." : "Could not load your activity. Please try again."); })
      .finally(() => { if (request === generation.current && !controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [params, bindingKey, revision]);
  useEffect(() => {
    const media = matchMedia("(max-width:759px)");
    let timer = 0;
    const selectDay = () => { clearTimeout(timer); timer = window.setTimeout(() => { if (media.matches && !date && data?.enabled) setDate(data.days.at(-1)!.date); }, 120); };
    selectDay(); media.addEventListener("change", selectDay);
    return () => { clearTimeout(timer); media.removeEventListener("change", selectDay); };
  }, [date, data?.days.at(-1)?.date]);

  const live = useMemo(() => liveJobs(snapshot, user), [snapshot, user, revision]);
  const jobs = mergeLiveJobs(data?.enabled ? data.jobs.items : [], live, snapshot).filter(job => {
    if (status !== "all" && status !== job.status) return false;
    if (query && !`${job.task_name} ${job.node_id} ${job.user} ${job.pids.join(" ")}`.toLowerCase().includes(query.toLowerCase())) return false;
    if (job.status === "running") return true;
    const selectedDay = data?.enabled ? data.days.find(d => d.date === date) : undefined;
    return job.intervals.some(r => (!model || r.model === model) && (!selectedDay || r.start < selectedDay.end && (r.end > selectedDay.start || r.start === r.end && r.start === selectedDay.start)));
  });
  const running = live.length;
  const bound = user.bindings.filter(b => !b.valid_to).length;
  const ready = data?.enabled;

  async function loadMore() {
    if (!data?.jobs.next_cursor || busy) return;
    const controller = new AbortController(); pageRequest.current = controller;
    const request = generation.current;
    setBusy(true); setError("");
    try {
      const next = await labRequest<Activity>(`/api/lab/me/activity?${params}&cursor=${encodeURIComponent(data.jobs.next_cursor)}`, { signal: controller.signal });
      if (request === generation.current) setData(previous => previous && ({ ...next, jobs: { ...next.jobs, items: [...previous.jobs.items, ...next.jobs.items] } }));
    } catch (caught) {
      if (!controller.signal.aborted) {
        if (caught instanceof LabApiError && caught.status === 409) setRevision(v => v + 1);
        else setError("Could not load more jobs. Please try again.");
      }
    } finally { if (request === generation.current && !controller.signal.aborted) setBusy(false); }
  }

  return <div class="profile-page">
    <header class="page-heading"><div><h2>Your activity</h2><p class="identity">{user.display_name || user.email}<span>·</span>{bound} connected {bound === 1 ? "account" : "accounts"}</p></div>
      <div class="heading-actions"><button class="quiet" onClick={() => { setStatus("running"); jobsHeading.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion:reduce)").matches ? "instant" : "smooth", block: "start" }); }}><span class="dot" />{running} running<Icon name="arrow-down" /></button><a class="profile-button" href="/profile/settings"><Icon name="settings-2" />Account settings</a></div></header>
    <div class="period-toolbar"><div class="period-field"><span class="field-label">Period</span><Choices label="Period" value={period} items={[["7d", "Last 7 days"], ["30d", "Last 30 days"]]} onChange={value => { setPeriod(value); setDate(""); }} /></div><div class="heading-actions"><p class="date-label">{ready ? `${dateLabel(data.days[0].date)} – ${dateLabel(data.days.at(-1)!.date)}` : ""}</p><button class="quiet" disabled={busy} onClick={() => setRevision(v => v + 1)} aria-label="Refresh activity"><Icon name="refresh-cw" /></button></div></div>
    {error && <div class="notice" role="alert"><strong>{error}</strong><button onClick={() => setRevision(v => v + 1)}>Retry</button></div>}
    {!data && busy && <div class="notice" role="status">Loading your activity…<div class="loading-skeleton"><span /><span /><span /></div></div>}
    {data && (!ready || data.availability === "unbound") && <div class="notice"><strong>{ready ? "Connect your node accounts" : "Activity history is unavailable"}</strong><p>{ready ? "Connect an account to see your GPU activity here. Activity is included from the time you connect." : "Live jobs are available below. Historical activity is not enabled for this workspace."}</p>{ready && <a class="profile-button" href="/profile/settings">Connect accounts</a>}</div>}
    {ready && data.availability !== "unbound" && <div aria-busy={busy}>
      <section class="stats-band" aria-label="Activity summary">
        <div><p class="stat-label">GPU hours<button class="quiet" aria-label="About GPU hours" aria-expanded={definition} onClick={() => setDefinition(!definition)}><Icon name="info" /></button></p><strong>{hours(data.summary.gpu_hours)}<small>h</small></strong><span class="stat-note">Across your connected accounts</span></div>
        <div><p class="stat-label">Time with active jobs</p><strong>{hours(data.summary.active_hours)}<small>h</small></strong><span class="stat-note">Overlapping jobs counted once</span></div>
        <div><p class="stat-label">Active days</p><strong>{data.summary.active_days}<small>/ {period.slice(0, -1)}</small></strong><span class="stat-note">Days with recorded activity</span></div>
      </section>
      {definition && <div class="notice"><strong>About GPU hours</strong><p>One GPU used for one hour equals one GPU hour. Multiple processes sharing the same GPU count once. Time with active jobs counts overlapping work across all your nodes once.</p><p>Hours are estimated from the first and last observations. Gaps between observations may be included; zero activity does not confirm that a node was idle. Only activity during your account connection is included.</p></div>}
      <div class="activity-layout"><section class="activity-panel" aria-labelledby="profile-activity-title"><div class="section-heading"><div><h3 id="profile-activity-title">{period === "7d" ? "Weekly activity" : "Activity this month"}</h3><p class="section-description">Select a day to explore your jobs.</p></div><span class="timezone">UTC+8</span></div>
        {model && <div class="activity-filter"><span>{model}</span><button class="quiet" aria-label="Clear GPU filter" onClick={() => setModel("")}><Icon name="x" /></button></div>}
        <ProfileTimeline data={data} date={date} model={model} onDate={setDate} onJob={setSelected} onInspect={setHovered} />
        <div class="activity-footer"><div class="activity-legend"><span>Concurrent GPUs</span>{["1", "2–3", "4+"].map((label, i) => <span><i class={`level-${i + 1}`} />{label}</span>)}</div><p class="peak-summary"><span>Busiest hours</span><strong>{data.busiest_hours ? `${clockHour(data.busiest_hours.start_hour)}–${clockHour(data.busiest_hours.end_hour)}` : "No activity"}</strong></p></div>
      </section><aside class="gpu-panel" aria-labelledby="profile-gpu-title"><h3 id="profile-gpu-title">Your GPUs</h3><div class="model-list">{data.gpu_models.map(gpu => <button class="gpu-model" key={gpu.model} aria-pressed={model === gpu.model} onClick={() => setModel(model === gpu.model ? "" : gpu.model)}><strong>{gpu.model}</strong><span class="model-hours">{hours(gpu.gpu_hours)} h</span><span class="model-share">{Math.round(gpu.share * 100)}% of GPU hours</span>{model === gpu.model && <Icon name="check" />}</button>)}</div>{!data.gpu_models.length && <p class="section-description">No GPU activity recorded yet.</p>}<div class="selection-summary"><p>{date ? dateLabel(date) : "Across this period"}{model ? ` · ${model}` : ""}</p><strong>{hours(data.selection.gpu_hours)} GPU hours</strong><span>{hours(data.selection.active_hours)} h with active jobs</span></div><p class="activity-tip">Select a GPU model to find its activity and jobs.</p></aside></div>
    </div>}
    <section class="jobs" aria-labelledby="profile-jobs-title"><div class="section-heading"><h3 id="profile-jobs-title" ref={jobsHeading}>Recent jobs <span class="running-label">{running} running</span></h3><a class="profile-button quiet" href={`/jobs?range=${period}`}>All jobs<Icon name="arrow-up-right" /></a></div>
      <div class="job-toolbar"><div><span class="field-label">Status</span><Choices label="Status" value={status} items={[["all", "All"], ["running", "Running"], ["ended", "Ended"]]} onChange={setStatus} /></div><label class="search-field">Search jobs<input type="search" value={search} placeholder="Job, node, or PID" maxLength={200} onInput={e => setSearch(e.currentTarget.value)} /></label></div>
      {(date || model) && <div class="date-filter"><span>{[date && dateLabel(date), model].filter(Boolean).join(" · ")} · Running jobs stay visible</span><button class="quiet" onClick={() => { setDate(""); setModel(""); }}>Clear filters<Icon name="x" /></button></div>}
      <div class="table-head"><span>Job / Status</span><span>Node / GPU</span><span>Duration</span><span>Last active</span><span /></div>
      <div aria-live="polite" aria-busy={busy}>{jobs.map(job => <div class={`job-row ${hovered && job.intervals.some(r => r.start < hovered.end && r.end > hovered.start) ? "is-related" : ""} ${selected?.job_key === job.job_key ? "is-selected" : ""}`} key={job.job_key}><div><div class="job-title">{job.task_name}</div><div class={`job-sub ${job.status}`}><span>{statusLabel(job.status)}</span><span>·</span><span>PID {job.pids.join(", ")}</span></div></div><div class="job-location">{job.node_id}<div class="job-sub">{job.models.join(" / ")} · {job.gpu_count} GPU{job.gpu_count === 1 ? "" : "s"}</div></div><div class="job-time">{fmtDuration(job.last_seen_at - job.started_at)}</div><div class="job-seen">{job.status === "running" ? "Now" : formatTime(job.last_seen_at)}</div><button class="job-open quiet" aria-label={`View ${job.task_name}`} onClick={() => setSelected(job)}><Icon name="chevron-right" /></button></div>)}{!jobs.length && !busy && <p class="empty-jobs">{error ? "Your jobs could not be loaded." : "No jobs match this view."}</p>}</div>
      {ready && data.jobs.next_cursor && <button class="profile-load-more" disabled={busy} onClick={() => void loadMore()}>{busy ? "Loading…" : "Load more jobs"}</button>}
    </section>
    <footer class="page-footer"><span>Based on recorded GPU activity. Hours are approximate.{ready ? ` Updated ${formatTime(data.as_of)}.` : ""}{data?.missing_uid_sessions ? " Some older records cannot be matched to your accounts." : ""}</span><span>Asia/Shanghai · UTC+8</span></footer>
    {selected && <ProfileJobDrawer key={selected.job_key} job={jobs.find(j => j.job_key === selected.job_key) || selected} snapshot={snapshot} onClose={() => setSelected(null)} />}
  </div>;
}

function Choices({ label, value, items, onChange }: { label: string; value: string; items: string[][]; onChange: (value: string) => void }) {
  return <div class="segments" role="group" aria-label={label}>{items.map(([key, text]) => <button key={key} aria-pressed={value === key} onClick={() => onChange(key)}>{text}</button>)}</div>;
}
