// Microphone input and spoken output. Audio never leaves the device: the backend's local
// speech models do the work.
import { api } from '../api.js';
import { el } from './common.js';

const workletUrl = new URL('../voice/pcm-worklet.js', import.meta.url);
const MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
const READ_ALOUD_KEY = 'lfa.readAloud';

/** Starts recording; the returned stop() resolves with 16 kHz mono signed 16-bit PCM. */
async function startRecording(onLevel) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const ctx = new AudioContext({ sampleRate: 16000 }); // the browser resamples the microphone for us
  await ctx.audioWorklet.addModule(workletUrl);
  const source = ctx.createMediaStreamSource(stream);
  const capture = new AudioWorkletNode(ctx, 'pcm-capture');
  const mute = ctx.createGain(); // the node must reach the output to be pulled; silence keeps it inaudible
  mute.gain.value = 0;
  const chunks = [];
  capture.port.onmessage = (e) => {
    chunks.push(e.data);
    if (onLevel) onLevel(Math.sqrt(e.data.reduce((s, x) => s + x * x, 0) / e.data.length));
  };
  source.connect(capture).connect(mute).connect(ctx.destination);
  return async () => {
    source.disconnect();
    capture.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    await ctx.close();
    const samples = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
    let at = 0;
    for (const c of chunks) {
      samples.set(c, at);
      at += c.length;
    }
    return Int16Array.from(samples, (x) => Math.max(-1, Math.min(1, x)) * 32767).buffer;
  };
}

function micProblem(e) {
  if (e?.name === 'NotAllowedError') return 'Microphone access is blocked. Allow it in Windows Settings > Privacy > Microphone.';
  if (e?.name === 'NotFoundError') return 'No microphone was found.';
  return e?.message || 'The microphone could not start.';
}

// Auto-stop: listening ends by itself after you stop talking, so silence isn't recorded.
const QUIET_MS = 1400; // pause after speech that counts as "done"
const NOTHING_MS = 7000; // no speech at all by then: give up
const MAX_MS = 45000; // hard limit for one recording

/**
 * A microphone button: press to talk; it stops by itself when you pause (or press again).
 * onText gets the transcript; onError a message; onState('idle'|'recording'|'working').
 * @returns {{button: HTMLElement, toggle: ()=>Promise<void>, isRecording: ()=>boolean}}
 */
export function micButton({ onText, onError, onState }) {
  const meter = el('span', { class: 'mic-meter' });
  const button = el('button', { type: 'button', class: 'mic', 'aria-label': 'Speak', 'aria-pressed': 'false', title: 'Speak (press again to stop)' }, meter);
  button.insertAdjacentHTML('afterbegin', MIC_ICON);
  let stop = null;
  let busy = false;
  const state = (s) => {
    button.dataset.state = s;
    button.setAttribute('aria-pressed', String(s === 'recording'));
    if (s !== 'recording') meter.style.transform = 'scaleX(0)';
    button.title = s === 'recording' ? 'Listening… stops when you pause (or press to stop)' : 'Speak';
    onState?.(s);
  };

  async function toggle() {
    if (busy) return;
    if (!stop) {
      // Speech is judged against the room's own noise, measured in the first 300 ms.
      const started = performance.now();
      let noise = 0;
      let noiseN = 0;
      let heard = false;
      let lastLoud = started;
      const onLevel = (level) => {
        meter.style.transform = `scaleX(${Math.min(1, level * 8)})`;
        const now = performance.now();
        if (now - started < 300) {
          noise += level;
          noiseN++;
          return;
        }
        const floor = noiseN ? noise / noiseN : 0;
        if (level > Math.max(0.012, floor * 3)) {
          heard = true;
          lastLoud = now;
        }
        const done = heard ? now - lastLoud > QUIET_MS : now - started > NOTHING_MS;
        if ((done || now - started > MAX_MS) && stop && !busy) toggle();
      };
      try {
        stop = await startRecording(onLevel);
        state('recording');
      } catch (e) {
        stop = null;
        onError?.(micProblem(e));
      }
      return;
    }
    busy = true;
    state('working');
    const finish = stop;
    stop = null;
    try {
      const { text } = await api.stt(await finish());
      if (text) onText(text);
      else onError?.('I didn’t hear anything. Try again, closer to the microphone.');
    } catch (e) {
      onError?.(e.message);
    } finally {
      busy = false;
      state('idle');
    }
  }

  button.addEventListener('click', toggle);
  state('idle');
  return { button, toggle, isRecording: () => stop !== null };
}

let playing = null;

export function stopSpeaking() {
  playing?.pause();
  playing = null;
}

/** Reads text aloud with the local voice. */
export async function speak(text) {
  stopSpeaking();
  const url = URL.createObjectURL(await api.tts(text));
  const audio = new Audio(url);
  audio.addEventListener('ended', () => URL.revokeObjectURL(url));
  playing = audio;
  await audio.play();
}

export const readAloud = {
  get() {
    try {
      return localStorage.getItem(READ_ALOUD_KEY) === '1';
    } catch {
      return false;
    }
  },
  set(on) {
    try {
      localStorage.setItem(READ_ALOUD_KEY, on ? '1' : '0');
    } catch { /* not remembered, still works for this session */ }
  },
};
