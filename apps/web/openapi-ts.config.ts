import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "openapi.json",
  output: "src/lib/api",
  plugins: [
    {
      name: "@hey-api/client-fetch",
      baseUrl: false,
      runtimeConfigPath: "./src/lib/api-config",
    },
    "@hey-api/typescript",
    "@hey-api/sdk",
  ],
});
