# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from discord import app_commands, Interaction, ui, ButtonStyle, SelectOption, TextStyle, PermissionOverwrite, Colour, Object, Role, CategoryChannel, TextChannel, VoiceChannel, Member
from discord.utils import get
import json
import os
from pathlib import Path
import logging
from typing import List, Dict, Optional, Union, Any, Tuple, cast, Callable, Coroutine
import asyncio
import copy

# --- Globale Konstanten ---
DEFAULT_API_DELAY = 0.6  # Sekunden für Delays nach Discord API Aktionen

# --- Konfiguration und Pfade ---
COG_FILE_DIR = Path(__file__).parent
CONFIG_SUBFOLDER_NAME = "mmhelfer_module"
MODULE_DATA_ROOT = COG_FILE_DIR / CONFIG_SUBFOLDER_NAME

CONFIG_DIR = MODULE_DATA_ROOT
TEAMS_DIR = MODULE_DATA_ROOT / "teams" # Name des Unterordners für Team-Configs (kleingeschrieben)
LOGO_PATH = MODULE_DATA_ROOT / "Logo.png"

MAIN_CONFIG_PATH = CONFIG_DIR / "mmhelfer_main_config.json"
COMPETITIVE_CONFIG_PATH = TEAMS_DIR / "mmhelfer_competitive_config.json"
COMMUNITY_CONFIG_PATH = TEAMS_DIR / "mmhelfer_community_config.json"
TEAMSUCHE_CONFIG_PATH = TEAMS_DIR / "mmhelfer_teamsuche_config.json"
CLANMEMBER_CONFIG_PATH = TEAMS_DIR / "mmhelfer_clanmember_config.json"

MODULE_DATA_ROOT.mkdir(exist_ok=True)
TEAMS_DIR.mkdir(exist_ok=True)
log = logging.getLogger(__name__)

# --- Hilfsfunktionen für JSON Handling ---
def load_json(filepath: Path) -> Dict[str, Any]:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            return json.loads(content) if content else {}
    except FileNotFoundError:
        log.warning(f"Konfigurationsdatei nicht gefunden: {filepath}. Leere Datei/Struktur wird erstellt.")
        if filepath == TEAMSUCHE_CONFIG_PATH:
            save_json(filepath, {"lft_member_ids": []})
            return {"lft_member_ids": []}
        elif filepath == CLANMEMBER_CONFIG_PATH:
            save_json(filepath, {"clan_member_ids": []})
            return {"clan_member_ids": []}
        else:
            save_json(filepath, {})
            return {}
    except json.JSONDecodeError:
        log.exception(f"Fehler beim Lesen der JSON-Datei: {filepath}.")
        return {}

def save_json(filepath: Path, data: Dict[str, Any]):
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except IOError:
        log.exception(f"Fehler beim Schreiben der JSON-Datei: {filepath}")
    except Exception as e:
        log.exception(f"Unerwarteter Fehler beim Speichern von JSON nach {filepath}: {e}")

# --- Modals ---
class TeamNameModal(ui.Modal):
    name_input = ui.TextInput(label='Team Name', placeholder='Gib den Teamnamen ein', required=True, style=TextStyle.short, max_length=100)
    def __init__(self, title: str, submit_callback: Callable[[Interaction, str], Coroutine[Any, Any, None]], current_name: Optional[str] = ""):
        super().__init__(title=title, timeout=300)
        self.submit_callback = submit_callback
        self.name_input.default = current_name
        if "Ändern" in title or "Neuer" in title:
            self.name_input.label = "Neuer Team Name"
        else:
            self.name_input.label = "Team Name"

    async def on_submit(self, interaction: Interaction):
        await self.submit_callback(interaction, self.name_input.value)

    async def on_error(self, interaction: Interaction, error: Exception):
        log.error(f"Fehler im TeamNameModal (Titel: {self.title}): {error}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Ein interner Fehler im Namens-Modal ist aufgetreten.", ephemeral=True)
            else:
                await interaction.followup.send("Ein interner Fehler im Namens-Modal ist aufgetreten.", ephemeral=True)
        except discord.HTTPException:
            log.error("Konnte Fehlermeldung für TeamNameModal nicht senden.")


# --- Views ---
class TeamCreateView(ui.View):
    def __init__(self, author_id: int, main_config: Dict, cog_instance: 'MMHelferCog'):
        super().__init__(timeout=600)
        self.author_id = author_id; self.main_config = main_config; self.cog_instance = cog_instance
        self.team_name: Optional[str] = None; self.members: List[Member] = []; self.captain: Optional[Member] = None
        self.team_type: Optional[str] = None
        self.add_item(self.TeamTypeSelect(self))

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur Befehlsaufrufer.", ephemeral=True); return False
        return True

    async def _update_original_message(self, interaction: Interaction):
        embed = self._build_embed(is_ephemeral=True)
        kwargs: Dict[str, Any] = {"embed": embed, "view": self, "attachments": []}
        
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(**kwargs)
            else:
                await interaction.edit_original_response(**kwargs)
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning(f"Fehler _update_original_message (TeamCreateView): {e}")
        except Exception as e:
            log.exception(f"Unerwarteter Fehler _update_original_message (TeamCreateView): {e}")

    def _build_embed(self, is_ephemeral: bool = False) -> discord.Embed:
        embed=discord.Embed(title="***Team Erstellung***", color=discord.Colour.blue())
        embed.set_author(name="⚙️ MMHelfer / ➕ Team Erstellung")
        if not is_ephemeral and LOGO_PATH.exists():
            embed.set_thumbnail(url=f"attachment://{LOGO_PATH.name}")
        
        name_v=self.team_name if self.team_name else "*Teamname*"
        mem_v=", ".join([m.mention for m in self.members]) if self.members else "*Optional*"
        cap_v=self.captain.mention if self.captain else "*Optional*"
        type_v=self.team_type if self.team_type else "*Community/Competitive*"
        embed.add_field(name="🏷️ Name des Team's", value=f"```\n{name_v}\n```", inline=False)
        embed.add_field(name="👥 Wähle die Mitglieder des Team's aus", value=f"{mem_v} (Max: 7)", inline=False)
        embed.add_field(name="👑 Wähle den Captain des Team's", value=f"{cap_v}", inline=False)
        embed.add_field(name="🔀 Wähle die Art des Team's", value=f"{type_v}", inline=False)
        embed.add_field(name="---", value="Drücke 'Erstellen'.", inline=False)
        return embed

    @ui.button(label="Team Name", style=ButtonStyle.primary, emoji="🏷️", row=0)
    async def set_name_button(self, interaction: Interaction, button: ui.Button):
        modal = TeamNameModal(title="Team Name Eingeben", submit_callback=self.process_name_input, current_name=self.team_name or "")
        await interaction.response.send_modal(modal)

    async def process_name_input(self, interaction: Interaction, name: str):
        self.team_name = name.strip()
        log.info(f"Name (CreateView): {self.team_name} von {interaction.user}")
        await self._update_original_message(interaction)

    @ui.button(label="Mitglieder", style=ButtonStyle.secondary, emoji="👥", row=0)
    async def set_members_button(self, interaction: Interaction, button: ui.Button):
        view = ui.View(timeout=180)
        sel = ui.UserSelect(placeholder="Wähle Mitglieder (max 7)", min_values=0, max_values=7)
        async def cb(inner_interaction: Interaction):
            self.members = sel.values
            log.info(f"Mitglieder: {[m.name for m in self.members]} von {inner_interaction.user}")
            await self._update_original_message(inner_interaction)
            view.stop()
        sel.callback=cb
        view.add_item(sel)
        await interaction.response.edit_message(content="Mitglieder:", embed=None, view=view, attachments=[])

    @ui.button(label="Captain", style=ButtonStyle.secondary, emoji="👑", row=0)
    async def set_captain_button(self, interaction: Interaction, button: ui.Button):
        view = ui.View(timeout=180)
        sel = ui.UserSelect(placeholder="Wähle Captain", min_values=0, max_values=1)
        async def cb(inner_interaction: Interaction):
            self.captain = sel.values[0] if sel.values else None
            log.info(f"Captain: {self.captain.name if self.captain else 'Keiner'} von {inner_interaction.user}")
            await self._update_original_message(inner_interaction)
            view.stop()
        sel.callback=cb
        view.add_item(sel)
        await interaction.response.edit_message(content="Captain:", embed=None, view=view, attachments=[])

    @ui.button(label="Abbruch", style=ButtonStyle.danger, emoji="❌", row=1)
    async def cancel_button(self, interaction: Interaction, button: ui.Button):
        log.info(f"Erstellung abgebrochen: {interaction.user}")
        self.stop()
        try:
            await interaction.response.edit_message(content="Abgebrochen.", view=None, embed=None, attachments=[])
        except (discord.NotFound, discord.HTTPException):
            pass

    @ui.button(label="Erstellen", style=ButtonStyle.success, emoji="✅", row=1)
    async def create_button(self, interaction: Interaction, button: ui.Button):
        async def send_ephemeral_error(msg: str):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(msg, ephemeral=True)
                else:
                    await interaction.followup.send(msg, ephemeral=True)
            except discord.HTTPException:
                log.error("Konnte ephemere Fehlermeldung nicht senden.")

        if not self.team_name:
            await send_ephemeral_error("Name fehlt.")
            return
        if not self.team_type:
            await send_ephemeral_error("Typ fehlt.")
            return
        if self.captain and self.members and self.captain not in self.members:
            self.members.append(self.captain)
        if len(self.members) > 7:
            await send_ephemeral_error("Max 7 Mitglieder.")
            if self.captain in self.members: 
                self.members.remove(self.captain)
            return

        for item in self.children:
            if isinstance(item, (ui.Button, ui.Select)):
                item.disabled=True
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=self, attachments=[])
            else:
                await interaction.edit_original_response(view=self, attachments=[])
        except discord.HTTPException as e:
            log.error(f"Fehler Button-Deaktivierung: {e}")

        log.info(f"Starte Erstellung '{self.team_name}' ({self.team_type}) von {interaction.user}")
        await self.cog_instance.execute_team_creation(interaction, self.team_name, self.team_type, self.members, self.captain, use_followup=True)
        self.stop()

    class TeamTypeSelect(ui.Select):
        def __init__(self, parent_view: 'TeamCreateView'):
            self.parent_view=parent_view
            opts=[SelectOption(label="Community", value="Community", emoji="🌐"), SelectOption(label="Competitive", value="Competitive", emoji="🏆")]
            super().__init__(placeholder="Wähle Team-Typ...", min_values=1, max_values=1, options=opts, row=2)
        async def callback(self, interaction: Interaction):
            self.parent_view.team_type = self.values[0]
            log.info(f"Typ: {self.parent_view.team_type} von {interaction.user}")
            await self.parent_view._update_original_message(interaction)

class ConfirmDeleteView(ui.View):
    def __init__(self, author_id: int, team_name: str, team_type: str, cog_instance: 'MMHelferCog'):
        super().__init__(timeout=180)
        self.author_id=author_id; self.team_name=team_name; self.team_type=team_type
        self.cog_instance=cog_instance; self.confirmed=False
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur Initiator.", ephemeral=True); return False
        return True
    @ui.button(label="Endgültig Löschen", style=ButtonStyle.danger, emoji="🗑️")
    async def confirm_button(self, interaction: Interaction, button: ui.Button):
        self.confirmed = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"Löschung '{self.team_name}' läuft...", view=self, embed=None, attachments=[])
        await self.cog_instance.execute_team_deletion(interaction, self.team_name, self.team_type)
        self.stop()
    @ui.button(label="Abbrechen", style=ButtonStyle.secondary, emoji="❌")
    async def cancel_button(self, interaction: Interaction, button: ui.Button):
        self.confirmed = False
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"Löschung '{self.team_name}' abgebrochen.", view=None, embed=None, attachments=[])
        self.stop()

class TeamDeleteSelect(ui.Select):
    def __init__(self, teams: Dict[str, str], cog_instance: 'MMHelferCog'):
        self.cog_instance = cog_instance; options = []
        for dn, idata in teams.items():
            options.append(SelectOption(label=dn[:100], value=idata))
        if not options:
            options.append(SelectOption(label="Keine Teams", value="nothing"))
        super().__init__(placeholder="Wähle zu löschendes Team...", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: Interaction):
        selected_value = self.values[0]
        if selected_value == "nothing":
            await interaction.response.edit_message(content="Kein Team gewählt.", view=None, attachments=[]); return
        try:
            team_type, team_name = selected_value.split(":", 1)
        except ValueError:
            log.error(f"Ungültiger interner Wert im TeamDeleteSelect: {selected_value}")
            await interaction.response.edit_message(content="Interner Fehler.", view=None, attachments=[]); return
        confirm_view = ConfirmDeleteView(interaction.user.id, team_name, team_type, self.cog_instance)
        embed = discord.Embed(title=f"🚨 Löschung Bestätigen: {team_name} ({team_type})",
                              description=f"**Bist du sicher, dass du das Team '{team_name}' endgültig löschen möchtest?**\n\n"
                                          f"Dies entfernt:\n- Die Team-Rolle '{team_name}'\n- Alle zugehörigen Text- und Voice-Kanäle\n"
                                          f"- Die Rollen (Teamrolle, {team_type}-Rolle, Captain) von allen Mitgliedern.\n\n"
                                          f"⚠️ **Dieser Vorgang kann nicht rückgängig gemacht werden!**",
                              color=discord.Color.red())
        await interaction.response.edit_message(content=None, embed=embed, view=confirm_view, attachments=[])

class TeamEditSelect(ui.Select):
    def __init__(self, teams: Dict[str, str], cog_instance: 'MMHelferCog'):
        self.cog_instance = cog_instance; options = []
        for dn, idata in teams.items():
            options.append(SelectOption(label=dn[:100], value=idata))
        if not options:
            options.append(SelectOption(label="Keine Teams zum Bearbeiten", value="nothing"))
        super().__init__(placeholder="Wähle das zu bearbeitende Team...", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: Interaction):
        selected_value = self.values[0]
        if selected_value == "nothing":
            await interaction.response.edit_message(content="Kein Team ausgewählt.", view=None, attachments=[]); return
        try:
            team_type, team_name = selected_value.split(":", 1)
        except ValueError:
            log.error(f"Fehler TeamEditSelect Value: {selected_value}")
            await interaction.response.edit_message(content="Interner Fehler.", view=None, attachments=[]); return
        original_team_data = self.cog_instance.get_team_data(team_name, team_type, interaction.guild_id if interaction.guild else None)
        if not original_team_data:
            await interaction.response.edit_message(content=f"Team '{team_name}' ({team_type}) nicht gefunden.", view=None, attachments=[]); return
        edit_view = TeamEditView(interaction.user.id, team_name, team_type, original_team_data, self.cog_instance)
        embed = edit_view._build_embed(is_ephemeral=True)
        kwargs: Dict[str, Any] = {"content": f"Bearbeite Team: **{team_name}** ({team_type})", "embed": embed, "view": edit_view, "attachments": []}
        await interaction.response.edit_message(**kwargs)

class TeamEditView(ui.View):
    def __init__(self, author_id: int, original_team_name: str, original_team_type: str, original_team_data: Dict[str, Any], cog_instance: 'MMHelferCog'):
        super().__init__(timeout=900)
        self.author_id = author_id; self.cog_instance = cog_instance
        self.original_team_name = original_team_name; self.original_team_type = original_team_type
        self.original_team_data = copy.deepcopy(original_team_data)
        self.current_team_name = original_team_name; self.current_team_type = original_team_type
        self.current_captain_id: Optional[int] = original_team_data.get("captain_id")
        self.current_member_ids: List[int] = list(original_team_data.get("member_ids", []))
        self.changes_made: Dict[str, Any] = {}
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id: await interaction.response.send_message("Nur Initiator.", ephemeral=True); return False
        return True
    async def _update_edit_message(self, interaction: Interaction, content: Optional[str] = None):
        embed = self._build_embed(is_ephemeral=True)
        kwargs:Dict[str, Any] = {"embed": embed, "view": self, "attachments": []};
        if content: kwargs["content"] = content
        try:
            if not interaction.response.is_done(): await interaction.response.edit_message(**kwargs)
            else: await interaction.edit_original_response(**kwargs)
        except (discord.NotFound, discord.HTTPException) as e: log.warning(f"Fehler _update_edit_message: {e}")
    def _build_embed(self, is_ephemeral: bool = False) -> discord.Embed:
        embed = discord.Embed(title=f"🔧 Team Bearbeiten: {self.original_team_name}", description=f"Aktueller Name: **{self.current_team_name}**\nAktueller Typ: **{self.current_team_type}**", color=discord.Colour(0x57F2E2))
        embed.set_author(name="⚙️ MMHelfer Module / 🔧 Team Edit")
        if not is_ephemeral and LOGO_PATH.exists(): embed.set_thumbnail(url=f"attachment://{LOGO_PATH.name}")
        embed.add_field(name="🏷️ Namen ändern", value=f"Aktuell: `{self.current_team_name}`", inline=False)
        cap_mention = f"<@{self.current_captain_id}>" if self.current_captain_id else "Kein Captain"
        embed.add_field(name="👑 Captain ändern", value=f"Aktuell: {cap_mention}", inline=False)
        guild_id = self.original_team_data.get("guild_id")
        members_value = f"{len(self.current_member_ids)} Mitglieder (Details nicht ladbar)"
        if guild_id and (guild := self.cog_instance.bot.get_guild(guild_id)):
            member_mentions = [f"<@{mid}>" for mid in self.current_member_ids]
            members_value = ", ".join(member_mentions) if member_mentions else "Keine Mitglieder"
        embed.add_field(name="👥 Mitglieder", value=f"Aktuell ({len(self.current_member_ids)}): {members_value}", inline=False)
        embed.add_field(name="📈📉 Typ ändern", value=f"Aktuell: `{self.current_team_type}`", inline=False)
        embed.add_field(name="---", value="Aktion wählen oder bestätigen.", inline=False)
        return embed
    @ui.button(label="Name ändern", style=ButtonStyle.primary, emoji="🏷️", row=0)
    async def change_name_button(self, interaction: Interaction, button: ui.Button):
        modal = TeamNameModal(title="Team Namen Ändern", submit_callback=self.process_new_name, current_name=self.current_team_name)
        await interaction.response.send_modal(modal)
    async def process_new_name(self, interaction: Interaction, new_name: str):
        new_name = new_name.strip()
        if new_name == self.original_team_name: self.changes_made.pop("name", None); self.current_team_name = self.original_team_name
        elif new_name: self.changes_made["name"] = new_name; self.current_team_name = new_name
        log.info(f"Name-Änderung für '{self.original_team_name}' zu '{self.current_team_name}' vorgemerkt.")
        await self._update_edit_message(interaction)
    @ui.button(label="Captain ändern", style=ButtonStyle.secondary, emoji="👑", row=0)
    async def change_captain_button(self, interaction: Interaction, button: ui.Button):
        view = ui.View(timeout=180); sel = ui.UserSelect(placeholder="Wähle neuen Captain...", min_values=0, max_values=1)
        async def cb(inner_interaction: Interaction):
            if sel.values: new_cap = sel.values[0]; self.changes_made["captain"] = new_cap.id; self.current_captain_id = new_cap.id; log.info(f"Captain für '{self.original_team_name}' zu '{new_cap.display_name}' vorgemerkt.")
            else: self.changes_made["captain"] = None; self.current_captain_id = None; log.info(f"Captain-Entfernung für '{self.original_team_name}' vorgemerkt.")
            await self._update_edit_message(inner_interaction); view.stop()
        sel.callback=cb; view.add_item(sel); await interaction.response.edit_message(content="Neuen Captain wählen oder Auswahl leeren zum Entfernen.", embed=None, view=view, attachments=[])
    @ui.button(label="Mitglied hinzufügen", style=ButtonStyle.success, emoji="➕", row=1)
    async def add_member_button(self, interaction: Interaction, button: ui.Button):
        if len(self.current_member_ids) >= 7: await interaction.response.send_message("Max. Mitgliederzahl (7) erreicht.", ephemeral=True); return
        max_add = 7 - len(self.current_member_ids);
        if max_add <=0: await interaction.response.send_message("Max. Mitgliederzahl erreicht.", ephemeral=True); return
        view = ui.View(timeout=180); sel = ui.UserSelect(placeholder=f"Füge bis zu {max_add} Mitglieder hinzu...", min_values=0, max_values=max_add)
        async def cb(inner_interaction: Interaction):
            added = self.changes_made.get("add_members", []); new_sel_ids = []
            for m in sel.values:
                if m.id not in self.current_member_ids and m.id not in added: added.append(m.id); self.current_member_ids.append(m.id); new_sel_ids.append(m.id)
            if new_sel_ids: self.changes_made["add_members"] = added; log.info(f"Hinzufügen zu '{self.original_team_name}' vorgemerkt: {new_sel_ids}")
            await self._update_edit_message(inner_interaction); view.stop()
        sel.callback=cb; view.add_item(sel); await interaction.response.edit_message(content="Mitglieder zum Hinzufügen auswählen:", embed=None, view=view, attachments=[])
    @ui.button(label="Mitglied entfernen", style=ButtonStyle.danger, emoji="➖", row=1)
    async def remove_member_button(self, interaction: Interaction, button: ui.Button):
        if not self.current_member_ids: await interaction.response.send_message("Keine Mitglieder zum Entfernen.", ephemeral=True); return
        view = ui.View(timeout=180); opts = []
        if guild := interaction.guild:
            for mid in self.current_member_ids:
                if m := guild.get_member(mid): opts.append(discord.SelectOption(label=m.display_name, value=str(m.id), emoji="👤"))
        if not opts: await interaction.response.send_message("Keine entfernbaren Mitglieder gefunden (oder Cache-Problem).", ephemeral=True); return
        sel = ui.Select(placeholder="Mitglieder zum Entfernen...", min_values=1, max_values=len(opts), options=opts)
        async def cb(inner_interaction: Interaction):
            removed_pending = self.changes_made.get("remove_members", []); new_rem_log = []
            for sid_str in sel.values:
                sid = int(sid_str)
                if sid in self.current_member_ids: self.current_member_ids.remove(sid)
                if sid not in removed_pending: removed_pending.append(sid); new_rem_log.append(sid)
                if self.current_captain_id == sid: self.changes_made["captain"] = None; self.current_captain_id = None; log.info(f"Captain {sid} durch Entfernung entfernt.")
            if new_rem_log: self.changes_made["remove_members"] = removed_pending; log.info(f"Entfernen aus '{self.original_team_name}' vorgemerkt: {new_rem_log}")
            await self._update_edit_message(inner_interaction); view.stop()
        sel.callback=cb; view.add_item(sel); await interaction.response.edit_message(content="Mitglieder zum Entfernen auswählen:", embed=None, view=view, attachments=[])
    @ui.button(label="Typ ändern", style=ButtonStyle.primary, emoji="🔄", row=2)
    async def change_type_button(self, interaction: Interaction, button: ui.Button):
        view = ui.View(timeout=60); curr_type = self.current_team_type; new_type = "Competitive" if curr_type == "Community" else "Community"; emoji = "🏆" if new_type == "Competitive" else "🌐"
        async def confirm_cb(inner_interaction: Interaction):
            if new_type == self.original_team_type: self.changes_made.pop("type", None)
            else: self.changes_made["type"] = new_type
            self.current_team_type = new_type
            log.info(f"Typ-Änderung für '{self.original_team_name}' zu '{new_type}' vorgemerkt.")
            await self._update_edit_message(inner_interaction); view.stop()
        confirm_btn = ui.Button(label=f"Zu {new_type}", style=ButtonStyle.success, emoji=emoji); confirm_btn.callback = confirm_cb; view.add_item(confirm_btn)
        async def cancel_cb(inner_interaction: Interaction): await self._update_edit_message(inner_interaction); view.stop()
        cancel_btn = ui.Button(label="Abbrechen", style=ButtonStyle.grey); cancel_btn.callback = cancel_cb; view.add_item(cancel_btn)
        await interaction.response.edit_message(content=f"Team von '{curr_type}' zu '{new_type}' ändern?\n**Achtung:** Kanalverschiebung & ggf. neuer/gelöschter privater Kanal.", embed=None, view=view, attachments=[])
    @ui.button(label="Bestätigen", style=ButtonStyle.success, emoji="✅", row=3)
    async def confirm_changes_button(self, interaction: Interaction, button: ui.Button):
        if not self.changes_made: await interaction.response.send_message("Keine Änderungen vorgenommen.", ephemeral=True); return
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="Änderungen werden verarbeitet...", embed=None, view=self, attachments=[])
        log.info(f"Bestätige Änderungen für Team '{self.original_team_name}': {self.changes_made}")
        await self.cog_instance.execute_team_edit(interaction, self.original_team_name, self.original_team_type, self.original_team_data, self.changes_made)
        self.stop()
    @ui.button(label="Abbrechen", style=ButtonStyle.danger, emoji="🚫", row=3)
    async def cancel_edit_button(self, interaction: Interaction, button: ui.Button):
        log.info(f"Bearbeitung von '{self.original_team_name}' abgebrochen.")
        await interaction.response.edit_message(content="Bearbeitung abgebrochen.", embed=None, view=None, attachments=[])
        self.stop()

class TeamsucheView(ui.View):
    def __init__(self, author_id: int, cog_instance: 'MMHelferCog'):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.cog_instance = cog_instance

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur der Befehlsaufrufer kann dieses Menü bedienen.", ephemeral=True)
            return False
        return True

    def _build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="👥 Teamsuche Menü", description="Verwalte hier Mitglieder, die auf Teamsuche sind.", color=discord.Colour(0x23a65a))
        embed.set_author(name="⚙️ MMHelfer / 👥 Teamsuche Menü")
        return embed

    @ui.button(label="Mitglied hinzufügen", style=ButtonStyle.success, emoji="➕", row=0)
    async def add_lft_member_button(self, interaction: Interaction, button: ui.Button):
        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(placeholder="Wähle Mitglieder für Teamsuche...", min_values=1, max_values=25)
        async def select_callback(inner_interaction: Interaction):
            await inner_interaction.response.defer(ephemeral=True, thinking=True)
            added_members, existing_members, failed_members = await self.cog_instance.add_members_to_lft(inner_interaction, user_select.values)
            msg_parts = []
            if added_members: msg_parts.append(f"✅ {len(added_members)} Mitglied(er) zur Teamsuche hinzugefügt und Rolle vergeben.")
            if existing_members: msg_parts.append(f"ℹ️ {len(existing_members)} Mitglied(er) war(en) bereits auf Teamsuche.")
            if failed_members: msg_parts.append(f"❌ {len(failed_members)} Mitglied(er) konnte(n) nicht verarbeitet werden.")
            response_message = "\n".join(msg_parts) if msg_parts else "Keine Änderungen vorgenommen."
            
            try: 
                await inner_interaction.followup.send(response_message, ephemeral=True)
            except discord.HTTPException as e:
                log.error(f"Fehler beim Senden der Followup-Nachricht (add_lft_member_button): {e}")
                if e.status == 429:
                    try: await inner_interaction.followup.send("Viele Aktionen ausgeführt. Discord hat die Antwort blockiert (Rate Limit). Bitte überprüfe die Rollen manuell.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte Rate Limit Fehlermeldung nicht senden (add_lft_member_button): {e_inner}")
                else:
                    try: await inner_interaction.followup.send("Ein Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte HTTP Fehlermeldung nicht senden (add_lft_member_button): {e_inner}")
            except Exception as e:
                log.error(f"Unerwarteter Fehler beim Senden der Followup-Nachricht (add_lft_member_button): {e}")
                try: await inner_interaction.followup.send("Ein unerwarteter Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte unerwartete Fehlermeldung nicht senden (add_lft_member_button): {e_inner}")

            try:
                await interaction.edit_original_response(content=None, embed=self._build_embed(), view=self)
            except discord.HTTPException as e: log.error(f"Fehler Wiederherstellen TeamsucheView nach Hinzufügen: {e}")
            select_view.stop()
        user_select.callback = select_callback
        select_view.add_item(user_select)
        await interaction.response.edit_message(content="Wähle Mitglieder, die zur Teamsuche hinzugefügt werden sollen:", embed=None, view=select_view, attachments=[])

    @ui.button(label="Mitglied entfernen", style=ButtonStyle.danger, emoji="➖", row=0)
    async def remove_lft_member_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Dieser Befehl funktioniert nur auf einem Server.",ephemeral=True); return

        teamsuche_role = await self.cog_instance._get_teamsuche_role(interaction.guild)
        if not teamsuche_role:
             await interaction.response.send_message("Teamsuche-Rolle nicht konfiguriert. Entfernen nicht möglich.", ephemeral=True); return

        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(placeholder="Wähle Mitglieder zum Entfernen...", min_values=1, max_values=25)

        async def select_callback(inner_interaction: Interaction):
            await inner_interaction.response.defer(ephemeral=True, thinking=True)
            selected_members_from_userselect = user_select.values
            valid_members_to_remove = []
            skipped_member_names = []

            if not inner_interaction.guild: 
                await inner_interaction.followup.send("Fehler: Guild-Kontext verloren.", ephemeral=True)
                select_view.stop(); return

            teamsuche_role_check = await self.cog_instance._get_teamsuche_role(inner_interaction.guild)

            for member_obj in selected_members_from_userselect:
                if teamsuche_role_check and teamsuche_role_check in member_obj.roles and \
                   member_obj.id in self.cog_instance.lft_members:
                    valid_members_to_remove.append(member_obj)
                else:
                    skipped_member_names.append(member_obj.display_name)
            
            removed_count = 0; failed_count = 0
            if valid_members_to_remove:
                removed_count, failed_count = await self.cog_instance.remove_members_from_lft(inner_interaction, valid_members_to_remove)
            
            msg_parts = []
            if removed_count > 0: msg_parts.append(f"✅ {removed_count} Mitglied(er) von Teamsuche entfernt und Rolle abgenommen.")
            if failed_count > 0: msg_parts.append(f"❌ Bei {failed_count} Mitglied(ern) gab es einen Fehler bei der Rollenabnahme.")
            if skipped_member_names: msg_parts.append(f"⚠️ {len(skipped_member_names)} ausgewählte(s) Mitglied(er) war(en) nicht (mehr) auf Teamsuche und wurde(n) übersprungen: {', '.join(skipped_member_names)}")
            
            response_message = "\n".join(msg_parts) if msg_parts else "Keine gültigen Mitglieder zum Entfernen ausgewählt oder keine Aktion durchgeführt."

            try: 
                await inner_interaction.followup.send(response_message, ephemeral=True)
            except discord.HTTPException as e:
                log.error(f"Fehler beim Senden der Followup-Nachricht (remove_lft_member_button): {e}")
                if e.status == 429:
                    try: await inner_interaction.followup.send("Viele Aktionen ausgeführt. Discord hat die Antwort blockiert (Rate Limit). Bitte überprüfe die Rollen manuell.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte Rate Limit Fehlermeldung nicht senden (remove_lft_member_button): {e_inner}")
                else:
                    try: await inner_interaction.followup.send("Ein Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte HTTP Fehlermeldung nicht senden (remove_lft_member_button): {e_inner}")
            except Exception as e:
                log.error(f"Unerwarteter Fehler beim Senden der Followup-Nachricht (remove_lft_member_button): {e}")
                try: await inner_interaction.followup.send("Ein unerwarteter Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte unerwartete Fehlermeldung nicht senden (remove_lft_member_button): {e_inner}")
                
            try:
                await interaction.edit_original_response(content=None, embed=self._build_embed(), view=self)
            except discord.HTTPException as e: log.error(f"Fehler Wiederherstellen TeamsucheView nach Entfernen: {e}")
            select_view.stop()

        user_select.callback = select_callback
        select_view.add_item(user_select)
        await interaction.response.edit_message(content="Wähle Mitglieder, die von der Teamsuche entfernt werden sollen (nur die mit Teamsuche-Rolle werden verarbeitet):", embed=None, view=select_view, attachments=[])

    @ui.button(label="Teamsuch Liste", style=ButtonStyle.secondary, emoji="📜", row=1)
    async def list_lft_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True) 
        lft_ids = list(self.cog_instance.lft_members)
        embed = discord.Embed(title="📜 Mitglieder auf Teamsuche", color=discord.Colour(0x23a65a))
        if not lft_ids: embed.description = "Momentan sucht niemand ein Team."
        else:
            members_found_mentions = []
            if interaction.guild:
                for member_id in lft_ids: 
                    if member := interaction.guild.get_member(member_id): members_found_mentions.append(member.mention)
                    else: members_found_mentions.append(f"<@{member_id}> (Unbekannt/Nicht auf Server)")
            if members_found_mentions: embed.description = "\n".join(members_found_mentions)
            else: embed.description = "Keine der gelisteten Teamsuchenden sind auf diesem Server."
        
        try: 
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            log.error(f"Fehler beim Senden der LFT-Liste: {e}")
            if e.status == 429:
                try: await interaction.followup.send("Discord hat das Senden der Liste blockiert (Rate Limit). Versuche es später erneut.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte Rate Limit Fehlermeldung nicht senden (list_lft_button): {e_inner}")
            else:
                try: await interaction.followup.send("Ein Fehler ist beim Anzeigen der Liste aufgetreten.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte HTTP Fehlermeldung nicht senden (list_lft_button): {e_inner}")
        except Exception as e:
            log.error(f"Unerwarteter Fehler beim Senden der LFT-Liste: {e}")
            try: await interaction.followup.send("Ein unerwarteter Fehler ist beim Anzeigen der Liste aufgetreten.", ephemeral=True)
            except Exception as e_inner: log.error(f"Konnte unerwartete Fehlermeldung nicht senden (list_lft_button): {e_inner}")


    @ui.button(label="Menü schließen", style=ButtonStyle.grey, emoji="❌", row=1)
    async def close_lft_menu_button(self, interaction: Interaction, button: ui.Button):
        log.info(f"Teamsuche-Menü geschlossen von {interaction.user}")
        await interaction.response.edit_message(content="Teamsuche-Menü geschlossen.", view=None, embed=None, attachments=[])
        self.stop()

# --- Clanmember View (Kopie von TeamsucheView, angepasst) ---
class ClanmemberView(ui.View):
    def __init__(self, author_id: int, cog_instance: 'MMHelferCog'):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.cog_instance = cog_instance

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Nur der Befehlsaufrufer kann dieses Menü bedienen.", ephemeral=True)
            return False
        return True

    def _build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="🛡️ Clanmember Menü", description="Verwalte hier Clanmember.", color=discord.Colour(0x7289DA)) 
        embed.set_author(name="⚙️ MMHelfer / 🛡️ Clanmember Menü")
        return embed

    @ui.button(label="Mitglied hinzufügen", style=ButtonStyle.success, emoji="➕", row=0)
    async def add_clan_member_button(self, interaction: Interaction, button: ui.Button):
        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(placeholder="Wähle Mitglieder für Clanmember-Rolle...", min_values=1, max_values=25)
        async def select_callback(inner_interaction: Interaction):
            await inner_interaction.response.defer(ephemeral=True, thinking=True)
            added_members, existing_members, failed_members = await self.cog_instance.add_members_to_clan(inner_interaction, user_select.values)
            msg_parts = []
            if added_members: msg_parts.append(f"✅ {len(added_members)} Mitglied(er) als Clanmember hinzugefügt und Rolle vergeben.")
            if existing_members: msg_parts.append(f"ℹ️ {len(existing_members)} Mitglied(er) war(en) bereits Clanmember.")
            if failed_members: msg_parts.append(f"❌ {len(failed_members)} Mitglied(er) konnte(n) nicht verarbeitet werden.")
            response_message = "\n".join(msg_parts) if msg_parts else "Keine Änderungen vorgenommen."
            
            try: 
                await inner_interaction.followup.send(response_message, ephemeral=True)
            except discord.HTTPException as e:
                log.error(f"Fehler beim Senden der Followup-Nachricht (add_clan_member_button): {e}")
                if e.status == 429: 
                    try: await inner_interaction.followup.send("Viele Aktionen ausgeführt. Discord hat die Antwort blockiert (Rate Limit). Bitte überprüfe die Rollen manuell.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte Rate Limit Fehlermeldung nicht senden (add_clan_member_button): {e_inner}")
                else: 
                    try: await inner_interaction.followup.send("Ein Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte HTTP Fehlermeldung nicht senden (add_clan_member_button): {e_inner}")
            except Exception as e: 
                log.error(f"Unerwarteter Fehler beim Senden der Followup-Nachricht (add_clan_member_button): {e}")
                try: await inner_interaction.followup.send("Ein unerwarteter Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte unerwartete Fehlermeldung nicht senden (add_clan_member_button): {e_inner}")

            try:
                await interaction.edit_original_response(content=None, embed=self._build_embed(), view=self)
            except discord.HTTPException as e: log.error(f"Fehler Wiederherstellen ClanmemberView nach Hinzufügen: {e}")
            select_view.stop()
        user_select.callback = select_callback
        select_view.add_item(user_select)
        await interaction.response.edit_message(content="Wähle Mitglieder, die als Clanmember hinzugefügt werden sollen:", embed=None, view=select_view, attachments=[])

    @ui.button(label="Mitglied entfernen", style=ButtonStyle.danger, emoji="➖", row=0)
    async def remove_clan_member_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("Dieser Befehl funktioniert nur auf einem Server.",ephemeral=True); return

        clan_member_role = await self.cog_instance._get_clan_member_role(interaction.guild)
        if not clan_member_role:
             await interaction.response.send_message("Clanmember-Rolle nicht konfiguriert. Entfernen nicht möglich.", ephemeral=True); return

        select_view = ui.View(timeout=180)
        user_select = ui.UserSelect(placeholder="Wähle Mitglieder zum Entfernen...", min_values=1, max_values=25)

        async def select_callback(inner_interaction: Interaction):
            await inner_interaction.response.defer(ephemeral=True, thinking=True)
            selected_members_from_userselect = user_select.values
            valid_members_to_remove = []
            skipped_member_names = []

            if not inner_interaction.guild:
                await inner_interaction.followup.send("Fehler: Guild-Kontext verloren.", ephemeral=True)
                select_view.stop(); return

            clan_member_role_check = await self.cog_instance._get_clan_member_role(inner_interaction.guild)

            for member_obj in selected_members_from_userselect:
                if clan_member_role_check and clan_member_role_check in member_obj.roles and \
                   member_obj.id in self.cog_instance.clan_members:
                    valid_members_to_remove.append(member_obj)
                else:
                    skipped_member_names.append(member_obj.display_name)
            
            removed_count = 0; failed_count = 0
            if valid_members_to_remove:
                removed_count, failed_count = await self.cog_instance.remove_members_from_clan(inner_interaction, valid_members_to_remove)
            
            msg_parts = []
            if removed_count > 0: msg_parts.append(f"✅ {removed_count} Mitglied(er) als Clanmember entfernt und Rolle abgenommen.")
            if failed_count > 0: msg_parts.append(f"❌ Bei {failed_count} Mitglied(ern) gab es einen Fehler bei der Rollenabnahme.")
            if skipped_member_names: msg_parts.append(f"⚠️ {len(skipped_member_names)} ausgewählte(s) Mitglied(er) war(en) nicht (mehr) Clanmember und wurde(n) übersprungen.")
            
            response_message = "\n".join(msg_parts) if msg_parts else "Keine gültigen Mitglieder zum Entfernen ausgewählt oder keine Aktion durchgeführt."

            try: 
                await inner_interaction.followup.send(response_message, ephemeral=True)
            except discord.HTTPException as e:
                log.error(f"Fehler beim Senden der Followup-Nachricht (remove_clan_member_button): {e}")
                if e.status == 429:
                    try: await inner_interaction.followup.send("Viele Aktionen ausgeführt. Discord hat die Antwort blockiert (Rate Limit). Bitte überprüfe die Rollen manuell.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte Rate Limit Fehlermeldung nicht senden (remove_clan_member_button): {e_inner}")
                else:
                    try: await inner_interaction.followup.send("Ein Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                    except Exception as e_inner: log.error(f"Konnte HTTP Fehlermeldung nicht senden (remove_clan_member_button): {e_inner}")
            except Exception as e:
                log.error(f"Unerwarteter Fehler beim Senden der Followup-Nachricht (remove_clan_member_button): {e}")
                try: await inner_interaction.followup.send("Ein unerwarteter Fehler ist beim Senden der Bestätigung aufgetreten.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte unerwartete Fehlermeldung nicht senden (remove_clan_member_button): {e_inner}")

            try:
                await interaction.edit_original_response(content=None, embed=self._build_embed(), view=self)
            except discord.HTTPException as e: log.error(f"Fehler Wiederherstellen ClanmemberView nach Entfernen: {e}")
            select_view.stop()

        user_select.callback = select_callback
        select_view.add_item(user_select)
        await interaction.response.edit_message(content="Wähle Mitglieder, die als Clanmember entfernt werden sollen (nur die mit Clanmember-Rolle werden verarbeitet):", embed=None, view=select_view, attachments=[])

    @ui.button(label="Clanmember Liste", style=ButtonStyle.secondary, emoji="📜", row=1)
    async def list_clan_member_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True) 
        clan_member_ids = list(self.cog_instance.clan_members)
        embed = discord.Embed(title="🛡️ Clanmember Liste", color=discord.Colour(0x7289DA))
        if not clan_member_ids: embed.description = "Momentan sind keine Clanmember registriert."
        else:
            members_found_mentions = []
            if interaction.guild:
                for member_id in clan_member_ids: 
                    if member := interaction.guild.get_member(member_id): members_found_mentions.append(member.mention)
                    else: members_found_mentions.append(f"<@{member_id}> (Unbekannt/Nicht auf Server)")
            if members_found_mentions: embed.description = "\n".join(members_found_mentions)
            else: embed.description = "Keine der gelisteten Clanmember sind auf diesem Server."
        
        try: 
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            log.error(f"Fehler beim Senden der Clanmember-Liste: {e}")
            if e.status == 429: 
                try: await interaction.followup.send("Discord hat das Senden der Liste blockiert (Rate Limit). Versuche es später erneut.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte Rate Limit Fehlermeldung nicht senden (list_clan_member_button): {e_inner}")
            else: 
                try: await interaction.followup.send("Ein Fehler ist beim Anzeigen der Liste aufgetreten.", ephemeral=True)
                except Exception as e_inner: log.error(f"Konnte HTTP Fehlermeldung nicht senden (list_clan_member_button): {e_inner}")
        except Exception as e: 
            log.error(f"Unerwarteter Fehler beim Senden der Clanmember-Liste: {e}")
            try: await interaction.followup.send("Ein unerwarteter Fehler ist beim Anzeigen der Liste aufgetreten.", ephemeral=True)
            except Exception as e_inner: log.error(f"Konnte unerwartete Fehlermeldung nicht senden (list_clan_member_button): {e_inner}")


    @ui.button(label="Menü schließen", style=ButtonStyle.grey, emoji="❌", row=1)
    async def close_clan_member_menu_button(self, interaction: Interaction, button: ui.Button):
        log.info(f"Clanmember-Menü geschlossen von {interaction.user}")
        await interaction.response.edit_message(content="Clanmember-Menü geschlossen.", view=None, embed=None, attachments=[])
        self.stop()

# --- Haupt-Cog Klasse ---
class MMHelferCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.main_config = load_json(MAIN_CONFIG_PATH)
        self.competitive_teams = load_json(COMPETITIVE_CONFIG_PATH)
        self.community_teams = load_json(COMMUNITY_CONFIG_PATH)
        
        lft_data = load_json(TEAMSUCHE_CONFIG_PATH)
        self.lft_members: List[int] = lft_data.get("lft_member_ids", [])
        if not isinstance(self.lft_members, list):
            log.warning(f"'{TEAMSUCHE_CONFIG_PATH}' -> 'lft_member_ids' ungültig. Zurückgesetzt.")
            self.lft_members = []
            save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": []})

        clanmember_data = load_json(CLANMEMBER_CONFIG_PATH)
        self.clan_members: List[int] = clanmember_data.get("clan_member_ids", [])
        if not isinstance(self.clan_members, list):
            log.warning(f"'{CLANMEMBER_CONFIG_PATH}' -> 'clan_member_ids' ungültig. Zurückgesetzt.")
            self.clan_members = []
            save_json(CLANMEMBER_CONFIG_PATH, {"clan_member_ids": []})

        required_keys = ["roles", "community_category_id", "competitive_category_id", 
                         "teamsuche_role_id", "clan_member_role_id", "delete_team_allowed_role_ids"]
        missing_keys = [key for key in required_keys if not self.main_config.get(key)]
        if missing_keys:
            log.critical(f"Wichtige Teile der mmhelfer_main_config.json fehlen! Benötigt: {', '.join(missing_keys)}")

    mm = app_commands.Group(name="mm", description="Befehle für das MMHelfer Team Management")

    def get_team_data(self, team_name: str, team_type: str, guild_id_for_logging: Optional[int] = None) -> Optional[Dict[str, Any]]:
        config_path = COMMUNITY_CONFIG_PATH if team_type == "Community" else COMPETITIVE_CONFIG_PATH
        config = load_json(config_path)
        data = config.get(team_name)
        if data and guild_id_for_logging: data["guild_id"] = guild_id_for_logging
        return data

    async def _get_teamsuche_role(self, guild: discord.Guild) -> Optional[Role]:
        teamsuche_role_id_str = self.main_config.get("teamsuche_role_id")
        if not teamsuche_role_id_str:
            log.error("'teamsuche_role_id' nicht in mmhelfer_main_config.json gefunden.")
            return None
        try:
            role = guild.get_role(int(teamsuche_role_id_str))
            if not role: log.error(f"Teamsuche Rolle mit ID {teamsuche_role_id_str} nicht auf dem Server gefunden.")
            return role
        except ValueError: log.error(f"Ungültige 'teamsuche_role_id' ({teamsuche_role_id_str}) in Config."); return None

    async def _get_clan_member_role(self, guild: discord.Guild) -> Optional[Role]:
        clan_member_role_id_str = self.main_config.get("clan_member_role_id")
        if not clan_member_role_id_str:
            log.error("'clan_member_role_id' nicht in mmhelfer_main_config.json gefunden.")
            return None
        try:
            role = guild.get_role(int(clan_member_role_id_str))
            if not role: log.error(f"Clanmember Rolle mit ID {clan_member_role_id_str} nicht auf dem Server gefunden.")
            return role
        except ValueError: log.error(f"Ungültige 'clan_member_role_id' ({clan_member_role_id_str}) in Config."); return None

    async def add_members_to_lft(self, interaction: Interaction, members_to_add: List[Member]) -> Tuple[List[str], List[str], List[str]]:
        if not interaction.guild: return [], [], [m.display_name for m in members_to_add]
        teamsuche_role = await self._get_teamsuche_role(interaction.guild)
        if not teamsuche_role:
            log.error("Konnte Mitglieder nicht zur Teamsuche hinzufügen: Teamsuche-Rolle fehlt.")
            return [], [], [m.display_name for m in members_to_add]

        added_names = []; existing_names = []; failed_names = []
        current_lft_data = load_json(TEAMSUCHE_CONFIG_PATH)
        current_lft_ids = current_lft_data.get("lft_member_ids", [])
        if not isinstance(current_lft_ids, list): current_lft_ids = []
        
        ids_to_add_to_cfg = []
        for member in members_to_add:
            if member.id in current_lft_ids: existing_names.append(member.display_name); continue
            try:
                await member.add_roles(teamsuche_role, reason="Zur Teamsuche hinzugefügt")
                await asyncio.sleep(DEFAULT_API_DELAY) 
                ids_to_add_to_cfg.append(member.id); added_names.append(member.display_name)
                log.info(f"Mitglied {member.name} ({member.id}) zu LFT hinzugefügt.")
            except discord.HTTPException as e: 
                log.error(f"HTTP Fehler beim Hinzufügen der LFT Rolle zu {member.name}: {e} (Status: {e.status}, Code: {e.code})")
                failed_names.append(member.display_name)
            except Exception as e: 
                log.error(f"Allg. Fehler Hinzufügen LFT Rolle zu {member.name}: {e}"); failed_names.append(member.display_name)

        if ids_to_add_to_cfg:
            final_lft_ids = list(set(current_lft_ids + ids_to_add_to_cfg))
            self.lft_members = final_lft_ids
            save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": final_lft_ids})
        return added_names, existing_names, failed_names

    async def remove_members_from_lft(self, interaction: Interaction, members_to_remove: List[Member]) -> Tuple[int, int]:
        if not interaction.guild: return 0, len(members_to_remove)
        teamsuche_role = await self._get_teamsuche_role(interaction.guild)
        
        removed_count = 0; failed_count = 0
        current_lft_data = load_json(TEAMSUCHE_CONFIG_PATH)
        current_lft_ids = current_lft_data.get("lft_member_ids", [])
        if not isinstance(current_lft_ids, list): current_lft_ids = []
        
        ids_actually_removed_from_config = []
        for member in members_to_remove:
            if member.id in current_lft_ids:
                if teamsuche_role:
                    try: 
                        await member.remove_roles(teamsuche_role, reason="Von Teamsuche entfernt")
                        await asyncio.sleep(DEFAULT_API_DELAY) 
                    except discord.HTTPException as e: 
                        log.error(f"HTTP Fehler beim Entfernen der LFT Rolle von {member.name}: {e} (Status: {e.status}, Code: {e.code})")
                        failed_count +=1 ; continue
                    except Exception as e: 
                        log.error(f"Allg. Fehler Entfernen LFT Rolle von {member.name}: {e}"); failed_count +=1 ; continue
                ids_actually_removed_from_config.append(member.id)
                removed_count += 1
        
        if ids_actually_removed_from_config:
            final_lft_ids = [id_ for id_ in current_lft_ids if id_ not in ids_actually_removed_from_config]
            self.lft_members = final_lft_ids
            save_json(TEAMSUCHE_CONFIG_PATH, {"lft_member_ids": final_lft_ids})
        return removed_count, failed_count

    async def add_members_to_clan(self, interaction: Interaction, members_to_add: List[Member]) -> Tuple[List[str], List[str], List[str]]:
        if not interaction.guild: return [], [], [m.display_name for m in members_to_add]
        clan_role = await self._get_clan_member_role(interaction.guild)
        if not clan_role:
            log.error("Konnte Mitglieder nicht als Clanmember hinzufügen: Clanmember-Rolle fehlt.")
            return [], [], [m.display_name for m in members_to_add]

        added_names = []; existing_names = []; failed_names = []
        current_clan_data = load_json(CLANMEMBER_CONFIG_PATH)
        current_clan_ids = current_clan_data.get("clan_member_ids", [])
        if not isinstance(current_clan_ids, list): current_clan_ids = []
        
        ids_to_add_to_cfg = []
        for member in members_to_add:
            if member.id in current_clan_ids: existing_names.append(member.display_name); continue
            try:
                await member.add_roles(clan_role, reason="Als Clanmember hinzugefügt")
                await asyncio.sleep(DEFAULT_API_DELAY) 
                ids_to_add_to_cfg.append(member.id); added_names.append(member.display_name)
                log.info(f"Mitglied {member.name} ({member.id}) als Clanmember hinzugefügt und Rolle '{clan_role.name}' erhalten.")
            except discord.HTTPException as e: 
                log.error(f"HTTP Fehler beim Hinzufügen der Clanmember Rolle zu {member.name}: {e} (Status: {e.status}, Code: {e.code})")
                failed_names.append(member.display_name)
            except Exception as e: 
                log.error(f"Allg. Fehler Hinzufügen Clanmember Rolle zu {member.name}: {e}"); failed_names.append(member.display_name)
        
        if ids_to_add_to_cfg:
            final_clan_ids = list(set(current_clan_ids + ids_to_add_to_cfg))
            self.clan_members = final_clan_ids
            save_json(CLANMEMBER_CONFIG_PATH, {"clan_member_ids": final_clan_ids})
        return added_names, existing_names, failed_names

    async def remove_members_from_clan(self, interaction: Interaction, members_to_remove: List[Member]) -> Tuple[int, int]:
        if not interaction.guild: return 0, len(members_to_remove)
        clan_role = await self._get_clan_member_role(interaction.guild)
        
        removed_count = 0; failed_count = 0
        current_clan_data = load_json(CLANMEMBER_CONFIG_PATH)
        current_clan_ids = current_clan_data.get("clan_member_ids", [])
        if not isinstance(current_clan_ids, list): current_clan_ids = []
        
        ids_removed_from_cfg = []
        for member in members_to_remove:
            if member.id in current_clan_ids:
                if clan_role:
                    try: 
                        await member.remove_roles(clan_role, reason="Als Clanmember entfernt")
                        await asyncio.sleep(DEFAULT_API_DELAY) 
                    except discord.HTTPException as e: 
                        log.error(f"HTTP Fehler beim Entfernen der Clanmember Rolle von {member.name}: {e} (Status: {e.status}, Code: {e.code})")
                        failed_count +=1 ; continue
                    except Exception as e: 
                        log.error(f"Allg. Fehler Entfernen Clanmember Rolle von {member.name}: {e}"); failed_count +=1 ; continue
                ids_removed_from_cfg.append(member.id)
                removed_count += 1
        
        if ids_removed_from_cfg:
            final_clan_ids = [id_ for id_ in current_clan_ids if id_ not in ids_removed_from_cfg]
            self.clan_members = final_clan_ids
            save_json(CLANMEMBER_CONFIG_PATH, {"clan_member_ids": final_clan_ids})
        return removed_count, failed_count

    async def _get_permission_overwrites(self, guild: discord.Guild, team_role: Role, team_type: str, channel_type: str) -> Dict[Union[Role, Member], PermissionOverwrite]:
        overwrites: Dict[Union[Role, Member], PermissionOverwrite] = {}
        cfg_r = self.main_config.get("roles", {})
        add_cfg = self.main_config.get("additional_roles", {})
        overwrites[guild.default_role] = PermissionOverwrite(view_channel=False)
        adm = ["Discord Admin", "Member Management"]; cl = ["Co-Lead"]; add_r_ids: List[int] = []

        if channel_type == "text":
            for n in adm:
                if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
            for n in cl:
                 if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, send_messages=True)
            overwrites[team_role] = PermissionOverwrite(view_channel=True, send_messages=True)
            add_r_ids = add_cfg.get("text", [])
        elif channel_type == "voice_community":
            p=PermissionOverwrite(view_channel=True, connect=True, move_members=True)
            vc_r=["Co-Lead","Coach","Caster","Co-Caster","Rookie Caster","Competitive Team","Community Team","Ehrenmember","Mafia"]
            vc_m=["Discord Admin","Member Management"]
            no_c_id = cfg_r.get("Rocket League"); no_c = guild.get_role(no_c_id) if no_c_id else None
            for n in vc_r:
                 if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, connect=True)
            for n in vc_m:
                 if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
            if no_c and (no_c not in overwrites or overwrites[no_c].connect is not True): overwrites[no_c] = PermissionOverwrite(view_channel=True, connect=False)
            overwrites[team_role]=p; add_r_ids=add_cfg.get("voice_community", [])
        elif channel_type == "voice_competitive":
            p=PermissionOverwrite(view_channel=True, connect=True, move_members=True)
            vc_r=["Coach","Caster","Co-Caster","Rookie Caster","Competitive Team","Community Team","Ehrenmember","Mafia"]
            vc_m=["Co-Lead","Discord Admin","Member Management"]
            no_c_id = cfg_r.get("Rocket League"); no_c = guild.get_role(no_c_id) if no_c_id else None
            for n in vc_r:
                 if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, connect=True)
            for n in vc_m:
                 if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
            if no_c and (no_c not in overwrites or overwrites[no_c].connect is not True): overwrites[no_c] = PermissionOverwrite(view_channel=True, connect=False)
            overwrites[team_role]=p; add_r_ids=add_cfg.get("voice_competitive", [])
        elif channel_type == "private_voice_competitive":
            p=PermissionOverwrite(view_channel=True, connect=True, move_members=True)
            vc_r=["Caster","Co-Caster","Mafia"]; vc_m=["Co-Lead","Discord Admin","Member Management"]
            no_c_id = cfg_r.get("Rookie Caster"); no_c = guild.get_role(no_c_id) if no_c_id else None
            for n in vc_r:
                 if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, connect=True)
            for n in vc_m:
                 if r := guild.get_role(cfg_r.get(n)): overwrites[r] = PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
            if no_c and (no_c not in overwrites or overwrites[no_c].connect is not True): overwrites[no_c] = PermissionOverwrite(view_channel=True, connect=False)
            overwrites[team_role]=p; add_r_ids=add_cfg.get("private_voice_competitive", [])
        else: log.error(f"Unbek. Kanaltyp: {channel_type}"); return {}

        for r_id in add_r_ids:
             if r := guild.get_role(r_id):
                  perm_args: Dict[str, bool] = {'view_channel': True}
                  if "voice" in channel_type: perm_args['connect'] = True
                  else: perm_args['send_messages'] = True
                  if r not in overwrites: overwrites[r] = PermissionOverwrite(**perm_args)
                  else:
                      cp = overwrites[r]; cp.view_channel=True
                      if "voice" in channel_type and cp.connect is None: cp.connect=True
                      elif "voice" not in channel_type and cp.send_messages is None: cp.send_messages=True
             else: log.warning(f"Zus. Rolle ID {r_id} fehlt.")
        return {t: o for t, o in overwrites.items() if t}


    @mm.command(name="menu", description="Öffnet das Hauptmenü für die Team-Verwaltung.")
    async def menu(self, interaction: Interaction):
        log.info(f"Hauptmenü: {interaction.user}")
        embed=discord.Embed(title="***Team Manager***", color=discord.Colour.from_rgb(255, 255, 255))
        embed.set_author(name="⚙️ MMHelfer Module")
        
        is_ephemeral_message = True 
        kwargs: Dict[str, Any] = {"embed":embed, "view":self.MainMenuView(interaction.user.id, self), "ephemeral":is_ephemeral_message}
        
        if not is_ephemeral_message and LOGO_PATH.exists():
            kwargs["file"] = discord.File(LOGO_PATH, filename=LOGO_PATH.name)
            embed.set_thumbnail(url=f"attachment://{LOGO_PATH.name}")
        elif LOGO_PATH.exists() and is_ephemeral_message:
            log.info("Hauptmenü ist ephemer, Logo-Thumbnail wird nicht über Attachment geladen.")
        else:
            log.warning(f"Logo fehlt unter: {LOGO_PATH}")

        embed.add_field(name="➕ Team Erstellung", value="Öffnet das Team Erstellungs Menü.", inline=False)
        embed.add_field(name="🔧 Team Edit", value="Öffnet das Team Edit Menü.", inline=False)
        embed.add_field(name="📜 Team Übersicht", value="Zeige alle aktuellen Teams an.", inline=False)
        embed.add_field(name="👥 Teamsuche", value="Verwalte Mitglieder auf Teamsuche.", inline=False)
        embed.add_field(name="🛡️ Clanmember", value="Verwalte Clanmember.", inline=False)
        embed.add_field(name="➖ Team löschen", value="Lösche ein ausgewähltes Team.", inline=False)
        embed.add_field(name="❌ Menü schließen", value="Schließe das Menü.", inline=False)
        
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(**kwargs)
            else:
                await interaction.followup.send(**kwargs)
        except Exception as e:
            log.exception(f"Fehler Senden Hauptmenü: {e}")
            try:
                await interaction.followup.send("Konnte Hauptmenü nicht anzeigen.", ephemeral=True)
            except Exception:
                pass

    class MainMenuView(ui.View):
        def __init__(self, author_id: int, cog_instance: 'MMHelferCog'):
            super().__init__(timeout=300)
            self.author_id = author_id
            self.cog_instance = cog_instance
            self._add_buttons()

        def _add_buttons(self):
            self.clear_items()
            self.add_item(ui.Button(label="Team Erstellung", style=ButtonStyle.success, emoji="➕", row=0, custom_id="main_create_team"))
            self.add_item(ui.Button(label="Team Edit", style=ButtonStyle.primary, emoji="🔧", row=0, custom_id="main_edit_team"))
            self.add_item(ui.Button(label="Team Übersicht", style=ButtonStyle.secondary, emoji="📜", row=0, custom_id="main_list_teams"))
            self.add_item(ui.Button(label="Teamsuche", style=ButtonStyle.grey, emoji="👥", row=1, custom_id="main_teamsuche"))
            self.add_item(ui.Button(label="Clanmember", style=ButtonStyle.grey, emoji="🛡️", row=1, custom_id="main_clanmember"))
            self.add_item(ui.Button(label="Team löschen", style=ButtonStyle.danger, emoji="➖", row=1, custom_id="main_delete_team"))
            self.add_item(ui.Button(label="Menü schließen", style=ButtonStyle.danger, emoji="🚫", row=2, custom_id="main_close_menu"))

            for child in self.children:
                if isinstance(child, ui.Button):
                    if child.custom_id == "main_create_team": child.callback = self.create_team_button_callback
                    elif child.custom_id == "main_edit_team": child.callback = self.edit_team_button_callback
                    elif child.custom_id == "main_list_teams": child.callback = self.list_teams_button_callback
                    elif child.custom_id == "main_teamsuche": child.callback = self.teamsuche_button_callback
                    elif child.custom_id == "main_clanmember": child.callback = self.clanmember_button_callback
                    elif child.custom_id == "main_delete_team": child.callback = self.delete_team_button_callback
                    elif child.custom_id == "main_close_menu": child.callback = self.close_menu_button_callback
        
        async def interaction_check(self, interaction: Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("Nur Befehlsaufrufer.", ephemeral=True); return False
            return True

        async def create_team_button_callback(self, interaction: Interaction):
             create_view = TeamCreateView(self.author_id, self.cog_instance.main_config, self.cog_instance)
             embed = create_view._build_embed(is_ephemeral=True)
             kwargs: Dict[str,Any] = {"embed": embed, "view": create_view, "ephemeral": True}
             try:
                 if not interaction.response.is_done():
                     await interaction.response.send_message(**kwargs)
                 else:
                     await interaction.followup.send(**kwargs)
             except Exception as e:
                 log.error(f"Fehler Senden Erstellungsmenü: {e}")
                 try:
                     await interaction.followup.send("Konnte Erstellungsmenü nicht öffnen.", ephemeral=True)
                 except Exception:
                     pass 
        
        async def edit_team_button_callback(self, interaction: Interaction):
            log.info(f"Edit Menü: {interaction.user}")
            comm = load_json(COMMUNITY_CONFIG_PATH); comp = load_json(COMPETITIVE_CONFIG_PATH); all_teams = {}
            for n in comp: all_teams[f"🏆 {n}"] = f"Competitive:{n}"
            for n in comm: all_teams[f"🌐 {n}"] = f"Community:{n}"
            if not all_teams:
                if not interaction.response.is_done(): await interaction.response.send_message("Keine Teams zum Bearbeiten.", ephemeral=True)
                else: await interaction.followup.send("Keine Teams zum Bearbeiten.", ephemeral=True)
                return
            view = ui.View(timeout=180); view.add_item(TeamEditSelect(all_teams, self.cog_instance))
            if not interaction.response.is_done():
                await interaction.response.send_message("Wähle zu bearbeitendes Team:", view=view, ephemeral=True)
            else: 
                await interaction.edit_original_response(content="Wähle zu bearbeitendes Team:", view=view, attachments=[])

        async def list_teams_button_callback(self, interaction: Interaction):
            comm_teams = load_json(COMMUNITY_CONFIG_PATH); comp_teams = load_json(COMPETITIVE_CONFIG_PATH)
            embed = discord.Embed(title="📜 Team Übersicht", color=discord.Colour.blurple())
            if not comm_teams and not comp_teams: embed.description = "Keine Teams vorhanden."
            else:
                def fmt(ts_data: Dict[str, Any]) -> str:
                    lines = []
                    for team_name_key, team_val_data in ts_data.items():
                        captain_id_val = team_val_data.get('captain_id')
                        captain_mention = f"<@{captain_id_val}>" if captain_id_val else "N/A"
                        members_count = len(team_val_data.get('member_ids', []))
                        lines.append(f"**{team_name_key}** (C: {captain_mention}, M: {members_count})")
                    val_str = "\n".join(lines) if lines else "Keine"
                    return val_str[:1020]+"..." if len(val_str)>1024 else val_str
                embed.add_field(name="🌐 Community", value=fmt(comm_teams), inline=False)
                embed.add_field(name="🏆 Competitive", value=fmt(comp_teams), inline=False)
            kwargs: Dict[str,Any] = {"embed": embed, "ephemeral": True}
            if not interaction.response.is_done():
                await interaction.response.send_message(**kwargs)
            else:
                await interaction.followup.send(**kwargs)

        async def teamsuche_button_callback(self, interaction: Interaction):
            log.info(f"Teamsuche Menü aufgerufen von {interaction.user}")
            teamsuche_view = TeamsucheView(self.author_id, self.cog_instance)
            embed = teamsuche_view._build_embed()
            kwargs: Dict[str,Any] = {"embed": embed, "view": teamsuche_view, "ephemeral": True}
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(**kwargs)
                else:
                    await interaction.followup.send(**kwargs)
            except Exception as e:
                log.error(f"Fehler beim Senden des Teamsuche-Menüs: {e}")
                try:
                    await interaction.followup.send("Konnte das Teamsuche-Menü nicht öffnen.", ephemeral=True)
                except Exception: 
                    pass

        async def clanmember_button_callback(self, interaction: Interaction): 
            log.info(f"Clanmember Menü aufgerufen von {interaction.user}")
            clanmember_view = ClanmemberView(self.author_id, self.cog_instance)
            embed = clanmember_view._build_embed()
            kwargs: Dict[str,Any] = {"embed": embed, "view": clanmember_view, "ephemeral": True}
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(**kwargs)
                else:
                    await interaction.followup.send(**kwargs)
            except Exception as e:
                log.error(f"Fehler beim Senden des Clanmember-Menüs: {e}")
                try:
                    await interaction.followup.send("Konnte das Clanmember-Menü nicht öffnen.", ephemeral=True)
                except Exception:
                    pass

        async def delete_team_button_callback(self, interaction: Interaction):
            log.info(f"Löschen Menü: {interaction.user}")
            allowed_role_ids = self.cog_instance.main_config.get("delete_team_allowed_role_ids", [])
            if not isinstance(interaction.user, Member):
                if not interaction.response.is_done(): await interaction.response.send_message("Nur auf Servern.", ephemeral=True)
                else: await interaction.followup.send("Nur auf Servern.", ephemeral=True)
                return
            user_roles = [role.id for role in interaction.user.roles]; can_delete = any(role_id in allowed_role_ids for role_id in user_roles)
            if interaction.user.guild_permissions.administrator: can_delete = True
            if not allowed_role_ids and not can_delete: log.warning(f"Keine 'delete_team_allowed_role_ids' & User {interaction.user} kein Admin."); can_delete = False
            if not can_delete:
                if not interaction.response.is_done(): await interaction.response.send_message("Keine Rechte.", ephemeral=True)
                else: await interaction.followup.send("Keine Rechte.", ephemeral=True)
                return
            comm = load_json(COMMUNITY_CONFIG_PATH); comp = load_json(COMPETITIVE_CONFIG_PATH); all_teams = {}
            for n in comp: all_teams[f"🏆 {n}"] = f"Competitive:{n}"
            for n in comm: all_teams[f"🌐 {n}"] = f"Community:{n}"
            if not all_teams:
                if not interaction.response.is_done(): await interaction.response.send_message("Keine Teams zum Löschen.", ephemeral=True)
                else: await interaction.followup.send("Keine Teams zum Löschen.", ephemeral=True)
                return
            view = ui.View(timeout=180); view.add_item(TeamDeleteSelect(all_teams, self.cog_instance))
            if not interaction.response.is_done(): await interaction.response.send_message("Wähle zu löschendes Team:", view=view, ephemeral=True)
            else: await interaction.edit_original_response(content="Wähle zu löschendes Team:", view=view, attachments=[])

        async def close_menu_button_callback(self, interaction: Interaction):
            log.info(f"Hauptmenü geschlossen von {interaction.user}"); self.stop()
            try:
                if not interaction.response.is_done():
                    await interaction.response.edit_message(content="Hauptmenü geschlossen.", view=None, embed=None, attachments=[])
                else:
                    await interaction.edit_original_response(content="Hauptmenü geschlossen.", view=None, embed=None, attachments=[])
            except (discord.NotFound, discord.HTTPException) as e:
                 log.warning(f"Fehler beim Schließen des Hauptmenüs: {e}")

    async def execute_team_creation(self, interaction: Interaction, team_name: str, team_type: str, members: List[Member], captain: Optional[Member], use_followup: bool = False) -> bool:
        guild = interaction.guild
        send_method = interaction.followup.send
        if not guild:
            await send_method("Nur auf Servern.", ephemeral=True)
            return False
        self.competitive_teams = load_json(COMPETITIVE_CONFIG_PATH)
        self.community_teams = load_json(COMMUNITY_CONFIG_PATH)
        if team_name in self.competitive_teams or team_name in self.community_teams:
            await send_method(f"Team '{team_name}' existiert.", ephemeral=True)
            return False

        created_role: Optional[Role] = None
        created_channels: Dict[str, Union[TextChannel,VoiceChannel]] = {}
        progress_message: Optional[discord.WebhookMessage] = None
        success = True
        try:
            progress_message = await send_method(f"🔄 Erstelle Team '{team_name}' ({team_type})...", ephemeral=True, wait=True)

            await progress_message.edit(content=f"✅ ...\n➡️ 1/4: Rolle...")
            try:
                created_role = await guild.create_role(name=team_name, colour=Colour(0xe67e22), mentionable=False)
                await asyncio.sleep(DEFAULT_API_DELAY) 
            except discord.Forbidden:
                await progress_message.edit(content=f"❌ Fehler 1: Rechte f. Rollen.")
                if created_role:
                    try: await created_role.delete(reason="Rollback: Fehler bei Erstellung"); await asyncio.sleep(DEFAULT_API_DELAY)
                    except Exception: pass
                return False
            except discord.HTTPException as e:
                await progress_message.edit(content=f"❌ Fehler 1: {e}")
                if created_role:
                    try: await created_role.delete(reason="Rollback: Fehler bei Erstellung"); await asyncio.sleep(DEFAULT_API_DELAY)
                    except Exception: pass
                return False
            
            if created_role is None:
                log.critical("Teamrolle wurde nicht erstellt, obwohl kein Fehler ausgelöst wurde.")
                await progress_message.edit(content=f"❌ Kritischer Fehler: Teamrolle konnte nicht erstellt werden.")
                return False


            await progress_message.edit(content=f"✅ ...\n➡️ 2/4: Kanäle...")
            cat_key=f"{team_type.lower()}_category_id"
            cat_id=self.main_config.get(cat_key)
            category: Optional[CategoryChannel] = None
            if isinstance(cat_id, int):
                category = guild.get_channel(cat_id)
            if not category or not isinstance(category, CategoryChannel):
                err=f"❌ Fehler 2: Kat. '{team_type}' ungültig ('{cat_key}': {cat_id}). Prüfe Config."
                await progress_message.edit(content=err)
                log.error(err)
                raise Exception("Kategorie ungültig")
            
            ch_defs = [{"type":"text", "name":f"{team_name}"}, {"type":f"voice_{team_type.lower()}", "name":f"🔸{team_name}"}]
            if team_type=="Competitive":
                ch_defs.append({"type":"private_voice_competitive", "name":f"🔒 {team_name}"})
            
            for cd in ch_defs:
                try:
                    ows=await self._get_permission_overwrites(guild,created_role,team_type,cd["type"])
                    cm=guild.create_voice_channel if "voice" in cd["type"] else guild.create_text_channel
                    nc=await cm(name=cd["name"],category=category,overwrites=ows,reason=f"Team:{team_name}")
                    await asyncio.sleep(DEFAULT_API_DELAY) 
                    created_channels[cd["type"]]=nc
                except discord.Forbidden:
                    await progress_message.edit(content=f"❌ Fehler 2: Rechte f. Kanäle.")
                    success=False; break
                except discord.HTTPException as e:
                    await progress_message.edit(content=f"❌ Fehler 2 ({cd['name']}): {e}")
                    success=False; break
                except Exception as e:
                    log.exception(f"Fehler Kanal {cd['name']}: {e}")
                    await progress_message.edit(content=f"❌ Fehler 2 ({cd['name']}).")
                    success=False; break
            if not success: raise Exception("Kanalerstellung fehlgeschlagen")

            await progress_message.edit(content=f"✅ ...\n➡️ 3/4: Rollen...")
            r_add=[created_role]
            r_rem=[]
            if r_ts_id := self.main_config.get("teamsuche_role_id"):
                if r := guild.get_role(r_ts_id): r_rem.append(r)
                else: log.warning(f"Teamsuche Rolle ID {r_ts_id} nicht gefunden.")
            else: log.warning("Teamsuche Rolle (teamsuche_role_id) nicht in Config.")
            
            k=f"{team_type} Team"
            if r_gen_id := self.main_config.get("roles",{}).get(k):
                if r := guild.get_role(r_gen_id):
                    if r not in r_add : r_add.append(r)
                else: log.warning(f"Allg. Rolle '{k}' (ID: {r_gen_id}) nicht gefunden.")
            else: log.warning(f"Allg. Rolle '{k}' nicht in Config/Roles.")
            
            c_role: Optional[Role] = None
            if c_role_id := self.main_config.get("captain_role_id"): c_role = guild.get_role(c_role_id)
            m_ids: List[int] = []
            if members:
                 v_add=[r for r in r_add if r is not None]; v_rem=[r for r in r_rem if r is not None]
                 for m_idx, m in enumerate(members): # Index für feineres Delay Management, falls nötig
                     try:
                         if v_add: 
                             await m.add_roles(*v_add, reason=f"Team {team_name}")
                             await asyncio.sleep(DEFAULT_API_DELAY / 2) # Halber Delay für Sub-Aktion
                         if v_rem: 
                             await m.remove_roles(*v_rem, reason=f"Team {team_name}")
                             await asyncio.sleep(DEFAULT_API_DELAY / 2) # Halber Delay für Sub-Aktion
                         m_ids.append(m.id)
                     except Exception as e: log.error(f"Fehler Rollen {m.name}: {e}")
                     if (m_idx + 1) % 3 == 0: # Nach jeweils 3 Mitgliedern ein etwas längerer Atemzug
                         await asyncio.sleep(DEFAULT_API_DELAY)

            c_id: Optional[int] = None
            if captain and c_role:
                 try:
                     await captain.add_roles(c_role, reason=f"Cpt {team_name}"); await asyncio.sleep(DEFAULT_API_DELAY)
                     c_id=captain.id
                     if captain not in members: # Wenn Captain nicht schon in members ist
                           v_add=[r for r in r_add if r is not None]; v_rem=[r for r in r_rem if r is not None]
                           if v_add: await captain.add_roles(*v_add, reason=f"Cpt Team {team_name}"); await asyncio.sleep(DEFAULT_API_DELAY / 2)
                           if v_rem: await captain.remove_roles(*v_rem, reason=f"Cpt Team {team_name}"); await asyncio.sleep(DEFAULT_API_DELAY / 2)
                           if captain.id not in m_ids: m_ids.append(captain.id)
                 except Exception as e: log.error(f"Fehler Cpt Rolle {captain.name}: {e}")
            elif captain: log.warning("Cpt Rolle fehlt in Config.")

            await progress_message.edit(content=f"✅ ...\n➡️ 4/4: Speichern...")
            t_ch=created_channels.get("text"); v_ch=created_channels.get(f"voice_{team_type.lower()}", created_channels.get("voice_competitive"))
            pv_ch=created_channels.get("private_voice_competitive") if team_type=="Competitive" else None
            t_data={"role_id":created_role.id,"text_channel_id":t_ch.id if t_ch else None,"voice_channel_id":v_ch.id if v_ch else None,"captain_id":c_id,"member_ids":m_ids}
            if team_type=="Competitive": t_data["private_voice_channel_id"]=pv_ch.id if pv_ch else None
            if not t_data["text_channel_id"] or not t_data["voice_channel_id"] or \
               (team_type=="Competitive" and not t_data.get("private_voice_channel_id")):
                await progress_message.edit(content="❌ Fehler 4: Channel-IDs."); success=False; raise Exception("Channel IDs fehlen")

            tgt_cfg, tgt_path = (self.community_teams, COMMUNITY_CONFIG_PATH) if team_type=="Community" else (self.competitive_teams, COMPETITIVE_CONFIG_PATH)
            tgt_cfg[team_name]=t_data
            save_json(tgt_path, tgt_cfg) # Lokale Aktion, kein Delay nötig
            await progress_message.edit(content=f"🎉 Team '{team_name}' erstellt!")
        except Exception as e:
            log.exception(f"Fehler Erstellung '{team_name}': {e}")
            if progress_message:
                try: await progress_message.edit(content=f"❌ Fehler. Rollback...")
                except Exception: pass
            success = False
            if created_role:
                try: await created_role.delete(reason="Rollback"); await asyncio.sleep(DEFAULT_API_DELAY)
                except Exception as de: log.error(f"Rollback Fehler Rolle: {de}")
            for ch_val in created_channels.values():
                try: await ch_val.delete(reason="Rollback"); await asyncio.sleep(DEFAULT_API_DELAY)
                except Exception as de: log.error(f"Rollback Fehler Kanal {ch_val.name}: {de}")
            if team_name in self.competitive_teams: del self.competitive_teams[team_name]; save_json(COMPETITIVE_CONFIG_PATH, self.competitive_teams)
            if team_name in self.community_teams: del self.community_teams[team_name]; save_json(COMMUNITY_CONFIG_PATH, self.community_teams)
            return False
        return True

    async def execute_team_deletion(self, interaction: Interaction, team_name: str, team_type: str) -> bool:
        guild = interaction.guild
        log.info(f"Starte Löschung '{team_name}' ({team_type}) von {interaction.user}")
        cfg_data: Optional[Dict[str, Any]] = None; cfg_path: Optional[Path] = None
        if team_type=="Community": cfg_path = COMMUNITY_CONFIG_PATH; cfg_data = load_json(cfg_path)
        elif team_type=="Competitive": cfg_path = COMPETITIVE_CONFIG_PATH; cfg_data = load_json(cfg_path)
        else: await interaction.followup.send(f"Typ '{team_type}' ungültig.", ephemeral=True); return False
        if cfg_data is None or cfg_path is None : await interaction.followup.send(f"Konfigurationsfehler für Typ '{team_type}'.", ephemeral=True); return False
        team_data = cfg_data.get(team_name)
        if not team_data: await interaction.followup.send(f"Team '{team_name}' ({team_type}) nicht gefunden.", ephemeral=True); return False

        progress_message = await interaction.followup.send(f"🔄 Lösche Team '{team_name}'...", ephemeral=True, wait=True)
        success = True; deleted = {"roles": [], "channels": [], "members": 0, "config": False}
        try:
            await progress_message.edit(content=f"🔄 ...\n➡️ 1/4: Rollen Mitglieder...")
            t_role_id=team_data.get("role_id"); c_id=team_data.get("captain_id"); m_ids=team_data.get("member_ids",[])
            t_role = guild.get_role(t_role_id) if t_role_id else None
            c_role_id_cfg = self.main_config.get("captain_role_id"); c_role = guild.get_role(c_role_id_cfg) if c_role_id_cfg else None
            g_key=f"{team_type} Team"; g_id=self.main_config.get("roles",{}).get(g_key); g_role=guild.get_role(g_id) if g_id else None
            r_rem = [r for r in [t_role, g_role] if r is not None]; c_r_rem = [r for r in [t_role, g_role, c_role] if r is not None]

            for m_idx, m_id_val in enumerate(m_ids): # Index für feineres Delay Management
                if not m_id_val: continue
                if m := guild.get_member(m_id_val):
                    current_roles_to_remove = c_r_rem if m_id_val == c_id else r_rem
                    try:
                        if current_roles_to_remove: 
                            await m.remove_roles(*current_roles_to_remove, reason=f"Team '{team_name}' gelöscht")
                            await asyncio.sleep(DEFAULT_API_DELAY / 2) # Halber Delay pro Mitglied
                        deleted["members"]+=1; log.debug(f"Rollen entfernt {m.name}")
                    except Exception as e: log.warning(f"Fehler Rollenentfernung {m.name}: {e}")
                    if (m_idx + 1) % 3 == 0: await asyncio.sleep(DEFAULT_API_DELAY) # Längere Pause nach 3 Mitgliedern
                else: log.info(f"Member {m_id_val} nicht gefunden.")

            await progress_message.edit(content=f"🔄 ...\n➡️ 2/4: Kanäle...")
            ch_ids_to_delete = [team_data.get("text_channel_id"), team_data.get("voice_channel_id"), team_data.get("private_voice_channel_id")]
            ch_ids_actual = filter(None, ch_ids_to_delete)
            for ch_id_val in ch_ids_actual:
                if ch_obj := guild.get_channel(ch_id_val):
                    try: 
                        await ch_obj.delete(reason=f"Team '{team_name}' gelöscht"); await asyncio.sleep(DEFAULT_API_DELAY)
                        deleted["channels"].append(f"'{ch_obj.name}'"); log.info(f"Kanal '{ch_obj.name}' gelöscht.")
                    except Exception as e: log.warning(f"Fehler Kanallöschung {ch_obj.name}: {e}")
                else: log.info(f"Kanal {ch_id_val} nicht gefunden.")

            await progress_message.edit(content=f"🔄 ...\n➡️ 3/4: Team-Rolle...")
            if t_role:
                try: 
                    r_name=t_role.name; await t_role.delete(reason=f"Team '{team_name}' gelöscht"); await asyncio.sleep(DEFAULT_API_DELAY)
                    deleted["roles"].append(f"'{r_name}'"); log.info(f"Rolle '{r_name}' gelöscht.")
                except Exception as e: log.warning(f"Fehler Rollenlöschung {t_role.name}: {e}")
            elif t_role_id: log.info(f"Team Rolle mit ID {t_role_id} nicht gefunden zum Löschen.")

            await progress_message.edit(content=f"🔄 ...\n➡️ 4/4: Konfiguration...")
            if team_name in cfg_data: 
                try: del cfg_data[team_name]; save_json(cfg_path, cfg_data); deleted["config"]=True; log.info(f"Eintrag '{team_name}' aus {cfg_path} entfernt.")
                except Exception as e: log.exception(f"Fehler Config Update {team_name}: {e}"); await progress_message.edit(content=f"⚠️ Fehler Config Update."); success = False 
            else: log.warning(f"Eintrag '{team_name}' bereits aus {cfg_path} entfernt."); deleted["config"]=True
            summary = f"🗑️ Team '{team_name}' ({team_type}) gelöscht.\n\n✅ Kanäle: {', '.join(deleted['channels']) if deleted['channels'] else 'Keine/Fehler'}\n✅ Rolle: {', '.join(deleted['roles']) if deleted['roles'] else 'Keine/Fehler'}\n✅ Mitglieder Rollen: {deleted['members']} angepasst.\n✅ Config: {'Ja' if deleted['config'] else 'Nein/Fehler'}\n"
            if not success: summary += "\n**⚠️ Fehler aufgetreten. Siehe Logs.**"
            await progress_message.edit(content=summary)
        except Exception as e:
            log.exception(f"Fehler Löschung '{team_name}': {e}")
            if progress_message:
                try: await progress_message.edit(content=f"❌ Unerwarteter Fehler. Siehe Logs.")
                except Exception: pass 
            return False
        return True

    async def execute_team_edit(self, interaction: Interaction, original_team_name: str, original_team_type: str, original_team_data: Dict[str, Any], changes: Dict[str, Any]) -> bool:
        guild = interaction.guild
        if not guild: await interaction.followup.send("Nur auf Servern.", ephemeral=True); return False
        log.info(f"Starte Edit Team '{original_team_name}' ({original_team_type}) von {interaction.user} mit Änderungen: {changes}")
        progress_message = await interaction.followup.send(f"🔄 Bearbeite Team '{original_team_name}'...", ephemeral=True, wait=True)
        working_data = copy.deepcopy(original_team_data); new_team_name = changes.get("name", original_team_name); new_team_type = changes.get("type", original_team_type)
        team_main_role_id = working_data.get("role_id"); team_main_role = guild.get_role(team_main_role_id) if team_main_role_id else None
        if not team_main_role: await progress_message.edit(content=f"❌ Kritischer Fehler: Haupt-Teamrolle {team_main_role_id} nicht gefunden. Abbruch."); return False

        if "name" in changes and new_team_name != original_team_name:
            await progress_message.edit(content=f"🔄 ...\n➡️ Name wird zu '{new_team_name}' geändert...")
            try: 
                await team_main_role.edit(name=new_team_name, reason=f"Team Edit: Name zu {new_team_name}"); await asyncio.sleep(DEFAULT_API_DELAY)
                log.info(f"Teamrolle zu '{new_team_name}' umbenannt.")
            except Exception as e: log.warning(f"Fehler Rollenumbenennung zu '{new_team_name}': {e}")
            
            ch_ids_to_rename = {"text": working_data.get("text_channel_id"), "voice": working_data.get("voice_channel_id"), "private_voice": working_data.get("private_voice_channel_id")}
            for ch_key, ch_id in ch_ids_to_rename.items():
                if not ch_id: continue
                channel = guild.get_channel(ch_id)
                if channel:
                    new_channel_name = new_team_name
                    if ch_key == "voice": new_channel_name = f"🔸{new_team_name}"
                    elif ch_key == "private_voice": new_channel_name = f"🔒 {new_team_name}"
                    try: 
                        await channel.edit(name=new_channel_name, reason=f"Team Edit: Name zu {new_team_name}"); await asyncio.sleep(DEFAULT_API_DELAY)
                    except Exception as e: log.warning(f"Fehler Kanalumbenennung {getattr(channel, 'name', ch_id)}: {e}")

        if "type" in changes and new_team_type != original_team_type:
            await progress_message.edit(content=f"🔄 ...\n➡️ Typ wird zu '{new_team_type}' geändert...")
            old_gen_role_id = self.main_config.get("roles", {}).get(f"{original_team_type} Team"); old_gen_role = guild.get_role(old_gen_role_id) if old_gen_role_id else None
            new_gen_role_id = self.main_config.get("roles", {}).get(f"{new_team_type} Team"); new_gen_role = guild.get_role(new_gen_role_id) if new_gen_role_id else None
            
            if not new_gen_role: 
                log.error(f"Neue allg. Rolle für {new_team_type} fehlt."); await progress_message.edit(content=f"⚠️ Fehler: Rolle für {new_team_type} fehlt!")
            else:
                for m_idx, mid_val in enumerate(working_data.get("member_ids", [])): # Index für feineres Delay Management
                    if mid_val and (m := guild.get_member(mid_val)):
                        try:
                            if old_gen_role: await m.remove_roles(old_gen_role, reason=f"Typwechsel"); await asyncio.sleep(DEFAULT_API_DELAY / 2)
                            await m.add_roles(new_gen_role, reason=f"Typwechsel"); await asyncio.sleep(DEFAULT_API_DELAY / 2)
                        except Exception as e: log.error(f"Fehler Rollenupdate Typwechsel {m.name}: {e}")
                        if (m_idx + 1) % 3 == 0: await asyncio.sleep(DEFAULT_API_DELAY) # Längere Pause nach 3 Mitgliedern
            
            text_ch_id = working_data.get("text_channel_id"); voice_ch_id = working_data.get("voice_channel_id")
            text_ch = guild.get_channel(cast(int,text_ch_id)) if text_ch_id else None; voice_ch = guild.get_channel(cast(int,voice_ch_id)) if voice_ch_id else None
            new_cat_id_val = self.main_config.get(f"{new_team_type.lower()}_category_id"); new_cat = guild.get_channel(cast(int,new_cat_id_val)) if isinstance(new_cat_id_val, int) else None
            
            if isinstance(new_cat, CategoryChannel):
                if text_ch and isinstance(text_ch, (TextChannel, VoiceChannel)): # Cast zu (TextChannel, VoiceChannel) für .edit
                    try: await text_ch.edit(category=new_cat, sync_permissions=False); await asyncio.sleep(DEFAULT_API_DELAY)
                    except Exception as e: log.warning(f"Fehler Verschieben Textkanal {text_ch.name if text_ch else 'ID unbekannt'}: {e}")
                if voice_ch and isinstance(voice_ch, (TextChannel, VoiceChannel)): # Cast zu (TextChannel, VoiceChannel) für .edit
                    try: await voice_ch.edit(category=new_cat, sync_permissions=False); await asyncio.sleep(DEFAULT_API_DELAY)
                    except Exception as e: log.warning(f"Fehler Verschieben Voicekanal {voice_ch.name if voice_ch else 'ID unbekannt'}: {e}")
            else: 
                log.error(f"Zielkategorie für '{new_team_type}' nicht gefunden."); await progress_message.edit(content=f"⚠️ Fehler: Zielkategorie für '{new_team_type}' fehlt!")

            if new_team_type == "Competitive" and original_team_type == "Community":
                if team_main_role and isinstance(new_cat, CategoryChannel):
                    ows = await self._get_permission_overwrites(guild, team_main_role, "Competitive", "private_voice_competitive")
                    try: 
                        pv_ch_new = await guild.create_voice_channel(name=f"🔒 {new_team_name}", category=new_cat, overwrites=ows); await asyncio.sleep(DEFAULT_API_DELAY)
                        working_data["private_voice_channel_id"] = pv_ch_new.id
                    except Exception as e: log.error(f"Fehler Erstellung priv. Voice: {e}")
            elif new_team_type == "Community" and original_team_type == "Competitive": 
                if pv_id := working_data.get("private_voice_channel_id"):
                    if pv_ch_old := guild.get_channel(pv_id): 
                        try: await pv_ch_old.delete(); await asyncio.sleep(DEFAULT_API_DELAY)
                        except Exception as e: log.warning(f"Fehler Löschen priv. Voice: {e}")
                working_data["private_voice_channel_id"] = None
        
        if "captain" in changes:
            new_cap_id = changes["captain"]; old_cap_id = original_team_data.get("captain_id")
            cap_role_id = self.main_config.get("captain_role_id", 0); cap_role = guild.get_role(cap_role_id) if cap_role_id else None
            if cap_role:
                if old_cap_id and old_cap_id != new_cap_id:
                    if old_m := guild.get_member(old_cap_id): 
                        try: await old_m.remove_roles(cap_role); await asyncio.sleep(DEFAULT_API_DELAY)
                        except Exception as e: log.error(f"Fehler Cpt-Rolle entfernen: {e}")
                if new_cap_id :
                    if new_m := guild.get_member(new_cap_id): 
                        try: await new_m.add_roles(cap_role); await asyncio.sleep(DEFAULT_API_DELAY)
                        except Exception as e: log.error(f"Fehler Cpt-Rolle hinzufügen: {e}")
            working_data["captain_id"] = new_cap_id

        gen_role_for_members_id = self.main_config.get("roles", {}).get(f"{new_team_type} Team", 0)
        gen_role_for_members = guild.get_role(gen_role_for_members_id) if gen_role_for_members_id else None
        roles_to_add = [r for r in [team_main_role, gen_role_for_members] if r]
        
        if "add_members" in changes:
            for m_idx, mid in enumerate(changes["add_members"]): # Index für feineres Delay Management
                if mid not in working_data.get("member_ids", []) and (m := guild.get_member(mid)):
                    try:
                        if roles_to_add: await m.add_roles(*roles_to_add); await asyncio.sleep(DEFAULT_API_DELAY / 2)
                        working_data.setdefault("member_ids", []).append(mid)
                    except Exception as e: log.error(f"Fehler Mitglied hinzufügen {m.name}: {e}")
                    if (m_idx + 1) % 3 == 0: await asyncio.sleep(DEFAULT_API_DELAY) # Längere Pause nach 3 Mitgliedern
        
        if "remove_members" in changes:
            for m_idx, mid in enumerate(changes["remove_members"]): # Index für feineres Delay Management
                if mid in working_data.get("member_ids", []) and (m := guild.get_member(mid)):
                    try:
                        roles_to_rem = [r for r in [team_main_role, gen_role_for_members] if r]
                        cap_role_main_id = self.main_config.get("captain_role_id",0); cap_role_main = guild.get_role(cap_role_main_id) if cap_role_main_id else None
                        if mid == working_data.get("captain_id") and cap_role_main and cap_role_main not in roles_to_rem: roles_to_rem.append(cap_role_main)
                        if roles_to_rem: await m.remove_roles(*roles_to_rem); await asyncio.sleep(DEFAULT_API_DELAY / 2)
                    except Exception as e: log.error(f"Fehler Mitglied entfernen {m.name}: {e}")
                    if mid in working_data["member_ids"]: working_data["member_ids"].remove(mid)
                    if mid == working_data.get("captain_id"): working_data["captain_id"] = None
                    if (m_idx + 1) % 3 == 0: await asyncio.sleep(DEFAULT_API_DELAY) # Längere Pause nach 3 Mitgliedern
        
        await progress_message.edit(content=f"🔄 ...\n➡️ Speichere Config...")
        old_cfg_path = COMMUNITY_CONFIG_PATH if original_team_type == "Community" else COMPETITIVE_CONFIG_PATH
        old_cfg = load_json(old_cfg_path)
        if original_team_name in old_cfg: del old_cfg[original_team_name]; save_json(old_cfg_path, old_cfg)
        
        new_cfg_path = COMMUNITY_CONFIG_PATH if new_team_type == "Community" else COMPETITIVE_CONFIG_PATH
        new_cfg = load_json(new_cfg_path); working_data.pop("guild_id", None); new_cfg[new_team_name] = working_data
        save_json(new_cfg_path, new_cfg) # Lokale Aktion, kein Delay

        log.info(f"Team '{original_team_name}' zu '{new_team_name}' ({new_team_type}) bearbeitet.")
        await progress_message.edit(content=f"✅ Team '{original_team_name}' erfolgreich zu '{new_team_name}' ({new_team_type}) bearbeitet!")
        return True

# --- Setup-Funktion ---
async def setup(bot: commands.Bot):
    cog = MMHelferCog(bot)
    await bot.add_cog(cog)
    log.info("MMHelferCog geladen.")