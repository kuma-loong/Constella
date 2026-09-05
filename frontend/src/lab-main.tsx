import { render } from "preact";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./styles-base.css";
import "./styles-performance.css";
import "./styles-analytics.css";
import "./styles-responsive.css";
import "./styles-lab.css";
import { LabRoot } from "./lab/LabRoot";

const appRoot = document.getElementById("app");
if (!appRoot) {
  throw new Error("Missing element: app");
}

render(<LabRoot />, appRoot);
