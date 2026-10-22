"use client";

import { useEffect, useState } from "react";
import { CloseIcon, FileIcon } from "@/ui/icons";
import { describeSize, type Attachment } from "./attachments";

/** Files waiting to go with the next message, above the box that sends them —
 *  the composer's and the ask box's, which hold the same files the same way. */
export function AttachedFiles({ files, onRemove, className = "attached" }: {
    files: readonly Attachment[];
    onRemove?: (key: string) => void;
    className?: string;
}) {
    if (files.length === 0) return null;
    return (
        <div className={className} aria-label="Attached files">
            {files.map((one) => <AttachedFile key={one.key} one={one} onRemove={onRemove} />)}
        </div>
    );
}

/** A picture is drawn as itself. A pasted screenshot is renamed on the way in
 *  (`pasted-20261022-101500.png`), so its name says nothing about which one it
 *  is; the picture does. Anything a browser cannot draw — a PDF, a HEIC from a
 *  phone — stays a named chip. */
function AttachedFile({ one, onRemove }: { one: Attachment; onRemove?: (key: string) => void }) {
    const preview = usePreview(one.file);
    const [unreadable, setUnreadable] = useState(false);
    const state = one.status === "uploading" ? "sending…"
        : one.status === "failed" ? "failed"
        : null;
    const drop = (
        <button
            type="button"
            className="attached__drop"
            aria-label={"Remove " + one.file.name}
            title={"Remove " + one.file.name}
            onClick={() => onRemove?.(one.key)}
        >
            <CloseIcon size={12} />
        </button>
    );

    if (preview && !unreadable) {
        return (
            <span className="attached__thumb" data-status={one.status} title={one.error ?? one.file.name}>
                <img src={preview} alt={one.file.name} onError={() => setUnreadable(true)} />
                {state && <span className="attached__state">{state}</span>}
                {drop}
            </span>
        );
    }
    return (
        <span className="attached__chip" data-status={one.status}>
            <FileIcon size={14} />
            <span className="attached__name" title={one.error ?? one.file.name}>{one.file.name}</span>
            <span className="attached__size">{state ?? describeSize(one.file.size)}</span>
            {drop}
        </span>
    );
}

/** A local URL for an image file, revoked when the file leaves the list. Made
 *  in an effect rather than during render so the strict-mode remount, which
 *  revokes the first one, makes a second instead of drawing a dead link. */
function usePreview(file: File): string | null {
    const [url, setUrl] = useState<string | null>(null);
    useEffect(() => {
        if (!file.type.startsWith("image/")) return;
        const made = URL.createObjectURL(file);
        setUrl(made);
        return () => {
            URL.revokeObjectURL(made);
            setUrl(null);
        };
    }, [file]);
    return url;
}
