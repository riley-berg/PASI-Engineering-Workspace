async function loadSuite() {
  const response = await fetch(chrome.runtime.getURL("suites/p0-smoke-v1.json"));
  if (!response.ok) throw new Error("failed to load suite");
  return await response.json();
}

async function requestOriginPermission(origin) {
  const normalized = origin.endsWith("/") ? origin + "*" : origin + "/*";
  const granted = await chrome.permissions.request({origins: [normalized]});
  if (!granted) throw new Error("origin permission was denied");
}

document.getElementById("run").addEventListener("click", async () => {
  const result = document.getElementById("result");
  result.textContent = "Running...";
  try {
    const origin = document.getElementById("origin").value.trim().replace(//$/, "");
    const codeHead = document.getElementById("code-head").value.trim();
    const token = document.getElementById("token").value;
    if (!origin) throw new Error("target origin is required");
    if (!/^[0-9a-f]{40}$/.test(codeHead)) throw new Error("code head must be a 40-character SHA");
    const suite = await loadSuite();
    await requestOriginPermission(origin);
    const response = await chrome.runtime.sendMessage({
      type: "pasi-human-test-run-suite",
      suite,
      targetOrigin: origin,
      codeHead,
      backendToken: token
    });
    result.textContent = JSON.stringify(response, null, 2);
  } catch (error) {
    result.textContent = String(error?.message || error);
  }
});