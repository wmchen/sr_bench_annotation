import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ServerLifecycle } from "./features/lifecycle/ServerLifecycle";
import "./style.css";

createRoot(document.getElementById("root")!).render(<ServerLifecycle><App /></ServerLifecycle>);
