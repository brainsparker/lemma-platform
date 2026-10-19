"""``domain.access.may_see`` as a SQL predicate, for every read of many channels.

Lists, counts, the inbox, mentions and search all filter channels with this,
so a channel the member may not open is never counted, listed or found.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, exists, false, or_, select
from sqlalchemy.orm import aliased

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import ChannelKind, Visibility
from app.modules.channels.infrastructure.models import ChannelMemberModel, ChannelModel


def is_member_of(viewer: Viewer) -> ColumnElement[bool]:
    # An alias, correlated to the channel only: a caller whose own query joins
    # ``channel_members`` (the inbox's totals) would otherwise have it
    # correlated away too, leaving the subquery with nothing to select from.
    member = aliased(ChannelMemberModel)
    return exists(
        select(member.channel_id)
        .where(member.channel_id == ChannelModel.id, member.user_id == viewer.user_id)
        .correlate(ChannelModel)
    )


def visible_to(viewer: Viewer) -> ColumnElement[bool]:
    """The channels of ``viewer``'s pod they may see."""
    channel = ChannelModel
    return and_(
        channel.pod_id == viewer.pod_id,
        or_(
            and_(
                channel.kind == ChannelKind.DIRECT.value,
                channel.member_user_id == viewer.user_id,
            ),
            and_(channel.kind == ChannelKind.CONTACT.value)
            if viewer.sees_contacts
            else false(),
            and_(
                channel.kind == ChannelKind.GROUP.value,
                or_(
                    channel.visibility == Visibility.PUBLIC.value,
                    is_member_of(viewer),
                ),
            ),
        ),
    )
