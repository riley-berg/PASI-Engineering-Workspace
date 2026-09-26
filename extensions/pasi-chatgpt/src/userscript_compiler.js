(() => {
  "use strict";

  function stripBalancedBlock(source, keyword) {
    const text = String(source);
    let start = text.indexOf(keyword);
    while (start >= 0) {
      const brace = text.indexOf("{", start);
      if (brace < 0) break;
      let depth = 0;
      let quote = null;
      let escaped = false;
      let end = -1;
      for (let i = brace; i < text.length; i++) {
        const char = text[i];
        if (quote) {
          if (escaped) escaped = false;
          else if (char === "\\") escaped = true;
          else if (char === quote) quote = null;
          continue;
        }
        if (char === '"' || char === "'" || char === "`") {
          quote = char;
          continue;
        }
        if (char === "{") depth++;
        if (char === "}") {
          depth--;
          if (depth === 0) {
            end = i + 1;
            break;
          }
        }
      }
      if (end < 0) break;
      return text.slice(0, start) + text.slice(end).replace(/^\s*;?/, "");
    }
    return text;
  }

  function transpile(source) {
    let code = String(source || "");
    let previous;
    do {
      previous = code;
      code = stripBalancedBlock(code, "interface ");
    } while (code !== previous);
    code = code.replace(/^\s*type\s+[A-Za-z_$][\w$]*(?:\s*<[^\n>]+>)?\s*=\s*[^;\n]+;?\s*$/gm, "");
    code = code.replace(/\s+as\s+(?:const\b|[A-Za-z_$][\w$]*(?:\s*<[^;\n]+>)?(?:\[\])?(?:\s*[|&]\s*[A-Za-z_$][\w$]*)*)/g, "");
    code = code.replace(/\b(?:public|private|protected|readonly|declare|abstract)\s+(?=[A-Za-z_$])/g, "");
    code = code.replace(/([A-Za-z_$][\w$]*\??)\s*\?\s*:\s*([^=,;){}\n]+)/g, "$1");
    code = code.replace(/([A-Za-z_$][\w$]*\??)\s*:\s*([A-Za-z_$][\w$]*(?:\s*<[^>]+>)?(?:\[\])?(?:\s*[|&]\s*[A-Za-z_$][\w$]*)*)\s*(?=[=,;){}])/g, "$1");
    return code;
  }

  function compile(source, options = {}) {
    if (options.typeScript !== true) return {code: String(source || ""), diagnostics: [], compiler: "javascript"};
    const fullCompiler = globalThis.ts || globalThis.TypeScript;
    if (fullCompiler?.transpileModule) {
      const result = fullCompiler.transpileModule(String(source || ""), {
        reportDiagnostics: true,
        compilerOptions: {
          target: fullCompiler.ScriptTarget?.ES2022 ?? 7,
          module: fullCompiler.ModuleKind?.None ?? 0,
          jsx: fullCompiler.JsxEmit?.Preserve ?? 1,
        },
      });
      return {
        code: result.outputText,
        diagnostics: (result.diagnostics || []).map((diagnostic) => fullCompiler.flattenDiagnosticMessageText?.(
          diagnostic.messageText, "\\n"
        ) || String(diagnostic.messageText)),
        compiler: "typescript",
      };
    }
    const code = transpile(source);
    return {
      code,
      diagnostics: [],
      compiler: "pasi-typescript-lite",
      note: "Supports common TypeScript annotations/interfaces/type aliases; full tsc is intentionally an optional build-time compiler.",
    };
  }

  globalThis.PASIUserScriptCompiler = Object.freeze({compile});
})();
