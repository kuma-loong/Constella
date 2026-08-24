import type { PerformanceMetricId } from "../performance-metrics";

export type GuideLocale = "en" | "zh-CN";

export type MetricGuideCopy = {
  title: string;
  localTitle?: string;
  definition: string;
  significance: string;
  related: PerformanceMetricId[];
};

export type PerformanceGuideCopy = {
  eyebrow: string;
  title: string;
  intro: string;
  startHere: string;
  principles: string[];
  jumpTo: string;
  groups: Record<string, string>;
  definitionLabel: string;
  significanceLabel: string;
  readWith: string;
  patternsTitle: string;
  patterns: { title: string; body: string }[];
  limitsTitle: string;
  limits: string;
  sources: string;
  metrics: Record<PerformanceMetricId, MetricGuideCopy>;
};
