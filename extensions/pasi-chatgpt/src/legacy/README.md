# Legacy DOM controller

dom-controller.js is the quarantined browser-DOM implementation retained only as a bounded fallback while the network path is promoted.

Rules:
- Do not add new DOM selectors, DOM heuristics, generation-state detection, response extraction, or recovery behavior here.
- New automation authority belongs in the network/request lifecycle path.
- Changes here are compatibility-only until the corresponding network implementation and tests exist.
- Removal is gated on network-native prompt submission, response correlation, completion, usage/context detection, recovery, chat rollover, and live acceptance covering the same contracts.

The active extension manifest and background injector must not point at src/content.js; the legacy controller is intentionally explicit under src/legacy/.
