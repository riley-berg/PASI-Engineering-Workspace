(() => {
  "use strict";

  function lcs(a, b) {
    const rows = a.length + 1;
    const cols = b.length + 1;
    const table = Array.from({length: rows}, () => new Uint16Array(cols));
    for (let i = a.length - 1; i >= 0; i--) {
      for (let j = b.length - 1; j >= 0; j--) {
        table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
      }
    }
    return table;
  }

  function compare(left, right) {
    const a = String(left || "").split(/\r?\n/);
    const b = String(right || "").split(/\r?\n/);
    const table = lcs(a, b);
    const rows = [];
    let i = 0, j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) {
        rows.push({type: "same", left: a[i], right: b[j]});
        i++; j++;
      } else if (j < b.length && (i === a.length || table[i][j + 1] >= table[i + 1]?.[j])) {
        rows.push({type: "add", left: "", right: b[j++]});
      } else {
        rows.push({type: "remove", left: a[i++], right: ""});
      }
    }
    return rows;
  }

  globalThis.PASIUserScriptDiff = Object.freeze({compare});
})();
