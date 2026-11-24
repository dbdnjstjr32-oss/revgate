import { createRequire } from "node:module";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

// pnpm 10 no longer hoists *eslint* packages, so resolve the plugins that eslint-config-next
// references (react, react-hooks, @next/next, jsx-a11y, import, @typescript-eslint) next to it.
const compat = new FlatCompat({
  baseDirectory: here,
  resolvePluginsRelativeTo: dirname(require.resolve("eslint-config-next/package.json")),
});

const config = [
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
];

export default config;
