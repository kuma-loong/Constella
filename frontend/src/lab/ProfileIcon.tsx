import { ArrowDown, ArrowUpRight, Check, ChevronRight, Info, RefreshCw, Settings2, X } from "lucide";
import { h } from "preact";

const icons = { "arrow-down": ArrowDown, "arrow-up-right": ArrowUpRight, check: Check,
  "chevron-right": ChevronRight, info: Info, "refresh-cw": RefreshCw, "settings-2": Settings2, x: X };

// Render the existing Lucide nodes declaratively so local updates do not depend on a cluster refresh.
export function Icon({ name }: { name: keyof typeof icons }) {
  const [tag, attrs, children] = icons[name];
  return h(tag, { ...attrs, "aria-hidden": "true", focusable: "false" }, children?.map(([child, props]) => h(child, props)));
}
