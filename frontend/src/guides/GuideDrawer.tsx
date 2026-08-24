import { useEffect, useRef } from "preact/hooks";
import { PERFORMANCE_GROUPS, type PerformanceMetricId } from "../performance-metrics";
import { PERFORMANCE_GUIDE } from "./content/performance";
import type { GuideLocale } from "./types";

type GuideDrawerProps = {
  locale: GuideLocale;
  activeMetricId: PerformanceMetricId | null;
  onLocaleChange: (locale: GuideLocale) => void;
  onMetricSelect: (metricId: PerformanceMetricId) => void;
  onClose: () => void;
};

export function GuideDrawer({
  locale,
  activeMetricId,
  onLocaleChange,
  onMetricSelect,
  onClose,
}: GuideDrawerProps) {
  const copy = PERFORMANCE_GUIDE[locale];
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const body = bodyRef.current;
    if (!body) {
      return;
    }
    if (!activeMetricId) {
      body.scrollTo({ top: 0 });
      return;
    }
    const target = body.querySelector<HTMLElement>(`#${metricDomId(activeMetricId)}`);
    if (target) {
      const bodyTop = body.getBoundingClientRect().top;
      const targetTop = target.getBoundingClientRect().top;
      body.scrollTop = Math.max(0, body.scrollTop + targetTop - bodyTop - 20);
    }
  }, [activeMetricId, locale]);

  return (
    <div
      class="guide-backdrop"
      role="presentation"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <aside
        class="guide-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="performance-guide-title"
        lang={locale}
      >
        <header class="guide-drawer-head">
          <div>
            <span>{copy.eyebrow}</span>
            <h2 id="performance-guide-title">{copy.title}</h2>
          </div>
          <div class="guide-head-actions">
            <div class="guide-language" role="group" aria-label="Guide language">
              <button
                type="button"
                class={locale === "zh-CN" ? "is-active" : ""}
                aria-pressed={locale === "zh-CN"}
                onClick={() => onLocaleChange("zh-CN")}
              >中文</button>
              <button
                type="button"
                class={locale === "en" ? "is-active" : ""}
                aria-pressed={locale === "en"}
                onClick={() => onLocaleChange("en")}
              >EN</button>
            </div>
            <button class="guide-close" type="button" data-guide-close onClick={onClose} aria-label="Close guide">×</button>
          </div>
        </header>

        <div ref={bodyRef} class="guide-drawer-body">
          <div class="guide-lede">
            <p>{copy.intro}</p>
          </div>

          <section class="guide-principles" aria-labelledby="guide-start-title">
            <h3 id="guide-start-title">{copy.startHere}</h3>
            <ol>
              {copy.principles.map((principle) => <li key={principle}>{principle}</li>)}
            </ol>
          </section>

          <nav class="guide-index" aria-label={copy.jumpTo}>
            <span>{copy.jumpTo}</span>
            {PERFORMANCE_GROUPS.map((group) => (
              <div key={group.id}>
                <strong>{copy.groups[group.id]}</strong>
                {group.metrics.map((metric) => (
                  <button
                    key={metric.id}
                    type="button"
                    class={activeMetricId === metric.id ? "is-active" : ""}
                    onClick={() => onMetricSelect(metric.id)}
                  >{copy.metrics[metric.id].title}</button>
                ))}
              </div>
            ))}
          </nav>

          <article class="guide-article">
            {PERFORMANCE_GROUPS.map((group) => (
              <section key={group.id} class="guide-metric-group">
                <span class="guide-group-label">{copy.groups[group.id]}</span>
                {group.metrics.map((metric) => {
                  const metricCopy = copy.metrics[metric.id];
                  return (
                    <section
                      key={metric.id}
                      id={metricDomId(metric.id)}
                      class={`guide-metric ${activeMetricId === metric.id ? "is-target" : ""}`}
                    >
                      <header>
                        <h3>{metricCopy.title}</h3>
                        {metricCopy.localTitle ? <span>{metricCopy.localTitle}</span> : null}
                      </header>
                      <div class="guide-reading-note">
                        <strong>{copy.definitionLabel}</strong>
                        <p>{metricCopy.definition}</p>
                      </div>
                      <div class="guide-significance">
                        <strong>{copy.significanceLabel}</strong>
                        <p>{metricCopy.significance}</p>
                      </div>
                      <div class="guide-related">
                        <span>{copy.readWith}</span>
                        {metricCopy.related.map((relatedId) => (
                          <button key={relatedId} type="button" onClick={() => onMetricSelect(relatedId)}>
                            {copy.metrics[relatedId].title}
                          </button>
                        ))}
                      </div>
                    </section>
                  );
                })}
              </section>
            ))}

            <section class="guide-patterns">
              <h3>{copy.patternsTitle}</h3>
              {copy.patterns.map((pattern) => (
                <div key={pattern.title}>
                  <strong>{pattern.title}</strong>
                  <p>{pattern.body}</p>
                </div>
              ))}
            </section>

            <section class="guide-limits">
              <h3>{copy.limitsTitle}</h3>
              <p>{copy.limits}</p>
              <strong>{copy.sources}</strong>
              <div>
                <a href="https://docs.nvidia.com/deploy/nvml-api/group__nvmlGpmEnums.html" target="_blank" rel="noreferrer">NVML GPM</a>
                <a href="https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html" target="_blank" rel="noreferrer">DCGM Profiling</a>
              </div>
            </section>
          </article>
        </div>
      </aside>
    </div>
  );
}

function metricDomId(metricId: PerformanceMetricId) {
  return `guide-${metricId.replaceAll(".", "-")}`;
}
