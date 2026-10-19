"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

/** How far the reader has read a channel, by the lines they have actually
 *  seen -- not by the server's clock, and not by the page being open.
 *
 *  A line counts as seen once most of it has been on screen while the tab is
 *  in front. Reading back through history sees only old lines, so the read
 *  position never jumps past what is below the fold; at the bottom, with the
 *  newest top-level line in view, the channel is read through its newest line
 *  of all (`lastSeq`), which may be a thread reply the timeline counts rather
 *  than shows. */

export interface ReadInputs {
    /** The highest `seq` seen on screen, or 0. */
    seenMax: number;
    /** The reader is at the bottom of the timeline. */
    atEnd: boolean;
    /** The newest top-level line the timeline holds. */
    newestShown: number;
    /** The newest line in the channel, replies included. */
    lastSeq: number;
    /** What the server last said the reader had read. */
    readSeq: number;
    /** What this view last asked to mark, so one line is marked once. */
    marked: number;
}

/** The `seq` to mark read now, or null when there is nothing new to mark. */
export function readTarget(inputs: ReadInputs): number | null {
    let target = inputs.seenMax;
    if (inputs.atEnd && inputs.newestShown > 0 && inputs.seenMax >= inputs.newestShown) {
        target = Math.max(target, inputs.lastSeq);
    }
    return target > Math.max(inputs.readSeq, inputs.marked) ? target : null;
}

/** The highest `data-seq` of any element under `box` that has been on
 *  screen while the page was visible. Re-observes when `version` changes --
 *  a new line, an older page. */
export function useSeenSeq(box: RefObject<HTMLElement | null>, version: unknown, active: boolean): number {
    const [seen, setSeen] = useState(0);
    const [shown, setShown] = useState(0);
    const pending = useRef(0);
    /* An observer reports a line once, when it crosses the edge; a line that
       sat on screen while the tab was behind another is reported again by
       observing afresh when the tab comes back. */
    useEffect(() => {
        if (typeof document === "undefined") return;
        const back = () => { if (document.visibilityState === "visible") setShown((was) => was + 1); };
        document.addEventListener("visibilitychange", back);
        return () => document.removeEventListener("visibilitychange", back);
    }, []);
    useEffect(() => {
        const root = box.current;
        if (!root || !active || typeof IntersectionObserver === "undefined") return;
        const observer = new IntersectionObserver((entries) => {
            if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
            let best = pending.current;
            for (const entry of entries) {
                /* Most of it, or -- for a line taller than the view -- half the view of it. */
                const enough = entry.intersectionRatio >= 0.6 || entry.intersectionRect.height >= root.clientHeight / 2;
                if (!entry.isIntersecting || !enough) continue;
                const seq = Number((entry.target as HTMLElement).dataset.seq);
                if (Number.isFinite(seq) && seq > best) best = seq;
            }
            if (best > pending.current) {
                pending.current = best;
                setSeen(best);
            }
        }, { root, threshold: [0, 0.6, 1] });
        for (const element of root.querySelectorAll<HTMLElement>("[data-seq]")) observer.observe(element);
        return () => observer.disconnect();
    }, [box, version, active, shown]);
    return seen;
}
