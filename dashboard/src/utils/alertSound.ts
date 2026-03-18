/**
 * Web Audio API sound generator for alert notifications.
 * No external audio files needed — generates tones programmatically.
 */

const SOUNDS = {
  ding: { freq: 880, type: 'sine' as OscillatorType, duration: 0.2 },
  chime: { freq: 660, type: 'sine' as OscillatorType, duration: 0.35 },
  alarm: { freq: 440, type: 'sawtooth' as OscillatorType, duration: 0.4 },
  cash: { freq: 1200, type: 'sine' as OscillatorType, duration: 0.15 },
} as const;

export type SoundType = keyof typeof SOUNDS;

let _ctx: AudioContext | null = null;

function getContext(): AudioContext {
  if (!_ctx || _ctx.state === 'closed') {
    _ctx = new AudioContext();
  }
  return _ctx;
}

export function playAlertSound(type: SoundType = 'ding', volume = 0.3): void {
  if (!isSoundEnabled()) return;
  try {
    const ctx = getContext();
    if (ctx.state === 'suspended') ctx.resume();

    const config = SOUNDS[type];
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.frequency.value = config.freq;
    osc.type = config.type;
    gain.gain.value = volume;
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + config.duration);

    osc.start();
    osc.stop(ctx.currentTime + config.duration);

    // For chime: add a second tone
    if (type === 'chime') {
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.frequency.value = config.freq * 1.5;
      osc2.type = config.type;
      gain2.gain.value = volume * 0.6;
      gain2.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + config.duration + 0.15);
      osc2.start(ctx.currentTime + 0.1);
      osc2.stop(ctx.currentTime + config.duration + 0.15);
    }
  } catch {
    // Audio not available
  }
}

const SOUND_KEY = 'alert-sound-enabled';

export function isSoundEnabled(): boolean {
  return localStorage.getItem(SOUND_KEY) !== 'false';
}

export function setSoundEnabled(enabled: boolean): void {
  localStorage.setItem(SOUND_KEY, String(enabled));
}

export function toggleSound(): boolean {
  const next = !isSoundEnabled();
  setSoundEnabled(next);
  return next;
}
