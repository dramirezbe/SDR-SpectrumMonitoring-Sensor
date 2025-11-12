// server.js
// Usage: node server.js
// Make sure you `npm install ws` first.

const { spawn } = require('child_process');
const WebSocket = require('ws');

const PORT = 8000;

// Replace this command with your exact pipeline (we use shell form).
const pipelineCmd = `
hackrf_transfer -r - -f 105700000 -s 2000000 -a 0 -l 40 -g 50 \
| csdr convert_s8_f \
| csdr fir_decimate_cc 10 0.05 \
| csdr fmdemod_quadri_cf \
| csdr deemphasis_wfm_ff 200000 5.0e-5 - \
| ffmpeg -hide_banner -loglevel error -f f32le -ar 200000 -ac 1 -i - -f s16le -ar 48000 -ac 1 -
`;
// Explanation: final ffmpeg emits raw s16le PCM @ 48000 Hz to stdout.

const wss = new WebSocket.Server({ port: PORT }, () => {
  console.log(`WebSocket server listening on ws://localhost:${PORT}`);
});

// Keep track of clients
wss.on('connection', ws => {
  console.log('Client connected. Sending raw PCM s16le@48k mono.');
  // Optionally send initial metadata (sampleRate, channels, format)
  ws.send(JSON.stringify({ sampleRate: 48000, channels: 1, format: 's16le' }));
  ws.on('close', () => console.log('Client disconnected'));
});

// Spawn the shell pipeline
console.log('Starting SDR pipeline (this may require root privileges for HackRF)...');
const sh = spawn(pipelineCmd, { shell: true, stdio: ['ignore', 'pipe', 'inherit'] });

// Broadcast binary chunks from stdout to all clients
sh.stdout.on('data', chunk => {
  // chunk is a Buffer of s16le PCM samples
  for (const client of wss.clients) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(chunk);
    }
  }
});

sh.on('exit', (code, sig) => {
  console.log(`Pipeline exited with ${code} ${sig}`);
  // close server
  wss.close();
});
sh.on('error', err => {
  console.error('Failed to start pipeline:', err);
});
