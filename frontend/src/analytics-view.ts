// Keep browser-owned state (scroll, focus and unsubmitted input) on stable DOM nodes.
export function createAnalyticsView(root: HTMLElement, disposeCharts: () => void = () => {}) {
  let modalTrigger: HTMLElement | null = null;
  const positions = new Map<string, { left: number; top: number; range: number }>();

  function render(markup: string, mountCharts: () => void = () => {}) {
    const document = root.ownerDocument;
    const previousModal = root.querySelector<HTMLElement>(".chart-modal");
    const focused = document.activeElement;
    const ownedFocus = root.contains(focused);
    for (const element of root.querySelectorAll<HTMLElement>(".chart-scroll, .chart-legend")) {
      positions.set(viewportKey(element), { left: element.scrollLeft, top: element.scrollTop, range: element.scrollWidth - element.clientWidth });
    }
    while (positions.size > 64) positions.delete(positions.keys().next().value!);
    const template = document.createElement("template");
    template.innerHTML = markup;
    disposeCharts();
    reconcileChildren(root, template.content);
    mountCharts();
    // Moving a keyed row can blur its control even though the node is reused.
    if (ownedFocus && focused instanceof HTMLElement && focused.isConnected && document.activeElement === document.body) {
      focused.focus({ preventScroll: true });
    }
    for (const element of root.querySelectorAll<HTMLElement>(".chart-scroll, .chart-legend")) {
      const position = positions.get(viewportKey(element));
      if (!position) continue;
      const { left, top, range } = position;
      const nextRange = element.scrollWidth - element.clientWidth;
      element.scrollLeft = element.matches(".chart-scroll") && range > 0 ? left / range * nextRange : left;
      element.scrollTop = top;
    }
    const modal = root.querySelector<HTMLElement>(".chart-modal");
    if (modal && !previousModal) {
      modalTrigger = focused instanceof HTMLElement ? focused : null;
      modal.tabIndex = -1;
      (modal.querySelector<HTMLElement>("[data-analytics-action='job-collapse']") || modal).focus({ preventScroll: true });
      modal.addEventListener("keydown", trapModalTab);
    } else if (previousModal && !modal) {
      if (previousModal.contains(focused) && modalTrigger?.isConnected) modalTrigger.focus({ preventScroll: true });
      modalTrigger = null;
    }
  }

  return { render };
}

function viewportKey(element: HTMLElement) {
  return `${element.closest(".chart-modal") ? "modal" : "inline"}:${element.getAttribute("data-view-key")}:${element.getAttribute("data-chart-scope") || ""}`;
}

function nodeKey(node: Node): string | null {
  if (!(node instanceof Element)) return null;
  if (node.hasAttribute("data-lucide")) return `icon:${node.getAttribute("data-lucide")}`;
  const explicit = node.getAttribute("data-view-key") || node.getAttribute("data-job-key");
  if (explicit) return `${node.tagName}:${explicit}`;
  const action = node.getAttribute("data-analytics-action");
  if (action) return `${action}:${["data-range", "data-metric", "data-resolution", "data-gpu-uuid", "data-legend-all"].map(name => node.getAttribute(name) || "").join(":")}`;
  return null;
}

function compatible(current: Node, next: Node) {
  if (current instanceof Element && next instanceof Element && current.hasAttribute("data-lucide") && current.getAttribute("data-lucide") === next.getAttribute("data-lucide")) return true;
  if (current.nodeType !== next.nodeType || current.nodeName !== next.nodeName) return false;
  if (current instanceof Element && next instanceof Element) {
    return current.getAttribute("data-view-scope") === next.getAttribute("data-view-scope");
  }
  return true;
}

function reconcileChildren(current: Node, next: Node) {
  const previous = [...current.childNodes];
  const used = new Set<Node>();
  let cursor = current.firstChild;
  for (const incoming of [...next.childNodes]) {
    const key = nodeKey(incoming);
    const match = previous.find(node => !used.has(node) && nodeKey(node) === key && compatible(node, incoming));
    if (!match) {
      current.insertBefore(incoming, cursor);
      continue;
    }
    used.add(match);
    if (match !== cursor) current.insertBefore(match, cursor);
    updateNode(match, incoming);
    cursor = match.nextSibling;
  }
  for (const node of previous) if (!used.has(node)) current.removeChild(node);
}

function updateNode(current: Node, next: Node) {
  if (!(current instanceof Element) || !(next instanceof Element)) {
    if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
    return;
  }
  if (current instanceof SVGElement && next.hasAttribute("data-lucide")) return;
  const chartHost = current.matches("[data-node-chart], [data-job-chart], [data-job-chart-modal]");
  const inputValueChanged = current instanceof HTMLInputElement && current.getAttribute("value") !== next.getAttribute("value");
  for (const attribute of [...current.attributes]) {
    if (!next.hasAttribute(attribute.name) && !(chartHost && attribute.name === "style")) current.removeAttribute(attribute.name);
  }
  for (const attribute of [...next.attributes]) {
    if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
  }
  if (inputValueChanged) (current as HTMLInputElement).value = next.getAttribute("value") || "";
  // uPlot owns these children; rebuilding the surrounding view must not reconcile its generated DOM.
  if (!chartHost) reconcileChildren(current, next);
}

function trapModalTab(event: KeyboardEvent) {
  if (event.key !== "Tab") return;
  const modal = event.currentTarget as HTMLElement;
  const controls = [...modal.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], select:not(:disabled), [tabindex='0']")];
  const first = controls[0], last = controls.at(-1);
  if (!first) { event.preventDefault(); modal.focus(); return; }
  if (event.shiftKey && (modal.ownerDocument.activeElement === first || modal.ownerDocument.activeElement === modal)) {
    event.preventDefault(); last?.focus();
  } else if (!event.shiftKey && modal.ownerDocument.activeElement === last) {
    event.preventDefault(); first.focus();
  }
}
