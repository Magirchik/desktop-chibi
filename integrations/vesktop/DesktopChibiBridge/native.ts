// SPDX-License-Identifier: GPL-3.0-or-later
// Only this private config file and this user's fixed local IPC endpoint are used.
import type { IpcMainInvokeEvent } from "electron";
import { lstat, readFile } from "node:fs/promises";
import { createConnection } from "node:net";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

interface BridgeConfig {
    version: 1;
    source: "vesktop";
    token: string;
    channel_id: string;
    users: string[];
}

const snowflake = (value: unknown): value is string => typeof value === "string" && /^\d{1,24}$/.test(value);

async function loadConfig(): Promise<BridgeConfig | null> {
    if (process.platform !== "linux") return null;
    const file = join(process.env.XDG_CONFIG_HOME || join(homedir(), ".config"), "DesktopChibi", "vesktop-bridge.json");
    try {
        const info = await lstat(file);
        if (!info.isFile() || info.uid !== process.getuid!() || (info.mode & 0o077) !== 0 || info.size > 8192) return null;
        const config = JSON.parse(await readFile(file, "utf8"));
        if (config.version !== 1 || config.source !== "vesktop" || typeof config.token !== "string"
            || !/^[\w-]{32,128}$/.test(config.token)
            || (config.channel_id !== "" && !snowflake(config.channel_id))
            || !Array.isArray(config.users) || config.users.length < 1 || config.users.length > 64
            || !config.users.every(snowflake)) return null;
        return config;
    } catch {
        return null;
    }
}

// No token is exposed to the renderer or plugin settings.
export async function getConfiguration(_event: IpcMainInvokeEvent) {
    const config = await loadConfig();
    return config ? { enabled: true, channelId: config.channel_id, users: config.users }
        : { enabled: false, channelId: "", users: [] as string[] };
}

export async function sendVoiceState(_event: IpcMainInvokeEvent, channel: string, user: string, active: boolean, keepalive: boolean, present?: boolean) {
    const config = await loadConfig();
    if (!config || !snowflake(channel) || !snowflake(user) || typeof active !== "boolean" || typeof keepalive !== "boolean"
        || (present !== undefined && typeof present !== "boolean")
        || (config.channel_id !== "" && channel !== config.channel_id) || !config.users.includes(user)) {
        return { ok: false, matched: 0, error: "not_allowed" };
    }
    // No arbitrary address, command, URL, file path, event name or source is accepted.
    const path = join(tmpdir(), `desktop-chibi-${process.getuid!()}.sock`);
    const packet = JSON.stringify({ source: "vesktop", event: "voice.speaking", subject: user,
        channel, active, keepalive, ...(present === undefined ? {} : { present }), token: config.token }) + "\n";
    return new Promise<{ ok: boolean; matched: number; error?: string; }>(resolve => {
        const socket = createConnection(path);
        let buffer = "";
        let finished = false;
        const done = (result: { ok: boolean; matched: number; error?: string; }) => {
            if (finished) return;
            finished = true;
            socket.destroy();
            resolve(result);
        };
        socket.setTimeout(1500, () => done({ ok: false, matched: 0, error: "timeout" }));
        socket.on("error", () => done({ ok: false, matched: 0, error: "unavailable" }));
        socket.on("connect", () => socket.write(packet));
        socket.on("end", () => done({ ok: false, matched: 0, error: "closed" }));
        socket.on("data", chunk => {
            buffer += chunk.toString("utf8");
            if (buffer.length > 4096) return done({ ok: false, matched: 0, error: "invalid_response" });
            if (!buffer.includes("\n")) return;
            try {
                const response = JSON.parse(buffer.split("\n", 1)[0]);
                done({ ok: response.ok === true, matched: Number.isInteger(response.matched) ? response.matched : 0,
                    ...(response.ok === true ? {} : { error: "rejected" }) });
            } catch {
                done({ ok: false, matched: 0, error: "invalid_response" });
            }
        });
    });
}
