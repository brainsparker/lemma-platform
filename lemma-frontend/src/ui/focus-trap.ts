"use client";

import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from "react";

/** A panel that holds the keyboard while it is open: focus moves into it,
 *  Tab and Shift+Tab go round inside it, Escape closes it, and closing puts
 *  focus back where it was -- on the button that opened it, as a rule.
 *
 *  For the side panels and popovers that are not `Modal`s (which do the same
 *  for themselves): the keys are heard on the panel, not the document, so a
 *  dialog opened over it still answers its own Escape first. */

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Where Tab goes from `active` among `items`, round the ends; null when it
 *  may go on as the browser would. */
export function nextFocus<T>(items: readonly T[], active: T | null, backwards: boolean): T | null {
    if (items.length === 0) return null;
    const at = active === null ? -1 : items.indexOf(active);
    if (at === -1) return backwards ? items[items.length - 1] : items[0];
    if (backwards && at === 0) return items[items.length - 1];
    if (!backwards && at === items.length - 1) return items[0];
    return null;
}

export function focusables(root: HTMLElement): HTMLElement[] {
    return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((element) => !element.closest("[hidden], [inert]"));
}

/** Wire a panel: returns the `onKeyDown` to put on it. */
export function usePanelFocus(panel: RefObject<HTMLElement | null>, onClose: () => void, options: { focusFirst?: boolean } = {}) {
    const close = useRef(onClose);
    close.current = onClose;
    useEffect(() => {
        const previous = typeof document !== "undefined" ? (document.activeElement as HTMLElement | null) : null;
        const root = panel.current;
        if (root) {
            const first = options.focusFirst === false ? null : focusables(root)[0];
            (first ?? root).focus({ preventScroll: true });
        }
        return () => {
            if (previous?.isConnected) previous.focus({ preventScroll: true });
        };
        /* Once, on open: focus is taken and given back by the panel's life. */
    }, []);
    return (event: ReactKeyboardEvent<HTMLElement>) => {
        if (event.key === "Escape") {
            if (event.defaultPrevented) return;
            event.preventDefault();
            event.stopPropagation();
            close.current();
            return;
        }
        if (event.key !== "Tab" || !panel.current) return;
        const items = focusables(panel.current);
        const target = nextFocus(items, document.activeElement as HTMLElement | null, event.shiftKey);
        if (target) {
            event.preventDefault();
            target.focus();
        }
    };
}
