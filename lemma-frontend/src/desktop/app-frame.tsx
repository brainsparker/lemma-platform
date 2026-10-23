"use client";

import { useEffect, useRef, useState } from "react";
import { readMissingGrantMessage, type MissingGrant } from "@/data/app-grants";
import { AppNotGrantedNotice } from "@/stage/app-grants";
import { AppWindowPanel } from "./app-window";
import { useAppFrame } from "./pod-apps";

/** What to call an app in a sentence, from its address: a published app lives
 *  at `<slug>.apps.<host>`, and the slug is the name its builder gave it. */
function appLabel(url: string): string {
    try {
        const [slug, second] = new URL(url).hostname.split(".");
        return second === "apps" && slug ? slug : "the app";
    } catch {
        return "the app";
    }
}

/** One pod app beside the agent: framed where a frame is signed in, offered
 *  as a window where it would not be. `pod-apps.ts` decides which. */
export function AppFrameView({
    url,
    hidden,
    frameRef,
    onFrameLoad,
    owner,
}: {
    url: string;
    hidden?: boolean;
    frameRef: (element: HTMLIFrameElement | null) => void;
    onFrameLoad: (view: Window | null) => void;
    /** The pod and app name, for saying what the app was refused and
     *  offering its grants. Absent, a refusal goes unsaid, as before. */
    owner?: { podId: string; podName: string; appName: string };
}) {
    const frame = useAppFrame(url);
    const view = useRef<Window | null>(null);
    const [missing, setMissing] = useState<MissingGrant | null>(null);
    /* The SDK inside the app posts each refusal for want of a grant to the
       page framing it. Only this frame's own window is heard: another frame,
       or a page that framed us, does not get to put words over this app. */
    useEffect(() => {
        if (!owner) return;
        const listen = (event: MessageEvent) => {
            if (!view.current || event.source !== view.current) return;
            const refused = readMissingGrantMessage(event.data);
            if (refused) setMissing(refused);
        };
        window.addEventListener("message", listen);
        return () => window.removeEventListener("message", listen);
    }, [owner]);
    if (frame.kind === "window") return <AppWindowPanel url={url} hidden={hidden} reason={frame.reason} />;
    /* No frame yet: the shell answers in milliseconds, and a frame on the
       app's own address first would load it signed out and then swap. Said,
       rather than a blank pane — milliseconds on a warm machine is seconds on
       a busy one, and a blank pane reads as broken. */
    if (frame.kind === "pending") {
        return (
            <div className="app-window" hidden={hidden} aria-busy="true">
                <p className="empty-row" role="status">Opening {appLabel(url)}…</p>
            </div>
        );
    }
    return (
        <>
            <iframe
                ref={frameRef}
                className="frame"
                title="App"
                src={frame.src}
                hidden={hidden}
                onLoad={event => {
                    /* A reload is a fresh start: what the old page was
                       refused is not this one's to say. */
                    view.current = event.currentTarget.contentWindow;
                    setMissing(null);
                    onFrameLoad(event.currentTarget.contentWindow);
                }}
            />
            {owner && missing && (
                <AppNotGrantedNotice podId={owner.podId} podName={owner.podName} appName={owner.appName} appLabel={appLabel(url)}
                    missing={missing} hidden={hidden} onDismiss={() => setMissing(null)} />
            )}
        </>
    );
}
