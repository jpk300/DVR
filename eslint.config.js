import js from "@eslint/js";
import globals from "globals";

export default [
  js.configs.recommended,
  {
    files: ["static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
    rules: {
      "no-unused-vars": ["error", {argsIgnorePattern: "^_"}],
      "no-unused-expressions": ["error", {allowTernary: true, allowShortCircuit: true}],
      eqeqeq: ["error", "always", {null: "ignore"}],
      "prefer-const": "error",
      "no-var": "error",
    },
  },
];
