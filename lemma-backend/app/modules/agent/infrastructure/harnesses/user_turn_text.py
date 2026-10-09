"""What the model reads for one user message, whichever harness sends it.

Split from ``pydantic_ai_history`` because it is shared: the in-process
history builder, the mid-run steering capability and the Agent Host prompt all
render a person's message the same way, and a fix to one of them -- quoting,
label defusing, attachments -- has to reach the others.
"""

from __future__ import annotations

import json

from app.modules.agent.domain.private_notes import defuse_lemma_labels, lemma_label
from app.modules.agent.domain.surface_prompts import attachment_listing_block
from app.modules.agent.domain.value_objects import to_json_value
from app.modules.agent.infrastructure.harnesses.channel_context import (
    audience_block,
    channel_context_block,
    participants_block,
)
from app.modules.agent.infrastructure.harnesses.sender_labels import (
    quoted_message_block,
    sender_label,
)

def _message_text(msg: object) -> str:
    return getattr(msg, "text", None) or ""


def user_prompt_text(msg: object, *, with_roster: bool = True) -> str:
    """What the model actually reads for one user message.

    The stored text plus everything the surface knows about it: who sent it,
    what else was said in the thread, what they attached. Each block is
    independent and any of them can be absent, which is why they are appended
    to a list rather than formatted into a template.

    ``with_roster`` is off for a turn replayed as history: who is in a group is
    what it is now, and an old turn's roster still naming somebody since
    removed from the pod as a member would contradict the current one.
    """
    body = _message_text(msg)
    metadata = getattr(msg, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return body

    platform = metadata.get("surface_platform")
    label = lemma_label(metadata)
    pieces = [
        label,
        sender_label(metadata, platform),
        # Only a message Lemma labelled is known to be a member's; any other
        # body could be a stranger's imitating that label.
        body if label else defuse_lemma_labels(body),
        quoted_message_block(metadata),
        participants_block(metadata) if with_roster else None,
        channel_context_block(metadata),
        audience_block(metadata),
        *_shared_files_blocks(metadata, platform),
        # Its own piece rather than part of the block above, because the two are
        # not alternatives: a message can carry three photos of which one
        # arrived, and the saved-paths block returns early once it has any.
        _failed_files_block(metadata),
    ]
    if "state" in metadata:
        pieces.append(_metadata_state_text(metadata["state"]))
    return "\n\n".join(piece for piece in pieces if piece)


def _transcribed_voice_paths(metadata: dict) -> set[str]:
    """Paths of inbound voice notes whose words are already in the message.

    Ingress transcribes voice notes before the run starts and folds the
    transcript in as the user's own words. Listing those files again as
    something to go and read invites the agent to call `listen` on audio that
    has already been transcribed — the same transcript, billed and waited for
    twice. A voice note that failed to transcribe is deliberately absent here,
    because for that one `listen` is the right call.
    """
    provenance = metadata.get("voice_transcripts")
    if not isinstance(provenance, list):
        return set()
    return {
        str(item.get("path"))
        for item in provenance
        if isinstance(item, dict) and item.get("path") and not item.get("failed")
    }


def _failed_files_block(metadata: dict) -> str | None:
    """What the person attached that never reached us, and why.

    Without this the run cannot tell an unattached message from one whose photo
    failed to download, so it answers the text alone and reads as though it
    ignored the file. Stated as fact, not as an instruction: whether to ask for
    a resend depends on whether the file mattered, which the agent can see and
    this layer cannot.
    """
    failed = metadata.get("failed_files")
    if not isinstance(failed, list) or not failed:
        return None
    lines = []
    for item in failed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "a file").strip()
        reason = str(item.get("reason") or "it could not be received").strip()
        lines.append(f"- {name} — {reason}")
    if not lines:
        return None
    return (
        "The person attached the following, and it did NOT reach you. You "
        "cannot open or read it:\n" + "\n".join(lines)
    )


def _shared_files_blocks(metadata: dict, platform: object) -> list[str]:
    """What the user attached, either already ingested or still raw.

    Ingested files win: once they are in the datastore the agent should reach
    them by path rather than re-reading whatever the surface sent. The
    `display_resource` mechanics are stated once as standing platform guidance,
    so only the paths are listed here.
    """
    ingested_files = metadata.get("ingested_files")
    if isinstance(ingested_files, list) and ingested_files:
        transcribed = _transcribed_voice_paths(metadata)
        saved = "\n".join(
            f"- {path}" for path in ingested_files if path and path not in transcribed
        )
        blocks: list[str] = []
        if saved:
            blocks.append(
                "The user shared files; they are saved in the pod datastore "
                f"at:\n{saved}"
            )
        if transcribed:
            spoken = "\n".join(f"- {path}" for path in sorted(transcribed))
            blocks.append(
                "The message above IS the transcript of the voice note they "
                "sent — their spoken words, already transcribed on arrival. "
                "Treat it as what they said and answer it directly. Calling "
                "`listen` on the file returns this same text and tells you "
                "nothing new. The audio itself is saved at:\n" + spoken
            )
        if blocks:
            return blocks
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return []
    block = attachment_listing_block(attachments, platform=str(platform or ""))
    return [block] if block else [f"Attachments: {len(attachments)}"]


def _metadata_state_text(state: object) -> str:
    try:
        state_json = json.dumps(to_json_value(state), indent=2, sort_keys=True)
    except Exception:
        state_json = json.dumps(str(state))
    return "UI state:\n```json\n" + state_json + "\n```"
