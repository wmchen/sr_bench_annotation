import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const temporary = mkdtempSync(join(tmpdir(), "realisr-api-"));
try {
  const output = join(temporary, "generated.ts");
  execFileSync(join(root, "node_modules/.bin/openapi-typescript"),
    [join(root, "../openapi.json"), "-o", output], { cwd: root, stdio: "pipe" });
  if (readFileSync(output, "utf8") !== readFileSync(join(root, "src/api/generated.ts"), "utf8")) {
    throw new Error("API 类型已过期，请运行 npm run generate:api");
  }
  console.log("OpenAPI / TypeScript contracts match");
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
