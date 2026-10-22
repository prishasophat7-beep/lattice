// Installs the sample knowledge base (the current web-platform export format) into the test engine.
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export default async function globalSetup() {
  const engine = `http://127.0.0.1:${process.env.UI_TEST_ENGINE_PORT}`;
  const bundle = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "sample_kb_acme_people_it.zip");
  const res = await fetch(`${engine}/api/kb/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: bundle }),
  });
  if (!res.ok) throw new Error(`Importing ${bundle} failed: ${res.status} ${await res.text()}`);
}
