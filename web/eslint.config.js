import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

/**
 * The network-escape rules, and why there are five of them rather than one.
 *
 * M9 clause 5 is "no mutation happens outside the tool surface", and the
 * mechanical guard is `tests/unit/test_web_writes_through_tools.py`. This is
 * the fast local echo of it — a reviewer gets the failure in their editor
 * rather than on a Python run.
 *
 * `no-restricted-globals` on `fetch` was the whole of it, and it covered one
 * spelling out of six. It says nothing about `window.fetch`, `globalThis.fetch`,
 * `new XMLHttpRequest()`, `new WebSocket()`, `new EventSource()`,
 * `navigator.sendBeacon()` or a `<form>` — every one of which the Python scan
 * catches and none of which ESLint did. Two guards over the same property are
 * worth having when they fail at different moments; two guards where one covers
 * a sixth of the property is one guard and a decoration.
 *
 * They stay **scoped to app files**. Tests stub `fetch` deliberately: that is
 * how `DatasetList.test.tsx` counts requests, and it is the measurement clause 3
 * turns on.
 */
const NETWORK_ESCAPES = [
  {
    selector: "NewExpression[callee.name=/^(XMLHttpRequest|WebSocket|EventSource)$/]",
    message: "Use callTool() in src/mcp/transport.ts. See DECISIONS.md [M9] and M9 clause 5.",
  },
  {
    selector:
      "MemberExpression[object.name=/^(window|globalThis|self)$/][property.name='fetch']",
    message: "Use callTool() in src/mcp/transport.ts — a qualified fetch is still a fetch.",
  },
  {
    selector: "MemberExpression[object.name='navigator'][property.name='sendBeacon']",
    message: "Use callTool() in src/mcp/transport.ts. sendBeacon is a write with no response.",
  },
  {
    selector: "CallExpression[callee.name='importScripts']",
    message: "No worker bootstrapping; the app has one interface and it is the tool surface.",
  },
  {
    // A `<form>` submits without JavaScript, so it is a mutation path no
    // function-level rule can see.
    selector: "JSXOpeningElement[name.name='form']",
    message:
      "An HTML form submits outside the tool surface. Use a button and callTool() instead.",
  },
];

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
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "Use callTool() in src/mcp/transport.ts. See DECISIONS.md [M9]." },
        {
          name: "XMLHttpRequest",
          message: "Use callTool() in src/mcp/transport.ts. See DECISIONS.md [M9].",
        },
        {
          name: "WebSocket",
          message: "Use callTool() in src/mcp/transport.ts. See DECISIONS.md [M9].",
        },
        {
          name: "EventSource",
          message: "Use callTool() in src/mcp/transport.ts. See DECISIONS.md [M9].",
        },
      ],
      "no-restricted-syntax": ["error", ...NETWORK_ESCAPES],
    },
  },
  {
    // The one module allowed to reach the network. It is also the only place
    // the Python guard permits a network primitive.
    files: ["src/mcp/transport.ts"],
    rules: { "no-restricted-globals": "off", "no-restricted-syntax": "off" },
  },
  {
    files: ["**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      // Tests stub `fetch` on purpose; that stub is the request counter clause
      // 3 is measured with.
      "no-restricted-globals": "off",
      "no-restricted-syntax": "off",
    },
  },
  { files: ["eslint.config.js", "vite.config.ts"], ...tseslint.configs.disableTypeChecked },
);
