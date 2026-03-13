from __future__ import annotations

import disnake


def has_moderator_role(member: disnake.abc.User | None, moderator_role_ids: set[int]) -> bool:
    if not isinstance(member, disnake.Member):
        return False
    member_role_ids = {role.id for role in member.roles}
    return bool(member_role_ids.intersection(moderator_role_ids))
