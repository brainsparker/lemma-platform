"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { Pod, Surface } from "@/data";
import { surfaceStatus } from "@/data/surface-settings";
import { ChannelIcon } from "@/shell/channels";
import { platformName, slackInvite, type Starter } from "./sources";

/** The ways to bring the space's bot into a group chat: a menu beside New
 *  channel, and the cards a space with no groups yet opens on. Each offers
 *  only what the space can do -- a platform its bot is not connected to is
 *  offered as connecting it, never as a dead option. */

/** One entry, as its platform stands: the thing to do where it can be done,
 *  and what has to happen first where it cannot. */
function starterWords(starter: Starter, bot: string): { title: string; note: string } {
    const name = platformName(starter.platform);
    if (starter.state === "missing") {
        return {
            title: "Connect " + name + " first",
            note: starter.platform === "WHATSAPP" ? "Then " + bot + " can start groups on it."
                : starter.platform === "TELEGRAM" ? "Then add " + bot + " to any group."
                : "Then invite @" + bot + " to any channel.",
        };
    }
    if (starter.state === "unwell") {
        return { title: name + " needs attention", note: surfaceStatus(starter.surface?.status, false) + ". Open its settings first." };
    }
    if (starter.platform === "WHATSAPP") return { title: "Start a WhatsApp group", note: bot + " creates it. You share the link." };
    if (starter.platform === "TELEGRAM") return { title: "Add " + bot + " to a Telegram group", note: "Pick the group in Telegram." };
    return { title: "Add " + bot + " to a Slack channel", note: "Invite " + slackInvite(starter.surface?.handle ?? "", bot).replace(/^\/invite /, "") + " in the channel." };
}

export function BringIn({ pod, starters, loading, onStart }: {
    pod: Pod;
    starters: Starter[];
    loading: boolean;
    onStart: (starter: Starter) => void;
}) {
    const [open, setOpen] = useState(false);
    const menuId = useId();
    const button = useRef<HTMLButtonElement>(null);
    const menu = useRef<HTMLDivElement>(null);

    /* Open: the first entry has the keyboard, a click anywhere else or Escape
       closes it, and focus goes back to the button it came from. */
    useEffect(() => {
        if (!open) return;
        menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            if (menu.current?.contains(target) || button.current?.contains(target)) return;
            setOpen(false);
        };
        document.addEventListener("mousedown", away);
        return () => document.removeEventListener("mousedown", away);
    }, [open]);

    const keys = (event: KeyboardEvent<HTMLDivElement>) => {
        const items = Array.from(menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []);
        const at = items.indexOf(document.activeElement as HTMLElement);
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); button.current?.focus(); }
        else if (event.key === "ArrowDown") { event.preventDefault(); items[(at + 1) % items.length]?.focus(); }
        else if (event.key === "ArrowUp") { event.preventDefault(); items[(at - 1 + items.length) % items.length]?.focus(); }
        else if (event.key === "Home") { event.preventDefault(); items[0]?.focus(); }
        else if (event.key === "End") { event.preventDefault(); items.at(-1)?.focus(); }
        else if (event.key === "Tab") setOpen(false);
    };

    return (
        <div className="gnew">
            <button
                ref={button}
                type="button"
                className="ghost-pill gnew__button"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                onClick={() => setOpen((was) => !was)}
            >
                Add to a group
            </button>
            {open && (
                <div className="gnew__menu" role="menu" id={menuId} aria-label={"Bring " + pod.name + " into a group"} ref={menu} onKeyDown={keys}>
                    {loading && <p className="gnew__quiet" role="status">Reading the channels…</p>}
                    {!loading && starters.map((starter) => {
                        const words = starterWords(starter, pod.name);
                        return (
                            <button
                                key={starter.platform}
                                type="button"
                                role="menuitem"
                                className="gnew__item"
                                data-ready={starter.state === "ready" || undefined}
                                onClick={() => { setOpen(false); onStart(starter); }}
                            >
                                <span className="gnew__mark"><ChannelIcon platform={starter.platform} size={24} /></span>
                                <span className="gnew__text">
                                    <span className="gnew__title">{words.title}</span>
                                    <span className="gnew__note">{words.note}</span>
                                </span>
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

/** Before there are any groups: the ways in, and what happens once there. */
export function FirstWays({ pod, starters, loading, onStart, onTelegramNow, onConnect }: {
    pod: Pod;
    starters: Starter[];
    loading: boolean;
    onStart: (starter: Starter) => void;
    /** Telegram's card opens Telegram itself, inside this click. */
    onTelegramNow: (surface: Surface) => void;
    onConnect: () => void;
}) {
    const bot = pod.name;
    const find = (platform: Starter["platform"]) => starters.find((starter) => starter.platform === platform);
    const order = [find("TELEGRAM"), find("WHATSAPP"), find("SLACK")].filter((starter): starter is Starter => Boolean(starter));
    return (
        <div className="gfirst">
            <header className="gfirst__head">
                <h2>Bring {bot} into a group chat</h2>
                <p>It answers people in {pod.name} as themselves, and everyone else from what you have marked Public. Each group gets a channel here.</p>
            </header>
            {loading ? (
                <p className="all__empty" role="status">Reading the channels…</p>
            ) : (
                <ul className="gfirst__ways">
                    {order.map((starter) => (
                        <FirstWay
                            key={starter.platform}
                            pod={pod}
                            starter={starter}
                            onGo={() => {
                                if (starter.state === "ready" && starter.surface && starter.platform === "TELEGRAM") onTelegramNow(starter.surface);
                                else if (starter.state === "ready") onStart(starter);
                                else onConnect();
                            }}
                        />
                    ))}
                </ul>
            )}
            <dl className="gfirst__how">
                <div>
                    <dt>Your questions</dt>
                    <dd>@mention {bot} or reply to it. It answers with your access.</dd>
                </div>
                <div>
                    <dt>Their questions</dt>
                    <dd>People outside {pod.name} get what is Public, nothing else.</dd>
                </div>
                <div>
                    <dt>Anything more</dt>
                    <dd>Comes to you here. Your answer goes back to the group.</dd>
                </div>
            </dl>
        </div>
    );
}

function FirstWay({ pod, starter, onGo }: { pod: Pod; starter: Starter; onGo: () => void }) {
    const bot = pod.name;
    const name = platformName(starter.platform);
    const ready = starter.state === "ready";
    const words = !ready
        ? {
            title: starter.platform === "SLACK" ? "Slack channels" : name + " groups",
            note: starter.state === "missing"
                ? "Connect " + name + " first. Then " + (starter.platform === "WHATSAPP" ? bot + " can start groups on it." : starter.platform === "TELEGRAM" ? "add " + bot + " to any group." : "invite @" + bot + " to any channel.")
                : name + ": " + surfaceStatus(starter.surface?.status, false).toLowerCase() + ". Open its settings first.",
            action: starter.state === "missing" ? "Connect " + name : "Open settings",
        }
        : starter.platform === "TELEGRAM"
            ? { title: "Add " + bot + " to a Telegram group", note: "One tap in Telegram. The group shows up here.", action: "Open Telegram" }
            : starter.platform === "WHATSAPP"
                ? { title: "Start a WhatsApp group", note: bot + " creates it on " + (starter.surface?.handle || "its number") + " and gives you a link to share.", action: "Start" }
                : { title: "Add " + bot + " to a Slack channel", note: "Invite " + slackInvite(starter.surface?.handle ?? "", bot).replace(/^\/invite /, "") + " in any channel. It shows up here.", action: "Add" };
    return (
        <li className="gway" data-ready={ready || undefined}>
            <span className="gway__mark"><ChannelIcon platform={starter.platform} size={40} /></span>
            <span className="gway__text">
                <span className="gway__title">{words.title}</span>
                <span className="gway__note">{words.note}</span>
            </span>
            <button
                type="button"
                className={ready && starter.platform === "TELEGRAM" ? "pill-button gway__go" : "ghost-pill gway__go"}
                onClick={onGo}
                aria-label={words.action === "Start" || words.action === "Add" ? words.title : undefined}
            >
                {words.action}
            </button>
        </li>
    );
}
