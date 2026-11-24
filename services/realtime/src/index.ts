import { configFromEnv, start } from "./server.js";

const service = await start(configFromEnv());
for (const sig of ["SIGTERM", "SIGINT"] as const) {
  process.on(sig, () => { void service.close().then(() => process.exit(0)); });
}
