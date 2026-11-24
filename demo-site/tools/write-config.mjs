// Writes public/config.js from environment variables (Cloudflare Pages build command:
// `node tools/write-config.mjs`), so a new Vapi account or assistant needs no code change:
// change the variables in Cloudflare and redeploy. With none of them set, the committed
// public/config.js is left as it is (local use).
//
// ESTARA_VAPI_PUBLIC_KEY   Vapi PUBLIC key (made for browsers). Never the private key.
// ESTARA_ASSISTANT_ID      the assistant the call button uses
// ESTARA_ASSISTANT_ID_B    optional, used with ?v=b
// ESTARA_API_URL           the backend address, woken up when the page opens
// ESTARA_DEMO_ENABLED      "false" pauses the call button (emergency switch)
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const env = process.env;
const names = ["ESTARA_VAPI_PUBLIC_KEY", "ESTARA_ASSISTANT_ID", "ESTARA_ASSISTANT_ID_B",
               "ESTARA_API_URL", "ESTARA_DEMO_ENABLED"];
if (!names.some((n) => env[n])) {
  console.log("write-config: no ESTARA_* variables set, keeping public/config.js");
  process.exit(0);
}
const settings = {
  vapiPublicKey: env.ESTARA_VAPI_PUBLIC_KEY || "",
  assistantId: env.ESTARA_ASSISTANT_ID || "",
  assistantIdB: env.ESTARA_ASSISTANT_ID_B || "",
  apiUrl: env.ESTARA_API_URL || "",
  enabled: (env.ESTARA_DEMO_ENABLED || "true").toLowerCase() !== "false",
};
const out = fileURLToPath(new URL("../public/config.js", import.meta.url));
writeFileSync(out, "// Written by tools/write-config.mjs from the host's environment variables.\n" +
  `window.ESTARA_DEMO = ${JSON.stringify(settings, null, 2)};\n`);
console.log(`write-config: wrote ${out} (call button ${settings.enabled ? "on" : "paused"})`);
