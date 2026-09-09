import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "coverage"] },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // The one rule this app is built around: a network call anywhere but the
      // transport module would be a surface outside the MCP tool surface.
      // `tests/unit/test_web_writes_through_tools.py` is the mechanical guard;
      // this is the fast local echo of it.
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "Use callTool() in src/mcp/transport.ts. See DECISIONS.md [M9]." },
      ],
    },
  },
  {
    files: ["src/mcp/transport.ts"],
    rules: { "no-restricted-globals": "off" },
  },
  {
    files: ["**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      "no-restricted-globals": "off",
    },
  },
  { files: ["eslint.config.js", "vite.config.ts"], ...tseslint.configs.disableTypeChecked },
);
