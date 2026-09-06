import type { ComponentChildren } from "preact";
import type { ClusterSnapshot } from "./types";
import type { Route } from "./analytics";

export type ExtensionRoute = { kind: "extension"; key: string };
export type AppRoute = Route | ExtensionRoute;

export type AppExtension = {
  parseRoute: (pathname: string) => ExtensionRoute | null;
  isPath: (pathname: string) => boolean;
  renderNavigation: (route: AppRoute) => ComponentChildren;
  renderHeaderActions: () => ComponentChildren;
  renderPage: (route: ExtensionRoute, snapshot: ClusterSnapshot | null) => ComponentChildren;
  canManageSettings: boolean;
  requestHeaders?: Record<string, string>;
  onAuthenticationRequired?: () => void;
};
