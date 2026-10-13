import type { SettingsSection } from "@/settings/settings-modal";

/** The parts of a teammate's About page, each its own page under it:
 *  `/t/{pod}/about/{section}`. Kept apart from the page so the address
 *  grammar can read them without importing a component. */
export type AboutSection = "people" | "channels" | "works-with" | "skills" | "memory" | "schedules" | "agents" | "model" | "ai-tools" | "connectors";

export const ABOUT_SECTIONS: readonly AboutSection[] = [
    "people", "channels", "works-with", "skills", "memory", "schedules", "agents", "model", "ai-tools", "connectors",
];

/** Whether a word from an address is one of About's sections. */
export function isAboutSection(value: string | null | undefined): value is AboutSection {
    return typeof value === "string" && (ABOUT_SECTIONS as readonly string[]).includes(value);
}

/** Sections About carried for a while that belong to the organization, and
 *  where Settings keeps them. A link that still names one opens Settings
 *  there rather than landing on nothing. Connectors stayed: what a teammate
 *  can reach is the first thing people look for on its page. */
const MOVED_TO_SETTINGS: Record<string, SettingsSection> = {
    models: "models",
    usage: "usage",
    "org-usage": "org-usage",
};

export function settingsSectionOf(value: string | null | undefined): SettingsSection | null {
    return typeof value === "string" && Object.hasOwn(MOVED_TO_SETTINGS, value) ? MOVED_TO_SETTINGS[value] : null;
}
