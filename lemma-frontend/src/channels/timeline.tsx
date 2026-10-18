"use client";

import { useLayoutEffect, useMemo, useRef } from "react";
import { LineView } from "./line";
import { lineDays, type ChannelDetail, type Line } from "./model";

/** A channel's lines, a day at a time, oldest at the top. It stays at the
 *  newest line while you are there, keeps your place while you read back,
 *  and keeps it again when an older page is put above. */
export function Timeline({ podId, channel, lines, loading, failed, hasOlder, loadingOlder, onOlder, onRetry, onThread, pin }: {
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
}) {
    const box = useRef<HTMLDivElement>(null);
    const atEnd = useRef(true);
    const fromEnd = useRef<number | null>(null);
    const days = useMemo(() => lineDays(lines), [lines]);
    const newest = lines.at(-1)?.id ?? null;

    useLayoutEffect(() => {
        const element = box.current;
        if (!element) return;
        if (fromEnd.current !== null) {
            element.scrollTop = element.scrollHeight - fromEnd.current;
            fromEnd.current = null;
            return;
        }
        if (atEnd.current) element.scrollTop = element.scrollHeight;
    }, [lines.length, newest]);

    useLayoutEffect(() => {
        const element = box.current;
        atEnd.current = true;
        if (element) element.scrollTop = element.scrollHeight;
    }, [pin]);

    const older = () => {
        const element = box.current;
        if (element) fromEnd.current = element.scrollHeight - element.scrollTop;
        onOlder();
    };

    return (
        <div
            className="ctimeline"
            ref={box}
            onScroll={(event) => {
                const element = event.currentTarget;
                atEnd.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
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
            <div role="log" aria-live="polite" aria-relevant="additions" aria-label={"Lines in this channel"}>
                {days.map((day) => (
                    <section key={day.key} className="cday" aria-label={day.label}>
                        <h2 className="cday__label">{day.label}</h2>
                        <ol className="cday__lines">
                            {day.lines.map((line) => (
                                <LineView key={line.id} podId={podId} channel={channel} line={line} onThread={onThread} />
                            ))}
                        </ol>
                    </section>
                ))}
            </div>
        </div>
    );
}
