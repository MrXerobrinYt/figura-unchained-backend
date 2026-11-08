"""Discord-бот фамы.

Команда /addme добавляет участника на доску семьи:
- работает только на сервере фамы (FAMA_DISCORD_GUILD_ID);
- по желанию можно ограничить ролью (FAMA_DISCORD_ROLE_ID);
- повторный вызов с тем же ником обновляет роль и заметку.

Бот живёт внутри того же процесса, что и сайт (aiohttp), поэтому
отдельный сервис на Render не нужен. Если DISCORD_BOT_TOKEN не задан,
бот просто не запускается и сайт работает как обычно.
"""

import asyncio


def _clip(text, limit=100):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


async def start_bot(token, guild_id, roles, add_member, public_url,
                    log_info, log_warn, allowed_role_id=""):
    """Запускает бота в фоне и возвращает состояние для остановки."""
    try:
        import discord
        from discord import app_commands
    except Exception as exc:  # discord.py не установлен
        log_warn(f"[BOT] discord.py недоступен: {exc!r}")
        return None

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    choices = []
    for role in roles:
        label = _clip(role.get("label"), 100)
        full = _clip(role.get("full") or label, 100)
        choices.append(app_commands.Choice(name=_clip(label + " — " + full, 100), value=label))

    synced = {"done": False}

    @client.event
    async def on_ready():
        log_info(f"[BOT] вошёл как {client.user} · серверов: {len(client.guilds)}")
        if synced["done"]:
            return
        synced["done"] = True
        try:
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)
                log_info(f"[BOT] команды синхронизированы для сервера {guild_id}")
            else:
                await tree.sync()
                log_info("[BOT] команды синхронизированы глобально")
        except Exception as exc:
            log_warn(f"[BOT] не удалось синхронизировать команды: {exc!r}")

    def guild_allowed(interaction):
        if not guild_id:
            return True
        return str(interaction.guild_id or "") == str(guild_id)

    def role_allowed(interaction):
        if not allowed_role_id:
            return True
        user = interaction.user
        roles_now = getattr(user, "roles", None) or []
        return any(str(getattr(r, "id", "")) == str(allowed_role_id) for r in roles_now)

    @tree.command(name="addme", description="Добавить себя на доску семьи")
    @app_commands.describe(
        name="Ник, который увидит семья",
        role="Твоя роль в семье",
        note="Пара слов о себе (необязательно)",
    )
    @app_commands.choices(role=choices)
    async def addme(interaction: discord.Interaction, name: str,
                    role: app_commands.Choice[str], note: str = ""):
        if not guild_allowed(interaction):
            await interaction.response.send_message(
                "Эта команда работает только на сервере семьи.", ephemeral=True
            )
            return
        if not role_allowed(interaction):
            await interaction.response.send_message(
                "У тебя нет роли, которая может добавлять карточку. Попроси основателя.",
                ephemeral=True,
            )
            return
        try:
            ok, message, member_id = await add_member(
                name=name,
                role=role.value,
                note=note,
                discord_id=str(interaction.user.id),
            )
        except Exception as exc:
            log_warn(f"[BOT] /addme ошибка: {exc!r}")
            await interaction.response.send_message(
                "Не получилось добавить карточку. Попробуй ещё раз позже.",
                ephemeral=True,
            )
            return
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        link = public_url.rstrip("/") + "/member/" + str(member_id)
        embed = discord.Embed(
            title="Карточка на доске семьи",
            description=f"**{_clip(name, 60)}** · {role.value}\n{message}",
            color=0xD8B45C,
        )
        embed.add_field(name="Профиль", value=link, inline=False)
        embed.set_footer(text="Ссылку можно скопировать и отправить семье")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        safe_name = name.encode("ascii", "replace").decode("ascii")
        log_info(f"[BOT] /addme OK member={safe_name!r} id={member_id} discord={interaction.user.id}")

    async def runner():
        try:
            await client.start(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_warn(f"[BOT] остановлен: {exc!r}")

    task = asyncio.create_task(runner(), name="fama_discord_bot")
    return {"client": client, "task": task}


async def stop_bot(state):
    if not state:
        return
    client = state.get("client")
    task = state.get("task")
    if client is not None and not client.is_closed():
        try:
            await client.close()
        except Exception:
            pass
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
