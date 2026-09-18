def permitted(settings, guild_id, role_ids, manager=False):
    if guild_id is None or str(guild_id) != settings.get("DISCORD_GUILD_ID"):
        return False
    roles = {str(role) for role in role_ids}
    if settings.get("DISCORD_MANAGER_ROLE_ID") in roles:
        return True
    return not manager and settings.get("DISCORD_BOT_ACCESS_ROLE_ID") in roles
