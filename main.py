"""
Discord Bot Main Module – Verbessert und optimiert für konsistente Command-Synchronisation
Author: HuntersDesingsPaid
Version: 2.1.2
Last Updated: 2025-05-08 13:10:00 UTC
"""

import discord
import os
import json
import logging
import asyncio
import sys
import traceback
import signal
from typing import Dict, Any, Optional
from datetime import datetime
from discord import app_commands
from discord.ext import commands, tasks

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
            "timezone": "UTC",
            "matchday": {
                "image_size": {"width": 1024, "height": 1024},
                "background_color": "#292929",
                "text_color": "#ffffff"
            },
            "dev_guild_id": "",  # Für Entwicklungsphase: Hier die ID des Test-Servers eintragen
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        self.config["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        self.start_time = datetime.now()
        self.shutdown_flag = False

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.reactions = True

        command_prefix = config.get("command_prefix", "!")
        # commands.Bot initialisiert intern bereits einen Command-Tree (self.tree)
        super().__init__(command_prefix=command_prefix, intents=intents, help_command=None)

        # Registrierung der Basis-Befehle und Events (alle Befehle sind nur als Administrator ausführbar)
        self._register_base_commands()
        self._register_base_events()

        # Starte die regelmäßige Überprüfung der Konfiguration
        self.check_config_updates.start()

    async def load_extensions(self):
        """Lädt alle Cogs im modules-Verzeichnis mittels load_extension()."""
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
        """
        Ausführung beim Start des Bots.
        WICHTIG: Nach dem Laden aller Extensions/Cogs wird await self.tree.sync() aufgerufen,
        damit Discord die korrekte Signatur der Befehle kennt.
        """
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
        uptime = datetime.now() - self.start_time
        logging.info(f"🕒 Uptime: {str(uptime).split('.')[0]}")

    def _register_base_commands(self) -> None:
        """
        Registriert grundlegende Admin-Befehle mittels Slash-Commands.
        Alle Befehle in diesem Hauptskript sind nur als Administrator ausführbar.
        """
        admin_group = app_commands.Group(name="admin", description="Admin-Befehle für die Bot-Verwaltung")

        @admin_group.command(name="status", description="Zeigt Statusinformationen des Bots an")
        @app_commands.checks.has_permissions(administrator=True)
        async def bot_status(interaction: discord.Interaction):
            uptime = datetime.now() - self.start_time
            days, remainder = divmod(uptime.total_seconds(), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s"
            embed = discord.Embed(
                title="🤖 Bot Status",
                color=discord.Color.blue(),
                timestamp=datetime.now()
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
                timestamp=datetime.now()
            )
            # Direkt senden kann zu Problemen führen, daher defer und followup verwenden
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
                timestamp=datetime.now()
            )
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            logging.info(f"🔄 Restart initiiert von {interaction.user}.")
            await self.close()  # Schließt die aktuelle Session
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
                timestamp=datetime.now()
            )
            if reloaded:
                embed.add_field(name="Geladen", value=", ".join(reloaded), inline=False)
            if failed:
                embed.add_field(name="Fehler", value="\n".join(failed), inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

        @admin_group.command(name="purge", description="Löscht Nachrichten im aktuellen Channel basierend auf Optionen.")
        @app_commands.checks.has_permissions(administrator=True)
        async def purge(interaction: discord.Interaction, amount: Optional[int] = 100, criteria: Optional[str] = None):
            """
            Löscht Nachrichten im Channel.
            - Ohne Kriterien werden 'amount' Nachrichten gelöscht.
            - Bei Kriterien "bot" werden nur Bot-Nachrichten gelöscht.
            - Bei Angabe eines beliebigen Textes in criteria werden alle Nachrichten, 
              welche diesen Text beinhalten, gelöscht.
            """
            # Defer the interaction to ensure enough time for processing
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

        self.tree.add_command(admin_group)

    def _register_base_events(self) -> None:
        """Registriert ergänzende Events für Logs und Fehlerbehandlung."""
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
        """Überprüft regelmäßig auf Änderungen in der Konfiguration und passt Status und Presence an."""
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

    async def close(self):
        """Fährt den Bot sauber herunter."""
        logging.info("🛑 Bot Shutdown wird eingeleitet...")
        self.check_config_updates.cancel()
        await super().close()

def setup_logging():
    if not os.path.exists("logs"):
        os.makedirs("logs")
    timestamp = datetime.now().strftime("%Y-%m-%d")
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
    logging.info(f"🚀 Bot-Logging gestartet am {datetime.now().strftime('%d.%m.%Y um %H:%M:%S')}")
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