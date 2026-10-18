"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source } from "@/data";
import { BellIcon, CloseIcon } from "@/ui/icons";
import { ClampedProse } from "@/thread/clamped-prose";
import { FormAnswer } from "./form-answer";
import {
    badgeCount,
    choicesOf,
    formTarget,
    moveFor,
    readInbox,
    whereAsked,
    type Inbox,
    type WaitingQuestion,
} from "./waiting";

/** The react-query key everything about one pod's inbox reads under. */
export const inboxKey = (podId: string) => ["inbox", podId] as const;

/** What is waiting on you in this pod, read from its inbox. */
export function useInbox(podId: string, enabled = true) {
    const sample = source.label === "sample";
    return useQuery({
        queryKey: inboxKey(podId),
        enabled,
        queryFn: async (): Promise<Inbox> => {
            if (sample) {
                const { SAMPLE_INBOX } = await import("@/data/fixtures");
                return readInbox(SAMPLE_INBOX);
            }
            return readInbox(await lemma(podId).channels.inbox(podId));
        },
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        refetchInterval: 60_000,
    });
}

/** The bell: every question put to you in this pod, answered where it is.
 *
 *  An agent asking you something, a teammate's agent, a workflow's form -- each
 *  is a line waiting on you in one of the pod's channels, and each is answered
 *  here, once, on that line.
 */
export function WaitingBell({ podId }: { podId: string }) {
    const [open, setOpen] = useState(false);
    /* In a portal, placed from the bell: the header clips its overflow, which
       would cut the panel off at its bottom edge. */
    const [at, setAt] = useState<{ top: number; right: number } | null>(null);
    const panel = useRef<HTMLDivElement | null>(null);
    const bell = useRef<HTMLButtonElement | null>(null);
    const sheet = useRef<HTMLDivElement | null>(null);
    const inbox = useInbox(podId);

    useEffect(() => {
        if (!open) { setAt(null); return; }
        const place = () => {
            const rect = bell.current?.getBoundingClientRect();
            if (!rect) return;
            setAt({ top: rect.bottom + 8, right: Math.max(16, window.innerWidth - rect.right) });
        };
        place();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            if (panel.current?.contains(target) || sheet.current?.contains(target)) return;
            setOpen(false);
        };
        const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", escape);
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        return () => {
            document.removeEventListener("mousedown", away);
            document.removeEventListener("keydown", escape);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [open]);

    const count = badgeCount(inbox.data?.waiting ?? 0);
    const items = inbox.data?.items ?? [];

    return (
        <div className="notify" ref={panel}>
            <button
                ref={bell}
                className="icon-button notify__bell"
                data-tour="needs"
                title={count ? count + " waiting on you" : "Nothing waiting on you"}
                aria-label={count ? "Waiting on you, " + count : "Waiting on you"}
                aria-expanded={open}
                onClick={() => setOpen(was => !was)}
            >
                <BellIcon size={19} />
                {count && <span className="notify__badge" aria-hidden="true">{count}</span>}
            </button>

            {open && at && createPortal((
                <div
                    className="notify__panel"
                    role="dialog"
                    aria-label="Waiting on you"
                    ref={sheet}
                    style={{ top: at.top, right: at.right }}
                >
                    <header className="notify__head">
                        <h2>Waiting on you</h2>
                        <button className="icon-button" aria-label="Close" onClick={() => setOpen(false)}>
                            <CloseIcon size={16} />
                        </button>
                    </header>
                    {inbox.isPending && <p className="notify__quiet" role="status">Reading…</p>}
                    {inbox.isError && (
                        <p className="notify__quiet" role="alert">
                            Couldn’t read what is waiting. <button onClick={() => void inbox.refetch()}>Try again</button>
                        </p>
                    )}
                    {!inbox.isPending && !inbox.isError && items.length === 0 && (
                        <p className="notify__quiet">Nothing is waiting on you here.</p>
                    )}
                    <div className="notify__list">
                        {items.map(question => (
                            <WaitingRow key={question.id} podId={podId} question={question} />
                        ))}
                    </div>
                </div>
            ), document.body)}
        </div>
    );
}

/** One question, and the way to answer it. `bare` on a channel's timeline,
 *  where the line above it already says who asked, and where. */
export function WaitingRow({ podId, question, bare = false }: { podId: string; question: WaitingQuestion; bare?: boolean }) {
    const cache = useQueryClient();
    const [text, setText] = useState("");
    const [problem, setProblem] = useState<string | null>(null);
    const sample = source.label === "sample";
    const move = moveFor(question);
    const target = formTarget(question);

    const answer = useMutation({
        mutationFn: async (given: Record<string, unknown>) => {
            if (sample) throw new Error("This is the sample source — connect a session to answer anything.");
            return lemma(podId).channels.answer(podId, question.channelId, question.id, given);
        },
        onSuccess: () => {
            setProblem(null);
            void cache.invalidateQueries({ queryKey: inboxKey(podId) });
            void cache.invalidateQueries({ queryKey: ["channel", podId] });
        },
        onError: (failure) => setProblem(failure instanceof Error ? failure.message : "Couldn’t send your answer."),
    });

    return (
        <article className="notify__item" data-unread="" data-bare={bare || undefined}>
            {!bare && <>
                <h3>{question.from}</h3>
                <p className="notify__delivery">{whereAsked(question)}</p>
                {question.text && <div className="notify__body"><ClampedProse text={question.text} /></div>}
            </>}

            {move === "choose" && (
                <div className="notify__choices" role="group" aria-label="Answers">
                    {choicesOf(question).map(choice => (
                        <button
                            key={choice}
                            className="notify__act"
                            disabled={answer.isPending}
                            onClick={() => answer.mutate({ choice })}
                        >
                            {choice}
                        </button>
                    ))}
                </div>
            )}

            {move === "approve" && (
                <div className="notify__choices" role="group" aria-label="Decision">
                    <button className="btn btn--primary" disabled={answer.isPending} onClick={() => answer.mutate({ decision: "APPROVE_ONCE" })}>
                        Approve
                    </button>
                    <button className="notify__act" disabled={answer.isPending} onClick={() => answer.mutate({ decision: "DENY" })}>
                        Deny
                    </button>
                </div>
            )}

            {move === "form" && target && (
                <FormAnswer
                    podId={podId}
                    runId={target.runId}
                    nodeId={target.nodeId}
                    onDone={() => void cache.invalidateQueries({ queryKey: inboxKey(podId) })}
                />
            )}

            {(move === "write" || move === "choose") && (
                <form
                    className="notify__answer"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (!text.trim()) return;
                        answer.mutate({ text: text.trim() });
                    }}
                >
                    <input
                        aria-label={"Answer " + question.from}
                        placeholder={move === "choose" ? "Or write your own" : "Your answer"}
                        value={text}
                        onChange={(event) => setText(event.target.value)}
                    />
                    <button className="btn btn--primary" type="submit" disabled={answer.isPending || !text.trim()}>
                        {answer.isPending ? "Sending…" : "Send"}
                    </button>
                </form>
            )}

            {problem && <p className="notify__problem" role="alert">{problem}</p>}
        </article>
    );
}
