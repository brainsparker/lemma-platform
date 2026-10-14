"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Screen } from "@/auth/screens";
import { source } from "@/data";
import type { SlackManagedWorkspace } from "@/data";
import { SessionGate } from "@/session/session";

/** Where "Connect your Lemma account" in Slack lands.
 *
 *  Lemma's app in Slack manages teammates, and managing needs to know who is
 *  asking for certain -- a Slack profile email is editable by a workspace admin,
 *  so it never stands in for an account. The link carries a signature only that
 *  person's own App Home could have shown them; signing in here and opening it
 *  is the proof, once. */
function readLink() {
    const query = new URLSearchParams(window.location.search);
    const expires = Number(query.get("exp"));
    const teamId = query.get("team") ?? "";
    const slackUserId = query.get("user") ?? "";
    const signature = query.get("sig") ?? "";
    if (!teamId || !slackUserId || !signature || !Number.isFinite(expires)) return null;
    return { teamId, slackUserId, expires, signature };
}

function backToSlack(teamId: string) {
    return `https://app.slack.com/client/${encodeURIComponent(teamId)}`;
}

function Linking() {
    const [link] = useState(readLink);
    const connect = useMutation({
        mutationFn: () => {
            if (!link) throw new Error("This link is missing a part. Open Lemma's Home tab in Slack and connect again.");
            return source.linkSlackPerson(link);
        },
    });
    useEffect(() => {
        if (connect.isIdle) connect.mutate();
    }, [connect]);

    if (connect.isError) {
        return <Screen title="That didn't connect" lead={connect.error.message} looking={false} />;
    }
    if (!connect.isSuccess) {
        return <Screen title="Connecting your Slack account…" looking={false} />;
    }
    const workspace: SlackManagedWorkspace = connect.data;
    return (
        <Screen
            title="You're connected"
            lead={`Lemma knows it's you in ${workspace.teamName ?? "Slack"} now. Your teammates are on Lemma's Home tab there.`}
            looking={false}
        >
            <a className="btn btn--primary" href={backToSlack(workspace.teamId)}>Back to Slack</a>
        </Screen>
    );
}

export function SlackConnectScreen() {
    return (
        <SessionGate>
            <Linking />
        </SessionGate>
    );
}
