# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from discord import app_commands, Interaction, ui, ButtonStyle, SelectOption, TextStyle, PermissionOverwrite, Colour, Object, Role, CategoryChannel, TextChannel, VoiceChannel, Member, User
from discord.utils import get
import json
import os
from pathlib import Path
import logging
from typing import List, Dict, Optional, Union, Any, Tuple, cast, Callable, Coroutine
import asyncio
import copy

# --- Globale Konstanten ---
DEFAULT_API_DELAY = 1.6

# --- Konfiguration und Pfade ---
COG_FILE_DIR = Path(__file__).parent
CONFIG_SUBFOLDER_NAME = "mmhelfer_module"
MODULE_DATA_ROOT = COG_FILE_DIR / CONFIG_SUBFOLDER_NAME
CONFIG_DIR = MODULE_DATA_ROOT
TEAMS_DIR = MODULE_DATA_ROOT / "teams"
LOGO_PATH = MODULE_DATA_ROOT / "Logo.png"
MAIN_CONFIG_PATH = CONFIG_DIR / "mmhelfer_main_config.json"
PERMISSIONS_CONFIG_PATH = CONFIG_DIR / "mmhelfer_permissions_config.json"
COMPETITIVE_CONFIG_PATH = TEAMS_DIR / "mmhelfer_competitive_config.json"
COMMUNITY_CONFIG_PATH = TEAMS_DIR / "mmhelfer_community_config.json"
TEAMSUCHE_CONFIG_PATH = TEAMS_DIR / "mmhelfer_teamsuche_config.json"
CLANMEMBER_CONFIG_PATH = TEAMS_DIR / "mmhelfer_clanmember_config.json"

MODULE_DATA_ROOT.mkdir(exist_ok=True)
TEAMS_DIR.mkdir(exist_ok=True)

# Logging-Level anpassen - Fehler, Warnungen für Rate-Limits und Team-Operationen
logging.basicConfig(level=logging.ERROR)  # Basis-Level: Nur Fehler und kritische Logs
logging.getLogger('discord').setLevel(logging.WARNING)  # Warnungen für Discord-Modul (für Rate-Limit-Logs)
logging.getLogger('discord.http').setLevel(logging.WARNING)  # Warnungen für HTTP-Anfragen (für Rate-Limit-Logs)
log = logging.getLogger(__name__)

# Eigene Filter-Klasse für Team-Operationen
class TeamOperationFilter(logging.Filter):
    def filter(self, record):
        # Nur Logs für Team-Erstellung, -Änderung und -Löschung durchlassen
        msg = record.getMessage()
        return any(op in msg for op in ["Team '", "Erstell", "Lösch", "Bearbeit", "execute_team_"])

# Filter anwenden
log.addFilter(TeamOperationFilter())

def load_json(filepath: Path) -> Dict[str, Any]:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            return json.loads(content) if content.strip() else {}
    except FileNotFoundError:
        default_data_map = {
            TEAMSUCHE_CONFIG_PATH: {"lft_member_ids": []},
            CLANMEMBER_CONFIG_PATH: {"clan_member_ids": []},
            MAIN_CONFIG_PATH: {
                "roles": {}, "additional_roles": {}, "community_category_id": None,
                "competitive_category_id": None, "teamsuche_role_id": None,
                "clan_member_role_id": None, "captain_role_id": None
            }
        }
        default_data = default_data_map.get(filepath, {})
        # Konfigurationsdatei nicht gefunden, erstelle mit Standardinhalt
        save_json(filepath, default_data)
        return default_data
    except json.JSONDecodeError:
        # Fehler beim Lesen der JSON-Datei, Inhalt möglicherweise korrupt
        return {}

def save_json(filepath: Path, data: Dict[str, Any]):
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError: pass # IO-Fehler beim Schreiben JSON
    except Exception: pass # Unerwarteter Fehler beim Speichern JSON
    
async def handle_rate_limit(
    api_call_func: Callable[[], Coroutine[Any, Any, Any]], 
    context_name: str, 
    max_retries: int = 5, 
    base_delay: float = 1.0
) -> Any:
    """
    Hilfsfunktion zum Umgang mit Discord API Rate Limits.
    
    Args:
        api_call_func: Eine asynchrone Funktion, die den API-Aufruf durchführt
        context_name: Name des Kontexts für Logging (z.B. Funktionsname)
        max_retries: Maximale Anzahl an Wiederholungsversuchen
        base_delay: Basisverzögerung in Sekunden für exponentiellen Backoff
        
    Returns:
        Das Ergebnis des API-Aufrufs, wenn erfolgreich
        
    Raises:
        discord.HTTPException: Wenn der API-Aufruf nach allen Versuchen fehlschlägt
    """
    logger = logging.getLogger('discord')
    
    for attempt in range(max_retries):
        try:
            return await api_call_func()
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limit error
                # Berechne Wartezeit mit exponentieller Backoff
                retry_after = e.retry_after if hasattr(e, 'retry_after') else base_delay * (2 ** attempt)
                
                # Log den Rate-Limit-Fehler
                logger.warning(f"Rate limit hit in {context_name}. Retrying in {retry_after:.2f}s (Attempt {attempt+1}/{max_retries})")
                
                # Warte vor dem nächsten Versuch
                await asyncio.sleep(retry_after)
                
                # Wenn dies der letzte Versuch war, wirf den Fehler
                if attempt == max_retries - 1:
                    logger.error(f"Max retries ({max_retries}) reached for {context_name}. Giving up.")
                    raise
            else:
                # Bei anderen HTTP-Fehlern, logge und wirf den Fehler weiter
                logger.error(f"HTTP error in {context_name}: {e}")
                raise

# Hilfsfunktionen für häufige API-Aufrufe mit Rate-Limit-Handling
async def safe_edit_original_response(interaction: Interaction, context_name: str, **kwargs) -> None:
    """Sichere Version von edit_original_response mit Rate-Limit-Handling"""
    await handle_rate_limit(
        lambda: interaction.edit_original_response(**kwargs),
        f"{context_name}.edit_original_response"
    )

async def safe_edit_message(interaction: Interaction, context_name: str, **kwargs) -> None:
    """Sichere Version von response.edit_message mit Rate-Limit-Handling"""
    await handle_rate_limit(
        lambda: interaction.response.edit_message(**kwargs),
        f"{context_name}.edit_message"
    )

async def safe_send_message(interaction: Interaction, context_name: str, content: str, ephemeral: bool = True) -> None:
    """Sichere Version von response.send_message mit Rate-Limit-Handling"""
    await handle_rate_limit(
        lambda: interaction.response.send_message(content=content, ephemeral=ephemeral),
        f"{context_name}.send_message"
    )

async def safe_followup_send(interaction: Interaction, context_name: str, content: str, ephemeral: bool = True) -> None:
    """Sichere Version von followup.send mit Rate-Limit-Handling"""
    await handle_rate_limit(
        lambda: interaction.followup.send(content=content, ephemeral=ephemeral),
        f"{context_name}.followup_send"
    )

class TeamNameModal(ui.Modal):
    name_input = ui.TextInput(label='Team Name', placeholder='Gib den Teamnamen ein', required=True, style=TextStyle.short, max_length=100)

    def __init__(self, title: str, 
                 submit_callback: Callable[[Interaction, str], Coroutine[Any, Any, None]], 
                 original_view_interaction: Interaction, 
                 current_name: Optional[str] = ""):
        super().__init__(title=title, timeout=300) 
        self.submit_callback = submit_callback
        self.original_view_interaction = original_view_interaction 
        self.name_input.default = current_name
        self.name_input.label = "Neuer Team Name" if "Ändern" in title or "Neuer" in title else "Team Name"

    async def on_submit(self, modal_interaction: Interaction):
        await modal_interaction.response.defer(thinking=False, ephemeral=True) 
        await self.submit_callback(self.original_view_interaction, self.name_input.value)

    async def on_error(self, modal_interaction: Interaction, error: Exception):
        try:
            if not modal_interaction.response.is_done():
                 await modal_interaction.response.send_message("Fehler im Modal.", ephemeral=True)
            else:
                 await modal_interaction.followup.send("Ein interner Fehler im Namens-Modal ist aufgetreten.", ephemeral=True)
        except discord.HTTPException:
            pass # Konnte Fehlermeldung nicht senden

class TeamCreateView(ui.View):
    def __init__(self, author_id: int, main_config: Dict, cog_instance: 'MMHelferCog', original_interaction: Interaction):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.main_config = main_config
        self.cog_instance = cog_instance
        self.view_interaction: Interaction = original_interaction
        self.team_name: Optional[str] = None
        self.members: List[Member] = []
        self.captain: Optional[Member] = None
        self.team_type: Optional[str] = None
        self.add_item(self.TeamTypeSelect(self))

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur der Befehlsaufrufer kann diese Aktion ausführen.", ephemeral=True)
            return False
        return True

    async def _update_view_message(self, content: Optional[str] = None):
        embed = self._build_embed(is_ephemeral=True)
        kwargs: Dict[str, Any] = {"embed": embed, "view": self, "attachments": []}
        if content: kwargs["content"] = content
        try:
            # Verwende die Rate-Limit-Handling-Funktion
            await handle_rate_limit(
                lambda: self.view_interaction.edit_original_response(**kwargs),
                "TeamCreateView._update_view_message"
            )
            await asyncio.sleep(DEFAULT_API_DELAY * 0.5)
        except (discord.NotFound, discord.HTTPException):
            pass # Fehler beim Aktualisieren der View
        except Exception:
            pass # Unerwarteter Fehler

    def _build_embed(self, is_ephemeral: bool = False) -> discord.Embed:
        embed_color = discord.Colour.blue()
        embed=discord.Embed(title="✨ Team Erstellung ✨", color=embed_color)
        embed.set_author(name="MMHelfer / Team Management")
        if not is_ephemeral and LOGO_PATH.exists():
            embed.set_thumbnail(url=f"attachment://{LOGO_PATH.name}")
        name_value = self.team_name if self.team_name else "*Noch nicht festgelegt*"
        members_value = ", ".join([m.mention for m in self.members]) if self.members else "*Keine Mitglieder ausgewählt*"
        captain_value = self.captain.mention if self.captain else "*Kein Captain ausgewählt*"
        type_value = self.team_type if self.team_type else "*Community oder Competitive*"
        embed.add_field(name="🏷️ Team Name", value=f"```{name_value}```", inline=False)
        embed.add_field(name="👥 Mitglieder (max. 7)", value=members_value, inline=False)
        embed.add_field(name="👑 Captain", value=captain_value, inline=False)
        embed.add_field(name="🔀 Team Typ", value=type_value, inline=False)
        embed.set_footer(text="Verwende die Buttons. Drücke 'Erstellen', wenn alles passt.")
        return embed

    @ui.button(label="Team Name", style=ButtonStyle.primary, emoji="🏷️", row=0)
    async def set_name_button(self, button_interaction: Interaction, button: ui.Button):
        modal = TeamNameModal(title="Team Namen Eingeben", 
                              submit_callback=self.process_name_input, 
                              original_view_interaction=self.view_interaction,
                              current_name=self.team_name or "")
        await button_interaction.response.send_modal(modal)

    async def process_name_input(self, view_interaction_for_update: Interaction, name: str):
        self.team_name = name.strip()
        await self._update_view_message() 

    @ui.button(label="Mitglieder", style=ButtonStyle.secondary, emoji="👥", row=0)
    async def set_members_button(self, button_interaction: Interaction, button: ui.Button):
        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(placeholder="Wähle bis zu 7 Mitglieder...", min_values=0, max_values=7)
        async def select_callback(select_interaction: Interaction): 
            await select_interaction.response.defer(thinking=False, ephemeral=True)
            self.members = user_select.values 
            await self._update_view_message() 
            select_view.stop()
        user_select.callback = select_callback
        select_view.add_item(user_select)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content="Wähle die Teammitglieder aus:", embed=None, view=select_view, attachments=[]),
            "TeamCreateView.set_members_button"
        )

    @ui.button(label="Captain", style=ButtonStyle.secondary, emoji="👑", row=0)
    async def set_captain_button(self, button_interaction: Interaction, button: ui.Button):
        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(placeholder="Wähle den Captain...", min_values=0, max_values=1)
        async def select_callback(select_interaction: Interaction):
            await select_interaction.response.defer(thinking=False, ephemeral=True)
            self.captain = user_select.values[0] if user_select.values else None
            await self._update_view_message()
            select_view.stop()
        user_select.callback = select_callback
        select_view.add_item(user_select)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content="Wähle den Team-Captain aus:", embed=None, view=select_view, attachments=[]),
            "TeamCreateView.set_captain_button"
        )

    class TeamTypeSelect(ui.Select['TeamCreateView']):
        def __init__(self, parent_view: 'TeamCreateView'):
            self.parent_view = parent_view
            options = [
                SelectOption(label="Community Team", value="Community", emoji="🌐"),
                SelectOption(label="Competitive Team", value="Competitive", emoji="🏆")
            ]
            super().__init__(placeholder="Wähle den Team-Typ...", min_values=1, max_values=1, options=options, row=2)
        async def callback(self, select_interaction: Interaction): 
            await select_interaction.response.defer(thinking=False, ephemeral=True) 
            self.parent_view.team_type = self.values[0]
            await self.parent_view._update_view_message()

    @ui.button(label="Abbrechen", style=ButtonStyle.danger, emoji="❌", row=3)
    async def cancel_button(self, interaction: Interaction, button: ui.Button):
        self.stop()
        await self.view_interaction.edit_original_response(content="Team-Erstellung abgebrochen.", view=None, embed=None, attachments=[])

    @ui.button(label="Erstellen", style=ButtonStyle.success, emoji="✅", row=3)
    async def create_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True) 
        validation_error_msg: Optional[str] = None
        if not self.team_name: validation_error_msg = "Bitte gib einen Teamnamen an."
        elif not self.team_type: validation_error_msg = "Bitte wähle einen Team-Typ aus."
        elif self.captain and self.captain not in self.members:
            if len(self.members) < 7: self.members.append(self.captain)
            else: validation_error_msg = "Mitgliederliste voll. Captain konnte nicht hinzugefügt werden."
        elif len(self.members) > 7: validation_error_msg = "Maximal 7 Mitglieder erlaubt."

        if validation_error_msg:
            await interaction.followup.send(validation_error_msg, ephemeral=True)
            return
        for item_child in self.children:
            if isinstance(item_child, (ui.Button, ui.Select)): item_child.disabled = True
        try: 
            await self.view_interaction.edit_original_response(view=self) 
            await asyncio.sleep(DEFAULT_API_DELAY * 0.1) 
        except Exception:
            pass # Fehler beim Deaktivieren der Buttons
        # Starte Erstellung für Team
        await self.cog_instance.execute_team_creation(interaction, self.team_name, self.team_type, self.members, self.captain, use_followup=True)
        self.stop()

class ConfirmDeleteView(ui.View):
    def __init__(self, author_id: int, team_name: str, team_type: str, cog_instance: 'MMHelferCog', original_interaction: Interaction):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.team_name = team_name
        self.team_type = team_type
        self.cog_instance = cog_instance
        self.view_interaction: Interaction = original_interaction 

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur der Initiator dieser Aktion kann dies bestätigen.", ephemeral=True)
            return False
        return True

    @ui.button(label="Endgültig Löschen", style=ButtonStyle.danger, emoji="🗑️")
    async def confirm_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True) 
        for item in self.children:
            if isinstance(item, ui.Button): item.disabled = True
        try: 
            await self.view_interaction.edit_original_response(
                content=f"Löschung von Team '{self.team_name}' ({self.team_type}) wird durchgeführt...", 
                view=self, 
                embed=None, attachments=[]
            )
            await asyncio.sleep(DEFAULT_API_DELAY * 0.1)
        except discord.HTTPException:
            pass # Fehler beim Editieren der Ursprungsnachricht
        await self.cog_instance.execute_team_deletion(interaction, self.team_name, self.team_type)
        self.stop()

    @ui.button(label="Abbrechen", style=ButtonStyle.secondary, emoji="❌")
    async def cancel_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(thinking=False) 
        for item in self.children:
            if isinstance(item, ui.Button): item.disabled = True
        await self.view_interaction.edit_original_response(
            content=f"Löschung von Team '{self.team_name}' abgebrochen.", 
            view=None, embed=None, attachments=[] 
        )
        self.stop()

class TeamDeleteSelect(ui.Select['ui.View']):
    def __init__(self, teams: Dict[str, str], cog_instance: 'MMHelferCog'):
        self.cog_instance = cog_instance
        options = [SelectOption(label=name[:100], value=val) for name, val in teams.items()] if teams \
            else [SelectOption(label="Keine Teams zum Löschen vorhanden", value="nothing_to_delete")]
        super().__init__(placeholder="Wähle das zu löschende Team...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: Interaction): 
        selected_value = self.values[0]
        if selected_value == "nothing_to_delete":
            await interaction.response.edit_message(content="Kein Team zum Löschen ausgewählt.", view=None, attachments=[])
            return
        await interaction.response.defer(thinking=False, ephemeral=True)
        try:
            team_type, team_name = selected_value.split(":", 1)
        except ValueError:
            # Ungültiger Wert im TeamDeleteSelect
            await interaction.followup.send("Interner Fehler (ungültiger Teamwert).", ephemeral=True)
            if interaction.message: 
                try: await interaction.edit_original_response(content="Fehler, bitte Auswahl wiederholen.", view=None)
                except: pass
            return
        confirm_view = ConfirmDeleteView(interaction.user.id, team_name, team_type, self.cog_instance, interaction)
        embed = discord.Embed(
            title=f"🚨 Löschung Bestätigen: {team_name} ({team_type})",
            description=(f"**Sicher, dass du Team '{team_name}' ({team_type}) endgültig löschen möchtest?**\n\n"
                         "Effekte:\n- Rolle entfernt\n- Kanäle gelöscht\n- Mitgliedschaften aufgehoben\n\n"
                         "⚠️ **NICHT RÜCKGÄNGIG ZU MACHEN!**"),
            color=discord.Color.red()
        )
        await interaction.edit_original_response(content=None, embed=embed, view=confirm_view, attachments=[])

class TeamEditSelect(ui.Select['ui.View']):
    def __init__(self, teams: Dict[str, str], cog_instance: 'MMHelferCog'):
        self.cog_instance = cog_instance
        options = [SelectOption(label=name[:100], value=val) for name, val in teams.items()] if teams \
            else [SelectOption(label="Keine Teams zum Bearbeiten vorhanden", value="nothing_to_edit")]
        super().__init__(placeholder="Wähle das zu bearbeitende Team...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: Interaction): 
        selected_value = self.values[0]
        if selected_value == "nothing_to_edit":
            await interaction.response.edit_message(content="Kein Team zum Bearbeiten ausgewählt.", view=None, attachments=[])
            return
        await interaction.response.defer(thinking=True, ephemeral=True) 
        try:
            team_type, team_name = selected_value.split(":", 1)
        except ValueError:
            # Fehler beim Parsen des TeamEditSelect-Werts
            await interaction.followup.send("Interner Fehler (ungültiger Teamwert für Edit).", ephemeral=True)
            if interaction.message: 
                try: await interaction.edit_original_response(content="Fehler, bitte Auswahl wiederholen.", view=None)
                except: pass
            return
        guild_id = interaction.guild_id if interaction.guild else None
        original_team_data = self.cog_instance.get_team_data(team_name, team_type, guild_id)
        if not original_team_data:
            await interaction.followup.send(f"Team '{team_name}' ({team_type}) nicht gefunden.", ephemeral=True)
            if interaction.message: 
                try: await interaction.edit_original_response(content=f"Team '{team_name}' nicht gefunden.", view=None)
                except: pass
            return
        edit_view = TeamEditView(interaction.user.id, team_name, team_type, original_team_data, self.cog_instance, interaction)
        initial_embed = edit_view._build_embed(is_ephemeral=True)
        kwargs_edit: Dict[str, Any] = {
            "content": f"Bearbeite Team: **{team_name}** (Typ: {team_type})",
            "embed": initial_embed, "view": edit_view, "attachments": []
        }
        await interaction.edit_original_response(**kwargs_edit)

class TeamEditView(ui.View):
    def __init__(self, author_id: int, original_team_name: str, original_team_type: str, 
                 original_team_data: Dict[str, Any], cog_instance: 'MMHelferCog', 
                 view_interaction: Interaction): 
        super().__init__(timeout=900)
        self.author_id = author_id
        self.cog_instance = cog_instance
        self.original_team_name = original_team_name
        self.original_team_type = original_team_type
        self.original_team_data = copy.deepcopy(original_team_data)
        self.view_interaction: Interaction = view_interaction 
        self.current_team_name = original_team_name
        self.current_team_type = original_team_type
        self.current_captain_id: Optional[int] = original_team_data.get("captain_id")
        self.current_member_ids: List[int] = list(original_team_data.get("member_ids", []))
        self.changes_made: Dict[str, Any] = {}

    async def on_timeout(self):
        if self.view_interaction and self.view_interaction.message:
            try:
                message = await self.view_interaction.original_response() 
                if message and message.components: 
                    # Verwende die Rate-Limit-Handling-Funktion
                    await handle_rate_limit(
                        lambda: self.view_interaction.edit_original_response(
                            content="Die Zeit für die Bearbeitung dieses Teams ist abgelaufen.", 
                            view=None, embed=None, attachments=[]
                        ),
                        "TeamEditView.on_timeout"
                    )
            except (discord.NotFound, discord.HTTPException): 
                pass

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur der Initiator kann dieses Team bearbeiten.", ephemeral=True)
            return False
        return True

    async def _update_view_message(self, content_override: Optional[str] = None):
        embed = self._build_embed(is_ephemeral=True)
        kwargs: Dict[str, Any] = {"embed": embed, "view": self, "attachments": []}
        if content_override: kwargs["content"] = content_override
        else: kwargs["content"] = f"Bearbeite Team: **{self.original_team_name}** (Typ: {self.original_team_type})"
        try:
            # Verwende die Rate-Limit-Handling-Funktion
            await handle_rate_limit(
                lambda: self.view_interaction.edit_original_response(**kwargs),
                "TeamEditView._update_view_message"
            )
            await asyncio.sleep(DEFAULT_API_DELAY * 0.5)
        except (discord.NotFound, discord.HTTPException):
            pass # Fehler beim Aktualisieren der View
        except Exception:
            pass # Unerwarteter Fehler

    def _build_embed(self, is_ephemeral: bool = False) -> discord.Embed:
        embed_color = discord.Colour(0x57F2E2)
        description_parts = [f"Urspr. Name: `{self.original_team_name}`", f"Urspr. Typ: `{self.original_team_type}`"]
        if self.current_team_name != self.original_team_name: description_parts.append(f"**Neu Name: `{self.current_team_name}`**")
        if self.current_team_type != self.original_team_type: description_parts.append(f"**Neu Typ: `{self.current_team_type}`**")
        embed = discord.Embed(title=f"🔧 Team Bearbeiten: {self.original_team_name}", description="\n".join(description_parts), color=embed_color)
        cap_mention = f"<@{self.current_captain_id}>" if self.current_captain_id else "Kein Captain"
        embed.add_field(name="🏷️ Name", value=f"`{self.current_team_name}`", inline=False)
        embed.add_field(name="👑 Captain", value=cap_mention, inline=False)
        guild_id = self.original_team_data.get("guild_id")
        mems_val = f"{len(self.current_member_ids)} Mitglieder"
        if guild_id and self.cog_instance.bot and (guild := self.cog_instance.bot.get_guild(guild_id)):
            m_mentions = [f"<@{mid}>" if (member_obj := guild.get_member(mid)) else f"ID: {mid} (Unbekannt)" for mid in self.current_member_ids]
            mems_val = ", ".join(m_mentions) if m_mentions else "Keine Mitglieder"
        embed.add_field(name=f"👥 Mitglieder ({len(self.current_member_ids)})", value=mems_val[:1024], inline=False)
        embed.add_field(name="🔄 Typ", value=f"`{self.current_team_type}`", inline=False)
        embed.set_footer(text="Aktion wählen oder bestätigen.")
        return embed

    @ui.button(label="Name ändern", style=ButtonStyle.primary, emoji="🏷️", row=0)
    async def change_name_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "TeamEditView", "change_name_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        modal = TeamNameModal(title="Neuen Team Namen Eingeben", 
                              submit_callback=self.process_new_name_from_modal, 
                              original_view_interaction=self.view_interaction,
                              current_name=self.current_team_name)
        await button_interaction.response.send_modal(modal)

    async def process_new_name_from_modal(self, view_interaction_for_update: Interaction, new_name: str):
        new_name_stripped = new_name.strip()
        update_content_msg = None
        if not new_name_stripped:
            update_content_msg = "Teamname darf nicht leer sein."
        elif new_name_stripped == self.original_team_name:
            self.changes_made.pop("name", None)
            self.current_team_name = self.original_team_name
        else:
            self.changes_made["name"] = new_name_stripped
            self.current_team_name = new_name_stripped
        await self._update_view_message(content_override=update_content_msg)

    @ui.button(label="Captain ändern", style=ButtonStyle.secondary, emoji="👑", row=0)
    async def change_captain_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "TeamEditView", "change_captain_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        select_captain_view = ui.View(timeout=180)
        user_select_captain = ui.UserSelect(placeholder="Neuen Captain wählen...", min_values=0, max_values=1)
        async def select_captain_callback(select_interaction: Interaction): 
            await select_interaction.response.defer(thinking=False, ephemeral=True) 
            if user_select_captain.values:
                new_captain = user_select_captain.values[0]
                self.changes_made["captain"] = new_captain.id
                self.current_captain_id = new_captain.id
            else: 
                self.changes_made["captain"] = None 
                self.current_captain_id = None
            await self._update_view_message() 
            select_captain_view.stop()
        user_select_captain.callback = select_captain_callback
        select_captain_view.add_item(user_select_captain)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content="Wähle neuen Captain:", embed=None, view=select_captain_view, attachments=[]),
            "TeamEditView.change_captain_button"
        )

    @ui.button(label="Mitglied hinzufügen", style=ButtonStyle.success, emoji="➕", row=1)
    async def add_member_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "TeamEditView", "add_member_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        if len(self.current_member_ids) >= 7:
            await button_interaction.response.send_message("Max. Mitgliederzahl (7) erreicht.", ephemeral=True); return
        max_add = 7 - len(self.current_member_ids)
        add_view = ui.View(timeout=180)
        user_select_add = ui.UserSelect(placeholder=f"Füge bis zu {max_add} hinzu...", min_values=0, max_values=max_add)
        async def add_callback(select_interaction: Interaction):
            await select_interaction.response.defer(thinking=False, ephemeral=True)
            pending_adds = self.changes_made.get("add_members", [])
            preview_adds = []
            for user in user_select_add.values:
                if user.id not in self.current_member_ids and user.id not in pending_adds:
                    pending_adds.append(user.id)
                    preview_adds.append(user.id)
            if preview_adds:
                self.changes_made["add_members"] = pending_adds
                self.current_member_ids.extend(preview_adds) 
            await self._update_view_message()
            add_view.stop()
        user_select_add.callback = add_callback
        add_view.add_item(user_select_add)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content="Wähle Mitglieder zum Hinzufügen:", embed=None, view=add_view, attachments=[]),
            "TeamEditView.add_member_button"
        )

    @ui.button(label="Mitglied entfernen", style=ButtonStyle.danger, emoji="➖", row=1)
    async def remove_member_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "TeamEditView", "remove_member_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        if not self.current_member_ids:
            await button_interaction.response.send_message("Keine Mitglieder zum Entfernen.", ephemeral=True); return
        remove_view = ui.View(timeout=180)
        options_list = []
        guild = button_interaction.guild
        if not guild: await button_interaction.response.send_message("Server-Kontext Fehler.", ephemeral=True); return
        for mid_val in self.current_member_ids:
            member_obj_val = guild.get_member(mid_val)
            label = member_obj_val.display_name[:100] if member_obj_val else f"ID: {mid_val} (Unbekannt)"
            options_list.append(SelectOption(label=label, value=str(mid_val), emoji="👤" if member_obj_val else "❓"))
        if not options_list: # Sollte nicht passieren, wenn current_member_ids nicht leer ist
            await button_interaction.response.send_message("Keine entfernbaren Mitglieder.", ephemeral=True); return
        user_select_remove = ui.Select(placeholder="Wähle Mitglieder zum Entfernen...", min_values=1, max_values=len(options_list), options=options_list)
        async def remove_callback(select_interaction: Interaction):
            await select_interaction.response.defer(thinking=False, ephemeral=True)
            pending_removes = self.changes_made.get("remove_members", [])
            preview_removes = []
            for sid_str_val_rem in user_select_remove.values: # type: ignore
                sid_int_val_rem = int(sid_str_val_rem)
                if sid_int_val_rem in self.current_member_ids:
                    self.current_member_ids.remove(sid_int_val_rem) 
                    preview_removes.append(sid_int_val_rem)
                if sid_int_val_rem not in pending_removes: pending_removes.append(sid_int_val_rem)
                if self.current_captain_id == sid_int_val_rem: # Wenn Captain entfernt wird
                    self.changes_made["captain"] = None # Signalisiere, dass Captain entfernt/geändert werden muss
                    self.current_captain_id = None
            if preview_removes: self.changes_made["remove_members"] = pending_removes
            await self._update_view_message()
            remove_view.stop()
        user_select_remove.callback = remove_callback
        remove_view.add_item(user_select_remove)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content="Wähle Mitglieder zum Entfernen:", embed=None, view=remove_view, attachments=[]),
            "TeamEditView.remove_member_button"
        )

    @ui.button(label="Typ ändern", style=ButtonStyle.primary, emoji="🔄", row=2)
    async def change_type_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "TeamEditView", "change_type_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        type_confirm_view = ui.View(timeout=120)
        curr_type_val, new_type_val = self.current_team_type, "Competitive" if self.current_team_type == "Community" else "Community"
        emoji_val = "🏆" if new_type_val == "Competitive" else "🌐"
        async def confirm_type_cb(type_confirm_interaction: Interaction):
            await type_confirm_interaction.response.defer(thinking=False, ephemeral=True)
            if new_type_val == self.original_team_type: self.changes_made.pop("type", None)
            else: self.changes_made["type"] = new_type_val
            self.current_team_type = new_type_val
            await self._update_view_message()
            type_confirm_view.stop()
        confirm_btn = ui.Button(label=f"Zu {new_type_val} wechseln", style=ButtonStyle.success, emoji=emoji_val)
        confirm_btn.callback = confirm_type_cb
        type_confirm_view.add_item(confirm_btn)
        async def cancel_type_cb(type_cancel_interaction: Interaction):
            await type_cancel_interaction.response.defer(thinking=False, ephemeral=True)
            await self._update_view_message() 
            type_confirm_view.stop()
        cancel_btn = ui.Button(label="Abbrechen", style=ButtonStyle.grey)
        cancel_btn.callback = cancel_type_cb
        type_confirm_view.add_item(cancel_btn)
        msg = f"Team von '{curr_type_val}' zu '{new_type_val}' ändern?\n\n**Auswirkungen:** Rollen, Kanal-Kategorie, ggf. private Kanäle."
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content=msg, embed=None, view=type_confirm_view, attachments=[]),
            "TeamEditView.change_type_button"
        )

    @ui.button(label="Änderungen Bestätigen", style=ButtonStyle.success, emoji="✅", row=3)
    async def confirm_changes_button(self, interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(interaction, "TeamEditView", "confirm_changes_button"):
            await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not self.changes_made:
            await interaction.followup.send("Es wurden keine Änderungen vorgenommen.", ephemeral=True)
            try: 
                await self.view_interaction.edit_original_response(
                    content=f"Bearbeite Team: **{self.original_team_name}** (Typ: {self.original_team_type})",
                    embed=self._build_embed(is_ephemeral=True), 
                    view=self
                )
            except Exception:
                pass # Konnte View nach 'Keine Änderungen' nicht zurücksetzen
            return 
        for child_item in self.children:
            if isinstance(child_item, (ui.Button, ui.Select)): child_item.disabled = True
        try:
            await self.view_interaction.edit_original_response(
                content="Änderungen werden verarbeitet. Bitte warten...", 
                view=self, embed=None 
            )
            await asyncio.sleep(DEFAULT_API_DELAY * 0.1)
        except Exception:
            pass # Fehler beim Deaktivieren der Buttons
        await self.cog_instance.execute_team_edit(interaction, self.original_team_name, self.original_team_type,
                                                  copy.deepcopy(self.original_team_data), self.changes_made)
        self.stop()

    @ui.button(label="Bearbeitung Abbrechen", style=ButtonStyle.danger, emoji="🚫", row=3)
    async def cancel_edit_button(self, interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(interaction, "TeamEditView", "cancel_edit_button"):
            await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        await interaction.response.defer(thinking=False)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: self.view_interaction.edit_original_response(
                content="Bearbeitung abgebrochen. Keine Änderungen gespeichert.", 
                embed=None, view=None, attachments=[]
            ),
            "TeamEditView.cancel_edit_button"
        )
        self.stop()

class TeamsucheView(ui.View):
    def __init__(self, author_id: int, cog_instance: 'MMHelferCog', original_interaction: Interaction):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.cog_instance = cog_instance
        self.view_interaction: Interaction = original_interaction

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur der Befehlsaufrufer kann dieses Menü bedienen.", ephemeral=True)
            return False
        return True

    def _build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="👥 Teamsuche Menü", description="Verwalte hier Mitglieder, die auf Teamsuche sind.", color=discord.Colour(0x23a65a))
        embed.set_author(name="⚙️ MMHelfer / Teamsuche Verwaltung")
        return embed

    @ui.button(label="Mitglied zur Teamsuche hinzufügen", style=ButtonStyle.success, emoji="➕", row=0)
    async def add_lft_member_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "TeamsucheView", "add_lft_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        select_view_add = ui.View(timeout=180)
        user_select_add = ui.UserSelect(placeholder="Wähle Mitglieder für Teamsuche...", min_values=1, max_values=25)
        async def select_callback_add(select_interaction: Interaction): 
            await select_interaction.response.defer(ephemeral=True, thinking=True) 
            added_names, existing_names, failed_names_list = await self.cog_instance.add_members_to_lft(
                select_interaction, 
                user_select_add.values # type: ignore
            ) 
            msg_parts_add = []
            if added_names: msg_parts_add.append(f"✅ {len(added_names)} Mitglied(er) zur Teamsuche hinzugefügt.")
            if existing_names: msg_parts_add.append(f"ℹ️ {len(existing_names)} Mitglied(er) war(en) bereits auf Teamsuche.")
            if failed_names_list: msg_parts_add.append(f"❌ {len(failed_names_list)} Fehler: {', '.join(failed_names_list)}")
            response_msg_add = "\n".join(msg_parts_add) if msg_parts_add else "Keine Änderungen oder keine Mitglieder ausgewählt."
            try:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_followup_send(
                    select_interaction,
                    "TeamsucheView.add_lft_member_button.select_callback_add",
                    response_msg_add
                )
            except discord.HTTPException: pass # Followup Fehler
            try: 
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_edit_original_response(
                    self.view_interaction,
                    "TeamsucheView.add_lft_member_button.select_callback_add",
                    content=None, 
                    embed=self._build_embed(), 
                    view=self
                )
            except discord.HTTPException: pass # Restore Fehler
            select_view_add.stop()
        user_select_add.callback = select_callback_add
        select_view_add.add_item(user_select_add)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content="Wähle Mitglieder, die zur Teamsuche hinzugefügt werden sollen:", embed=None, view=select_view_add, attachments=[]),
            "TeamsucheView.add_lft_member_button"
        )

    @ui.button(label="Mitglied von Teamsuche entfernen", style=ButtonStyle.danger, emoji="➖", row=0)
    async def remove_lft_member_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "TeamsucheView", "remove_lft_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        if not button_interaction.guild:
            await button_interaction.response.send_message("Nur auf Servern.", ephemeral=True); return
        teamsuche_role_obj = await self.cog_instance._get_teamsuche_role(button_interaction.guild)
        if not teamsuche_role_obj:
             await button_interaction.response.send_message("Teamsuche-Rolle nicht konfiguriert.", ephemeral=True); return
        options_for_select = []
        members_with_role = [m for m in button_interaction.guild.members if teamsuche_role_obj in m.roles]
        if not members_with_role:
            # Verwende die Rate-Limit-Handling-Funktion
            await handle_rate_limit(
                lambda: button_interaction.response.edit_message(content="Aktuell hat niemand die Teamsuche-Rolle.", view=None, attachments=[]),
                "TeamsucheView.remove_lft_member_button.no_members"
            )
            return
        for member_with_role in members_with_role:
            options_for_select.append(SelectOption(label=member_with_role.display_name[:100], value=str(member_with_role.id), emoji="👤"))
        select_view_remove = ui.View(timeout=180)
        select_component = ui.Select(placeholder="Wähle Mitglieder zum Entfernen...", min_values=1, max_values=len(options_for_select), options=options_for_select)
        async def select_callback_remove(select_interaction: Interaction): 
            await select_interaction.response.defer(ephemeral=True, thinking=True)
            selected_ids = [int(value) for value in select_component.values] # type: ignore
            members_to_remove_list: List[Member] = []
            if not select_interaction.guild: 
                await select_interaction.followup.send("Fehler: Server-Kontext verloren.", ephemeral=True); select_view_remove.stop(); return
            for member_id in selected_ids:
                member_obj = select_interaction.guild.get_member(member_id)
                if member_obj and teamsuche_role_obj and teamsuche_role_obj in member_obj.roles:
                    members_to_remove_list.append(member_obj)
            removed_count_val, failed_role_rem_val = 0,0
            if members_to_remove_list:
                removed_count_val, failed_role_rem_val = await self.cog_instance.remove_members_from_lft(select_interaction, members_to_remove_list)
            msg_parts_rem = []
            if removed_count_val > 0: msg_parts_rem.append(f"✅ {removed_count_val} Mitglied(er) von Teamsuche entfernt.")
            if failed_role_rem_val > 0: msg_parts_rem.append(f"❌ Bei {failed_role_rem_val} Fehler bei Rollenabnahme.")
            response_msg_rem = "\n".join(msg_parts_rem) if msg_parts_rem else "Keine Aktion (evtl. keine passenden Mitglieder ausgewählt)."
            try: 
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_followup_send(
                    select_interaction,
                    "TeamsucheView.remove_lft_member_button.select_callback_remove",
                    response_msg_rem
                )
            except Exception: pass # Followup Fehler
            try:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_edit_original_response(
                    self.view_interaction,
                    "TeamsucheView.remove_lft_member_button.select_callback_remove",
                    content=None, 
                    embed=self._build_embed(), 
                    view=self
                )
            except Exception: pass # Restore Fehler
            select_view_remove.stop()
        select_component.callback = select_callback_remove
        select_view_remove.add_item(select_component)
        # Verwende die Rate-Limit-Handling-Funktion
        await handle_rate_limit(
            lambda: button_interaction.response.edit_message(content="Wähle Mitglieder zum Entfernen von Teamsuche (zeigt nur Mitglieder mit Rolle):", embed=None, view=select_view_remove, attachments=[]),
            "TeamsucheView.remove_lft_member_button"
        )

    @ui.button(label="Teamsuche-Liste Anzeigen", style=ButtonStyle.secondary, emoji="📜", row=1)
    async def list_lft_button(self, interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(interaction, "TeamsucheView", "show_lft_list_button"):
            await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True) 
        embed_lft_list = discord.Embed(title="📜 Aktive Teamsuchende", color=discord.Colour(0x23a65a))
        if not interaction.guild:
            embed_lft_list.description = "Fehler: Server-Kontext nicht verfügbar."
        else:
            teamsuche_role = await self.cog_instance._get_teamsuche_role(interaction.guild)
            if not teamsuche_role:
                embed_lft_list.description = "Teamsuche-Rolle ist nicht konfiguriert."
            else:
                members_with_role = [member for member in interaction.guild.members if teamsuche_role in member.roles]
                if not members_with_role:
                    embed_lft_list.description = "Momentan sucht niemand aktiv ein Team (hat die Rolle)."
                else:
                    member_display_list = [f"{m.mention} (`{m.name}`)" for m in members_with_role[:25]]
                    if len(members_with_role) > 25:
                        member_display_list.append(f"\n... und {len(members_with_role) - 25} weitere.")
                    embed_lft_list.description = "\n".join(member_display_list)
        try: 
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: interaction.followup.send(embed=embed_lft_list, ephemeral=True),
                "TeamsucheView.list_lft_button.followup"
            )
        except Exception: pass # Fehler beim Senden der LFT-Liste

    @ui.button(label="Menü schließen", style=ButtonStyle.grey, emoji="❌", row=1)
    async def close_lft_menu_button(self, interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(interaction, "TeamsucheView", "close_menu_button"):
            await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        await interaction.response.defer(thinking=False) 
        try:
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await safe_edit_original_response(
                self.view_interaction, 
                "TeamsucheView.close_lft_menu_button",
                content="Teamsuche-Menü geschlossen.", 
                view=None, 
                embed=None, 
                attachments=[]
            )
        except discord.HTTPException: 
            pass # Konnte Teamsuche-Menü-Schließen nicht finalisieren
            try: 
                if not interaction.response.is_done(): # Nur senden, wenn noch nicht geantwortet wurde
                    # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                    await safe_send_message(
                        interaction,
                        "TeamsucheView.close_lft_menu_button.fallback",
                        "Menü geschlossen (fallback)."
                    )
                else: # Andernfalls followup, falls die Interaktion schon eine Antwort hatte aber nicht die Menü-Schließ-Nachricht
                    # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                    await safe_followup_send(
                        interaction,
                        "TeamsucheView.close_lft_menu_button.fallback_followup",
                        "Menü geschlossen (fallback)."
                    )
            except Exception: pass # Ignoriere Fehler beim Fallback-Senden
        self.stop()

class ClanmemberView(ui.View): 
    def __init__(self, author_id: int, cog_instance: 'MMHelferCog', original_interaction: Interaction):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.cog_instance = cog_instance
        self.view_interaction: Interaction = original_interaction

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur Befehlsaufrufer.", ephemeral=True); return False
        return True

    def _build_embed(self) -> discord.Embed:
        return discord.Embed(title="🛡️ Clanmember Menü", description="Verwalte Clanmember.", color=discord.Colour(0x7289DA))

    @ui.button(label="Clanmember hinzufügen", style=ButtonStyle.success, emoji="➕", row=0)
    async def add_clan_member_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "ClanmemberView", "add_clanmember_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(placeholder="Wähle Mitglieder für Clanmember-Rolle...", min_values=1, max_values=25)
        async def cb(sel_interaction: Interaction): 
            await sel_interaction.response.defer(ephemeral=True, thinking=True)
            added, existing, failed = await self.cog_instance.add_members_to_clan(sel_interaction, user_select.values) # type: ignore
            parts = []
            if added: parts.append(f"✅ {len(added)} als Clanmember hinzugefügt.")
            if existing: parts.append(f"ℹ️ {len(existing)} war(en) bereits Clanmember.")
            if failed: parts.append(f"❌ {len(failed)} Fehler: {', '.join(failed)}")
            msg = "\n".join(parts) if parts else "Keine Aktion."
            try: 
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_followup_send(
                    sel_interaction,
                    "ClanmemberView.add_clan_member_button.cb",
                    msg
                )
            except Exception: pass # Followup Fehler
            try:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_edit_original_response(
                    self.view_interaction,
                    "ClanmemberView.add_clan_member_button.cb",
                    content=None, 
                    embed=self._build_embed(), 
                    view=self
                )
            except Exception: pass # Restore Fehler
            select_view.stop()
        user_select.callback = cb
        select_view.add_item(user_select)
        # Verwende die Rate-Limit-Handling-Funktion
        await safe_edit_message(
            button_interaction,
            "ClanmemberView.add_clan_member_button",
            content="Wähle Mitglieder als Clanmember:", 
            embed=None, 
            view=select_view, 
            attachments=[]
        )

    @ui.button(label="Clanmember entfernen", style=ButtonStyle.danger, emoji="➖", row=0)
    async def remove_clan_member_button(self, button_interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(button_interaction, "ClanmemberView", "remove_clanmember_button"):
            await button_interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        if not button_interaction.guild: 
            await button_interaction.response.send_message("Nur auf Servern.", ephemeral=True)
            return
            
        clan_role_obj = await self.cog_instance._get_clan_member_role(button_interaction.guild)
        if not clan_role_obj: 
            await button_interaction.response.send_message("Clanmember-Rolle nicht konfiguriert.", ephemeral=True)
            return

        # Prüfen, ob es überhaupt Mitglieder mit der Clanmember-Rolle gibt
        members_with_clan_role = [m for m in button_interaction.guild.members if clan_role_obj in m.roles]
        if not members_with_clan_role:
            # Verwende die Rate-Limit-Handling-Funktion
            await safe_edit_message(
                button_interaction,
                "ClanmemberView.remove_clan_member_button.no_members",
                content="Aktuell hat niemand die Clanmember-Rolle.", 
                view=None, 
                attachments=[]
            )
            return
        
        # UserSelect-Menü erstellen (wie beim Hinzufügen)
        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(
            placeholder="Wähle Clanmember zum Entfernen...", 
            min_values=1, 
            max_values=25
        )
        
        async def cb_clan_remove(sel_interaction: Interaction): 
            await sel_interaction.response.defer(ephemeral=True, thinking=True)
            
            if not sel_interaction.guild: 
                await sel_interaction.followup.send("Server-Kontext Fehler.", ephemeral=True)
                select_view.stop()
                return
                
            # Nur Benutzer mit der Clanmember-Rolle filtern
            valid_members_to_remove_clan: List[Member] = []
            not_clan_members: List[str] = []
            
            for member in user_select.values:
                member_obj = sel_interaction.guild.get_member(member.id)
                if member_obj and clan_role_obj and clan_role_obj in member_obj.roles:
                    valid_members_to_remove_clan.append(member_obj)
                else:
                    not_clan_members.append(member.display_name)
            
            # Entfernen der Mitglieder
            removed_count_clan, failed_role_rem_clan = 0, 0
            if valid_members_to_remove_clan: 
                removed_count_clan, failed_role_rem_clan = await self.cog_instance.remove_members_from_clan(sel_interaction, valid_members_to_remove_clan)
            
            # Ergebnisnachricht erstellen
            parts_clan = []
            if removed_count_clan: 
                parts_clan.append(f"✅ {removed_count_clan} von Clanmember-Liste entfernt.")
            if failed_role_rem_clan: 
                parts_clan.append(f"❌ Bei {failed_role_rem_clan} Fehler bei Rollenabnahme.")
            if not_clan_members: 
                parts_clan.append(f"ℹ️ {len(not_clan_members)} haben keine Clanmember-Rolle: {', '.join(not_clan_members[:5])}" + 
                                 (f" und {len(not_clan_members) - 5} weitere" if len(not_clan_members) > 5 else ""))
                
            msg_clan = "\n".join(parts_clan) if parts_clan else "Keine Aktion."

            try: 
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_followup_send(
                    sel_interaction,
                    "ClanmemberView.remove_clan_member_button.cb_clan_remove",
                    msg_clan
                )
            except Exception: 
                pass # Followup Fehler
                
            try:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_edit_original_response(
                    self.view_interaction,
                    "ClanmemberView.remove_clan_member_button.cb_clan_remove",
                    content=None, 
                    embed=self._build_embed(), 
                    view=self
                )
            except Exception: 
                pass # Restore Fehler
                
            select_view.stop()
            
        user_select.callback = cb_clan_remove
        select_view.add_item(user_select)
        
        # Verwende die Rate-Limit-Handling-Funktion
        await safe_edit_message(
            button_interaction,
            "ClanmemberView.remove_clan_member_button",
            content="Wähle Clanmember zum Entfernen (nur Mitglieder mit der Clanmember-Rolle werden entfernt):", 
            embed=None, 
            view=select_view, 
            attachments=[]
        )


    @ui.button(label="Clanmember-Liste Anzeigen", style=ButtonStyle.secondary, emoji="📜", row=1)
    async def list_clan_member_button(self, interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(interaction, "ClanmemberView", "show_clanmember_list_button"):
            await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(title="🛡️ Registrierte Clanmember", color=discord.Colour(0x7289DA))
        if not interaction.guild: embed.description = "Server-Kontext Fehler."
        else:
            clan_role = await self.cog_instance._get_clan_member_role(interaction.guild)
            if not clan_role: embed.description = "Clanmember-Rolle nicht konfiguriert."
            else:
                members_with_clan_role = [m for m in interaction.guild.members if clan_role in m.roles]
                if not members_with_clan_role: embed.description = "Keine Clanmember mit Rolle registriert."
                else:
                    mlist = [f"{m.mention} (`{m.name}`)" for m in members_with_clan_role[:25]]
                    if len(members_with_clan_role) > 25: mlist.append(f"\n... und {len(members_with_clan_role)-25} weitere.")
                    embed.description = "\n".join(mlist)
        try: 
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: interaction.followup.send(embed=embed, ephemeral=True),
                "ClanmemberView.list_clan_member_button.followup"
            )
        except Exception: pass # Fehler beim Senden der Clanmember-Liste

    @ui.button(label="Menü schließen", style=ButtonStyle.grey, emoji="❌", row=1)
    async def close_clan_member_menu_button(self, interaction: Interaction, button: ui.Button):
        # Berechtigungsprüfung
        if not self.cog_instance.check_button_permission(interaction, "ClanmemberView", "close_menu_button"):
            await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
            
        await interaction.response.defer(thinking=False)
        try:
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await safe_edit_original_response(
                self.view_interaction,
                "ClanmemberView.close_clan_member_menu_button",
                content="Clanmember-Menü geschlossen.", 
                view=None, 
                embed=None, 
                attachments=[]
            )
        except discord.HTTPException:
            try: 
                if not interaction.response.is_done():
                    # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                    await safe_send_message(
                        interaction,
                        "ClanmemberView.close_clan_member_menu_button.fallback",
                        "Menü geschlossen (fallback)."
                    )
                else:
                    # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                    await safe_followup_send(
                        interaction,
                        "ClanmemberView.close_clan_member_menu_button.fallback_followup",
                        "Menü geschlossen (fallback)."
                    )
            except Exception as e: log.warning(f"Finalisieren Clanmember-Menü-Schließen fehlgeschlagen: {e}")
        self.stop()

class MMHelferCog(commands.Cog, name="MMHelfer"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.main_config = load_json(MAIN_CONFIG_PATH)
        self.permissions_config = load_json(PERMISSIONS_CONFIG_PATH)
        self.competitive_teams = load_json(COMPETITIVE_CONFIG_PATH)
        self.community_teams = load_json(COMMUNITY_CONFIG_PATH)
        self.lft_members: List[int] = load_json(TEAMSUCHE_CONFIG_PATH).get("lft_member_ids", [])
        self.clan_members: List[int] = load_json(CLANMEMBER_CONFIG_PATH).get("clan_member_ids", [])
        if not isinstance(self.lft_members, list): self.lft_members = []
        if not isinstance(self.clan_members, list): self.clan_members = []
        self._validate_main_config()
        
    def check_button_permission(self, interaction: Interaction, view_name: str, button_name: str) -> bool:
        """
        Überprüft, ob ein Benutzer die Berechtigung hat, einen bestimmten Button zu verwenden.
        
        Args:
            interaction: Die Discord-Interaktion
            view_name: Der Name der View-Klasse (z.B. 'TeamCreateView')
            button_name: Der Name des Buttons (z.B. 'delete_button')
            
        Returns:
            True, wenn der Benutzer die Berechtigung hat, False sonst
        """
        # Debug-Logging
        log.info(f"Berechtigungsprüfung für {view_name}.{button_name} - User: {interaction.user.name} (ID: {interaction.user.id})")
        
        # Administrator hat immer Zugriff
        if interaction.user.guild_permissions.administrator:
            log.info(f"Benutzer {interaction.user.name} ist Administrator - Zugriff erlaubt")
            return True
            
        # Prüfe, ob die View und der Button in der Berechtigungskonfiguration existieren
        if view_name not in self.permissions_config or button_name not in self.permissions_config.get(view_name, {}):
            log.warning(f"Berechtigungsprüfung: {view_name}.{button_name} nicht in Konfiguration gefunden.")
            return False  # Standardmäßig verbieten, wenn nicht konfiguriert
            
        # Hole die erlaubten Rollen-IDs für diesen Button
        allowed_role_ids = self.permissions_config[view_name][button_name]
        log.info(f"Erlaubte Rollen-IDs für {view_name}.{button_name}: {allowed_role_ids}")
        
        # Wenn Rollen konfiguriert sind, prüfe ob der Benutzer eine davon hat
        if allowed_role_ids:
            # Konvertiere alle Rollen-IDs zu Integers für den Vergleich
            allowed_role_ids_int = {int(role_id) for role_id in allowed_role_ids}
            user_role_ids = {role.id for role in interaction.user.roles}
            
            log.info(f"Benutzer {interaction.user.name} hat Rollen-IDs: {user_role_ids}")
            
            # Prüfe, ob eine der Benutzerrollen in den erlaubten Rollen enthalten ist
            has_permission = any(role_id in allowed_role_ids_int for role_id in user_role_ids)
            log.info(f"Benutzer {interaction.user.name} hat {'Zugriff' if has_permission else 'keinen Zugriff'} auf {view_name}.{button_name}")
            return has_permission
        
        # Wenn keine Rollen konfiguriert sind, ist der Button für niemanden verfügbar (außer Admins)
        log.info(f"Keine Rollen für {view_name}.{button_name} konfiguriert - Zugriff verweigert")
        return False

    @commands.Cog.listener()
    async def on_ready(self):
        log.info(f'{self.bot.user} ist eingeloggt. MMHelferCog ist bereit für initiale Synchronisation.')
        await asyncio.sleep(10) 
        if self.bot.guilds:
            # Idealerweise sollte die Guild-ID aus der Config kommen oder es sollte über alle Guilds iteriert werden,
            # für die eine Konfiguration existiert. Hier wird der erste Guild als Beispiel genommen.
            example_guild = self.bot.guilds[0] 
            log.info(f"Versuche initiale Rollensynchronisation für Guild: {example_guild.name} (ID: {example_guild.id})")
            await self._synchronize_role_lists_for_guild(example_guild)
        else:
            log.warning("Bot ist keinen Guilds beigetreten. Initiale Rollensynchronisation kann nicht durchgeführt werden.")

    async def _synchronize_role_lists_for_guild(self, guild: discord.Guild):
        log.info(f"Starte Synchronisation der LFT- und Clanmember-Listen für Guild {guild.name}...")
        teamsuche_role = await self._get_teamsuche_role(guild)
        if teamsuche_role:
            current_lft_role_members_ids = {member.id for member in guild.members if teamsuche_role in member.roles}
            stored_lft_ids = set(self.lft_members)
            if stored_lft_ids != current_lft_role_members_ids:
                log.info(f"Synchronisiere LFT-Liste für {guild.name}. Gespeichert: {len(stored_lft_ids)}, Live mit Rolle: {len(current_lft_role_members_ids)}.")
                self.lft_members = list(current_lft_role_members_ids)
                save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": self.lft_members})
        else:
            log.warning(f"Teamsuche-Rolle nicht in {guild.name} konfiguriert. LFT-Sync übersprungen.")
        clan_member_role = await self._get_clan_member_role(guild)
        if clan_member_role:
            current_clan_role_members_ids = {member.id for member in guild.members if clan_member_role in member.roles}
            stored_clan_ids = set(self.clan_members)
            if stored_clan_ids != current_clan_role_members_ids:
                log.info(f"Synchronisiere Clanmember-Liste für {guild.name}. Gespeichert: {len(stored_clan_ids)}, Live mit Rolle: {len(current_clan_role_members_ids)}.")
                self.clan_members = list(current_clan_role_members_ids)
                save_json(CLANMEMBER_CONFIG_PATH, {"clan_member_ids": self.clan_members})
        else:
            log.warning(f"Clanmember-Rolle nicht in {guild.name} konfiguriert. Clanmember-Sync übersprungen.")
        log.info(f"Synchronisation für Guild {guild.name} abgeschlossen.")

    def _validate_main_config(self):
        required_keys = ["community_category_id", "competitive_category_id", "teamsuche_role_id", "clan_member_role_id", "captain_role_id"]
        missing = [key for key in required_keys if not self.main_config.get(key)]
        if not isinstance(self.main_config.get("roles"), dict) or not self.main_config.get("roles"): # 'roles' sollte existieren und ein Dict sein
            missing.append("'roles' (als dict mit Team-Typ-Rollen IDs)")
        if missing:
            log.critical(f"MMHelfer Main Config: Fehlende/ungültige Schlüssel: {', '.join(missing)}.")

    mm = app_commands.Group(name="mm", description="MMHelfer Team Management")

    def get_team_data(self, team_name: str, team_type: str, guild_id_for_logging: Optional[int] = None) -> Optional[Dict[str, Any]]:
        cfg_path = COMMUNITY_CONFIG_PATH if team_type == "Community" else COMPETITIVE_CONFIG_PATH
        config = load_json(cfg_path) # Lädt die Daten bei jedem Aufruf neu, um aktuell zu sein
        data = config.get(team_name)
        if data and guild_id_for_logging: data["guild_id"] = guild_id_for_logging # Temporär für Logging/Kontext
        return data

    async def _get_role_from_config(self, guild: discord.Guild, role_key: str, config_section: Optional[str] = None) -> Optional[Role]:
        role_id_val: Any = None
        if config_section: role_id_val = self.main_config.get(config_section, {}).get(role_key)
        else: role_id_val = self.main_config.get(role_key)
        if not role_id_val: return None
        try:
            role = guild.get_role(int(role_id_val))
            if not role: log.warning(f"Rolle ID {role_id_val} (Key: '{role_key}') nicht auf Server '{guild.name}' gefunden.")
            return role
        except ValueError: log.error(f"Ungültige Rollen-ID '{role_id_val}' (Key: '{role_key}')"); return None
        except Exception as e: log.exception(f"Fehler bei Rolle '{role_key}': {e}"); return None

    async def _get_teamsuche_role(self, guild: discord.Guild) -> Optional[Role]:
        return await self._get_role_from_config(guild, "teamsuche_role_id")

    async def _get_clan_member_role(self, guild: discord.Guild) -> Optional[Role]:
        return await self._get_role_from_config(guild, "clan_member_role_id")

    async def _get_captain_role(self, guild: discord.Guild) -> Optional[Role]:
        return await self._get_role_from_config(guild, "captain_role_id")

    async def _get_generic_team_type_role(self, guild: discord.Guild, team_type: str) -> Optional[Role]:
        # Die Namen der generischen Team-Typ-Rollen MÜSSEN in der main_config unter "roles" definiert sein
        # z.B. "roles": { "Community Team": "ID_DER_COMMUNITY_ROLLE", "Competitive Team": "ID_DER_COMPETITIVE_ROLLE" }
        role_name_in_config = f"{team_type} Team" # z.B. "Community Team" oder "Competitive Team"
        return await self._get_role_from_config(guild, role_name_in_config, "roles")

    async def add_members_to_lft(self, interaction_context: Interaction, members_to_add_raw: List[Union[Member, User]]) -> Tuple[List[str], List[str], List[str]]:
        if not interaction_context.guild: return [], [], [m.display_name for m in members_to_add_raw if m]
        teamsuche_role = await self._get_teamsuche_role(interaction_context.guild)
        if not teamsuche_role:
            log.error("LFT hinzufügen: Teamsuche-Rolle fehlt.")
            return [], [], [m.display_name for m in members_to_add_raw if m]
        added_names, existing_names, failed_names = [], [], []
        current_lft_ids = set(self.lft_members) 
        actual_members_to_process: List[Member] = []
        for user_or_member_obj in members_to_add_raw:
            if isinstance(user_or_member_obj, Member): actual_members_to_process.append(user_or_member_obj)
            elif isinstance(user_or_member_obj, User) and interaction_context.guild:
                member_obj = interaction_context.guild.get_member(user_or_member_obj.id)
                if member_obj: actual_members_to_process.append(member_obj)
                else: failed_names.append(user_or_member_obj.display_name + " (nicht auf Server)")
        ids_added_this_run = []
        for member_to_process in actual_members_to_process:
            if member_to_process.id in current_lft_ids and teamsuche_role in member_to_process.roles : # Nur als "existing" zählen, wenn auch Rolle schon da
                existing_names.append(member_to_process.display_name); continue
            try:
                if teamsuche_role not in member_to_process.roles:
                    await member_to_process.add_roles(teamsuche_role, reason="MMHelfer: Zur Teamsuche")
                    await asyncio.sleep(DEFAULT_API_DELAY * 0.5) 
                ids_added_this_run.append(member_to_process.id) # ID auch hinzufügen, wenn Rolle schon da war, aber nicht in JSON
                added_names.append(member_to_process.display_name)
            except Exception as e:
                log.error(f"Fehler Hinzufügen LFT Rolle zu {member_to_process.name}: {e}")
                failed_names.append(member_to_process.display_name)
        if ids_added_this_run:
            self.lft_members.extend(ids_added_this_run)
            self.lft_members = list(set(self.lft_members)) 
            save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": self.lft_members})
        return added_names, existing_names, failed_names
    
    async def remove_members_from_lft(self, interaction_context: Interaction, members_to_remove: List[Member]) -> Tuple[int, int]:
        if not interaction_context.guild: return 0, len(members_to_remove)
        teamsuche_role = await self._get_teamsuche_role(interaction_context.guild)
        removed_cfg_count, failed_role_rem_count = 0, 0
        ids_to_remove_from_file = []
        for member_obj in members_to_remove: 
            if teamsuche_role and teamsuche_role in member_obj.roles:
                try:
                    await member_obj.remove_roles(teamsuche_role, reason="MMHelfer: Von Teamsuche entfernt")
                    await asyncio.sleep(DEFAULT_API_DELAY * 0.5)
                except Exception as e:
                    log.error(f"Fehler Entfernen LFT Rolle von {member_obj.name}: {e}"); failed_role_rem_count += 1
            if member_obj.id in self.lft_members: # Aus JSON entfernen, auch wenn Rolle evtl. schon manuell weg war
                ids_to_remove_from_file.append(member_obj.id)
                # removed_cfg_count nur erhöhen, wenn ID wirklich in Liste war.
                # Die Rückgabe spiegelt eher die JSON-Änderung wider, nicht unbedingt Rollenänderung.
                removed_cfg_count +=1 
        if ids_to_remove_from_file:
            self.lft_members = [mid for mid in self.lft_members if mid not in ids_to_remove_from_file]
            save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": self.lft_members})
        return removed_cfg_count, failed_role_rem_count

    async def add_members_to_clan(self, interaction_context: Interaction, members_to_add_raw: List[Union[Member, User]]) -> Tuple[List[str], List[str], List[str]]:
        if not interaction_context.guild: return [], [], [m.display_name for m in members_to_add_raw if m]
        clan_role = await self._get_clan_member_role(interaction_context.guild)
        if not clan_role:
            log.error("Clanmember hinzufügen: Clanmember-Rolle fehlt.")
            return [], [], [m.display_name for m in members_to_add_raw if m]
        added_names, existing_names, failed_names = [], [], []
        current_clan_ids = set(self.clan_members)
        actual_members_clan: List[Member] = []
        for u_or_m in members_to_add_raw:
            if isinstance(u_or_m, Member): actual_members_clan.append(u_or_m)
            elif isinstance(u_or_m, User) and interaction_context.guild:
                m_obj = interaction_context.guild.get_member(u_or_m.id)
                if m_obj: actual_members_clan.append(m_obj)
                else: failed_names.append(u_or_m.display_name + " (nicht auf Server)")
        ids_added_cfg_list = []
        for member_proc in actual_members_clan:
            if member_proc.id in current_clan_ids and clan_role in member_proc.roles:
                existing_names.append(member_proc.display_name); continue
            try:
                if clan_role not in member_proc.roles:
                    await member_proc.add_roles(clan_role, reason="MMHelfer: Als Clanmember")
                    await asyncio.sleep(DEFAULT_API_DELAY * 0.5)
                ids_added_cfg_list.append(member_proc.id)
                added_names.append(member_proc.display_name)
            except Exception as e:
                log.error(f"Fehler Hinzufügen Clan Rolle zu {member_proc.name}: {e}"); failed_names.append(member_proc.display_name)
        if ids_added_cfg_list:
            self.clan_members.extend(ids_added_cfg_list)
            self.clan_members = list(set(self.clan_members))
            save_json(CLANMEMBER_CONFIG_PATH, {"clan_member_ids": self.clan_members})
        return added_names, existing_names, failed_names

    async def remove_members_from_clan(self, interaction_context: Interaction, members_to_remove: List[Member]) -> Tuple[int, int]:
        if not interaction_context.guild: return 0, len(members_to_remove)
        clan_role = await self._get_clan_member_role(interaction_context.guild)
        removed_cfg, failed_role = 0,0
        ids_rem_instance = []
        for member_obj in members_to_remove:
            if clan_role and clan_role in member_obj.roles:
                try:
                    await member_obj.remove_roles(clan_role, reason="MMHelfer: Von Clanmembern entfernt")
                    await asyncio.sleep(DEFAULT_API_DELAY * 0.5)
                except Exception as e:
                    log.error(f"Fehler Entfernen Clan Rolle von {member_obj.name}: {e}"); failed_role += 1
            if member_obj.id in self.clan_members:
                ids_rem_instance.append(member_obj.id)
                removed_cfg +=1
        if ids_rem_instance:
            self.clan_members = [mid for mid in self.clan_members if mid not in ids_rem_instance]
            save_json(CLANMEMBER_CONFIG_PATH, {"clan_member_ids": self.clan_members})
        return removed_cfg, failed_role

    async def _get_permission_overwrites(self, guild: discord.Guild, team_role: Role, team_type: str, channel_type_detail: str) -> Dict[Union[Role, Member, Object], PermissionOverwrite]:
        # Alle Channels sind standardmäßig privat - niemand kann sie sehen
        overwrites: Dict[Union[Role, Member, Object], PermissionOverwrite] = {guild.default_role: PermissionOverwrite(view_channel=False)}
        main_cfg_roles = self.main_config.get("roles", {}) # Enthält z.B. "Discord Admin", "Co-Lead" etc.
        
        def get_role_from_main_cfg(key: str) -> Optional[Role]: # Hilfsfunktion für Rollen aus main_config.roles
            role_id = main_cfg_roles.get(key)
            return guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None

        # Standardberechtigungen für die Team-Rolle - kann den Channel sehen
        overwrites[team_role] = PermissionOverwrite(view_channel=True)
        
        # Community Team Berechtigungen
        if team_type == "Community":
            # Text-Channel Berechtigungen
            if channel_type_detail == "text":
                # Team-Rolle
                overwrites[team_role].send_messages = True
                
                # Co-Lead
                if co_lead_role := get_role_from_main_cfg("Co-Lead"):
                    overwrites[co_lead_role] = PermissionOverwrite(view_channel=True, send_messages=True)
                
                # Discord Admin
                if discord_admin_role := get_role_from_main_cfg("Discord Admin"):
                    overwrites[discord_admin_role] = PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
                
                # Member Management
                if member_mgmt_role := get_role_from_main_cfg("Member Management"):
                    overwrites[member_mgmt_role] = PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
                
                # Erstelle Team Rolle
                if erstelle_team_role := get_role_from_main_cfg("Erstelle Team Rolle"):
                    overwrites[erstelle_team_role] = PermissionOverwrite(view_channel=True, send_messages=True)
            
            # Voice-Channel Berechtigungen
            elif channel_type_detail == "voice_community":
                # Team-Rolle
                overwrites[team_role].connect = True
                overwrites[team_role].move_members = True
                
                # Co-Lead
                if co_lead_role := get_role_from_main_cfg("Co-Lead"):
                    overwrites[co_lead_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Discord Admin
                if discord_admin_role := get_role_from_main_cfg("Discord Admin"):
                    overwrites[discord_admin_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Member Management
                if member_mgmt_role := get_role_from_main_cfg("Member Management"):
                    overwrites[member_mgmt_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Coach
                if coach_role := get_role_from_main_cfg("Coach"):
                    overwrites[coach_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Caster
                if caster_role := get_role_from_main_cfg("Caster"):
                    overwrites[caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Co-Caster
                if co_caster_role := get_role_from_main_cfg("Co-Caster"):
                    overwrites[co_caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Rookie Caster
                if rookie_caster_role := get_role_from_main_cfg("Rookie Caster"):
                    overwrites[rookie_caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Erstelle Team Rolle
                if erstelle_team_role := get_role_from_main_cfg("Erstelle Team Rolle"):
                    overwrites[erstelle_team_role] = PermissionOverwrite(view_channel=True, connect=True, move_members=True)
                
                # Competitive Team
                if competitive_team_role := get_role_from_main_cfg("Competitive Team"):
                    overwrites[competitive_team_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Community Team
                if community_team_role := get_role_from_main_cfg("Community Team"):
                    overwrites[community_team_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Rocket League
                if rocket_league_role := get_role_from_main_cfg("Rocket League"):
                    overwrites[rocket_league_role] = PermissionOverwrite(view_channel=True, connect=False)
                
                # Ehrenmember
                if ehrenmember_role := get_role_from_main_cfg("Ehrenmember"):
                    overwrites[ehrenmember_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Mafia
                if mafia_role := get_role_from_main_cfg("Mafia"):
                    overwrites[mafia_role] = PermissionOverwrite(view_channel=True, connect=True)
        
        # Competitive Team Berechtigungen
        elif team_type == "Competitive":
            # Text-Channel Berechtigungen
            if channel_type_detail == "text":
                # Team-Rolle
                overwrites[team_role].send_messages = True
                
                # Co-Lead
                if co_lead_role := get_role_from_main_cfg("Co-Lead"):
                    overwrites[co_lead_role] = PermissionOverwrite(view_channel=True, send_messages=True)
                
                # Discord Admin
                if discord_admin_role := get_role_from_main_cfg("Discord Admin"):
                    overwrites[discord_admin_role] = PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
                
                # Member Management
                if member_mgmt_role := get_role_from_main_cfg("Member Management"):
                    overwrites[member_mgmt_role] = PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
                
                # Erstelle Team Rolle
                if erstelle_team_role := get_role_from_main_cfg("Erstelle Team Rolle"):
                    overwrites[erstelle_team_role] = PermissionOverwrite(view_channel=True, send_messages=True)
            
            # Voice-Channel Berechtigungen
            elif channel_type_detail == "voice_competitive":
                # Team-Rolle
                overwrites[team_role].connect = True
                overwrites[team_role].move_members = True
                
                # Co-Lead
                if co_lead_role := get_role_from_main_cfg("Co-Lead"):
                    overwrites[co_lead_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Discord Admin
                if discord_admin_role := get_role_from_main_cfg("Discord Admin"):
                    overwrites[discord_admin_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Member Management
                if member_mgmt_role := get_role_from_main_cfg("Member Management"):
                    overwrites[member_mgmt_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Coach
                if coach_role := get_role_from_main_cfg("Coach"):
                    overwrites[coach_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Caster
                if caster_role := get_role_from_main_cfg("Caster"):
                    overwrites[caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Co-Caster
                if co_caster_role := get_role_from_main_cfg("Co-Caster"):
                    overwrites[co_caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Rookie Caster
                if rookie_caster_role := get_role_from_main_cfg("Rookie Caster"):
                    overwrites[rookie_caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Erstelle Team Rolle
                if erstelle_team_role := get_role_from_main_cfg("Erstelle Team Rolle"):
                    overwrites[erstelle_team_role] = PermissionOverwrite(view_channel=True, connect=True, move_members=True)
                
                # Competitive Team
                if competitive_team_role := get_role_from_main_cfg("Competitive Team"):
                    overwrites[competitive_team_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Community Team
                if community_team_role := get_role_from_main_cfg("Community Team"):
                    overwrites[community_team_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Rocket League
                if rocket_league_role := get_role_from_main_cfg("Rocket League"):
                    overwrites[rocket_league_role] = PermissionOverwrite(view_channel=True, connect=False)
                
                # Ehrenmember
                if ehrenmember_role := get_role_from_main_cfg("Ehrenmember"):
                    overwrites[ehrenmember_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Mafia
                if mafia_role := get_role_from_main_cfg("Mafia"):
                    overwrites[mafia_role] = PermissionOverwrite(view_channel=True, connect=True)
            
            # Privater Voice-Channel Berechtigungen
            elif channel_type_detail == "private_voice_competitive":
                # Team-Rolle
                overwrites[team_role].connect = True
                overwrites[team_role].move_members = True
                
                # Co-Lead
                if co_lead_role := get_role_from_main_cfg("Co-Lead"):
                    overwrites[co_lead_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Discord Admin
                if discord_admin_role := get_role_from_main_cfg("Discord Admin"):
                    overwrites[discord_admin_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Member Management
                if member_mgmt_role := get_role_from_main_cfg("Member Management"):
                    overwrites[member_mgmt_role] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
                
                # Caster
                if caster_role := get_role_from_main_cfg("Caster"):
                    overwrites[caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Co-Caster
                if co_caster_role := get_role_from_main_cfg("Co-Caster"):
                    overwrites[co_caster_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Rookie Caster
                if rookie_caster_role := get_role_from_main_cfg("Rookie Caster"):
                    overwrites[rookie_caster_role] = PermissionOverwrite(view_channel=True, connect=False)
                
                # Mafia
                if mafia_role := get_role_from_main_cfg("Mafia"):
                    overwrites[mafia_role] = PermissionOverwrite(view_channel=True, connect=True)
                
                # Erstelle Team Rolle
                if erstelle_team_role := get_role_from_main_cfg("Erstelle Team Rolle"):
                    overwrites[erstelle_team_role] = PermissionOverwrite(view_channel=True, connect=True, move_members=True)
        
        return {k:v for k,v in overwrites.items() if k} # Entferne None-Keys (sollte nicht passieren)

    @mm.command(name="menu", description="Öffnet das MMHelfer Hauptmenü.")
    async def menu(self, interaction: Interaction):
        # Berechtigungsprüfung mit dem neuen System
        if not self.check_button_permission(interaction, "SlashCommands", "menu"):
            await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
            return
        embed = discord.Embed(title="🛡️ MMHelfer Team Management 🛡️", color=discord.Colour.blurple())
        main_menu_view = self.MainMenuView(interaction.user.id, self, interaction) # self ist die Cog-Instanz
        kwargs_menu: Dict[str, Any] = {"embed": embed, "view": main_menu_view, "ephemeral": True}
        embed.add_field(name="➕ Team Erstellen", value="Neues Team gründen.", inline=False)
        embed.add_field(name="🔧 Team Bearbeiten", value="Bestehende Teams anpassen.", inline=False)
        embed.add_field(name="📜 Team Übersicht", value="Alle Teams anzeigen.", inline=False)
        embed.add_field(name="👥 Teamsuche", value="Teamsuchende verwalten.", inline=False)
        embed.add_field(name="🛡️ Clanmember", value="Clanmember verwalten.", inline=False)
        embed.add_field(name="➖ Team löschen", value="Ein Team auflösen.", inline=False)
        embed.set_footer(text="Wähle eine Aktion.")
        try:
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: interaction.response.send_message(**kwargs_menu),
                "MMHelferCog.menu"
            )
        except Exception as e:
            log.exception(f"Fehler Senden Hauptmenü: {e}")
            if not interaction.response.is_done(): 
                try:
                    # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                    await safe_send_message(
                        interaction,
                        "MMHelferCog.menu.fallback",
                        "Konnte Hauptmenü nicht anzeigen."
                    )
                except: # Fallback falls send_message auch fehlschlägt
                    log.error("Konnte auch Fallback-Nachricht für Hauptmenü nicht senden.")


    class MainMenuView(ui.View): 
        def __init__(self, author_id: int, cog_instance: 'MMHelferCog', original_interaction: Interaction):
            super().__init__(timeout=300) # Timeout für das Hauptmenü
            self.author_id = author_id
            self.cog_instance = cog_instance # Die Instanz der MMHelferCog Klasse
            self.view_interaction: Interaction = original_interaction # Die Interaktion, die das Menü geöffnet hat
            self._add_buttons()

        def _add_buttons(self): 
            self.clear_items()
            buttons_def = [
                ("main_create_team", "Team Erstellung", ButtonStyle.success, "➕", 0, self.create_team_button_callback),
                ("main_edit_team", "Team Bearbeiten", ButtonStyle.primary, "🔧", 0, self.edit_team_button_callback),
                ("main_list_teams", "Team Übersicht", ButtonStyle.secondary, "📜", 0, self.list_teams_button_callback),
                ("main_teamsuche", "Teamsuche", ButtonStyle.blurple, "👥", 1, self.teamsuche_button_callback),
                ("main_clanmember", "Clanmember", ButtonStyle.blurple, "🛡️", 1, self.clanmember_button_callback),
                ("main_delete_team", "Team löschen", ButtonStyle.danger, "➖", 2, self.delete_team_button_callback),
                ("main_close_menu", "Menü schließen", ButtonStyle.grey, "🚫", 2, self.close_menu_button_callback)
            ]
            for custom_id, label, style, emoji, row, callback_func in buttons_def:
                button = ui.Button(label=label, style=style, emoji=emoji, row=row, custom_id=custom_id)
                button.callback = callback_func 
                self.add_item(button)
        
        async def on_timeout(self):
            # Diese Methode wird aufgerufen, wenn das View (Hauptmenü) abläuft
            if self.view_interaction and self.view_interaction.message:
                try: 
                    # Versuche, die ursprüngliche Nachricht zu bearbeiten, um die View zu entfernen
                    message_to_edit = await self.view_interaction.original_response()
                    if message_to_edit and message_to_edit.components: # Nur bearbeiten, wenn noch Komponenten da sind
                         # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                         await safe_edit_original_response(
                             self.view_interaction,
                             "MainMenuView.on_timeout",
                             content="Das Hauptmenü ist abgelaufen.", 
                             view=None, 
                             embed=None
                         )
                except discord.HTTPException: 
                    # Kann passieren, wenn die Nachricht bereits gelöscht wurde oder Interaktion ungültig ist
                    pass 

        async def interaction_check(self, interaction: Interaction) -> bool:
            # Stellt sicher, dass nur der ursprüngliche Aufrufer die Buttons im Menü bedienen kann
            if interaction.user.id != self.author_id:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_send_message(
                    interaction,
                    "MainMenuView.interaction_check",
                    "Nur der Befehlsaufrufer kann dieses Menü bedienen."
                )
                return False
            return True

        async def create_team_button_callback(self, interaction: Interaction): 
             # interaction ist hier die Interaktion des "Team Erstellung" Buttons
             # self.view_interaction ist die ursprüngliche /menu Interaktion
             
             # Berechtigungsprüfung
             if not self.cog_instance.check_button_permission(interaction, "MainMenuView", "create_team_button"):
                 # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                 await safe_send_message(
                     interaction,
                     "MainMenuView.create_team_button_callback",
                     "Du hast keine Berechtigung, diese Funktion zu nutzen."
                 )
                 return
                 
             create_view = TeamCreateView(self.author_id, self.cog_instance.main_config, self.cog_instance, interaction) # Übergib die Button-Interaktion
             embed = create_view._build_embed(is_ephemeral=True)
             # Bearbeite die Nachricht, die durch den Klick auf "Team Erstellung" ausgelöst wurde
             # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
             await safe_edit_message(
                 interaction,
                 "MainMenuView.create_team_button_callback",
                 content=None, 
                 embed=embed, 
                 view=create_view, 
                 attachments=[]
             )

        async def edit_team_button_callback(self, interaction: Interaction): 
            # interaction ist die des "Team Bearbeiten" Buttons
            
            # Berechtigungsprüfung
            if not self.cog_instance.check_button_permission(interaction, "MainMenuView", "edit_team_button"):
                await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
                return
                
            await interaction.response.defer(thinking=False, ephemeral=True) # Defer, da Laden der Teams kurz dauern kann
            
            # Lade Teamdaten direkt aus den Instanzvariablen der Cog, die bei Bedarf neu geladen werden
            comm = self.cog_instance.community_teams
            comp = self.cog_instance.competitive_teams
            
            all_teams_map = {f"🏆 {n}": f"Competitive:{n}" for n in sorted(comp.keys())}
            all_teams_map.update({f"🌐 {n}": f"Community:{n}" for n in sorted(comm.keys())})
            
            content_msg_edit = "Wähle das Team aus, das du bearbeiten möchtest:" if all_teams_map else "Es sind keine Teams zum Bearbeiten vorhanden."
            edit_select_view_instance: Optional[ui.View] = None
            if all_teams_map:
                edit_select_view_instance = ui.View(timeout=180)
                select_comp = TeamEditSelect(all_teams_map, self.cog_instance)
                edit_select_view_instance.add_item(select_comp)
            
            # Bearbeite die Nachricht, die durch den Klick auf "Team Bearbeiten" ausgelöst wurde (die Hauptmenü-Nachricht)
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await safe_edit_original_response(
                interaction,
                "MainMenuView.edit_team_button_callback",
                content=content_msg_edit, 
                view=edit_select_view_instance, 
                embed=None, 
                attachments=[]
            )


        async def list_teams_button_callback(self, interaction: Interaction):
            # interaction ist die des "Team Übersicht" Buttons
            
            # Berechtigungsprüfung
            if not self.cog_instance.check_button_permission(interaction, "MainMenuView", "list_teams_button"):
                await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
                return
                
            await interaction.response.defer(thinking=True, ephemeral=True) # Bot denkt nach...
            
            guild = interaction.guild
            if not guild: # Sollte nicht passieren, da Menü nur auf Servern geht
                await interaction.followup.send("Dieser Befehl kann nur auf einem Server verwendet werden.", ephemeral=True)
                return

            # Lade aktuelle Teamdaten
            community_teams_data = load_json(COMMUNITY_CONFIG_PATH) # Immer frisch laden
            competitive_teams_data = load_json(COMPETITIVE_CONFIG_PATH) # Immer frisch laden
            
            description_segments = []
            team_separator = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬" 

            async def format_team_entry(team_name: str, team_data: Dict[str, Any], team_type_str: str, guild_ref: discord.Guild) -> Optional[str]:
                emote = "🏆" if team_type_str == "Competitive" else "🌐"
                role_id = team_data.get("role_id")
                team_role_obj: Optional[Role] = guild_ref.get_role(role_id) if role_id else None
                
                role_mention = team_role_obj.mention if team_role_obj else f"`{team_name}` (Rolle ID: {role_id or 'N/A'})"
                entry_parts = [f"{role_mention} | {emote}"]

                captain_id = team_data.get("captain_id")
                member_ids: List[int] = team_data.get("member_ids", [])
                
                # Captain zuerst
                if captain_id:
                    captain_member_obj = guild_ref.get_member(captain_id)
                    if captain_member_obj:
                        entry_parts.append(f"{captain_member_obj.mention} | 👑")
                    else:
                        entry_parts.append(f"`ID: {captain_id} (Captain nicht auf Server)` | 👑")
                
                # Andere Mitglieder (alphabetisch sortiert)
                other_members_list: List[Member] = []
                unresolved_member_ids: List[int] = []

                for mid in member_ids:
                    if mid == captain_id: # Bereits als Captain behandelt
                        continue
                    member_obj = guild_ref.get_member(mid)
                    if member_obj:
                        other_members_list.append(member_obj)
                    else:
                        unresolved_member_ids.append(mid) # Für später, falls benötigt
                
                other_members_list.sort(key=lambda m: m.display_name.lower())
                
                for member_obj_sorted in other_members_list:
                    entry_parts.append(member_obj_sorted.mention)
                
                # Füge IDs hinzu, die nicht zu Member-Objekten aufgelöst werden konnten (und nicht der Captain waren)
                for unresolved_mid in unresolved_member_ids:
                     entry_parts.append(f"`ID: {unresolved_mid} (Nicht auf Server)`")
                
                return "\n".join(entry_parts)

            # Competitive Teams zuerst, alphabetisch nach Teamnamen
            for team_name_key, team_values in sorted(competitive_teams_data.items()):
                formatted_entry = await format_team_entry(team_name_key, team_values, "Competitive", guild)
                if formatted_entry:
                    description_segments.append(formatted_entry)
            
            # Dann Community Teams, alphabetisch nach Teamnamen
            for team_name_key, team_values in sorted(community_teams_data.items()):
                formatted_entry = await format_team_entry(team_name_key, team_values, "Community", guild)
                if formatted_entry:
                    description_segments.append(formatted_entry)

            final_description = ""
            if not description_segments:
                final_description = "Momentan sind keine Teams registriert."
            else:
                current_length = 0
                max_desc_length = 4096 # Discord Embed Description Limit
                limit_reached_msg = f"\n{team_separator}\n*(Weitere Teams vorhanden, aber Anzeigelimit erreicht)*"
                
                for i, segment in enumerate(description_segments):
                    segment_to_add = segment
                    separator_to_add = ""
                    if i > 0: 
                        separator_to_add = f"\n{team_separator}\n"
                    
                    # Prüfe Länge vor dem Hinzufügen
                    # Berücksichtige die Länge der "Limit erreicht"-Nachricht, falls dies der letzte Eintrag ist, der passt
                    potential_length_with_limit_msg = current_length + len(separator_to_add) + len(segment_to_add) + len(limit_reached_msg)
                    
                    if current_length + len(separator_to_add) + len(segment_to_add) > max_desc_length:
                        # Dieser Eintrag passt nicht mehr, füge Limit-Nachricht hinzu, wenn noch nicht geschehen
                        if not final_description.endswith(limit_reached_msg.strip()): # Strip, falls der letzte Separator schon da war
                             # Versuche, nur den Separator + Limit-Nachricht anzuhängen, wenn der letzte Eintrag zu lang war
                            if current_length + len(separator_to_add if i > 0 else "") + len(limit_reached_msg) <= max_desc_length:
                                final_description += (separator_to_add if i > 0 else "") + limit_reached_msg
                                current_length += len(separator_to_add if i > 0 else "") + len(limit_reached_msg)
                        log.warning("Teamübersicht: Embed-Beschreibungslimit vorzeitig erreicht.")
                        break 
                    elif i == len(description_segments) -1 : # Letzter Eintrag
                         final_description += separator_to_add + segment_to_add
                         current_length += len(separator_to_add) + len(segment_to_add)
                    elif current_length + len(separator_to_add) + len(segment_to_add) + len(f"\n{team_separator}\n") + len(description_segments[i+1]) > max_desc_length and \
                         current_length + len(separator_to_add) + len(segment_to_add) + len(limit_reached_msg) <= max_desc_length:
                        # Nächster Eintrag würde Limit sprengen, aber aktueller + LimitNachricht passt
                        final_description += separator_to_add + segment_to_add
                        final_description += limit_reached_msg
                        current_length = max_desc_length # Setze auf Max, um Loop zu beenden
                        log.warning("Teamübersicht: Embed-Beschreibungslimit erreicht, Limit-Nachricht angefügt.")
                        break
                    else: # Passt und ist nicht der letzte oder Vorletzte vor Limit
                        final_description += separator_to_add + segment_to_add
                        current_length += len(separator_to_add) + len(segment_to_add)
            
            embed = discord.Embed(title="📜 Team Übersicht", description=final_description if final_description else "Fehler: Keine Teamdaten zum Anzeigen.", color=discord.Colour.blurple())
            # KEIN FOOTER mehr hier
            
            try:
                # Bearbeite die Hauptmenü-Nachricht mit der neuen Hilfsfunktion für Rate-Limit-Handling
                await safe_edit_original_response(
                    interaction,
                    "MainMenuView.list_teams_button_callback",
                    embed=embed, 
                    view=None, 
                    content=None, 
                    attachments=[]
                )
            except discord.HTTPException as e:
                log.error(f"Fehler beim Senden der Teamübersicht: {e}")
                # Fallback, falls edit_original_response fehlschlägt (selten)
                if not interaction.response.is_done(): # Prüfen, ob schon geantwortet wurde (sollte nicht der Fall sein nach defer)
                    # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                    await safe_followup_send(
                        interaction,
                        "MainMenuView.list_teams_button_callback.fallback",
                        "Konnte die Teamübersicht nicht anzeigen (Fallback)."
                    )


        async def teamsuche_button_callback(self, interaction: Interaction): 
            # interaction ist die des "Teamsuche" Buttons
            
            # Berechtigungsprüfung
            if not self.cog_instance.check_button_permission(interaction, "MainMenuView", "teamsuche_button"):
                await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
                return
                
            ts_view = TeamsucheView(self.author_id, self.cog_instance, interaction) 
            embed = ts_view._build_embed()
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await safe_edit_message(
                interaction,
                "MainMenuView.teamsuche_button_callback",
                embed=embed, 
                view=ts_view, 
                content=None, 
                attachments=[]
            )

        async def clanmember_button_callback(self, interaction: Interaction): 
            # interaction ist die des "Clanmember" Buttons
            
            # Berechtigungsprüfung
            if not self.cog_instance.check_button_permission(interaction, "MainMenuView", "clanmember_button"):
                await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
                return
                
            cm_view = ClanmemberView(self.author_id, self.cog_instance, interaction) 
            embed = cm_view._build_embed()
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await safe_edit_message(
                interaction,
                "MainMenuView.clanmember_button_callback",
                embed=embed, 
                view=cm_view, 
                content=None, 
                attachments=[]
            )

        async def delete_team_button_callback(self, interaction: Interaction): 
            # interaction ist die des "Team löschen" Buttons
            await interaction.response.defer(thinking=False, ephemeral=True) 
            
            if not isinstance(interaction.user, Member) or not interaction.guild: # Sicherstellen, dass es ein Member auf einem Guild ist
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_followup_send(
                    interaction,
                    "MainMenuView.delete_team_button_callback",
                    "Dieser Befehl kann nur auf einem Server von einem Servermitglied ausgeführt werden."
                )
                return

            # Berechtigungsprüfung mit dem neuen System
            can_delete = self.cog_instance.check_button_permission(interaction, "MainMenuView", "delete_team_button")
            
            if not can_delete:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await safe_followup_send(
                    interaction,
                    "MainMenuView.delete_team_button_callback.permission_check",
                    "Du hast keine Berechtigung, diese Funktion zu nutzen."
                )
                return

            comm = load_json(COMMUNITY_CONFIG_PATH)
            comp = load_json(COMPETITIVE_CONFIG_PATH)
            all_teams_map = {f"🏆 {n}": f"Competitive:{n}" for n in sorted(comp.keys())}
            all_teams_map.update({f"🌐 {n}": f"Community:{n}" for n in sorted(comm.keys())})
            
            content_msg = "Wähle das Team aus, das du löschen möchtest:" if all_teams_map else "Es sind keine Teams zum Löschen vorhanden."
            select_view_instance: Optional[ui.View] = None # Typ explizit machen
            if all_teams_map:
                select_view_instance = ui.View(timeout=180)
                select_view_instance.add_item(TeamDeleteSelect(all_teams_map, self.cog_instance))
            
            # Bearbeite die Nachricht, die durch den Klick auf "Team löschen" ausgelöst wurde
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await safe_edit_original_response(
                interaction,
                "MainMenuView.delete_team_button_callback",
                content=content_msg, 
                view=select_view_instance, 
                embed=None, 
                attachments=[]
            )


        async def close_menu_button_callback(self, interaction: Interaction): 
            # interaction ist die des "Menü schließen" Buttons
            
            # Berechtigungsprüfung
            if not self.cog_instance.check_button_permission(interaction, "MainMenuView", "close_menu_button"):
                await interaction.response.send_message("Du hast keine Berechtigung, diese Funktion zu nutzen.", ephemeral=True)
                return
                
            await interaction.response.defer(thinking=False) # Keine langwierige Aktion
            self.stop() # Stoppt diese View (MainMenuView)
            try:
                # Bearbeite die ursprüngliche Nachricht mit der neuen Hilfsfunktion für Rate-Limit-Handling
                await safe_edit_original_response(
                    self.view_interaction,
                    "MainMenuView.close_menu_button_callback",
                    content="Hauptmenü geschlossen.", 
                    view=None, 
                    embed=None, 
                    attachments=[]
                )
            except discord.HTTPException as e:
                # Bei HTTP-Fehlern, logge den Fehler
                log.warning(f"Fehler Schließen Hauptmenü (Nachricht evtl. schon weg): {e}")
                # Breche ab, da dies nicht kritisch ist
                # Fallback, falls die ursprüngliche Nachricht nicht mehr bearbeitet werden kann
                # (z.B. weil die Interaktion des Buttons "Menü schließen" schon eine Antwort gesendet hat,
                # was aber durch defer() und dann edit_original_response() vermieden werden sollte)
                try:
                    # Sende eine neue, flüchtige Nachricht, wenn die Interaktion des Buttons noch nicht beantwortet wurde
                    if not interaction.response.is_done():
                        # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                        await safe_send_message(
                            interaction,
                            "MainMenuView.close_menu_button_callback.fallback",
                            "Hauptmenü geschlossen."
                        )
                    # else: # Wenn Interaktion schon beantwortet (sollte nicht passieren), könnte man followup nutzen, aber ist hier nicht kritisch
                    #    await interaction.followup.send("Hauptmenü geschlossen (Fallback).", ephemeral=True)
                except discord.HTTPException as e_fallback:
                    log.error(f"Konnte auch Fallback-Nachricht für Menü-Schließen nicht senden: {e_fallback}")


    async def execute_team_creation(self, original_button_interaction: Interaction, team_name: str, team_type: str, members: List[Member], captain: Optional[Member], use_followup: bool = True) -> bool:
        # original_button_interaction ist die Interaction vom "Erstellen"-Button in TeamCreateView
        guild = original_button_interaction.guild
        progress_message: Optional[Union[discord.Message, discord.WebhookMessage]] = None
        
        # Verwende followup, da die Button-Interaktion bereits mit defer() beantwortet wurde
        try:
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            progress_message = await handle_rate_limit(
                lambda: original_button_interaction.followup.send(f"🔄 Erstelle Team '{team_name}'...", ephemeral=True, wait=True),
                "MMHelferCog.execute_team_creation"
            )
        except discord.HTTPException as e: log.error(f"Fehler Followup Progress Team Erstellung: {e}"); return False
        if not progress_message: return False # Sollte nicht passieren mit wait=True
        
        edit_method = progress_message.edit # Methode zum Bearbeiten der Followup-Nachricht

        if not guild: 
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content="Fehler: Dieser Befehl kann nur auf einem Server ausgeführt werden."),
                "MMHelferCog.execute_team_creation.edit_error"
            )
            return False

        # Lade aktuelle Teamdaten, um Duplikate zu prüfen
        self.competitive_teams = load_json(COMPETITIVE_CONFIG_PATH)
        self.community_teams = load_json(COMMUNITY_CONFIG_PATH)
        if team_name in self.competitive_teams or team_name in self.community_teams:
            await edit_method(content=f"Fehler: Ein Team mit dem Namen '{team_name}' existiert bereits.")
            return False

        created_role: Optional[Role] = None
        created_channels: Dict[str, Union[TextChannel,VoiceChannel]] = {}
        
        try:
            # 1. Team-Rolle erstellen
            await edit_method(content=f"Team '{team_name}': Erstelle Rolle..."); await asyncio.sleep(DEFAULT_API_DELAY * 0.2)
            # Für Competitive Teams: hoist=True (Mitglieder in der rechten Anzeige anhand ihrer Rolle gruppieren)
            # Für alle Teams: mentionable=True (Rolle kann von jedem markiert werden)
            hoist_setting = True if team_type == "Competitive" else False
            created_role = await guild.create_role(
                name=team_name, 
                colour=Colour.orange(), 
                mentionable=True,  # Rolle kann von jedem markiert werden
                hoist=hoist_setting,  # Gruppierung in der Mitgliederliste basierend auf Team-Typ
                reason=f"MMHelfer: Team {team_name} erstellt"
            )
            if not created_role: raise Exception("Spezifische Team-Rolle konnte nicht erstellt werden.")

            # 2. Kanäle erstellen
            await edit_method(content=f"Team '{team_name}': Erstelle Kanäle..."); await asyncio.sleep(DEFAULT_API_DELAY * 0.2)
            category_id_key = f"{team_type.lower()}_category_id" # z.B. "community_category_id"
            target_category_id = self.main_config.get(category_id_key)
            target_category = guild.get_channel(int(target_category_id)) if isinstance(target_category_id, (str, int)) and str(target_category_id).isdigit() else None
            
            if not isinstance(target_category, CategoryChannel): 
                raise Exception(f"Zielkategorie für '{team_type}' (ID: {target_category_id}) nicht gefunden oder ungültig.")

            channel_definitions = [
                {"type_detail": "text", "name_template": f"{team_name}"}, # Textkanal
                {"type_detail": f"voice_{team_type.lower()}", "name_template": f"🔸{team_name.replace(' ', '')}"} # Allgemeiner Voice ohne Leerzeichen
            ]
            if team_type == "Competitive":
                channel_definitions.append({"type_detail": "private_voice_competitive", "name_template": f"🔒 {team_name}"}) # Privater Voice für Competitive mit Leerzeichen

            for ch_def in channel_definitions:
                permissions = await self._get_permission_overwrites(guild, created_role, team_type, ch_def["type_detail"])
                channel_creator_method = guild.create_text_channel if "text" in ch_def["type_detail"] else guild.create_voice_channel
                # Für private Voice-Channels behalten wir das Leerzeichen bei, für andere ersetzen wir es
                if ch_def["type_detail"] == "private_voice_competitive":
                    safe_channel_name = ch_def["name_template"][:100]  # Behalte Leerzeichen für private Channels
                else:
                    safe_channel_name = ch_def["name_template"].replace(" ", "-")[:100]  # Ersetze Leerzeichen für andere Channels
                
                # Erstelle den Channel als privat
                if "text" in ch_def["type_detail"]:
                    new_channel = await channel_creator_method(
                        name=safe_channel_name,
                        category=target_category,
                        overwrites=permissions,  # Hier sind bereits die korrekten Berechtigungen gesetzt
                        reason=f"MMHelfer: Team {team_name}",
                        nsfw=False,  # Nicht NSFW
                        topic=f"Privater Textkanal für Team {team_name}",
                        slowmode_delay=0
                    )
                else:
                    # Voice-Channel - genau wie Text-Channel mit privaten Berechtigungen
                    new_channel = await channel_creator_method(
                        name=safe_channel_name,
                        category=target_category,
                        overwrites=permissions,  # Hier sind bereits die korrekten Berechtigungen gesetzt
                        reason=f"MMHelfer: Team {team_name}",
                        bitrate=64000,  # 64 kbps
                        user_limit=0,  # Kein Limit
                        rtc_region=None
                    )
                await asyncio.sleep(DEFAULT_API_DELAY) # Rate Limiting
                created_channels[ch_def["type_detail"]] = new_channel
            
            # 3. Rollen zuweisen
            await edit_method(content=f"Team '{team_name}': Weise Rollen zu..."); await asyncio.sleep(DEFAULT_API_DELAY * 0.2)
            
            roles_to_add_to_members: List[Role] = [created_role] # Spezifische Team-Rolle
            generic_type_role = await self._get_generic_team_type_role(guild, team_type)
            if generic_type_role: roles_to_add_to_members.append(generic_type_role)
            else: log.warning(f"Generische Rolle für Typ '{team_type}' nicht in Config gefunden. Wird übersprungen.")

            teamsuche_role_to_remove = await self._get_teamsuche_role(guild)
            captain_role_to_add = await self._get_captain_role(guild)

            member_ids_for_json: List[int] = []
            all_involved_members: set[Member] = {m for m in members if isinstance(m, Member)}
            if captain and isinstance(captain, Member): all_involved_members.add(captain)

            for member_obj in all_involved_members:
                # Füge Team- und Typ-Rollen hinzu
                current_roles_to_add = [r for r in roles_to_add_to_members if r and r not in member_obj.roles]
                if current_roles_to_add:
                    await member_obj.add_roles(*current_roles_to_add, reason=f"MMHelfer: Beitritt Team {team_name}")
                    await asyncio.sleep(DEFAULT_API_DELAY / 3)
                
                # Entferne Teamsuche-Rolle und aus LFT-Liste
                if teamsuche_role_to_remove and teamsuche_role_to_remove in member_obj.roles:
                    await member_obj.remove_roles(teamsuche_role_to_remove, reason=f"MMHelfer: Teambeitritt {team_name}")
                    await asyncio.sleep(DEFAULT_API_DELAY / 3)
                    # Auch aus der LFT-Liste entfernen
                    if member_obj.id in self.lft_members:
                        self.lft_members = [mid for mid in self.lft_members if mid != member_obj.id]
                        save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": self.lft_members})

                # Füge Captain-Rolle hinzu (nur für Captain)
                if member_obj == captain and captain_role_to_add and captain_role_to_add not in member_obj.roles:
                    await member_obj.add_roles(captain_role_to_add, reason=f"MMHelfer: Captain von Team {team_name}")
                    await asyncio.sleep(DEFAULT_API_DELAY / 3)
                
                if member_obj.id not in member_ids_for_json:
                    member_ids_for_json.append(member_obj.id)

            # 4. Config speichern
            await edit_method(content=f"Team '{team_name}': Speichere Konfiguration..."); await asyncio.sleep(DEFAULT_API_DELAY * 0.2)
            
            text_channel_obj = created_channels.get("text")
            voice_channel_obj = created_channels.get(f"voice_{team_type.lower()}")
            private_voice_obj = created_channels.get("private_voice_competitive") if team_type == "Competitive" else None

            team_json_data: Dict[str, Any] = {
                "role_id": created_role.id,
                "text_channel_id": text_channel_obj.id if text_channel_obj else None,
                "voice_channel_id": voice_channel_obj.id if voice_channel_obj else None,
                "captain_id": captain.id if captain else None,
                "member_ids": member_ids_for_json
            }
            if team_type == "Competitive":
                team_json_data["private_voice_channel_id"] = private_voice_obj.id if private_voice_obj else None

            # Validierung kritischer IDs
            if not team_json_data["text_channel_id"] or \
               not team_json_data["voice_channel_id"] or \
               (team_type == "Competitive" and not team_json_data.get("private_voice_channel_id")):
                raise Exception("Eine oder mehrere kritische Channel-IDs fehlen nach der Erstellung.")

            target_config_dict, target_config_path = (self.community_teams, COMMUNITY_CONFIG_PATH) if team_type == "Community" \
                                                     else (self.competitive_teams, COMPETITIVE_CONFIG_PATH)
            target_config_dict[team_name] = team_json_data
            save_json(target_config_path, target_config_dict)

            await edit_method(content=f"🎉 Team '{team_name}' ({team_type}) erfolgreich erstellt!")
            log.info(f"Team '{team_name}' ({team_type}) erfolgreich erstellt von {original_button_interaction.user.name}.")
            return True

        except Exception as e:
            log.exception(f"Fehler bei der Erstellung von Team '{team_name}': {e}")
            await edit_method(content=f"❌ Fehler bei der Erstellung von Team '{team_name}'. Führe Rollback durch...")
            
            # Rollback-Versuche
            if created_role:
                try: await created_role.delete(reason="MMHelfer: Rollback Teamerstellung"); await asyncio.sleep(DEFAULT_API_DELAY)
                except Exception as e_rb_role: log.error(f"Rollback Fehler (Rolle {created_role.name}): {e_rb_role}")
            for channel_obj in created_channels.values():
                try: await channel_obj.delete(reason="MMHelfer: Rollback Teamerstellung"); await asyncio.sleep(DEFAULT_API_DELAY)
                except Exception as e_rb_ch: log.error(f"Rollback Fehler (Kanal {channel_obj.name}): {e_rb_ch}")
            
            # Entferne Eintrag aus Config, falls schon geschrieben
            rb_config, rb_path = (self.community_teams, COMMUNITY_CONFIG_PATH) if team_type == "Community" else (self.competitive_teams, COMPETITIVE_CONFIG_PATH)
            if team_name in rb_config:
                del rb_config[team_name]
                save_json(rb_path, rb_config)
            
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"❌ Ein Fehler ist bei der Erstellung von '{team_name}' aufgetreten. Änderungen wurden zurückgerollt. Bitte prüfe die Logs."),
                "MMHelferCog.execute_team_creation.edit_error_rollback"
            )
            return False

    async def execute_team_deletion(self, button_interaction: Interaction, team_name: str, team_type: str) -> bool:
        # button_interaction ist die des "Endgültig Löschen" Buttons in ConfirmDeleteView
        guild = button_interaction.guild
        progress_message: Optional[Union[discord.Message, discord.WebhookMessage]] = None
        try: 
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            progress_message = await handle_rate_limit(
                lambda: button_interaction.followup.send(f"🔄 Team '{team_name}' ({team_type}): Löschung wird gestartet...", ephemeral=True, wait=True),
                "MMHelferCog.execute_team_deletion"
            )
        except discord.HTTPException as e: log.error(f"Fehler Progress Msg Deletion: {e}"); return False
        if not progress_message: return False
        edit_method = progress_message.edit

        if not guild: 
            await edit_method(content="Fehler: Nur auf Servern möglich."); return False
        
        # Lade aktuelle Teamdaten
        self.community_teams = load_json(COMMUNITY_CONFIG_PATH)
        self.competitive_teams = load_json(COMPETITIVE_CONFIG_PATH)
        
        current_config_dict, config_path = (self.community_teams, COMMUNITY_CONFIG_PATH) if team_type == "Community" \
                                          else (self.competitive_teams, COMPETITIVE_CONFIG_PATH)
        team_data = current_config_dict.get(team_name)

        if not team_data:
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"Fehler: Team '{team_name}' ({team_type}) nicht in der Konfiguration gefunden."),
                "MMHelferCog.execute_team_deletion.edit_error_not_exists"
            )
            return False

        summary = {"roles_removed_from_members": 0, "channels_deleted": [], "team_role_deleted": "N/A", "config_entry_removed": "N/A"}
        all_successful = True

        try:
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"Team '{team_name}': Entferne Rollen von Mitgliedern..."),
                "MMHelferCog.execute_team_deletion.edit_progress_roles"
            );
            await asyncio.sleep(DEFAULT_API_DELAY*.2)
            
            team_specific_role_id = team_data.get("role_id")
            captain_id = team_data.get("captain_id")
            member_ids: List[int] = team_data.get("member_ids", [])

            team_specific_role_obj = guild.get_role(team_specific_role_id) if team_specific_role_id else None
            main_captain_role_obj = await self._get_captain_role(guild)
            generic_type_role_obj = await self._get_generic_team_type_role(guild, team_type)

            roles_to_strip_for_regular_member: List[Role] = []
            if team_specific_role_obj: roles_to_strip_for_regular_member.append(team_specific_role_obj)
            if generic_type_role_obj: roles_to_strip_for_regular_member.append(generic_type_role_obj)

            roles_to_strip_for_captain = list(roles_to_strip_for_regular_member) # Kopie
            if main_captain_role_obj: roles_to_strip_for_captain.append(main_captain_role_obj)
            
            for m_id in member_ids:
                member_obj = guild.get_member(m_id)
                if member_obj:
                    roles_to_attempt_remove = roles_to_strip_for_captain if m_id == captain_id else roles_to_strip_for_regular_member
                    actual_roles_to_remove = [r for r in roles_to_attempt_remove if r and r in member_obj.roles] # Nur die, die er wirklich hat
                    
                    if actual_roles_to_remove:
                        try:
                            await member_obj.remove_roles(*actual_roles_to_remove, reason=f"MMHelfer: Team {team_name} gelöscht")
                            await asyncio.sleep(DEFAULT_API_DELAY / 3)
                            summary["roles_removed_from_members"] += 1
                        except Exception as e_rem_roles:
                            log.error(f"Fehler beim Entfernen der Rollen von {member_obj.display_name} für Team {team_name}: {e_rem_roles}")
                            all_successful = False # Fehler beim Rollenentfernen bei Mitgliedern

            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"Team '{team_name}': Lösche Kanäle..."),
                "MMHelferCog.execute_team_deletion.edit_progress_channels"
            );
            await asyncio.sleep(DEFAULT_API_DELAY*.2)
            channel_ids_to_delete = [
                team_data.get("text_channel_id"),
                team_data.get("voice_channel_id"),
                team_data.get("private_voice_channel_id") # Ist None, wenn nicht vorhanden
            ]
            for ch_id in filter(None, channel_ids_to_delete): # filter(None, ...) entfernt None-Werte
                channel_obj = guild.get_channel(ch_id)
                if channel_obj:
                    ch_name_for_log = channel_obj.name
                    try:
                        await channel_obj.delete(reason=f"MMHelfer: Team {team_name} gelöscht")
                        await asyncio.sleep(DEFAULT_API_DELAY)
                        summary["channels_deleted"].append(f"'{ch_name_for_log}'")
                    except Exception as e_del_ch:
                        log.warning(f"Fehler beim Löschen von Kanal '{ch_name_for_log}' (ID: {ch_id}): {e_del_ch}")
                        all_successful = False # Fehler beim Kanallöschen
            
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"Team '{team_name}': Lösche Team-Rolle..."),
                "MMHelferCog.execute_team_deletion.edit_progress_role"
            );
            await asyncio.sleep(DEFAULT_API_DELAY*.2)
            if team_specific_role_obj:
                role_name_for_log = team_specific_role_obj.name
                try:
                    await team_specific_role_obj.delete(reason=f"MMHelfer: Team {team_name} gelöscht")
                    await asyncio.sleep(DEFAULT_API_DELAY)
                    summary["team_role_deleted"] = f"'{role_name_for_log}' erfolgreich gelöscht."
                except Exception as e_del_role:
                    summary["team_role_deleted"] = f"Fehler beim Löschen der Rolle '{role_name_for_log}': {e_del_role}"
                    log.error(summary["team_role_deleted"])
                    all_successful = False # Fehler beim Löschen der Team-Rolle
            elif team_specific_role_id:
                summary["team_role_deleted"] = f"Team-Rolle (ID: {team_specific_role_id}) nicht auf Server gefunden."
            else:
                summary["team_role_deleted"] = "Keine Team-Rollen-ID in Config gefunden."

            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"Team '{team_name}': Entferne Config-Eintrag..."),
                "MMHelferCog.execute_team_deletion.edit_progress_config"
            );
            await asyncio.sleep(DEFAULT_API_DELAY*.2)
            if team_name in current_config_dict:
                try:
                    del current_config_dict[team_name]
                    save_json(config_path, current_config_dict)
                    summary["config_entry_removed"] = "Erfolgreich."
                except Exception as e_save_cfg:
                    summary["config_entry_removed"] = f"Fehler beim Speichern der Config: {e_save_cfg}"
                    log.error(summary["config_entry_removed"])
                    all_successful = False # Fehler beim Speichern der Config
            else:
                summary["config_entry_removed"] = "Eintrag war bereits nicht mehr in Config."

            final_msg_parts = [
                f"🗑️ Team '{team_name}' ({team_type}) gelöscht.",
                f"Rollen von Mitgliedern entfernt: {summary['roles_removed_from_members']}",
                f"Kanäle gelöscht: {len(summary['channels_deleted'])} ({', '.join(summary['channels_deleted']) if summary['channels_deleted'] else 'Keine'})",
                f"Status Team-Rolle: {summary['team_role_deleted']}",
                f"Status Config-Eintrag: {summary['config_entry_removed']}"
            ]
            if not all_successful:
                final_msg_parts.append("\n**⚠️ Es sind Fehler während des Löschvorgangs aufgetreten! Bitte prüfe die Logs.**")
            
            final_message_content = "\n".join(final_msg_parts)
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=final_message_content),
                "MMHelferCog.execute_team_deletion.edit_final_message"
            )
            
            if all_successful: 
                log.info(f"Team '{team_name}' ({team_type}) erfolgreich und vollständig gelöscht von {button_interaction.user.name}.")
            else: 
                log.warning(f"Team '{team_name}' ({team_type}) wurde mit Fehlern gelöscht von {button_interaction.user.name}. Details: {summary}")
            
        except Exception as e_fatal:
            log.exception(f"Fataler Fehler beim Löschen von Team '{team_name}': {e_fatal}")
            try:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await handle_rate_limit(
                    lambda: edit_method(content=f"❌ Ein schwerwiegender Fehler ist beim Löschen von Team '{team_name}' aufgetreten. Bitte prüfe die Logs."),
                    "MMHelferCog.execute_team_deletion.edit_error_fatal"
                )
            except: pass # Wenn selbst das fehlschlägt
            return False
        return all_successful

    async def execute_team_edit(self, original_button_interaction: Interaction, original_team_name: str, original_team_type: str, original_team_data: Dict[str, Any], changes: Dict[str, Any]) -> bool:
        # original_button_interaction ist die vom "Änderungen Bestätigen" Button in TeamEditView
        guild = original_button_interaction.guild
        progress_message: Optional[Union[discord.Message, discord.WebhookMessage]] = None
        try: 
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            progress_message = await handle_rate_limit(
                lambda: original_button_interaction.followup.send(f"🔄 Bearbeite Team '{original_team_name}'...", ephemeral=True, wait=True),
                "MMHelferCog.execute_team_edit"
            )
        except discord.HTTPException as e: log.error(f"Fehler Progress Msg Edit: {e}"); return False
        if not progress_message: return False
        edit_method = progress_message.edit
        
        if not guild: 
            await edit_method(content="Fehler: Nur auf Servern möglich."); return False
        
        working_data = copy.deepcopy(original_team_data) # Kopie der ursprünglichen Daten zum Bearbeiten
        current_team_name = changes.get("name", original_team_name) # Name nach der Änderung (falls geändert)
        current_team_type = changes.get("type", original_team_type) # Typ nach der Änderung (falls geändert)
        
        team_role_id = working_data.get("role_id")
        team_role_obj = guild.get_role(team_role_id) if team_role_id else None
        if not team_role_obj: 
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"Fehler: Die Hauptrolle des Teams (ID: {team_role_id}) wurde nicht gefunden."),
                "MMHelferCog.execute_team_edit.edit_error_role_not_found"
            ); 
            return False

        try:
            # 1. Namensänderung (Rolle und Kanäle umbenennen)
            if "name" in changes and current_team_name != original_team_name:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await handle_rate_limit(
                    lambda: edit_method(content=f"Team '{current_team_name}': Benenne Rolle und Kanäle um..."),
                    "MMHelferCog.execute_team_edit.edit_progress_rename"
                ); 
                await asyncio.sleep(DEFAULT_API_DELAY*.2)
                await team_role_obj.edit(name=current_team_name, reason="MMHelfer: Teamname geändert"); await asyncio.sleep(DEFAULT_API_DELAY)
                
                channel_keys_to_rename = { # Mapping von JSON-Key zu Kanalpräfix
                    "text_channel_id": "", 
                    "voice_channel_id": "🔸", 
                    "private_voice_channel_id": "🔒" # Wird nur umbenannt, wenn vorhanden
                }
                for ch_key, prefix in channel_keys_to_rename.items():
                    ch_id = working_data.get(ch_key)
                    if ch_id:
                        channel_to_rename = guild.get_channel(ch_id)
                        if channel_to_rename:
                            # Für private Voice-Channels behalten wir das Leerzeichen bei, für andere ersetzen wir es
                            if ch_key == "private_voice_channel_id":
                                new_ch_name = f"{prefix} {current_team_name}"[:100]  # Behalte Leerzeichen für private Voice-Channels
                            else:
                                new_ch_name = f"{prefix}{current_team_name}".replace(" ", "-")[:100]  # Ersetze Leerzeichen für andere Channels
                            await channel_to_rename.edit(name=new_ch_name, reason="MMHelfer: Teamname geändert"); await asyncio.sleep(DEFAULT_API_DELAY)
            
            # 2. Typänderung (Rollen für Mitglieder anpassen, Kanäle verschieben/erstellen/löschen)
            if "type" in changes and current_team_type != original_team_type:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await handle_rate_limit(
                    lambda: edit_method(content=f"Team '{current_team_name}': Ändere Team-Typ..."),
                    "MMHelferCog.execute_team_edit.edit_progress_type"
                ); 
                await asyncio.sleep(DEFAULT_API_DELAY*.2)
                
                # Team-Rolle aktualisieren: hoist-Eigenschaft basierend auf neuem Team-Typ setzen
                # Competitive: hoist=True (Mitglieder in der rechten Anzeige anhand ihrer Rolle gruppieren)
                # Community: hoist=False (keine Gruppierung)
                hoist_setting = True if current_team_type == "Competitive" else False
                await team_role_obj.edit(hoist=hoist_setting, reason=f"MMHelfer: Team-Typ zu {current_team_type} geändert")
                await asyncio.sleep(DEFAULT_API_DELAY)
                
                old_generic_role = await self._get_generic_team_type_role(guild, original_team_type)
                new_generic_role = await self._get_generic_team_type_role(guild, current_team_type)

                if not new_generic_role:
                    log.error(f"Generische Rolle für neuen Typ '{current_team_type}' nicht konfiguriert. Typänderung kann nicht vollständig durchgeführt werden für Rollen.")
                else: # Rollen für alle Mitglieder anpassen
                    for m_id in working_data.get("member_ids", []):
                        member_obj = guild.get_member(m_id)
                        if member_obj:
                            if old_generic_role and old_generic_role in member_obj.roles:
                                await member_obj.remove_roles(old_generic_role, reason="MMHelfer: Team-Typ geändert"); await asyncio.sleep(DEFAULT_API_DELAY/3)
                            if new_generic_role not in member_obj.roles: # new_generic_role wurde oben geprüft
                                await member_obj.add_roles(new_generic_role, reason="MMHelfer: Team-Typ geändert"); await asyncio.sleep(DEFAULT_API_DELAY/3)
                
                # Kanäle in neue Kategorie verschieben
                new_category_id = self.main_config.get(f"{current_team_type.lower()}_category_id")
                new_category = guild.get_channel(int(new_category_id)) if new_category_id and str(new_category_id).isdigit() else None
                if isinstance(new_category, CategoryChannel):
                    for ch_key_move in ["text_channel_id", "voice_channel_id"]: # Private VC wird speziell behandelt
                        ch_id_move = working_data.get(ch_key_move)
                        if ch_id_move:
                            channel_to_move = guild.get_channel(ch_id_move)
                            if channel_to_move: 
                                await channel_to_move.edit(category=new_category, reason="MMHelfer: Team-Typ geändert"); await asyncio.sleep(DEFAULT_API_DELAY)
                else:
                    log.warning(f"Neue Kategorie für Typ '{current_team_type}' (ID: {new_category_id}) nicht gefunden. Kanäle nicht verschoben.")

                # Privaten Voice-Channel behandeln
                private_vc_id = working_data.get("private_voice_channel_id")
                if current_team_type == "Competitive" and not private_vc_id: # Von Community zu Comp, erstelle privaten VC
                    if isinstance(new_category, CategoryChannel): # Nur erstellen, wenn Kategorie gültig ist
                        pv_perms = await self._get_permission_overwrites(guild, team_role_obj, "Competitive", "private_voice_competitive")
                        pv_name = f"🔒 {current_team_name}"[:100]  # Behalte Leerzeichen für private Voice-Channels
                        created_private_vc = await guild.create_voice_channel(name=pv_name, category=new_category, overwrites=pv_perms, reason="MMHelfer: Team-Typ zu Competitive geändert")
                        working_data["private_voice_channel_id"] = created_private_vc.id; await asyncio.sleep(DEFAULT_API_DELAY)
                    else: log.warning(f"Konnte privaten VC für Competitive Team '{current_team_name}' nicht erstellen, da Zielkategorie ungültig.")
                elif current_team_type == "Community" and private_vc_id: # Von Comp zu Community, lösche privaten VC
                    private_vc_to_delete = guild.get_channel(private_vc_id)
                    if private_vc_to_delete: 
                        await private_vc_to_delete.delete(reason="MMHelfer: Team-Typ zu Community geändert"); await asyncio.sleep(DEFAULT_API_DELAY)
                    working_data["private_voice_channel_id"] = None # ID aus Config entfernen
                elif current_team_type == "Competitive" and private_vc_id and isinstance(new_category, CategoryChannel): # War schon Comp und bleibt Comp, nur Kategorie moven
                    private_vc_to_move = guild.get_channel(private_vc_id)
                    if private_vc_to_move and private_vc_to_move.category != new_category:
                         await private_vc_to_move.edit(category=new_category, reason="MMHelfer: Team-Typ (Kategorie) geändert"); await asyncio.sleep(DEFAULT_API_DELAY)


            # 3. Captain ändern
            if "captain" in changes: # Enthält neue captain_id oder None
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await handle_rate_limit(
                    lambda: edit_method(content=f"Team '{current_team_name}': Aktualisiere Captain..."),
                    "MMHelferCog.execute_team_edit.edit_progress_captain"
                ); 
                await asyncio.sleep(DEFAULT_API_DELAY*.2)
                new_captain_id = changes["captain"] # Kann None sein
                old_captain_id = original_team_data.get("captain_id") # Der Captain vor dieser Änderung
                captain_role_obj = await self._get_captain_role(guild)

                if captain_role_obj:
                    # Alte Captain-Rolle entfernen, wenn nötig
                    if old_captain_id and old_captain_id != new_captain_id:
                        old_captain_member = guild.get_member(old_captain_id)
                        if old_captain_member and captain_role_obj in old_captain_member.roles:
                            await old_captain_member.remove_roles(captain_role_obj, reason="MMHelfer: Captain-Wechsel"); await asyncio.sleep(DEFAULT_API_DELAY/3)
                    
                    # Neue Captain-Rolle hinzufügen, wenn nötig
                    if new_captain_id:
                        new_captain_member = guild.get_member(new_captain_id)
                        if new_captain_member and captain_role_obj not in new_captain_member.roles:
                            await new_captain_member.add_roles(captain_role_obj, reason="MMHelfer: Neuer Captain"); await asyncio.sleep(DEFAULT_API_DELAY/3)
                            # Sicherstellen, dass der neue Captain auch in member_ids ist (falls nicht schon durch add_members)
                            if new_captain_id not in working_data.get("member_ids", []):
                                working_data.setdefault("member_ids", []).append(new_captain_id)
                                # Und dem neuen Captain ggf. Team- und Typ-Rollen geben, falls er ganz neu ist
                                roles_for_new_capt = [r for r in [team_role_obj, await self._get_generic_team_type_role(guild, current_team_type)] if r and r not in new_captain_member.roles]
                                if roles_for_new_capt: await new_captain_member.add_roles(*roles_for_new_capt, reason="MMHelfer: Neuer Captain dem Team hinzugefügt")

                working_data["captain_id"] = new_captain_id # Aktualisiere in working_data

            # 4. Mitglieder hinzufügen
            if "add_members" in changes:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await handle_rate_limit(
                    lambda: edit_method(content=f"Team '{current_team_name}': Füge Mitglieder hinzu..."),
                    "MMHelferCog.execute_team_edit.edit_progress_add_members"
                ); 
                await asyncio.sleep(DEFAULT_API_DELAY*.2)
                generic_role_for_adds = await self._get_generic_team_type_role(guild, current_team_type) # Rolle des aktuellen Team-Typs
                roles_for_newly_added = [r for r in [team_role_obj, generic_role_for_adds] if r]
                teamsuche_role_strip = await self._get_teamsuche_role(guild)
                
                current_member_ids_in_wd = working_data.setdefault("member_ids", [])
                for member_id_to_add in changes["add_members"]:
                    if member_id_to_add not in current_member_ids_in_wd: # Nur wenn wirklich neu
                        member_to_add_obj = guild.get_member(member_id_to_add)
                        if member_to_add_obj:
                            roles_to_assign = [r for r in roles_for_newly_added if r not in member_to_add_obj.roles]
                            if roles_to_assign:
                                await member_to_add_obj.add_roles(*roles_to_assign, reason="MMHelfer: Zum Team hinzugefügt (Edit)"); await asyncio.sleep(DEFAULT_API_DELAY/3)
                            if teamsuche_role_strip and teamsuche_role_strip in member_to_add_obj.roles:
                                await member_to_add_obj.remove_roles(teamsuche_role_strip, reason="MMHelfer: Team beigetreten (Edit)"); await asyncio.sleep(DEFAULT_API_DELAY/3)
                                # Auch aus der LFT-Liste entfernen
                                if member_to_add_obj.id in self.lft_members:
                                    self.lft_members = [mid for mid in self.lft_members if mid != member_to_add_obj.id]
                                    save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": self.lft_members})
                            current_member_ids_in_wd.append(member_id_to_add)
            
            # 5. Mitglieder entfernen
            if "remove_members" in changes:
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await handle_rate_limit(
                    lambda: edit_method(content=f"Team '{current_team_name}': Entferne Mitglieder..."),
                    "MMHelferCog.execute_team_edit.edit_progress_remove_members"
                ); 
                await asyncio.sleep(DEFAULT_API_DELAY*.2)
                captain_role_obj_strip = await self._get_captain_role(guild)
                # Die zu entfernende generische Rolle ist die des aktuellen Team-Typs (current_team_type),
                # da eine eventuelle Typ-Änderung bereits vorher stattgefunden hat und die Mitglieder diese Rolle hätten.
                generic_role_for_removes = await self._get_generic_team_type_role(guild, current_team_type)
                
                roles_to_strip_from_removed = [r for r in [team_role_obj, generic_role_for_removes] if r]
                current_member_ids_in_wd = working_data.setdefault("member_ids", [])

                for member_id_to_remove in changes["remove_members"]:
                    if member_id_to_remove in current_member_ids_in_wd: # Nur wenn im Team laut working_data
                        member_to_remove_obj = guild.get_member(member_id_to_remove)
                        if member_to_remove_obj:
                            actual_roles_to_strip = list(roles_to_strip_from_removed) # Kopie
                            # Wenn der zu Entfernende der Captain ist, auch Captain-Rolle entfernen
                            if member_id_to_remove == working_data.get("captain_id") and captain_role_obj_strip and captain_role_obj_strip not in actual_roles_to_strip:
                                actual_roles_to_strip.append(captain_role_obj_strip)
                            
                            final_strip_list = [r for r in actual_roles_to_strip if r and r in member_to_remove_obj.roles]
                            if final_strip_list:
                                try:
                                    await member_to_remove_obj.remove_roles(*final_strip_list, reason="MMHelfer: Aus Team entfernt (Edit)")
                                    await asyncio.sleep(DEFAULT_API_DELAY / 2)
                                except Exception as e_rem_r_edit:
                                    log.error(f"Fehler Entfernen Rollen von {member_to_remove_obj.name} (Edit): {e_rem_r_edit}")
                        
                        current_member_ids_in_wd.remove(member_id_to_remove)
                        if member_id_to_remove == working_data.get("captain_id"): # Wenn Captain entfernt wurde
                            working_data["captain_id"] = None # Captain-ID in working_data zurücksetzen
            
            # 6. Config speichern
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"Team '{current_team_name}': Speichere Konfiguration..."),
                "MMHelferCog.execute_team_edit.edit_progress_save_config"
            ); 
            await asyncio.sleep(DEFAULT_API_DELAY*.2)
            
            # Wenn sich Name oder Typ geändert haben, muss der alte Eintrag gelöscht und ein neuer angelegt werden
            if original_team_name != current_team_name or original_team_type != current_team_type:
                old_config_path = COMMUNITY_CONFIG_PATH if original_team_type == "Community" else COMPETITIVE_CONFIG_PATH
                old_config_data = load_json(old_config_path)
                if original_team_name in old_config_data:
                    del old_config_data[original_team_name]
                    save_json(old_config_path, old_config_data)
            
            # Neuen/aktualisierten Eintrag speichern
            new_config_path = COMMUNITY_CONFIG_PATH if current_team_type == "Community" else COMPETITIVE_CONFIG_PATH
            new_config_data = load_json(new_config_path) # Lade die aktuelle Config, um sie zu erweitern
            working_data.pop("guild_id", None) # guild_id war nur temporär, nicht in JSON speichern
            new_config_data[current_team_name] = working_data # Füge das (ggf. umbenannte) Team hinzu/überschreibe es
            save_json(new_config_path, new_config_data)

            # Instanzvariablen der Cog aktualisieren, damit sie den neuesten Stand widerspiegeln
            self.community_teams = load_json(COMMUNITY_CONFIG_PATH)
            self.competitive_teams = load_json(COMPETITIVE_CONFIG_PATH)
            
            # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
            await handle_rate_limit(
                lambda: edit_method(content=f"✅ Team '{original_team_name}' (Typ: {original_team_type}) erfolgreich zu '{current_team_name}' (Typ: {current_team_type}) bearbeitet!"),
                "MMHelferCog.execute_team_edit.edit_success"
            )
            log.info(f"Team '{original_team_name}' ({original_team_type}) zu '{current_team_name}' ({current_team_type}) bearbeitet von {original_button_interaction.user.name}.")
            return True

        except Exception as e_fatal_edit:
            log.exception(f"Fataler Fehler beim Bearbeiten von Team '{original_team_name}': {e_fatal_edit}")
            try: 
                # Verwende die neue Hilfsfunktion für Rate-Limit-Handling
                await handle_rate_limit(
                    lambda: edit_method(content=f"❌ Ein schwerwiegender Fehler ist bei der Bearbeitung aufgetreten. Bitte prüfe die Logs!"),
                    "MMHelferCog.execute_team_edit.edit_error_fatal"
                )
            except: pass
            return False

async def setup(bot: commands.Bot):
    MODULE_DATA_ROOT.mkdir(exist_ok=True); TEAMS_DIR.mkdir(exist_ok=True)
    # Standard-Hauptkonfiguration, falls die Datei nicht existiert oder leer ist
    default_main_cfg = {
        "roles": { # Hier MÜSSEN die IDs der generischen Team-Typ-Rollen rein!
            "Community Team": None, # Beispiel: "123456789012345678"
            "Competitive Team": None, # Beispiel: "098765432109876543"
            # Weitere Rollen, die in _get_permission_overwrites verwendet werden könnten
            "Discord Admin": None,
            "Member Management": None,
            "Co-Lead": None,
            "Coach": None,
            "Caster": None,
            "Co-Caster": None,
            "Ehrenmember": None,
            "Mafia": None,
            "Rocket League": None # Beispiel für eine Rolle mit speziellen Rechten (z.B. no connect)
        },
        "additional_roles": { # Für spezielle Kanal-Overrides, Key ist der channel_type_detail
             # "text": ["role_id_1", "role_id_2"],
             # "voice_community": ["role_id_3"]
        },
        "community_category_id": None, # ID der Kategorie für Community-Teams
        "competitive_category_id": None, # ID der Kategorie für Competitive-Teams
        "teamsuche_role_id": None, # ID der Teamsuche-Rolle
        "clan_member_role_id": None, # ID der Clanmember-Rolle
        "captain_role_id": None # ID der globalen Captain-Rolle
    }
    # Standard-Berechtigungskonfiguration
    default_permissions_cfg = {
        "TeamCreateView": {
            "set_name_button": [],
            "set_members_button": [],
            "set_captain_button": [],
            "cancel_button": [],
            "create_button": []
        },
        "ConfirmDeleteView": {
            "confirm_button": [],
            "cancel_button": []
        },
        "TeamEditView": {
            "change_name_button": [],
            "change_captain_button": [],
            "add_member_button": [],
            "remove_member_button": [],
            "change_type_button": [],
            "confirm_changes_button": [],
            "cancel_edit_button": []
        },
        "TeamsucheView": {
            "add_lft_button": [],
            "remove_lft_button": [],
            "show_lft_list_button": [],
            "close_menu_button": []
        },
        "ClanmemberView": {
            "add_clanmember_button": [],
            "remove_clanmember_button": [],
            "show_clanmember_list_button": [],
            "close_menu_button": []
        },
        "MainMenuView": {
            "create_team_button": [],
            "edit_team_button": [],
            "delete_team_button": [],
            "teamsuche_button": [],
            "clanmember_button": [],
            "close_menu_button": []
        },
        "SlashCommands": {
            "menu": []
        }
    }
    
    # Sicherstellen, dass alle Konfigurationsdateien existieren
    cfg_files = { 
        MAIN_CONFIG_PATH: default_main_cfg, 
        PERMISSIONS_CONFIG_PATH: default_permissions_cfg,
        COMPETITIVE_CONFIG_PATH: {}, 
        COMMUNITY_CONFIG_PATH: {},
        TEAMSUCHE_CONFIG_PATH: {"lft_member_ids": []}, 
        CLANMEMBER_CONFIG_PATH: {"clan_member_ids": []} 
    }
    for path, default_content in cfg_files.items():
        if not path.exists() or (path.stat().st_size == 0 and default_content is not None): # Nur erstellen/überschreiben wenn leer und default da
            log.info(f"Konfigurationsdatei {path.name} nicht vorhanden oder leer. Erstelle mit Standardinhalt.")
            save_json(path, default_content)
    
    cog_module = MMHelferCog(bot)
    await bot.add_cog(cog_module)
    log.info(f"{cog_module.qualified_name} wurde erfolgreich geladen und initialisiert.")