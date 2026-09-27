export default [
  {
    files: [
      "extensions/pasi-chatgpt/src/userscript_*.js",
      "extensions/pasi-chatgpt/src/background-userscripts.js",
      "extensions/pasi-chatgpt/src/api_v4.js",
      "extensions/pasi-chatgpt/src/background.js",
      "extensions/pasi-chatgpt/options.js",
      "extensions/pasi-chatgpt/popup.js",
      "extensions/pasi-chatgpt/editor.js",
    ],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        chrome: "readonly",
        DOMException: "readonly",
        ErrorEvent: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        TextEncoder: "readonly",
        TextDecoder: "readonly",
        Headers: "readonly",
        Response: "readonly",
        Request: "readonly",
        AbortController: "readonly",
      },
    },
    rules: {
      "no-unreachable": "error",
      "no-unsafe-finally": "error",
      "no-constant-condition": "error",
      "valid-typeof": "error",
    },
  },
];
