import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const require = createRequire(import.meta.url);
const { build } = require(resolve(root, '.tools/vencord/node_modules/esbuild'));
const state = { members: [{ userId: '456' }], channel: '123', speaking: [{ user: { id: '456' }, speaking: true }, { user: { id: 'FORBIDDEN' }, speaking: true }] };
globalThis.__chibiTest = state;
const calls = [];
let channelId = '123';
globalThis.VencordNative = { pluginHelpers: { DesktopChibiBridge: {
    getConfiguration: async () => ({ enabled: true, channelId, users: ['456'] }),
    sendVoiceState: async (...args) => { calls.push(args); return { ok: true, matched: 1 }; }
} } };
const timers = new Map();
let nextTimer = 0;
globalThis.setInterval = (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; };
globalThis.clearInterval = id => timers.delete(id);
const result = await build({ entryPoints: [resolve(root, 'integrations/vesktop/DesktopChibiBridge/index.ts')],
    bundle: true, platform: 'node', format: 'esm', write: false,
    plugins: [{ name: 'fixtures', setup(builder) {
        builder.onResolve({ filter: /^@/ }, args => ({ path: args.path, namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ contents:
            args.path === '@utils/types' ? 'export default function definePlugin(p) { return p; }' :
            args.path === '@webpack' ? 'export const findStoreLazy = () => ({isSpeaking: () => false});' :
            'export const VoiceStateStore = {getVoiceStatesForChannel: () => globalThis.__chibiTest.members}; export const ChannelRTCStore = {getSpeakingParticipants: () => globalThis.__chibiTest.speaking}; export const SelectedChannelStore = {getVoiceChannelId: () => globalThis.__chibiTest.channel};'
        }));
    } }]
});
const { default: plugin } = await import('data:text/javascript;base64,' + Buffer.from(result.outputFiles[0].text).toString('base64'));
const settle = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
await plugin.start(); await settle();
assert.deepEqual(calls[0], ['123', '456', true, false, true]);
const tick = [...timers.values()].find(t => t.delay === 250).callback;
state.channel = 'OTHER'; tick(); await settle();
assert.deepEqual(calls.at(-1), ['123', '456', false, false, false]);
state.channel = '123'; state.speaking = []; tick(); await settle();
assert.equal(calls.at(-1)[2], false);
state.speaking = [{ user: { id: '456' }, speaking: true }]; tick(); await settle();
assert.deepEqual(calls.at(-1), ['123', '456', true, false, true]);
plugin.stop(); await settle();
assert.equal(calls.at(-1)[2], false);
assert.equal(timers.size, 0);
assert(calls.every(c => c[0] === '123' && c[1] === '456'));
console.log('PASS: speaker start/stop, channel filter, user filter, plugin shutdown');

channelId = '';
state.channel = '789';
await plugin.start(); await settle();
assert.deepEqual(calls.at(-1), ['789', '456', true, false, true]);
const autoTick = [...timers.values()].find(t => t.delay === 250).callback;
state.channel = '987'; autoTick(); await settle();
assert.deepEqual(calls.at(-1), ['987', '456', true, false, true]);
state.speaking = []; autoTick(); await settle();
assert.deepEqual(calls.at(-1), ['987', '456', false, false, true]);
state.members = []; autoTick(); await settle();
assert.deepEqual(calls.at(-1), ['987', '456', false, true, false]);
state.members = [{ userId: '456' }]; state.speaking = [{ user: { id: '456' }, speaking: true }];
state.channel = null; autoTick(); await settle();
assert.deepEqual(calls.at(-1), ['0', '456', false, false, false]);
state.channel = '789'; autoTick(); await settle();
assert.deepEqual(calls.at(-1), ['789', '456', true, false, true]);
plugin.stop(); await settle();
console.log('PASS: automatic channel switch, disconnect and rejoin');
