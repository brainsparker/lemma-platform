"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { LineView } from "./line";
import type { Answering } from "./live";
import { lineDays, type ChannelDetail, type Line } from "./model";
import { useSeenSeq } from "./read-state";

/** A channel's lines, a day at a time, oldest at the top. It stays at the
 *  newest line while you are there, keeps your place while you read back,
 *  and keeps it again when an older page is put above.
 *
 *  It tells its page how far the reader has seen (`onProgress`) -- the
 *  newest line on screen, and whether they are at the bottom -- which is what
 *  marks the channel read. Only new lines are said to a screen reader
 *  (`announced`), not the history as it loads. */
export function Timeline({ podId, channel, lines, loading, failed, hasOlder, loadingOlder, onOlder, onRetry, onThread, pin, visible,
    mayWrite, answering, agentName, highlight, announced, onProgress }: {
    podId: string;
    channel: ChannelDetail;
    lines: Line[];
    loading: boolean;
    failed: boolean;
    hasOlder: boolean;
    loadingOlder: boolean;
    onOlder: () => void;
    onRetry: () => void;
    onThread?: (line: Line) => void;
    /** Bumped when the reader sends something: back to the newest line. */
    pin: number;
    visible: boolean;
    mayWrite: boolean;
    answering: Answering[];
    agentName: (agentId: string | null) => string;
    /** A line to draw out and scroll to: opened from search or the bell. */
    highlight: string | null;
    announced: string;
    onProgress: (progress: { seenMax: number; atEnd: boolean }) => void;
}) {
    const box = useRef<HTMLDivElement>(null);
    const atEnd = useRef(true);
    const [endState, setEndState] = useState(true);
    const fromEnd = useRef<number | null>(null);
    const days = useMemo(() => lineDays(lines), [lines]);
    const newest = lines.at(-1)?.id ?? null;
    const seenMax = useSeenSeq(box, lines.length + ":" + newest, visible);

    useEffect(() => { onProgress({ seenMax, atEnd: endState }); }, [seenMax, endState, onProgress]);

    useLayoutEffect(() => {
        const element = box.current;
        if (!element) return;
        if (fromEnd.current !== null) {
            element.scrollTop = element.scrollHeight - fromEnd.current;
            fromEnd.current = null;
            return;
        }
        if (atEnd.current && !highlight) element.scrollTop = element.scrollHeight;
    }, [lines.length, newest, answering.length, highlight]);

    useLayoutEffect(() => {
        const element = box.current;
        atEnd.current = true;
        setEndState(true);
        if (element) element.scrollTop = element.scrollHeight;
    }, [pin]);

    /* Opened at one line: there, once it has loaded. */
    useEffect(() => {
        if (!highlight) return;
        const element = box.current?.querySelector<HTMLElement>("#line-" + CSS.escape(highlight));
        if (!element) return;
        element.scrollIntoView({ block: "center" });
        atEnd.current = false;
        setEndState(false);
    }, [highlight, lines.length]);

    const older = () => {
        const element = box.current;
        if (element) fromEnd.current = element.scrollHeight - element.scrollTop;
        onOlder();
    };

    return (
        <div
            className="ctimeline"
            ref={box}
            tabIndex={0}
            aria-label={"Lines in this channel"}
            onScroll={(event) => {
                const element = event.currentTarget;
                const end = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
                atEnd.current = end;
                if (end !== endState) setEndState(end);
            }}
        >
            {hasOlder && (
                <button type="button" className="ghost-pill ctimeline__older" disabled={loadingOlder} onClick={older}>
                    {loadingOlder ? "Loading…" : "Show earlier lines"}
                </button>
            )}
            {loading && <p className="ctimeline__quiet" role="status">Loading what was said…</p>}
            {failed && (
                <p className="ctimeline__quiet" role="alert">
                    Couldn’t load what was said. <button type="button" className="gsheet__inline" onClick={onRetry}>Try again</button>
                </p>
            )}
            {!loading && !failed && lines.length === 0 && <p className="ctimeline__quiet">Nothing said here yet.</p>}
            <div>
                {days.map((day) => (
                    <section key={day.key} className="cday" aria-label={day.label}>
                        <h2 className="cday__label">{day.label}</h2>
                        <ol className="cday__lines">
                            {day.lines.map((line) => (
                                <LineView
                                    key={line.id}
                                    podId={podId}
                                    channel={channel}
                                    line={line}
                                    onThread={onThread}
                                    mayWrite={mayWrite}
                                    answering={answering.filter((one) => one.messageId === line.id)}
                                    agentName={agentName}
                                    highlighted={highlight === line.id}
                                />
                            ))}
                        </ol>
                    </section>
                ))}
            </div>
            {/* New lines only: the history loading is not news. */}
            <p className="sr-only" aria-live="polite" aria-atomic="true">{announced}</p>
        </div>
    );
}
