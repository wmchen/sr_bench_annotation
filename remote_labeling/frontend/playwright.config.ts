import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const python = process.env.REALISR_PYTHON ?? "python";
export default defineConfig({
  testDir: "../tests/e2e",
  testMatch: "*.spec.ts",
  workers: 1,
  reporter: [["list"], ["json", {outputFile:"test-results/results.json"}]],
  use: { baseURL: "http://127.0.0.1:8877", viewport: {width:1600,height:1000}, trace:"retain-on-failure" },
  projects: [
    { name:"chromium", use:{browserName:"chromium",launchOptions:{
      executablePath: process.env.REALISR_CHROME ?? (existsSync("/opt/google/chrome/chrome")?"/opt/google/chrome/chrome":undefined),
      args:["--no-sandbox"],
    }}},
    { name:"firefox", use:{browserName:"firefox"} },
  ],
  webServer: {
    command: python + " -m remote_labeling.tests.e2e.serve_fixture",
    cwd: "../..", url:"http://127.0.0.1:8877/api/v1/health",
    reuseExistingServer:false, timeout:60000,
  },
});
