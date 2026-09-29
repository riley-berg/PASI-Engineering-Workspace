  async function submitPrompt(expected, options = {}) {
    const networkStartedPromise = options.networkStartedPromise || null;
    const fastPath = options.fastPath === true;
    const handoffBox = options.readyBox && options.readyBox.isConnected === true
      ? options.readyBox
      : null;
    const networkSubmission = Boolean(networkStartedPromise);
    const snapshot = snapshotUserMessages();
    const { head, tail } = promptFingerprints(expected);
    const newMessageState = () => classifyNewUserMessages(userMessages(), snapshot, head, tail, messageText);
    const accepted = () => {
      if (networkSubmission) return null;
      const state = newMessageState();
      if (state === 'match') return 'verified';
      if (state === 'new_unmatched' && generating()) return 'new_message_generating';
      return null;
    };

    const strategies = [
      async (box, button) => {
        if (!networkSubmission && generating()) return false;
        const form = (button || box).closest?.('form') || box.closest?.('form') || null;
        if (!form || typeof form.requestSubmit !== 'function') return false;
        try {
          const type = String(button?.getAttribute?.('type') || 'submit').toLowerCase();
          if (!button || type === 'submit') form.requestSubmit(button || undefined);
          else form.requestSubmit();
          return true;
        } catch (_) {
          return false;
        }
      },
      async (_box, button) => {
        if ((!networkSubmission && generating()) || !button || disabled(button)) return false;
        nativeMouseActivate(button);
        return true;
      },
      async (box) => {
        if ((!networkSubmission && generating()) || !composerContainsPrompt(box, expected)) return false;
        dispatchEnter(box);
        return true;
      }
    ];

    for (let attempt = 1; attempt <= strategies.length; attempt += 1) {
      let via = accepted();
      if (via) return {
        via,
        attempt,
        verified: via === 'verified',
        timing: {
          injected_at_ms: null,
          ack_at_ms: Date.now(),
          user_messages_added: countNewUserMessages(userMessages(), snapshot),
          ack_verified: via === 'verified',
          submission_via: via
        }
      };

      if (fastPath) {
        if (reasoningMode !== 'thinking' && reasoningMode !== 'unavailable') await ensureThinkingBestEffort();
      } else {
        await ensurePromptSubmissionReady();
      }
      const box = handoffBox || composer();
      const composerEmptied = !box || !composerContainsPrompt(box, expected);
      const alreadySubmitted =
        !networkSubmission &&
        (generating() || newMessageState());
      if ((attempt > 1 && composerEmptied) || alreadySubmitted) {
        via = await waitUntil(accepted, SUBMISSION_ACK_MS, DOM_POLL_MS);
        const finalVia = via || 'sent_unverified';
        return {
          via: finalVia,
          attempt,
          verified: via === 'verified',
          timing: {
            injected_at_ms: null,
            ack_at_ms: Date.now(),
            user_messages_added: countNewUserMessages(userMessages(), snapshot),
            ack_verified: via === 'verified',
            submission_via: finalVia
          }
        };
      }

      let readyBox = box;
      if (!readyBox) throw new Error('PASI_NATIVE: composer disappeared');

      if (!composerContainsPrompt(readyBox, expected)) {
        if (normalize(readText(readyBox))) {
          throw new Error('PASI_NATIVE: composer holds unrelated text; refusing to overwrite');
        }
        insertText(readyBox, expected);
        readyBox = await waitUntil(() => {
          const current = composer();
          return current && composerContainsPrompt(current, expected) ? current : null;
        }, 2000, DOM_POLL_MS) || composer();
      }

      if (!readyBox || !composerContainsPrompt(readyBox, expected)) {
        if (attempt < strategies.length) continue;
        throw new Error('PASI_NATIVE: composer lost the requested prompt before submission after bounded recovery');
      }

      const immediateButton = sendCandidatesForComposer(readyBox)[0] ||
        labeledSendInScope(readyBox.closest?.('form') || readyBox.parentElement || null);
      const button = immediateButton || await waitForSend(readyBox);
      if (!button) {
        if (attempt < strategies.length) continue;
        throw new Error('PASI_NATIVE: send control unavailable');
      }

      // Once a send strategy has fired, never invoke another send mechanism:
      // the delayed acknowledgement may simply trail the real submission, and
      // a second click can duplicate work.
      const injectedAtMs = Date.now();
      const fired = await strategies[attempt - 1](readyBox, button);
      if (!fired) continue;

      if (networkStartedPromise) {
        return {
          via: 'network_armed',
          attempt,
          verified: false,
          timing: {
            injected_at_ms: injectedAtMs,
            ack_at_ms: Date.now(),
            user_messages_added: null,
            ack_verified: false,
            submission_via: 'network_armed'
          }
        };
      }

      via = await waitUntil(accepted, SUBMISSION_ACK_MS, DOM_POLL_MS);
      const finalVia = via || 'sent_unverified';
      return {
        via: finalVia,
        attempt,
        verified: via === 'verified',
        timing: {
          injected_at_ms: injectedAtMs,
          ack_at_ms: Date.now(),
          user_messages_added: countNewUserMessages(userMessages(), snapshot),
          ack_verified: via === 'verified',
          submission_via: finalVia
        }
      };
    }