import { parseDisplayResource, resourceLabel, type DisplayResource } from "@/thread/display-resource";
import type { CanvasFrameItem } from "./call-socket";

/** One thing on the call's canvas: the tool call that put it there.
 *
 *  It carries its own conversation because anyone in the call may draw -- the
 *  coordinator and each of its sub-agents -- and a widget is served by the
 *  conversation and tool call that made it. */
export interface CanvasItem {
    id: string;
    conversationId: string;
    resource: DisplayResource;
    label: string;
    /** The earlier item this one stands in for, when it redraws one. */
    replaces: string | null;
}

export function canvasItemFrom(frame: CanvasFrameItem): CanvasItem | null {
    const resource = parseDisplayResource(frame.args, frame.result);
    if (!resource || !frame.tool_call_id || !frame.conversation_id) return null;
    const args = frame.args && typeof frame.args === "object" ? (frame.args as Record<string, unknown>) : {};
    const replaces = typeof args.replaces === "string" && args.replaces ? args.replaces : null;
    return { id: frame.tool_call_id, conversationId: frame.conversation_id, resource, label: resourceLabel(resource), replaces };
}

/** The canvas as the person pages through it: an item that replaces an
 *  earlier one takes its place in the order rather than stacking a copy. */
export function canvasDeck(items: CanvasItem[]): CanvasItem[] {
    const deck: CanvasItem[] = [];
    for (const item of items) {
        const at = item.replaces ? deck.findIndex((one) => one.id === item.replaces) : -1;
        if (at >= 0) deck[at] = item;
        else deck.push(item);
    }
    return deck;
}
