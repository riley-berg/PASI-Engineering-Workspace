(() => {
  "use strict";

  let tail = Promise.resolve();

  function run(task) {
    const execute = tail.then(() => task(), () => task());
    tail = execute.catch(() => undefined);
    return execute;
  }

  globalThis.PASIUserScriptInstallQueue = Object.freeze({run});
})();
