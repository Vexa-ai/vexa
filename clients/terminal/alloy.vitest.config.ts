import { fileURLToPath } from "node:url";
import react from "../../node_modules/.pnpm/@vitejs+plugin-react@6.0.3__178d0d3c03bb3e1d9c3ba11f4067ef7a/node_modules/@vitejs/plugin-react/dist/index.js";

const workspacePackage = (path: string) =>
  fileURLToPath(new URL(`../../node_modules/.pnpm/${path}`, import.meta.url));

export default {
  plugins: [react()],
  resolve: {
    alias: [
      {
        find: "react/jsx-dev-runtime",
        replacement: workspacePackage(
          "react@19.2.7/node_modules/react/jsx-dev-runtime.js",
        ),
      },
      {
        find: "react-dom/client",
        replacement: workspacePackage(
          "react-dom@19.2.7_react@19.2.7/node_modules/react-dom/client.js",
        ),
      },
      {
        find: "react/jsx-runtime",
        replacement: workspacePackage(
          "react@19.2.7/node_modules/react/jsx-runtime.js",
        ),
      },
      {
        find: "react",
        replacement: workspacePackage("react@19.2.7/node_modules/react/index.js"),
      },
    ],
  },
  test: {
    environment: "jsdom",
    include: [
      "src/workbench/__tests__/AlloySttTelemetryMonitor.test.tsx",
      "src/workbench/__tests__/alloySttTelemetry.test.ts",
      "src/workbench/__tests__/alloySttTelemetry.resilience.test.ts",
      "src/app/api/__tests__/proxyMode.test.ts",
    ],
  },
};
