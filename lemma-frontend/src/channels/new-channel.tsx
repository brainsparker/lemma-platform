"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Member, Pod } from "@/data";
import { useMe } from "@/session/use-me";
import { Modal } from "@/shell/modal";
import { channelApi } from "./api";
import type { Visibility } from "./model";
import { keepChannel } from "./queries";

/** A channel made in Lemma: a name, what it is for, and who of the space is
 *  in it. Nothing outside feeds it until a group is brought in. */
export function NewChannel({ pod, onClose, onMade }: {
    pod: Pod;
    onClose: () => void;
    onMade: (channelId: string) => void;
}) {
    const me = useMe();
    const ids = useId();
    const cache = useQueryClient();
    const [title, setTitle] = useState("");
    const [topic, setTopic] = useState("");
    const [members, setMembers] = useState<Set<string>>(new Set());
    const [visibility, setVisibility] = useState<Visibility>("PUBLIC");
    const nameField = useRef<HTMLInputElement>(null);
    useEffect(() => { nameField.current?.focus(); }, []);
    /* The pod's members were read when the space opened, and are kept a
       while: anybody who joined since is asked for again here, where they
       are offered. */
    useEffect(() => { void cache.invalidateQueries({ queryKey: ["pod-detail", pod.id], exact: true }); }, [cache, pod.id]);
    /* People, not teammates; and not the reader, who is in it already. */
    const people = pod.members.filter((member): member is Member & { userId: string } =>
        member.kind !== "teammate" && Boolean(member.userId) && member.userId !== me);
    const make = useMutation({
        mutationFn: () => channelApi(pod.id).create({ title: title.trim(), topic: topic.trim() || null, members: [...members], visibility }),
        onSuccess: (made) => { keepChannel(cache, pod.id, made); onMade(made.id); },
    });
    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (title.trim() && !make.isPending) make.mutate();
    };
    const toggle = (userId: string, on: boolean) => setMembers((was) => {
        const next = new Set(was);
        if (on) next.add(userId); else next.delete(userId);
        return next;
    });
    return (
        <Modal title="New channel" subtitle={"In " + pod.name} onClose={onClose}>
            <form className="gsheet" onSubmit={submit}>
                <label className="gsheet__field">
                    <span className="gsheet__label">Name</span>
                    <input ref={nameField} value={title} maxLength={200} required placeholder="launch" onChange={(event) => setTitle(event.target.value)} />
                </label>
                <label className="gsheet__field">
                    <span className="gsheet__label">What it is for <span className="gsheet__hint">(optional)</span></span>
                    <input value={topic} maxLength={500} placeholder="Beta launch, week of the 20th" onChange={(event) => setTopic(event.target.value)} />
                </label>
                <fieldset className="cnew__visibility">
                    <legend className="gsheet__label">Who can find it</legend>
                    <label className="gsheet__check">
                        <input type="radio" name={ids + "-visibility"} checked={visibility === "PUBLIC"} onChange={() => setVisibility("PUBLIC")} />
                        <span>Everyone in {pod.name} <span className="gsheet__hint">— anyone can read it and join</span></span>
                    </label>
                    <label className="gsheet__check">
                        <input type="radio" name={ids + "-visibility"} checked={visibility === "PRIVATE"} onChange={() => setVisibility("PRIVATE")} />
                        <span>Only the people in it <span className="gsheet__hint">— private</span></span>
                    </label>
                </fieldset>
                <fieldset className="cnew__people" aria-describedby={ids + "-people"}>
                    <legend className="gsheet__label">Who is in it</legend>
                    <span className="gsheet__hint" id={ids + "-people"}>You are. Add anyone else from {pod.name}; more can join later.</span>
                    {people.length === 0 ? (
                        <p className="gsheet__hint">Nobody else is in {pod.name} yet.</p>
                    ) : (
                        <ul className="cnew__list">
                            {people.map((member) => (
                                <li key={member.userId}>
                                    <label className="gsheet__check" htmlFor={ids + "-member-" + member.userId}>
                                        <input id={ids + "-member-" + member.userId} type="checkbox" aria-label={member.name} checked={members.has(member.userId)} onChange={(event) => toggle(member.userId, event.target.checked)} />
                                        <span>{member.name}</span>
                                    </label>
                                </li>
                            ))}
                        </ul>
                    )}
                </fieldset>
                {make.isError && <p className="gsheet__problem" role="alert">{make.error instanceof Error && make.error.message ? make.error.message : "Couldn’t make the channel. Try again."}</p>}
                <div className="gsheet__foot">
                    <button type="button" className="ghost-pill" onClick={onClose}>Cancel</button>
                    <button type="submit" className="pill-button" disabled={make.isPending || !title.trim()}>{make.isPending ? "Making…" : "Make channel"}</button>
                </div>
            </form>
        </Modal>
    );
}
