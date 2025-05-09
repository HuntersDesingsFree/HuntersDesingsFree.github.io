"""
Discord Bot Main Module – Verbessert und optimiert für konsistente Command-Synchronisation
Author: HuntersDesingsPaid
Version: 2.1.2
Last Updated: 2025-05-09 23:XX:XX Local Time
"""

import discord
import os
import json
import logging
import asyncio
import sys
import traceback
import signal
import zipfile  # Für das Backup
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple
from discord import app_commands
from discord.ext import commands, tasks
from io import BytesIO

# Hilfsfunktion zur Rückgabe der aktuellen Zeit in deiner lokalen Zeitzone (UTC+2)
def get_current_time() -> datetime:
    return datetime.now(timezone(timedelta(hours=+2)))

# Neues UI-Modal für Backup-Einstellungen
class BackupSettingsModal(discord.ui.Modal, title="⚙️ Backup Einstellungen"):
    channel_id = discord.ui.TextInput(
        label="Channel ID für Auto-Backups",
        placeholder="Gib die Ziel-Channel-ID ein...",
        required=True
    )
    days = discord.ui.TextInput(
        label="Tage Intervall",
        placeholder="z.B. 1 für alle 1 Tag(e)",
        required=True
    )
    hours = discord.ui.TextInput(
        label="Stunden Intervall",
        placeholder="z.B. 12 für alle 12 Stunde(n)",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value.strip())
            days_val = int(self.days.value.strip())
            hours_val = int(self.hours.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Ungültige Eingabe! Bitte nur Zahlen verwenden.", ephemeral=True)
            return

        bot: DiscordBot = interaction.client  # type: ignore
        bot.auto_backup_channel_id = channel_id
        bot.auto_backup_interval = timedelta(days=days_val, hours=hours_val)
        bot.next_backup_time = (bot.last_backup_time or get_current_time()) + bot.auto_backup_interval

        # Speichere die Einstellungen in der config.json
        bot.config.set("auto_backup_channel_id", channel_id)
        bot.config.set("auto_backup_interval", f"{days_val}:{hours_val}")

        embed = discord.Embed(
            title="⚙️ Backup Einstellungen gespeichert",
            description=(f"Auto-Backup wird gesendet in Channel ID: **{channel_id}**\n"
                         f"Intervall: **{days_val} Tag(e) und {hours_val} Stunde(n)**\n"
                         f"Nächstes Backup: **{bot.next_backup_time.strftime('%d-%m-%Y %H:%M')}**"),
            color=discord.Color.red()
        )
        embed.set_footer(text="🕒 Backup Einstellungen aktualisiert")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class BackupView(discord.ui.View):
    def __init__(self, bot: "DiscordBot"):
        super().__init__(timeout=180)  # 3 Minuten Timeout
        self.bot = bot

    @discord.ui.button(label="⚙️ Settings", style=discord.ButtonStyle.primary, emoji="🔧")
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = BackupSettingsModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📥 Backup", style=discord.ButtonStyle.success, emoji="🚀")
    async def backup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the interaction immediately so that later followup messages can be sent.
        await interaction.response.defer(ephemeral=True)
        if not self.bot.auto_backup_channel_id:
            await interaction.followup.send("❌ Auto-Backup Channel nicht gesetzt. Bitte zuerst im Settings-Button konfigurieren.", ephemeral=True)
            return

        backup_embed, backup_file, filename = await self.bot.perform_backup()
        if backup_file is None:
            await interaction.followup.send(embed=backup_embed, ephemeral=True)
            return

        channel = self.bot.get_channel(self.bot.auto_backup_channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            await channel.send(embed=backup_embed, file=discord.File(backup_file, filename=filename))
            # Delay, damit der Bot genügend Zeit hat, das Backup in den Channel zu senden
            await asyncio.sleep(5)
            try:
                os.remove(backup_file)
            except Exception:
                pass
            await interaction.followup.send("✅ Backup gesendet.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Ziel-Channel für Auto-Backups konnte nicht gefunden werden.", ephemeral=True)

    @discord.ui.button(label="ℹ️ Info", style=discord.ButtonStyle.secondary, emoji="📋")
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="ℹ️ Backup Informationen",
            color=discord.Color.red()
        )
        if self.bot.last_backup_time:
            embed.add_field(name="⏱ Letztes Backup", value=self.bot.last_backup_time.strftime("%d-%m-%Y %H:%M"), inline=False)
        else:
            embed.add_field(name="⏱ Letztes Backup", value="Noch kein Backup durchgeführt", inline=False)

        if self.bot.next_backup_time:
            embed.add_field(name="🕒 Nächstes Auto-Backup", value=self.bot.next_backup_time.strftime("%d-%m-%Y %H:%M"), inline=False)
        else:
            embed.add_field(name="🕒 Nächstes Auto-Backup", value="Nicht konfiguriert", inline=False)

        # Emoji-Mapping für Dateitypen
        emoji_mapping = {
            '.py': "🐍",
            '.json': "📄",
            '.txt': "📝",
            '.db': "💽",
            '.sqlite': "💽",
            '.log': "📒",
            '.md': "📘"
        }

        file_list = []
        for foldername, subfolders, filenames in os.walk("."):
            for fn in filenames:
                if fn.endswith(".zip"):
                    continue
                file_path = os.path.join(foldername, fn)
                relative_path = os.path.relpath(file_path, ".")
                ext = os.path.splitext(fn)[1].lower()
                emoji = emoji_mapping.get(ext, "📄")
                file_list.append(f"{emoji} {relative_path}")
        file_list = file_list[:20]
        if file_list:
            embed.add_field(name="📁 Dateien im Hauptverzeichnis", value="\n".join(file_list), inline=False)
        else:
            embed.add_field(name="📁 Dateien im Hauptverzeichnis", value="Keine Dateien gefunden.", inline=False)

        embed.set_footer(text="🔴 Alle Angaben in Rot – Backup Command")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class BotConfig:
    """Hilfsklasse zum Laden und Verwalten der Bot-Konfiguration."""
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self._load_config()
        self.last_modified = self._get_file_modified_time()
        self.user = "HuntersDesingsPaid"

    def _get_file_modified_time(self) -> float:
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0

    def _load_config(self) -> Dict[str, Any]:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logging.error(f"❌ Fehler: Konfigurationsdatei '{self.config_path}' nicht gefunden.")
            self._create_example_config()
            logging.info(f"📝 Eine Beispielkonfiguration wurde unter '{self.config_path}' erstellt.")
            exit(1)
        except json.JSONDecodeError as e:
            logging.error(f"❌ Fehler: Ungültiges JSON-Format in '{self.config_path}': {str(e)}")
            exit(1)

    def _create_example_config(self) -> None:
        example_config = {
            "discord_token": "DEIN_DISCORD_TOKEN_HIER",
            "command_prefix": "!",
            "module_path": "modules",
            "enabled_modules": ["matchday_module"],
            "log_level": "INFO",
            "admin_users": [],
            "admin_roles": [],
            "activity_type": "playing",
            "activity_name": "mit Slash-Commands",
            "user": self.user,
            "timezone": "UTC+2",
            # Neue Backup-Einstellungen
            "auto_backup_channel_id": "",
            "auto_backup_interval": "",
            "matchday": {
                "image_size": {"width": 1024, "height": 1024},
                "background_color": "#292929",
                "text_color": "#ffffff"
            },
            "dev_guild_id": "",
            "last_updated": get_current_time().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(example_config, f, indent=4)
        except Exception as e:
            logging.error(f"❌ Fehler beim Erstellen der Beispielkonfiguration: {str(e)}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value
        self.config["last_updated"] = get_current_time().strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def save(self) -> bool:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
            self.last_modified = self._get_file_modified_time()
            logging.info("💾 Konfiguration wurde gespeichert.")
            return True
        except Exception as e:
            logging.error(f"❌ Fehler beim Speichern der Konfiguration: {str(e)}")
            return False

    def reload(self) -> bool:
        current_modified = self._get_file_modified_time()
        if current_modified > self.last_modified:
            try:
                self.config = self._load_config()
                self.last_modified = current_modified
                logging.info("🔄 Konfiguration wurde neu geladen.")
                return True
            except Exception as e:
                logging.error(f"❌ Fehler beim Neuladen der Konfiguration: {str(e)}")
                return False
        return False

    def check_duplicate_modules(self) -> None:
        if "enabled_modules" not in self.config:
            self.config["enabled_modules"] = ["matchday_module"]
            self.save()
            return
        seen = set()
        unique_modules = []
        for module in self.config["enabled_modules"]:
            if module not in seen:
                seen.add(module)
                unique_modules.append(module)
        self.config["enabled_modules"] = unique_modules
        self.save()

    def validate_matchday_config(self) -> bool:
        try:
            if "enabled_modules" not in self.config:
                self.config["enabled_modules"] = []
            if "matchday_module" not in self.config["enabled_modules"]:
                self.config["enabled_modules"].append("matchday_module")
                logging.info("✅ Matchday-Modul wurde automatisch aktiviert.")
            if "matchday" not in self.config:
                self.config["matchday"] = {
                    "image_size": {"width": 1024, "height": 1024},
                    "background_color": "#292929",
                    "text_color": "#ffffff",
                    "last_updated": get_current_time().strftime("%Y-%m-%d %H:%M:%S")
                }
            self.save()
            return True
        except Exception as e:
            logging.error(f"❌ Fehler bei der Matchday-Konfiguration: {str(e)}")
            return False

class DiscordBot(commands.Bot):
    """Hauptklasse für den Discord Bot mit optimierter Command-Synchronisation."""
    def __init__(self, config: BotConfig):
        self.config = config
        self.start_time = get_current_time()
        self.shutdown_flag = False

        # Automatisches Backup - Attribute initialisieren mit Werten aus der Config (falls vorhanden)
        auto_backup_channel_id = config.get("auto_backup_channel_id")
        auto_backup_interval_str = config.get("auto_backup_interval")
        self.auto_backup_channel_id = int(auto_backup_channel_id) if auto_backup_channel_id and str(auto_backup_channel_id).isdigit() else None
        if auto_backup_interval_str and ":" in auto_backup_interval_str:
            days_val, hours_val = auto_backup_interval_str.split(":")
            self.auto_backup_interval = timedelta(days=int(days_val), hours=int(hours_val))
        else:
            self.auto_backup_interval = None

        self.last_backup_time: Optional[datetime] = None
        self.next_backup_time: Optional[datetime] = None

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.reactions = True

        command_prefix = config.get("command_prefix", "!")
        super().__init__(command_prefix=command_prefix, intents=intents, help_command=None)

        self._register_base_commands()
        self._register_base_events()
        self.check_config_updates.start()
        self.auto_backup_loop.start()

    async def load_extensions(self):
        module_path = self.config.get("module_path", "modules")
        if not os.path.exists(module_path):
            os.makedirs(module_path)
            logging.warning(f"🔧 Modulordner '{module_path}' wurde erstellt.")
            return

        for filename in os.listdir(module_path):
            if filename.endswith(".py"):
                module_name = filename[:-3]
                ext_path = f"{module_path.replace(os.sep, '.')}.{module_name}"
                try:
                    await self.load_extension(ext_path)
                    logging.info(f"✅ Cog '{module_name}' wurde erfolgreich geladen.")
                except Exception as e:
                    logging.error(f"❌ Fehler beim Laden von Cog '{module_name}': {str(e)}")
                    traceback.print_exc()

    async def setup_hook(self):
        required_paths = [
            "modules",
            os.path.join("modules", "matchday"),
            os.path.join("modules", "matchday", "zwischenspeicher"),
            "logs"
        ]
        for path in required_paths:
            os.makedirs(path, exist_ok=True)
        await self.load_extensions()
        dev_guild_id = self.config.get("dev_guild_id")
        try:
            if dev_guild_id:
                guild = discord.Object(id=int(dev_guild_id))
                await self.tree.sync(guild=guild)
                logging.info(f"🚀 Befehle für Entwicklungs-Gilde (ID: {dev_guild_id}) synchronisiert.")
            else:
                await self.tree.sync()
                logging.info("🚀 Globale Befehle synchronisiert.")
        except discord.HTTPException as e:
            logging.error(f"❌ Fehler beim Synchronisieren der Slash-Commands: {str(e)}")

    async def on_ready(self):
        logging.info(f"🚀 Bot ist bereit! Eingeloggt als {self.user} (ID: {self.user.id})")
        logging.info(f"🌐 Verbunden mit {len(self.guilds)} Server(n).")
        uptime = get_current_time() - self.start_time
        logging.info(f"🕒 Uptime: {str(uptime).split('.')[0]}")

    def _register_base_commands(self) -> None:
        admin_group = app_commands.Group(name="admin", description="Admin-Befehle für die Bot-Verwaltung")

        @admin_group.command(name="status", description="Zeigt Statusinformationen des Bots an")
        @app_commands.checks.has_permissions(administrator=True)
        async def bot_status(interaction: discord.Interaction):
            uptime = get_current_time() - self.start_time
            days, remainder = divmod(uptime.total_seconds(), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s"
            embed = discord.Embed(
                title="🤖 Bot Status",
                color=discord.Color.blue(),
                timestamp=get_current_time()
            )
            embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=False)
            embed.add_field(name="📚 Geladene Cogs", value=str(len(self.extensions)), inline=True)
            embed.add_field(name="🌐 Verbundene Server", value=str(len(self.guilds)), inline=True)
            embed.add_field(name="🐍 Python-Version", value=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @admin_group.command(name="shutdown", description="Fährt den Bot sicher herunter")
        @app_commands.checks.has_permissions(administrator=True)
        async def shutdown_bot(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🛑 Bot Shutdown",
                description="Der Bot fährt nun sicher herunter...",
                color=discord.Color.red(),
                timestamp=get_current_time()
            )
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            logging.info(f"🛑 Shutdown initiiert von {interaction.user}.")
            self.shutdown_flag = True
            await self.close()

        @admin_group.command(name="restart", description="Startet den Bot neu")
        @app_commands.checks.has_permissions(administrator=True)
        async def restart_bot(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🔄 Bot Restart",
                description="Der Bot wird neu gestartet...",
                color=discord.Color.orange(),
                timestamp=get_current_time()
            )
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            logging.info(f"🔄 Restart initiiert von {interaction.user}.")
            await self.close()
            os.execv(sys.executable, [sys.executable] + sys.argv)

        @admin_group.command(name="reload", description="Lädt alle Cogs neu")
        @app_commands.checks.has_permissions(administrator=True)
        async def reload_commands(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            reloaded = []
            failed = []
            for ext in list(self.extensions.keys()):
                try:
                    await self.reload_extension(ext)
                    reloaded.append(ext)
                except Exception as e:
                    failed.append(f"{ext}: {str(e)}")
            try:
                await self.tree.sync()
            except Exception as e:
                logging.error(f"Sync-Fehler: {str(e)}")
            embed = discord.Embed(
                title="🔄 Reload Commands",
                description="Alle Cogs wurden neu geladen." if not failed else "Einige Cogs konnten nicht neu geladen werden.",
                color=discord.Color.green() if not failed else discord.Color.orange(),
                timestamp=get_current_time()
            )
            if reloaded:
                embed.add_field(name="Geladen", value=", ".join(reloaded), inline=False)
            if failed:
                embed.add_field(name="Fehler", value="\n".join(failed), inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

        @admin_group.command(name="purge", description="Löscht Nachrichten im aktuellen Channel basierend auf Optionen.")
        @app_commands.checks.has_permissions(administrator=True)
        async def purge(interaction: discord.Interaction, amount: Optional[int] = 100, criteria: Optional[str] = None):
            await interaction.response.defer(ephemeral=True)
            channel = interaction.channel
            if not channel or not isinstance(channel, discord.TextChannel):
                await interaction.followup.send("❌ Dieser Befehl kann nur in Textkanälen verwendet werden.", ephemeral=True)
                return
            if criteria is None:
                deleted = await channel.purge(limit=amount)
            elif criteria.lower() == "bot":
                deleted = await channel.purge(limit=amount, check=lambda m: m.author.bot)
            else:
                word = criteria
                deleted = await channel.purge(limit=amount, check=lambda m: word.lower() in m.content.lower())
            await interaction.followup.send(f"✅ {len(deleted)} Nachrichten gelöscht.", ephemeral=True)

        @admin_group.command(name="backup", description="Interaktives Backup-Menü")
        @app_commands.checks.has_permissions(administrator=True)
        async def backup_menu(interaction: discord.Interaction):
            view = BackupView(self)
            embed = discord.Embed(
                title="📦 Backup Menü",
                description="Wähle eine Option:",
                color=discord.Color.red()
            )
            embed.add_field(name="⚙️ Settings", value="Konfiguriere den Auto-Backup Channel und das Backup Intervall.", inline=False)
            embed.add_field(name="📥 Backup", value="Erstelle ein sofortiges Backup und sende es an den konfigurierten Channel.", inline=False)
            embed.add_field(name="ℹ️ Info", value="Zeige Backup-Informationen und eine optimierte Dateiliste an.", inline=False)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        self.tree.add_command(admin_group)

    def _register_base_events(self) -> None:
        @self.event
        async def on_guild_join(guild: discord.Guild):
            logging.info(f"✅ Bot hinzugefügt zu: {guild.name} (ID: {guild.id})")

        @self.event
        async def on_guild_remove(guild: discord.Guild):
            logging.info(f"❌ Bot entfernt von: {guild.name} (ID: {guild.id})")

        @self.event
        async def on_error(event, *args, **kwargs):
            error_trace = traceback.format_exc()
            logging.error(f"⚠️ Fehler beim Event '{event}': {error_trace}")

    @tasks.loop(minutes=5.0)
    async def check_config_updates(self):
        if self.config.reload():
            logging.info("🔄 Konfiguration wurde aktualisiert. Präsenz wird angepasst...")
            activity_type = self.config.get("activity_type", "playing")
            activity_name = self.config.get("activity_name", "mit Slash-Commands")
            activity: Optional[discord.Activity] = None
            if activity_type.lower() == "playing":
                activity = discord.Game(name=activity_name)
            elif activity_type.lower() == "listening":
                activity = discord.Activity(type=discord.ActivityType.listening, name=activity_name)
            elif activity_type.lower() == "watching":
                activity = discord.Activity(type=discord.ActivityType.watching, name=activity_name)
            if activity:
                await self.change_presence(activity=activity)

    @check_config_updates.before_loop
    async def before_check_config(self):
        await self.wait_until_ready()

    @tasks.loop(seconds=30)
    async def auto_backup_loop(self):
        if self.auto_backup_channel_id and self.auto_backup_interval:
            now = get_current_time()
            if not self.next_backup_time:
                self.next_backup_time = now + self.auto_backup_interval
            if now >= self.next_backup_time:
                backup_embed, backup_file, filename = await self.perform_backup()
                if backup_file is not None:
                    channel = self.get_channel(self.auto_backup_channel_id)
                    if channel and isinstance(channel, discord.TextChannel):
                        await channel.send(embed=backup_embed, file=discord.File(backup_file, filename=filename))
                        await asyncio.sleep(5)
                        try:
                            os.remove(backup_file)
                        except Exception:
                            pass
                self.last_backup_time = now
                self.next_backup_time = now + self.auto_backup_interval

    async def perform_backup(self) -> Tuple[discord.Embed, Optional[str], Optional[str]]:
        now = get_current_time()
        filename = f"Backup_{now.strftime('%d-%m-%Y')}_{now.strftime('%H%M')}.zip"
        backup_file = filename
        try:
            with zipfile.ZipFile(backup_file, "w", zipfile.ZIP_DEFLATED) as zf:
                for foldername, subfolders, filenames in os.walk("."):
                    for fn in filenames:
                        if fn == backup_file:
                            continue
                        file_path = os.path.join(foldername, fn)
                        relative_path = os.path.relpath(file_path, ".")
                        zf.write(file_path, arcname=relative_path)
            embed = discord.Embed(
                title="🚀 Backup erstellt",
                description=f"Backup **{filename}** wurde erfolgreich erstellt und gesendet.",
                color=discord.Color.red(),
                timestamp=get_current_time()
            )
            embed.set_footer(text="📦 Backup Command")
            self.last_backup_time = now
            return embed, backup_file, filename
        except Exception as e:
            embed = discord.Embed(
                title="❌ Backup Fehler",
                description=f"Fehler beim Erstellen des Backups: {str(e)}",
                color=discord.Color.red(),
                timestamp=get_current_time()
            )
            return embed, None, None

    async def close(self):
        logging.info("🛑 Bot Shutdown wird eingeleitet...")
        self.check_config_updates.cancel()
        self.auto_backup_loop.cancel()
        await super().close()

def setup_logging():
    if not os.path.exists("logs"):
        os.makedirs("logs")
    timestamp = get_current_time().strftime("%Y-%m-%d")
    log_filename = f"logs/bot_{timestamp}.log"
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(log_filename, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.info("=" * 60)
    logging.info(f"🚀 Bot-Logging gestartet am {get_current_time().strftime('%d.%m.%Y um %H:%M:%S')}")
    logging.info("=" * 60)

def handle_sigterm(signum, frame):
    logging.info("🛑 SIGTERM empfangen – Shutdown wird eingeleitet...")
    asyncio.create_task(shutdown())

async def shutdown():
    if "bot" in globals():
        logging.info("🔽 Führe sauberen Shutdown durch...")
        await bot.close()
    logging.info("👋 Bot-Prozess beendet.")
    sys.exit(0)

async def main():
    global bot
    setup_logging()
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown()))
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigterm)

    print("\n" + "=" * 60)
    print("🤖 ZEALOX BOT STARTER".center(60))
    print("=" * 60)

    retry_count = 0
    max_retries = 5
    retry_delay = 5
    while retry_count < max_retries:
        try:
            config = BotConfig()
            config.check_duplicate_modules()
            config.validate_matchday_config()
            token = config.get("discord_token")
            if not token or token == "DEIN_DISCORD_TOKEN_HIER":
                logging.error("❌ Fehler: Discord-Token nicht gesetzt.")
                print("\n❌ Fehler: Discord-Token fehlt. Bitte in config.json eintragen.")
                return
            enabled_modules = config.get("enabled_modules", [])
            print(f"📋 Konfiguration geladen:")
            print(f"   • Prefix: {config.get('command_prefix', '!')}")
            print(f"   • Aktivierte Module: {len(enabled_modules)}")
            if enabled_modules:
                print(f"     {', '.join(enabled_modules)}")
            print("\n🔄 Initialisiere Bot...")
            bot = DiscordBot(config)
            print("🔌 Verbinde mit Discord...")
            async with bot:
                await bot.start(token)
            if bot.shutdown_flag:
                logging.info("✅ Bot wurde ordnungsgemäß heruntergefahren.")
                print("\n✅ Bot wurde ordnungsgemäß beendet.")
                break
            else:
                logging.warning("⚠️ Bot-Verbindung unterbrochen. Es wird erneut versucht...")
                print("\n⚠️ Unerwartete Trennung – Neustart...")
                retry_count += 1
                await asyncio.sleep(retry_delay)
        except discord.LoginFailure:
            logging.error("❌ Fehler: Ungültiges Discord-Token.")
            print("\n❌ Fehler: Ungültiges Discord-Token.")
            break
        except Exception as e:
            error_trace = traceback.format_exc()
            logging.error(f"❌ Kritischer Fehler: {str(e)}")
            logging.debug(f"🔍 Detail: {error_trace}")
            print(f"\n❌ Kritischer Fehler: {str(e)}")
            retry_count += 1
            if retry_count < max_retries:
                logging.info(f"🔄 Neustart in {retry_delay}s... (Versuch {retry_count}/{max_retries})")
                print(f"🔄 Neustart in {retry_delay}s... (Versuch {retry_count}/{max_retries})")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                logging.error("❌ Maximale Wiederholungen erreicht.")
                print("\n❌ Maximale Wiederholungen erreicht.")
                break
    print("\n" + "=" * 60)
    print("👋 ZEALOX BOT BEENDET".center(60))
    print("=" * 60 + "\n")

if __name__ == "__main__":
    asyncio.run(main())