import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { createSelectionRepository } from "./repository";
import "./styles.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("找不到应用挂载节点");
}
const rootContainer: HTMLElement = root;

async function bootstrap(): Promise<void> {
  const repository = await createSelectionRepository();
  createRoot(rootContainer).render(
    <StrictMode>
      <App repository={repository} />
    </StrictMode>,
  );
}

void bootstrap().catch((reason) => {
  const message = reason instanceof Error ? reason.message : String(reason);
  createRoot(rootContainer).render(
    <main className="loading-screen">
      <h1>亚马逊选品评审台</h1>
      <p>插件初始化失败：{message}</p>
    </main>,
  );
});
