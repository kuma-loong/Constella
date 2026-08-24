import { render } from "preact";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import App from "./App";
import "./styles-base.css";
import "./styles-performance.css";
import "./styles-analytics.css";
import "./styles-responsive.css";

const appRoot = document.getElementById("app");
if (!appRoot) {
  throw new Error("Missing element: app");
}

render(<App />, appRoot);
