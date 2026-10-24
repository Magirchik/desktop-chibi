// SPDX-License-Identifier: GPL-3.0-or-later
import definePlugin, { PluginNative } from "@utils/types";
import { findStoreLazy } from "@webpack";
import { ChannelRTCStore, SelectedChannelStore, VoiceStateStore } from "@webpack/common";

const Native = VencordNative.pluginHelpers.DesktopChibiBridge as PluginNative<typeof import("./native")>;
// Compatibility with clients predating ChannelRTCStore's participant API.
const SpeakingStore = findStoreLazy("SpeakingStore") as { isSpeaking(id: string): boolean; };
type Config = Awaited<ReturnType<typeof Native.getConfiguration>>;

let config: Config = { enabled: false, channelId: "", users: [] };
let states = new Map<string, boolean>();
const presence = new Map<string, boolean>();
let timer: ReturnType<typeof setInterval> | undefined;
let configTimer: ReturnType<typeof setInterval> | undefined;
let queues = new Map<string, Promise<void>>();
const pending = new Map<string, { channel: string; active: boolean; keepalive: boolean; present?: boolean; }>();
let generation = 0;
let loading = false;
let heartbeatAt = 0;
let reportedError = false;
let currentChannel = "0";

function send(user: string, active: boolean, keepalive: boolean, present?: boolean) {
    pending.set(user, { channel: currentChannel, active, keepalive, present });
    if (queues.has(user)) return;
    const job = (async () => {
        while (pending.has(user)) {
            const packet = pending.get(user)!;
            pending.delete(user);
            try {
                const result = await Native.sendVoiceState(packet.channel, user, packet.active, packet.keepalive, packet.present);
                if (!result.ok && !reportedError) {
                    reportedError = true;
                    console.warn("[DesktopChibiBridge] Local bridge unavailable. Enable the Vesktop source in Desktop Chibi.");
                }
                if (result.ok) reportedError = false;
            } catch { /* Desktop Chibi expires interrupted activity automatically. */ }
        }
    })();
    queues.set(user, job);
    void job.finally(() => { if (queues.get(user) === job) queues.delete(user); });
}

function speaking(user: string): boolean {
    try {
        if (typeof ChannelRTCStore?.getSpeakingParticipants === "function") {
            return ChannelRTCStore.getSpeakingParticipants(currentChannel)
                .some(participant => participant.user?.id === user && participant.speaking);
        }
        return Boolean(SpeakingStore.isSpeaking(user));
    } catch {
        return false;
    }
}

function sample(forceHeartbeat = false) {
    if (!config.enabled) return;
    const selected = SelectedChannelStore.getVoiceChannelId() || "0";
    const nextChannel = config.channelId || selected;
    if (nextChannel !== currentChannel) {
        // A channel switch is a new speaking session, even if the same friend keeps talking.
        states.clear();
        presence.clear();
        currentChannel = nextChannel;
    }
    const inChannel = selected !== "0" && selected === currentChannel;
    const heartbeat = forceHeartbeat || Date.now() - heartbeatAt >= 2000;
    if (heartbeat) heartbeatAt = Date.now();
    const members = inChannel ? VoiceStateStore.getVoiceStatesForChannel(currentChannel) : {};
    // Only the configured IDs and configured voice channel are ever sent.
    for (const user of config.users) {
        const present = inChannel && Object.values(members || {}).some(member => member.userId === user);
        const active = present && speaking(user);
        const previous = states.get(user);
        if (previous !== active || presence.get(user) !== present || heartbeat) {
            states.set(user, active);
            presence.set(user, present);
            send(user, active, previous === active, present);
        }
    }
}

async function refreshConfig(currentGeneration: number) {
    if (loading) return;
    loading = true;
    try {
        const next = await Native.getConfiguration();
        if (generation !== currentGeneration) return;
        if (JSON.stringify(next) !== JSON.stringify(config)) {
            states.clear();
            presence.clear();
            config = next;
            sample(true);
        }
    } finally {
        loading = false;
    }
}

export default definePlugin({
    name: "DesktopChibiBridge",
    description: "Local speaking indicators for selected users in Desktop Chibi. No audio, messages or Discord tokens are accessed.",
    tags: ["Voice", "Utility"],
    authors: [{ name: "Desktop Chibi", id: 0n }],
    async start() {
        const currentGeneration = ++generation;
        await refreshConfig(currentGeneration);
        if (currentGeneration !== generation) return;
        timer = setInterval(() => sample(), 250);
        configTimer = setInterval(() => { void refreshConfig(currentGeneration).catch(() => {}); }, 2000);
    },
    stop() {
        ++generation;
        clearInterval(timer);
        clearInterval(configTimer);
        for (const user of config.users) send(user, false, false, false);
        states.clear();
        presence.clear();
        config = { enabled: false, channelId: "", users: [] };
    }
});
