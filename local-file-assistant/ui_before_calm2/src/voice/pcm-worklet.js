// Runs on the audio thread: hands each block of microphone samples (Float32, mono) to the page.
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}

registerProcessor('pcm-capture', PcmCapture);
