"use client";

import { useEffect, useState } from "react";

/** Where Slack's consent for Lemma's own app lands, after "Add to Slack".
 *
 *  Nothing is left to do here: the workspace is adopted when the grant comes
 *  back, and Lemma says hello in Slack. This page only says which way it went
 *  -- the callback names the outcome in the query string. */
export default function SlackInstalledPage() {
    const [refused, setRefused] = useState<string | null>(null);
    useEffect(() => {
        const query = new URLSearchParams(window.location.search);
        if (query.get("connect") === "error") setRefused(query.get("reason") || "Slack didn't add Lemma.");
    }, []);
    return (
        <main className="portal">
            <div className="portal__pane">
                <div className="portal__form">
                    <h2>{refused ? "Lemma isn't in Slack yet" : "Lemma is in your Slack workspace"}</h2>
                    <p className="portal__lead">
                        {refused
                            ? `${refused} Try "Add to Slack" again.`
                            : "Open Slack: Lemma has sent you a message with your first teammate to add."}
                    </p>
                    {!refused && <a className="btn btn--primary" href="https://app.slack.com/client">Open Slack</a>}
                </div>
            </div>
        </main>
    );
}
