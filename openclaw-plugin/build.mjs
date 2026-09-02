import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { stripTypeScriptTypes } from "node:module";

const root = dirname(fileURLToPath(import.meta.url));
const output = join(root, "dist");
const sources = [
  "core.ts",
  "direct-conversation.ts",
  "hooks.ts",
  "index.ts",
  "projection-client.ts",
  "spoken-text.ts",
];

await rm(output, { recursive: true, force: true });
await mkdir(output);
for (const sourceName of sources) {
  const source = await readFile(join(root, sourceName), "utf8");
  const javascript = stripTypeScriptTypes(source, { mode: "strip" })
    .replaceAll(/from ("\.\/[^"\n]+)\.ts"/g, 'from $1.js"');
  await writeFile(
    join(output, sourceName.replace(/\.ts$/, ".js")),
    javascript,
  );
}
