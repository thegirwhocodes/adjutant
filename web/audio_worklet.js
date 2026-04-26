// AudioWorklet processor for capturing microphone audio.
// Runs on the audio rendering thread (not main) — process() is called every
// 128 samples regardless of AudioContext sample rate.
//
// We accept whatever native sample rate the AudioContext is using (typically
// 44100 or 48000 on macOS Chrome) and downsample to 16000 Hz mono PCM16,
// the format Whisper/Silero expect. Each 32 ms output frame (512 samples) is
// posted to the main thread, which forwards it to the WebSocket as a binary
// message.
//
// Why downsample here instead of in the AudioContext: AudioContext({sampleRate:16000})
// is supposedly supported but Chrome silently upmixes input back to 48 kHz on
// some macOS builds. Doing the downsample explicitly is the only reliable path.

class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetSampleRate = 16000;
    this.frameSize = 512;        // 32 ms at 16 kHz — Silero ONNX expects exactly this
    this.outputBuffer = new Int16Array(this.frameSize);
    this.outputIdx = 0;
    // Resampler state — track fractional position in the input stream.
    this.ratio = sampleRate / this.targetSampleRate;  // sampleRate is global in worklets
    this.inputCursor = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel || channel.length === 0) return true;

    // Linear-interpolation downsample. For 48 kHz → 16 kHz the ratio is 3 so
    // this is effectively decimation; for 44.1 kHz → 16 kHz it's true LERP.
    while (this.inputCursor < channel.length) {
      const idxFloor = Math.floor(this.inputCursor);
      const idxNext = Math.min(idxFloor + 1, channel.length - 1);
      const frac = this.inputCursor - idxFloor;
      const sample = channel[idxFloor] * (1 - frac) + channel[idxNext] * frac;
      // Clamp + convert to int16
      const clamped = Math.max(-1, Math.min(1, sample));
      this.outputBuffer[this.outputIdx++] = (clamped * 0x7fff) | 0;
      if (this.outputIdx === this.frameSize) {
        // Post a copy — buffer is reused
        this.port.postMessage(this.outputBuffer.buffer.slice(0));
        this.outputIdx = 0;
      }
      this.inputCursor += this.ratio;
    }
    // Carry fractional remainder into the next callback so we don't drift.
    this.inputCursor -= channel.length;
    return true;
  }
}

registerProcessor("capture", CaptureProcessor);