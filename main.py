"""
Discord Bot Main Module – Verbessert und optimiert für konsistente Command-Synchronisation
Author: HuntersDesingsPaid
Version: 2.1.4
Last Updated: 2025-05-14 02:XX:XX Local Time
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
from typing import Dict, Any, Optional, Tuple, List

from discord import app_commands
from discord.ext import commands, tasks
# from io import BytesIO  # BytesIO wurde nicht direkt verwendet, kann entfernt werden, falls nicht doch benötigt

# Globale Konstante für API Delays
DEFAULT_API_DELAY = 0.6  # Sekunden für Delays nach Discord API Aktionen

# Hilfsfunktion zur Rückgabe der aktuellen Zeit in deiner lokalen Zeitzone (UTC+2)
def get_current_time() -> datetime:
    return datetime.now(timezone(timedelta(hours=+2)))

# --- UI-Elemente (Modals, Views) ---
class BackupSettingsModal(discord.ui.Modal, title="⚙️ Backup Einstellungen"):
    channel_id_input = discord.ui.TextInput(  # Umbenannt, um Konflikt mit Attribut zu vermeiden
        label="Channel ID für Auto-Backups",
        placeholder="Gib die Ziel-Channel-ID ein...",
        required=True
    )
    days_input = discord.ui.TextInput(  # Umbenannt
        label="Tage Intervall",
        placeholder="z.B. 1 für alle 1 Tag(e)",
        required=True
    )
    hours_input = discord.ui.TextInput(  # Umbenannt
        label="Stunden Intervall",
        placeholder="z.B. 12 für alle 12 Stunde(n)",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id_str = self.channel_id_input.value.strip()
            if not channel_id_str.isdigit():
                await interaction.response.send_message("❌ Ungültige Channel ID! Bitte nur Zahlen verwenden.", ephemeral=True)
                await asyncio.sleep(DEFAULT_API_DELAY)
                return
            channel_id = int(channel_id_str)

            days_str = self.days_input.value.strip()
            if not days_str.isdigit():
                await interaction.response.send_message("❌ Ungültige Eingabe für Tage! Bitte nur Zahlen verwenden.", ephemeral=True)
                await asyncio.sleep(DEFAULT_API_DELAY)
                return
            days_val = int(days_str)

            hours_str = self.hours_input.value.strip()
            if not hours_str.isdigit():
                await interaction.response.send_message("❌ Ungültige Eingabe für Stunden! Bitte nur Zahlen verwenden.", ephemeral=True)
                await asyncio.sleep(DEFAULT_API_DELAY)
                return
            hours_val = int(hours_str)

            if days_val < 0 or hours_val < 0 or (days_val == 0 and hours_val == 0):
                await interaction.response.send_message("❌ Intervall ungültig! Tage und Stunden dürfen nicht negativ sein und nicht beide null.", ephemeral=True)
                await asyncio.sleep(DEFAULT_API_DELAY)
                return

        except ValueError:
            await interaction.response.send_message("❌ Interne Verarbeitungsfehler der Eingabe.", ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
            return

        bot: "DiscordBot" = interaction.client  # type: ignore
        bot.auto_backup_channel_id = channel_id
        bot.auto_backup_interval = timedelta(days=days_val, hours=hours_val)
        
        current_time = get_current_time()
        bot.last_backup_time = bot.last_backup_time or current_time  # Setze last_backup_time, falls noch nicht vorhanden
        bot.next_backup_time = bot.last_backup_time + bot.auto_backup_interval

        bot.config.set("auto_backup_channel_id", channel_id)
        bot.config.set("auto_backup_interval", f"{days_val}:{hours_val}")

        embed = discord.Embed(
            title="⚙️ Backup Einstellungen gespeichert",
            description=(f"Auto-Backup wird gesendet in Channel ID: **{channel_id}**\n"
                         f"Intervall: **{days_val} Tag(e) und {hours_val} Stunde(n)**\n"
                         f"Nächstes Backup: **{bot.next_backup_time.strftime('%d.%m.%Y %H:%M:%S %Z')}**"),
            color=discord.Color.green()  # Grün für Erfolg
        )
        embed.set_footer(text="🕒 Backup Einstellungen aktualisiert")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await asyncio.sleep(DEFAULT_API_DELAY)

class BackupView(discord.ui.View):
    def __init__(self, bot: "DiscordBot"):
        super().__init__(timeout=180)  # 3 Minuten Timeout
        self.bot = bot

    @discord.ui.button(label="⚙️ Settings", style=discord.ButtonStyle.primary, emoji="🔧")
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = BackupSettingsModal()
        await interaction.response.send_modal(modal)
        # Kein API-Delay direkt nach send_modal, da auf User-Input gewartet wird.

    @discord.ui.button(label="📥 Backup", style=discord.ButtonStyle.success, emoji="🚀")
    async def backup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)  # thinking=True gibt direktes Feedback
        await asyncio.sleep(DEFAULT_API_DELAY)

        if not self.bot.auto_backup_channel_id:
            await interaction.followup.send("❌ Auto-Backup Channel nicht gesetzt. Bitte zuerst im Settings-Button konfigurieren.", ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
            return

        backup_embed, backup_file_path, filename = await self.bot.perform_backup()

        if backup_file_path is None or filename is None:  # Fehler beim Backup erstellen
            await interaction.followup.send(embed=backup_embed, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
            return

        channel = self.bot.get_channel(self.bot.auto_backup_channel_id)
        if not (channel and isinstance(channel, discord.TextChannel)):
            err_msg = "❌ Ziel-Channel für Auto-Backups konnte nicht gefunden werden oder ist kein Textkanal."
            await interaction.followup.send(err_msg, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
            try:
                os.remove(backup_file_path)
                logging.warning(f"Lokale Backup-Datei {backup_file_path} entfernt, da Ziel-Channel ungültig.")
            except OSError as e:
                logging.error(f"Konnte lokale Backup-Datei {backup_file_path} nicht entfernen (Ziel-Channel ungültig): {e}")
            return

        try:
            file_size_bytes = os.path.getsize(backup_file_path)
            limit_bytes = 25 * 1024 * 1024  # 25 MB API Limit für Bots

            if file_size_bytes > limit_bytes:
                error_message = (
                    f"❌ Backup **{filename}** ({file_size_bytes / (1024*1024):.2f} MB) "
                    f"ist zu groß zum Senden über Discord (Bot-Limit: {limit_bytes / (1024*1024):.0f} MB).\n"
                    "Das Backup wurde lokal erstellt, kann aber nicht hochgeladen werden."
                )
                await channel.send(embed=backup_embed)  # Info-Embed über Erstellung
                await asyncio.sleep(DEFAULT_API_DELAY)
                await channel.send(error_message)  # Fehlermeldung über Größe
                await asyncio.sleep(DEFAULT_API_DELAY)
                await interaction.followup.send(error_message, ephemeral=True)  # Info an auslösenden User
                await asyncio.sleep(DEFAULT_API_DELAY)
                logging.warning(f"Backup {filename} ({file_size_bytes / (1024*1024):.2f} MB) too large for Discord. Stored locally at {backup_file_path}")
                return

            await channel.send(embed=backup_embed, file=discord.File(backup_file_path, filename=filename))
            await asyncio.sleep(DEFAULT_API_DELAY)
            logging.info(f"Backup {filename} gesendet an Channel {channel.id}. Size: {file_size_bytes / (1024*1024):.2f} MB")
            
            await asyncio.sleep(2)  # Kurzer zusätzlicher Delay, bevor die Datei gelöscht wird (optional)
            try:
                os.remove(backup_file_path)
                logging.info(f"Lokale Backup-Datei {backup_file_path} entfernt nach erfolgreichem Senden.")
                await interaction.followup.send("✅ Backup gesendet und lokale Kopie entfernt.", ephemeral=True)
            except OSError as e:
                logging.error(f"Konnte lokale Backup-Datei {backup_file_path} nicht entfernen nach Senden: {e}")
                await interaction.followup.send("✅ Backup gesendet, aber lokale Kopie konnte nicht entfernt werden.", ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)

        except discord.Forbidden:
            err_msg = f"❌ Keine Berechtigung, in Channel {channel.name} (ID: {channel.id}) zu schreiben oder Dateien zu senden."
            logging.error(err_msg)
            await interaction.followup.send(err_msg, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
        except discord.HTTPException as e:
            logging.error(f"HTTP-Fehler beim Senden des Backups {filename} an Discord: {e}")
            await interaction.followup.send(f"❌ Fehler beim Senden des Backups an Discord: {e.status} {e.text}", ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
        except Exception as e:
            logging.error(f"Allgemeiner Fehler im Backup-Button beim Senden: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Ein unerwarteter Fehler ist aufgetreten: {type(e).__name__}", ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)

    @discord.ui.button(label="ℹ️ Info", style=discord.ButtonStyle.secondary, emoji="📋")
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="ℹ️ Backup Informationen",
            color=discord.Color.blue()  # Blauer für Info
        )
        if self.bot.last_backup_time:
            embed.add_field(name="⏱ Letztes Backup", value=self.bot.last_backup_time.strftime("%d.%m.%Y %H:%M:%S %Z"), inline=False)
        else:
            embed.add_field(name="⏱ Letztes Backup", value="Noch kein Backup durchgeführt", inline=False)

        if self.bot.next_backup_time and self.bot.auto_backup_interval:
            embed.add_field(name="🕒 Nächstes Auto-Backup", value=self.bot.next_backup_time.strftime("%d.%m.%Y %H:%M:%S %Z"), inline=False)
            interval = self.bot.auto_backup_interval
            total_hours, remainder = divmod(interval.total_seconds(), 3600)
            total_days, hours = divmod(total_hours, 24)
            minutes = remainder // 60
            embed.add_field(name="⚙️ Intervall", value=f"{int(total_days)} Tag(e), {int(hours)} Stunde(n), {int(minutes)} Minute(n)", inline=False)
        else:
            embed.add_field(name="🕒 Nächstes Auto-Backup", value="Nicht konfiguriert", inline=False)

        emoji_mapping = {
            '.py': "🐍", '.json': "📄", '.txt': "📝", '.db': "💽",
            '.sqlite': "💽", '.log': "📒", '.md': "📘", '.env': "🔒"
        }
        file_list_preview: List[str] = []
        try:
            count = 0
            excluded_dirs_preview = {".git", "__pycache__", "logs", "modules/__pycache__", ".venv", "venv", "backups_temp"}
            for foldername, subfolders, filenames in os.walk(".", topdown=True):
                subfolders[:] = [d for d in subfolders if d not in excluded_dirs_preview and not d.startswith('.')]
                if count >= 15: break
                for fn in filenames:
                    if count >= 15: break
                    if fn.endswith(".zip") or fn.startswith("."): continue
                    file_path = os.path.join(foldername, fn)
                    relative_path = os.path.relpath(file_path, ".")
                    ext = os.path.splitext(fn)[1].lower()
                    emoji = emoji_mapping.get(ext, "📄")
                    file_list_preview.append(f"{emoji} `{relative_path}`")
                    count += 1
        except Exception as e:
            logging.error(f"Fehler beim Auflisten der Dateien für Backup-Info: {e}")
            file_list_preview.append("Fehler beim Auflisten der Dateien.")

        if file_list_preview:
            embed.add_field(name="📁 Beispiel-Dateien im Backup (max. 15)", value="\n".join(file_list_preview), inline=False)
        else:
            embed.add_field(name="📁 Beispiel-Dateien im Backup", value="Keine relevanten Dateien gefunden.", inline=False)

        embed.set_footer(text=f"{self.bot.config.get('user', 'Bot')} Backup System")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await asyncio.sleep(DEFAULT_API_DELAY)

# --- Bot-Konfiguration ---
class BotConfig:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.last_modified: float = 0
        self.user: str = "Bot"  # Default, wird aus Config geladen/überschrieben
        self._initialize_config()

    def _initialize_config(self):
        try:
            self.config = self._load_config_data()
            self.last_modified = self._get_file_modified_time()
            self.user = self.config.get("user", "Bot")
        except FileNotFoundError:
            logging.error(f"❌ FATAL: Konfigurationsdatei '{self.config_path}' nicht gefunden.")
            self._create_example_config()
            logging.critical(f"📝 Eine Beispielkonfiguration wurde unter '{self.config_path}' erstellt. "
                             f"BITTE BEARBEITE DIESE DATEI (insbesondere den discord_token) UND STARTE DEN BOT NEU.")
            sys.exit(1)
        except json.JSONDecodeError as e:
            logging.error(f"❌ FATAL: Ungültiges JSON-Format in '{self.config_path}': {str(e)}")
            logging.critical(f"BITTE KORRIGIERE DIE SYNTAXFEHLER IN '{self.config_path}' UND STARTE DEN BOT NEU. "
                             "Du kannst einen Online-JSON-Validator (z.B. jsonlint.com) zur Hilfe nehmen.")
            sys.exit(1)
        except Exception as e:
            logging.critical(f"❌ FATAL: Ein unerwarteter Fehler ist beim Laden der Konfiguration '{self.config_path}' aufgetreten: {e}", exc_info=True)
            sys.exit(1)

    def _get_file_modified_time(self) -> float:
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0

    def _load_config_data(self) -> Dict[str, Any]:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _create_example_config(self) -> None:
        example_config = {
            "discord_token": "DEIN_DISCORD_TOKEN_HIER",
            "command_prefix": "!",  # Für Message Commands (falls später benötigt)
            "module_path": "modules",
            "enabled_modules": [],
            "log_level": "INFO",  # Mögliche Werte: DEBUG, INFO, WARNING, ERROR, CRITICAL
            "admin_users": [],  # Liste von User IDs
            "admin_roles": [],  # Liste von Role IDs
            "activity_type": "playing",  # playing, listening, watching, streaming
            "activity_name": "mit Slash-Commands",
            "user": "Bot",  # Name des Bots, z.B. für Backup-Dateinamen
            "timezone": "Europe/Berlin",
            "auto_backup_channel_id": None,
            "auto_backup_interval": "1:0",
            "dev_guild_id": None,
            "last_updated": get_current_time().isoformat()
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(example_config, f, indent=4)
        except Exception as e:
            logging.error(f"❌ Fehler beim Erstellen der Beispielkonfiguration: {e}", exc_info=True)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value
        self.config["last_updated"] = get_current_time().isoformat()
        self.save()

    def save(self) -> bool:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
            self.last_modified = self._get_file_modified_time()
            logging.info("💾 Konfiguration wurde erfolgreich gespeichert.")
            return True
        except Exception as e:
            logging.error(f"❌ Fehler beim Speichern der Konfiguration: {e}", exc_info=True)
            return False

    def reload(self) -> bool:
        current_modified = self._get_file_modified_time()
        if current_modified == 0 or current_modified > self.last_modified:
            try:
                self.config = self._load_config_data()
                self.last_modified = current_modified
                self.user = self.config.get("user", "Bot")
                logging.info("🔄 Konfiguration wurde erfolgreich neu geladen.")
                return True
            except Exception as e:
                logging.error(f"❌ Fehler beim Neuladen der Konfiguration: {e}", exc_info=True)
                return False
        return False

# --- Haupt-Bot-Klasse ---
class DiscordBot(commands.Bot):
    def __init__(self, config: BotConfig):
        self.config = config
        self.start_time = get_current_time()
        self.shutdown_flag = False

        # Backup Attribute
        self.auto_backup_channel_id: Optional[int] = None
        self.auto_backup_interval: Optional[timedelta] = None
        self.last_backup_time: Optional[datetime] = None
        self.next_backup_time: Optional[datetime] = None
        self._load_backup_settings()

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.reactions = True

        super().__init__(command_prefix=commands.when_mentioned_or(self.config.get("command_prefix", "!")), intents=intents, help_command=None)

        self._register_base_commands()
        self._register_base_events()
        self.check_config_updates.start()
        self.auto_backup_loop.start()

    def _load_backup_settings(self):
        channel_id_str = self.config.get("auto_backup_channel_id")
        if isinstance(channel_id_str, (str, int)) and str(channel_id_str).isdigit():
            self.auto_backup_channel_id = int(channel_id_str)
        else:
            self.auto_backup_channel_id = None

        interval_str = self.config.get("auto_backup_interval")
        if isinstance(interval_str, str) and ":" in interval_str:
            try:
                days_val, hours_val = map(int, interval_str.split(":"))
                if days_val >= 0 and hours_val >= 0:
                    self.auto_backup_interval = timedelta(days=days_val, hours=hours_val)
                else:
                    self.auto_backup_interval = None
                    logging.warning(f"Negatives Backup-Intervall in Config gefunden ({interval_str}). Deaktiviert.")
            except ValueError:
                self.auto_backup_interval = None
                logging.warning(f"Ungültiges auto_backup_interval Format in Config: {interval_str}. Deaktiviert.")
        else:
            self.auto_backup_interval = None

        if self.auto_backup_interval and self.auto_backup_channel_id:
            self.last_backup_time = get_current_time()
            self.next_backup_time = self.last_backup_time + self.auto_backup_interval
            logging.info(f"Auto-Backup konfiguriert. Nächstes Backup um: {self.next_backup_time.strftime('%d.%m.%Y %H:%M:%S %Z')}")
        else:
            logging.info("Auto-Backup ist nicht vollständig konfiguriert (Channel ID oder Intervall fehlt).")

    async def load_extensions_from_config(self):
        module_path_str = self.config.get("module_path", "modules")
        if not os.path.exists(module_path_str):
            try:
                os.makedirs(module_path_str)
                logging.warning(f"🔧 Modulordner '{module_path_str}' wurde erstellt, da er nicht existierte.")
            except OSError as e:
                logging.error(f"Konnte Modulordner '{module_path_str}' nicht erstellen: {e}")
                return

        enabled_modules: List[str] = self.config.get("enabled_modules", [])
        if not enabled_modules:
            logging.info("Keine Module in 'enabled_modules' in der Konfiguration gefunden.")
            return

        for module_name in enabled_modules:
            ext_path = f"{module_path_str.replace(os.sep, '.')}.{module_name.replace('.py', '')}"
            try:
                await self.load_extension(ext_path)
                await asyncio.sleep(DEFAULT_API_DELAY)
                logging.info(f"✅ Cog '{module_name}' erfolgreich geladen.")
            except commands.ExtensionAlreadyLoaded:
                logging.warning(f"⚠️ Cog '{module_name}' war bereits geladen.")
            except commands.ExtensionNotFound:
                logging.error(f"❌ Cog '{module_name}' nicht gefunden unter Pfad '{ext_path}'. Stelle sicher, dass die .py Datei existiert.")
            except commands.NoEntryPointError:
                logging.error(f"❌ Cog '{module_name}' hat keinen 'setup' Einstiegspunkt.")
            except Exception as e:
                logging.error(f"❌ Unbekannter Fehler beim Laden von Cog '{module_name}': {e}", exc_info=True)

    async def setup_hook(self):
        for path_to_create in ["modules", "logs", "backups_temp"]:
            os.makedirs(path_to_create, exist_ok=True)

        await self.load_extensions_from_config()

        dev_guild_id_str = self.config.get("dev_guild_id")
        dev_guild_id: Optional[int] = None
        if isinstance(dev_guild_id_str, (str, int)) and str(dev_guild_id_str).isdigit():
            dev_guild_id = int(dev_guild_id_str)
        
        try:
            if dev_guild_id:
                guild = discord.Object(id=dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                await asyncio.sleep(DEFAULT_API_DELAY)
                logging.info(f"🚀 Befehle für Entwicklungs-Gilde (ID: {dev_guild_id}) synchronisiert.")
            else:
                await self.tree.sync()
                await asyncio.sleep(DEFAULT_API_DELAY)
                logging.info("🚀 Globale Befehle synchronisiert.")
        except discord.HTTPException as e:
            logging.error(f"❌ HTTP-Fehler beim Synchronisieren der Slash-Commands: {e.status} {e.text}", exc_info=True)
        except Exception as e:
            logging.error(f"❌ Allgemeiner Fehler beim Synchronisieren der Slash-Commands: {e}", exc_info=True)

    async def on_ready(self):
        bot_user = self.user if self.user else "Unbekannter Bot"
        bot_id = self.user.id if self.user else "N/A"
        logging.info(f"🚀 Bot ist bereit! Eingeloggt als {bot_user} (ID: {bot_id})")
        logging.info(f"🌐 Verbunden mit {len(self.guilds)} Server(n).")
        await self._update_presence()

    async def _update_presence(self):
        activity_type_str = self.config.get("activity_type", "playing").lower()
        activity_name = self.config.get("activity_name", "mit Slash-Commands")
        activity: Optional[discord.Activity] = None

        if activity_type_str == "playing":
            activity = discord.Game(name=activity_name)
        elif activity_type_str == "listening":
            activity = discord.Activity(type=discord.ActivityType.listening, name=activity_name)
        elif activity_type_str == "watching":
            activity = discord.Activity(type=discord.ActivityType.watching, name=activity_name)
        elif activity_type_str == "streaming":
            streaming_url = self.config.get("streaming_url", "https://www.twitch.tv/discord")
            activity = discord.Streaming(name=activity_name, url=streaming_url)
        
        if activity:
            try:
                await self.change_presence(activity=activity, status=discord.Status.online)
                await asyncio.sleep(DEFAULT_API_DELAY)
                logging.info(f"📊 Bot-Präsenz gesetzt: {activity_type_str.capitalize()} {activity_name}")
            except Exception as e:
                logging.error(f"❌ Fehler beim Setzen der Bot-Präsenz: {e}", exc_info=True)

    def _register_base_commands(self) -> None:
        admin_group = app_commands.Group(name="admin", description="Admin-Befehle für die Bot-Verwaltung", guild_only=True)

        @admin_group.command(name="status", description="Zeigt Statusinformationen des Bots an.")
        @app_commands.checks.has_permissions(administrator=True)
        async def bot_status(interaction: discord.Interaction):
            uptime = get_current_time() - self.start_time
            days, remainder = divmod(uptime.total_seconds(), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s"
            
            embed = discord.Embed(title="🤖 Bot Status", color=discord.Color.blue(), timestamp=get_current_time())
            embed.add_field(name="👤 Bot Name", value=self.config.get('user', 'N/A'), inline=True)
            embed.add_field(name="🆔 Bot ID", value=self.user.id if self.user else 'N/A', inline=True)
            embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=False)
            embed.add_field(name="📚 Geladene Cogs", value=str(len(self.extensions)), inline=True)
            embed.add_field(name="🌐 Server", value=str(len(self.guilds)), inline=True)
            embed.add_field(name="🐍 Python", value=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", inline=True)
            embed.set_footer(text=f"Angefordert von {interaction.user.display_name}")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)

        @admin_group.command(name="shutdown", description="Fährt den Bot sicher herunter.")
        @app_commands.checks.has_permissions(administrator=True)
        async def shutdown_bot(interaction: discord.Interaction):
            embed = discord.Embed(title="🛑 Bot Shutdown", description="Der Bot fährt nun sicher herunter...", color=discord.Color.red(), timestamp=get_current_time())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
            logging.info(f"🛑 Shutdown initiiert von {interaction.user} (ID: {interaction.user.id}).")
            self.shutdown_flag = True
            await self.close()

        @admin_group.command(name="restart", description="Startet den Bot neu.")
        @app_commands.checks.has_permissions(administrator=True)
        async def restart_bot(interaction: discord.Interaction):
            embed = discord.Embed(title="🔄 Bot Restart", description="Der Bot wird neu gestartet...", color=discord.Color.orange(), timestamp=get_current_time())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
            logging.info(f"🔄 Restart initiiert von {interaction.user} (ID: {interaction.user.id}).")
            await self.close()
            try:
                logging.info("Führe os.execv für Neustart aus...")
                os.execv(sys.executable, ['python'] + sys.argv)
            except Exception as e:
                logging.critical(f"Fehler beim Neustarten des Prozesses via os.execv: {e}", exc_info=True)
                sys.exit(1)

        @admin_group.command(name="reloadcogs", description="Lädt alle aktivierten Cogs neu.")
        @app_commands.checks.has_permissions(administrator=True)
        async def reload_cogs(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            await asyncio.sleep(DEFAULT_API_DELAY)

            reloaded_cogs: List[str] = []
            failed_cogs: List[str] = []
            
            enabled_modules: List[str] = self.config.get("enabled_modules", [])
            module_path_str = self.config.get("module_path", "modules")

            for module_name in enabled_modules:
                ext_path = f"{module_path_str.replace(os.sep, '.')}.{module_name.replace('.py', '')}"
                try:
                    await self.reload_extension(ext_path)
                    await asyncio.sleep(DEFAULT_API_DELAY)
                    reloaded_cogs.append(module_name)
                except commands.ExtensionNotLoaded:
                    try:
                        await self.load_extension(ext_path)
                        await asyncio.sleep(DEFAULT_API_DELAY)
                        reloaded_cogs.append(f"{module_name} (neu geladen)")
                    except Exception as load_e:
                        failed_cogs.append(f"{module_name}: Fehler beim Neuladen/Nachladen - {type(load_e).__name__}")
                except Exception as e:
                    failed_cogs.append(f"{module_name}: {type(e).__name__} - {str(e)[:100]}")
            
            sync_status = "Befehlssynchronisation übersprungen (keine Änderungen an Cogs)."
            if reloaded_cogs or failed_cogs:
                try:
                    dev_guild_id_str = self.config.get("dev_guild_id")
                    dev_guild_id = int(dev_guild_id_str) if dev_guild_id_str and dev_guild_id_str.isdigit() else None
                    if dev_guild_id:
                        await self.tree.sync(guild=discord.Object(id=dev_guild_id))
                    else:
                        await self.tree.sync()
                    await asyncio.sleep(DEFAULT_API_DELAY)
                    sync_status = "Befehle erfolgreich synchronisiert."
                    logging.info("🔄 Befehle nach Cog-Reload synchronisiert.")
                except Exception as e:
                    sync_status = f"Fehler bei Befehlssynchronisation: {type(e).__name__}"
                    logging.error(f"Sync-Fehler nach Cog-Reload: {e}", exc_info=True)
            
            embed_color = discord.Color.green() if not failed_cogs else (discord.Color.orange() if reloaded_cogs else discord.Color.red())
            embed = discord.Embed(title="🔄 Reload Cogs", description=sync_status, color=embed_color, timestamp=get_current_time())
            if reloaded_cogs:
                embed.add_field(name="✅ Erfolgreich (neu)geladen", value="\n".join(reloaded_cogs) or "Keine", inline=False)
            if failed_cogs:
                embed.add_field(name="❌ Fehler beim Laden", value="\n".join(failed_cogs) or "Keine", inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)

        @admin_group.command(name="purge", description="Löscht Nachrichten im aktuellen Channel.")
        @app_commands.checks.has_permissions(manage_messages=True)
        @app_commands.describe(amount="Anzahl der zu löschenden Nachrichten (1-100).",
                               criteria="Optional: 'bot' für Bot-Nachrichten, oder ein Wort, das im Inhalt vorkommen soll.")
        async def purge_messages(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100], criteria: Optional[str] = None):
            if not interaction.channel or not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message("❌ Dieser Befehl kann nur in Textkanälen verwendet werden.", ephemeral=True)
                await asyncio.sleep(DEFAULT_API_DELAY)
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            await asyncio.sleep(DEFAULT_API_DELAY)
            
            deleted_count = 0
            try:
                if criteria is None:
                    deleted_messages = await interaction.channel.purge(limit=amount)
                    deleted_count = len(deleted_messages)
                elif criteria.lower() == "bot":
                    deleted_messages = await interaction.channel.purge(limit=amount, check=lambda m: m.author.bot)
                    deleted_count = len(deleted_messages)
                else:
                    search_word = criteria.lower()
                    deleted_messages = await interaction.channel.purge(limit=amount, check=lambda m: search_word in m.content.lower())
                    deleted_count = len(deleted_messages)
            except discord.Forbidden:
                await interaction.followup.send("❌ Ich habe keine Berechtigung, Nachrichten in diesem Kanal zu löschen.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Ein Discord API Fehler ist beim Löschen aufgetreten: {e.status}", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Ein unerwarteter Fehler ist aufgetreten: {type(e).__name__}", ephemeral=True)
                logging.error(f"Fehler im Purge Command: {e}", exc_info=True)
            else:
                await interaction.followup.send(f"✅ {deleted_count} Nachrichten erfolgreich gelöscht.", ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)

        @admin_group.command(name="backup", description="Interaktives Backup-Menü.")
        @app_commands.checks.has_permissions(administrator=True)
        async def backup_menu(interaction: discord.Interaction):
            view = BackupView(self)
            embed = discord.Embed(title="📦 Backup Menü", description="Wähle eine Aktion:", color=discord.Color.dark_red())
            embed.add_field(name="⚙️ Settings", value="Konfiguriere Auto-Backups.", inline=False)
            embed.add_field(name="📥 Backup Now", value="Sofortiges Backup erstellen.", inline=False)
            embed.add_field(name="ℹ️ Info", value="Zeige Backup-Status und -Details.", inline=False)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await asyncio.sleep(DEFAULT_API_DELAY)

        self.tree.add_command(admin_group)

    def _register_base_events(self) -> None:
        @self.event
        async def on_guild_join(guild: discord.Guild):
            logging.info(f"✅ Bot wurde zum Server '{guild.name}' (ID: {guild.id}) hinzugefügt. Mitglieder: {guild.member_count}")

        @self.event
        async def on_guild_remove(guild: discord.Guild):
            logging.info(f"❌ Bot wurde vom Server '{guild.name}' (ID: {guild.id}) entfernt.")

        @self.event
        async def on_error(event_method: str, *args: Any, **kwargs: Any):
            exc_type, exc_value, exc_traceback_obj = sys.exc_info()
            if exc_type is not None and exc_value is not None and exc_traceback_obj is not None:
                error_trace = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback_obj))
                logging.error(f"⚠️ Unbehandelter Fehler im Event '{event_method}':\n{error_trace}")
            else:
                logging.error(f"⚠️ Unbehandelter Fehler im Event '{event_method}' (Details nicht verfügbar). Args: {args}, Kwargs: {kwargs}")

    @tasks.loop(minutes=5.0)
    async def check_config_updates(self):
        if self.config.reload():
            logging.info("🔄 Konfiguration wurde extern aktualisiert. Bot-Präsenz wird angepasst...")
            await self._update_presence()
            self._load_backup_settings()

    @check_config_updates.before_loop
    async def before_check_config(self):
        await self.wait_until_ready()

    @tasks.loop(seconds=60)
    async def auto_backup_loop(self):
        if not self.auto_backup_channel_id or not self.auto_backup_interval or self.auto_backup_interval.total_seconds() == 0:
            return

        current_time = get_current_time()
        if not self.next_backup_time:
            self.last_backup_time = self.last_backup_time or current_time
            self.next_backup_time = self.last_backup_time + self.auto_backup_interval
            logging.info(f"Auto-Backup-Loop: Nächstes Backup initial auf {self.next_backup_time.strftime('%d.%m.%Y %H:%M:%S %Z')} gesetzt.")
            return

        if current_time >= self.next_backup_time:
            logging.info(f"🕒 Auto-Backup wird jetzt durchgeführt. Geplant für: {self.next_backup_time.strftime('%d.%m.%Y %H:%M:%S %Z')}")
            backup_embed, backup_file_path, filename = await self.perform_backup()

            if backup_file_path and filename:
                channel = self.get_channel(self.auto_backup_channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    try:
                        file_size_bytes = os.path.getsize(backup_file_path)
                        limit_bytes = 25 * 1024 * 1024

                        if file_size_bytes > limit_bytes:
                            error_message = (f"❌ Automatisches Backup **{filename}** ({file_size_bytes / (1024*1024):.2f} MB) "
                                             f"ist zu groß (Limit: {limit_bytes / (1024*1024):.0f} MB). Lokal gespeichert.")
                            await channel.send(embed=backup_embed)
                            await asyncio.sleep(DEFAULT_API_DELAY)
                            await channel.send(error_message)
                            await asyncio.sleep(DEFAULT_API_DELAY)
                            logging.warning(f"Auto-Backup {filename} zu groß. Lokal gespeichert: {backup_file_path}")
                        else:
                            await channel.send(embed=backup_embed, file=discord.File(backup_file_path, filename=filename))
                            await asyncio.sleep(DEFAULT_API_DELAY)
                            logging.info(f"✅ Automatisches Backup {filename} gesendet an Channel {channel.id}.")
                            await asyncio.sleep(2)
                            try:
                                os.remove(backup_file_path)
                            except OSError as e_rem:
                                logging.error(f"Konnte lokale Auto-Backup Datei {backup_file_path} nicht entfernen: {e_rem}")
                    except discord.Forbidden:
                        logging.error(f"Keine Berechtigung für Auto-Backup in Channel {self.auto_backup_channel_id}.")
                    except discord.HTTPException as e_http:
                        logging.error(f"HTTP-Fehler beim Senden des Auto-Backups {filename}: {e_http.status} {e_http.text}")
                    except Exception as e_gen:
                        logging.error(f"Allgemeiner Fehler beim Senden des Auto-Backups {filename}: {e_gen}", exc_info=True)
                else:
                    logging.warning(f"Auto-Backup Channel {self.auto_backup_channel_id} nicht gefunden/ungültig.")
            else:
                logging.error(f"Fehler bei Erstellung des Auto-Backups. Embed-Titel: {backup_embed.title if backup_embed and backup_embed.title else 'N/A'}")
                if self.auto_backup_channel_id:
                    err_channel = self.get_channel(self.auto_backup_channel_id)
                    if err_channel and isinstance(err_channel, discord.TextChannel) and backup_embed:
                        try:
                            await err_channel.send(embed=backup_embed)
                            await asyncio.sleep(DEFAULT_API_DELAY)
                        except Exception:
                            pass

            self.last_backup_time = current_time
            self.next_backup_time = current_time + self.auto_backup_interval
            logging.info(f"Nächstes automatisches Backup geplant für: {self.next_backup_time.strftime('%d.%m.%Y %H:%M:%S %Z')}")

    def _create_zip_archive_thread(self, backup_file_path: str, base_dir: str, user_name: str) -> None:
        try:
            backup_storage_dir = os.path.dirname(backup_file_path)
            if not os.path.exists(backup_storage_dir):
                os.makedirs(backup_storage_dir, exist_ok=True)

            excluded_files = {os.path.basename(backup_file_path), "config.json.lock", ".env"}
            excluded_dirs = {".git", "__pycache__", "logs", ".venv", "venv", "node_modules", "backups_temp"}
            for root, dirs, _ in os.walk(base_dir):
                if "__pycache__" in dirs and root.startswith(os.path.join(base_dir, "modules")):
                    excluded_dirs.add(os.path.join(os.path.relpath(root, base_dir), "__pycache__"))

            with zipfile.ZipFile(backup_file_path, "w", zipfile.ZIP_DEFLATED, compresslevel=zipfile.ZIP_DEFLATED) as zf:
                info_filename = f"backup_info_{user_name}.txt"
                info_content = (f"Backup erstellt von: {user_name}\n"
                                f"Erstellungsdatum (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                                f"Lokale Zeit (Server): {get_current_time().strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                                f"Bot Version: {self.config.get('version', 'N/A')} (Annahme: version in config)\n"
                                f"Python Version: {sys.version.split()[0]}")
                zf.writestr(info_filename, info_content)

                for foldername, subfolders, filenames in os.walk(base_dir, topdown=True):
                    subfolders[:] = [d for d in subfolders if os.path.join(os.path.relpath(foldername, base_dir), d) not in excluded_dirs and d not in excluded_dirs and not d.startswith('.')]
                    
                    for fn in filenames:
                        if fn in excluded_files or fn.startswith('.'):
                            continue
                        
                        current_file_path = os.path.join(foldername, fn)
                        relative_file_path = os.path.relpath(current_file_path, base_dir)

                        skip = False
                        for excluded_dir_path in excluded_dirs:
                            if relative_file_path.startswith(excluded_dir_path + os.sep):
                                skip = True
                                break
                        if skip: 
                            continue

                        if not os.path.exists(current_file_path) or not os.path.isfile(current_file_path):
                            logging.warning(f"[BackupThread] Überspringe nicht-existente/ungültige Datei: {current_file_path}")
                            continue
                        
                        zf.write(current_file_path, arcname=relative_file_path)
            logging.info(f"[BackupThread] Zip-Archiv erfolgreich erstellt: {backup_file_path}")
        except Exception as e:
            logging.error(f"[BackupThread] Fehler beim Erstellen des Zip-Archivs {backup_file_path}: {e}", exc_info=True)
            if os.path.exists(backup_file_path):
                try:
                    os.remove(backup_file_path)
                except OSError:
                    pass
            raise

    async def perform_backup(self) -> Tuple[discord.Embed, Optional[str], Optional[str]]:
        current_time_obj = get_current_time()
        bot_user_name = self.config.get('user', 'Bot')
        
        backup_dir = "backups_temp"  # Backup directory
        os.makedirs(backup_dir, exist_ok=True)  # Ensure the backup directory exists
        
        filename = f"Backup_{bot_user_name}_{current_time_obj.strftime('%Y%m%d_%H%M%S')}.zip"
        backup_file_full_path = os.path.join(backup_dir, filename)
    
        try:
            await asyncio.to_thread(self._create_zip_archive_thread, backup_file_full_path, ".", bot_user_name)
    
            if not os.path.exists(backup_file_full_path):
                raise FileNotFoundError(f"Backup file {backup_file_full_path} was not created.")
    
            file_size_mb = os.path.getsize(backup_file_full_path) / (1024 * 1024)
            embed = discord.Embed(
                title="🚀 Backup erstellt",
                description=f"Backup **{filename}** wurde erfolgreich erstellt.\nGröße: **{file_size_mb:.2f} MB**",
                color=discord.Color.green(),
                timestamp=current_time_obj
            )
            embed.set_footer(text=f"📦 Backup von {bot_user_name}")
            self.last_backup_time = current_time_obj
            return embed, backup_file_full_path, filename
        except Exception as e:
            logging.error(f"Fehler in perform_backup beim Aufruf von _create_zip_archive_thread: {e}", exc_info=True)
            embed = discord.Embed(
                title="❌ Backup Erstellungsfehler",
                description=f"Ein interner Fehler ist beim Erstellen des Backups aufgetreten: {type(e).__name__}",
                color=discord.Color.red(),
                timestamp=current_time_obj
            )
            return embed, None, None

    async def close(self):
        logging.info("🛑 Bot Shutdown wird eingeleitet...")
        # Setze shutdown_flag, um ordnungsgemäßes Herunterfahren zu signalisieren
        self.shutdown_flag = True
        
        if self.check_config_updates.is_running(): 
            self.check_config_updates.cancel()
        if self.auto_backup_loop.is_running(): 
            self.auto_backup_loop.cancel()
        
        try:
            logging.info("Schließe Verbindung zu Discord...")
            await super().close()
            await asyncio.sleep(DEFAULT_API_DELAY)
            logging.info("🚪 Verbindung zu Discord erfolgreich geschlossen.")
        except Exception as e:
            logging.error(f"Fehler beim Schließen der Bot-Verbindung: {e}", exc_info=True)

# --- Logging & Signal Handling ---
def setup_logging(log_level_str: str = "INFO", bot_name: str = "Bot"):
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    timestamp = get_current_time().strftime("%Y-%m-%d")
    log_filename = os.path.join(log_dir, f"{bot_name.replace(' ', '_')}_{timestamp}.log")
    
    log_format = "%(asctime)s [%(levelname)-8s] [%(name)-15s]: %(message)s"  # Quelleninfo entfernt
    date_format = "%Y-%m-%d %H:%M:%S"

    numeric_level = getattr(logging, log_level_str.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_filename, encoding="utf-8", mode='a'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("websockets.protocol").setLevel(logging.WARNING)

    logging.info("=" * 70)
    logging.info(f"🚀 {bot_name} Bot-Logging gestartet. Log-Level: {log_level_str.upper()}")
    logging.info("=" * 70)

_bot_instance_for_signal: Optional[DiscordBot] = None

def signal_handler_routine(signum, frame):
    signal_name = signal.Signals(signum).name
    logging.info(f"🛑 Signal {signal_name} (Nummer {signum}) empfangen. Leite sauberen Shutdown ein...")
    global _bot_instance_for_signal
    if _bot_instance_for_signal and _bot_instance_for_signal.loop and _bot_instance_for_signal.loop.is_running():
        if not _bot_instance_for_signal.is_closed():
            # Setze shutdown_flag direkt, um sicherzustellen, dass es gesetzt ist
            _bot_instance_for_signal.shutdown_flag = True
            asyncio.run_coroutine_threadsafe(_bot_instance_for_signal.close(), _bot_instance_for_signal.loop)
    else:
        logging.info("Bot-Instanz oder Event-Loop nicht verfügbar für sauberen Shutdown durch Signal. Beende direkt.")
        sys.exit(0)

# --- Hauptprogramm ---
async def main():
    global _bot_instance_for_signal

    try:
        config = BotConfig()
    except SystemExit:
        print("Bot kann aufgrund eines Konfigurationsproblems nicht gestartet werden. Siehe Logs für Details.")
        return

    setup_logging(config.get("log_level", "INFO"), config.get("user", "Bot"))
    bot_user_name = config.get('user', 'Bot')

    logging.info("\n" + "=" * 70 + f"\n{bot_user_name.upper()} BOT STARTER".center(70) + "\n" + "=" * 70)

    retry_count = 0
    max_retries = config.get("max_retries", 3)
    base_retry_delay = config.get("base_retry_delay", 5)

    _bot_instance_for_signal = DiscordBot(config)

    while retry_count < max_retries:
        try:
            token = config.get("discord_token")
            if not token or token == "DEIN_DISCORD_TOKEN_HIER":
                logging.critical("❌ FATAL: Discord-Token nicht in config.json gesetzt oder ist der Platzhalter.")
                print("\n❌ FATAL: Discord-Token fehlt. Bitte in config.json eintragen und Bot neustarten.")
                return

            logging.info(f"🔄 Initialisiere Bot (Versuch {retry_count + 1}/{max_retries})...")
            
            if retry_count > 0:
                _bot_instance_for_signal = DiscordBot(config)

            logging.info(f"🔌 Verbinde mit Discord als '{_bot_instance_for_signal.config.get('user', 'Bot')}'...")
            await _bot_instance_for_signal.start(token)
            
            if _bot_instance_for_signal.shutdown_flag:
                logging.info("✅ Bot wurde ordnungsgemäß heruntergefahren (shutdown_flag gesetzt).")
                break
            else:
                logging.warning("⚠️ Bot-Loop wurde unerwartet beendet (kein shutdown_flag). Dies sollte nicht passieren.")
                raise Exception("Bot-Loop unerwartet beendet ohne gesetztes Shutdown-Flag.")

        except discord.LoginFailure:
            logging.critical("❌ FATAL LOGIN FAILURE: Ungültiges Discord-Token. Bot kann nicht starten.")
            break 
        except discord.PrivilegedIntentsRequired as e_intents:
            logging.critical(f"❌ FATAL PRIVILEGED INTENTS REQUIRED: {e_intents}. Aktiviere sie im Discord Developer Portal.")
            break
        except Exception as e:
            logging.critical(f"❌ KRITISCHER FEHLER im Bot-Lebenszyklus (Versuch {retry_count + 1}): {type(e).__name__} - {e}", exc_info=True)
            retry_count += 1
            if retry_count < max_retries:
                actual_retry_delay = base_retry_delay * (2 ** (retry_count - 1))
                logging.info(f"🔄 Nächster Verbindungsversuch in {actual_retry_delay}s...")
                await asyncio.sleep(actual_retry_delay)
            else:
                logging.error("❌ Maximale Wiederholungsversuche für Neustart erreicht. Bot wird beendet.")
                break
    
    logging.info("=" * 70 + f"\n👋 {bot_user_name.upper()} BOT BEENDET".center(70) + "\n" + "=" * 70)

if __name__ == "__main__":
    loop = None
    try:
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, signal_handler_routine)
        signal.signal(signal.SIGINT, signal_handler_routine)
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 KeyboardInterrupt im __main__ Block. Programm wird beendet.")
    except Exception as e_outer:
        logging.critical(f"💥 Unerwarteter Fehler auf oberster Ebene: {e_outer}", exc_info=True)
    finally:
        logging.info("🏁 Hauptprogramm-Ausführung abgeschlossen.")