import { render } from "preact";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import App from "./App";
import "./styles.css";

const appRoot = document.getElementById("app");
if (!appRoot) {
  throw new Error("Missing element: app");
}

render(<App />, appRoot);
