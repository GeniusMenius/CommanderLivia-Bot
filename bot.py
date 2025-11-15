import os
from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands
from discord import app_commands
import csv, io, json, logging, time
import datetime
from collections import Counter
import uuid

# ----------------------------
# Konfiguration och logging
# ----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN saknas. Sätt den som miljövariabel.")

AUTO_CLEAN_DAYS = int(os.getenv("AUTO_CLEAN_DAYS", "7"))

# GW2-klasser och roller
CLASSES = [
    "Guardian","Warrior","Revenant","Engineer","Ranger",
    "Thief","Elementalist","Mesmer","Necromancer"
]

ROLES = ["🩹 Support","⚔️ DPS"]

TIER_ORDER = {"S+":0,"S":1,"A":2,"B":3,"C":4}
ALLOWED_TIERS = ["S+","S","A","B","C"]

# Viktigt: Commander syns i roll-listan
WVW_ROLES_DISPLAY = [
    "Commander",
    "Primary Support","Secondary Support","Tertiary Support",
    "DPS","Strip DPS","Utility"
]

# --- Squad-regler & prompt-cooldown ---
MAX_SQUADS = 10  # max 10 squads => 50 spelare
PROMPT_COOLDOWN_SECONDS = 30  # per-användare cooldown för roll-prompten
last_prompt: dict[int, float] = {}  # user_id -> epoch sekunder

# ----------------------------
# Squad Templates (kvar för kompatibilitet)
# ----------------------------
SQUAD_TEMPLATES_FILE = "squad_templates.json"
squad_templates = {
    "standard": {
        "name": "Standard 2-2-1 Squad",
        "slots": [
            {"name": "Primary Support", "count": 1, "allowed_roles": ["Primary Support"]},
            {"name": "Secondary Support", "count": 1, "allowed_roles": ["Secondary Support"]},
            {"name": "DPS 1", "count": 1, "allowed_roles": ["DPS", "Strip DPS"]},
            {"name": "DPS 2", "count": 1, "allowed_roles": ["DPS", "Strip DPS"]},
            {"name": "Flex", "count": 1, "allowed_roles": ["Utility", "Tertiary Support", "DPS", "Strip DPS"]},
        ],
    },
    "ranged": {
        "name": "Ranged Comp",
        "slots": [
            {"name": "Primary Support", "count": 1, "allowed_roles": ["Primary Support"]},
            {"name": "Secondary Support", "count": 1, "allowed_roles": ["Secondary Support"]},
            {"name": "Tertiary Support", "count": 1, "allowed_roles": ["Tertiary Support"]},
            {"name": "Ranged DPS 1", "count": 1, "allowed_roles": ["DPS"]},
            {"name": "Ranged DPS 2", "count": 1, "allowed_roles": ["DPS"]},
        ],
    },
    "melee": {
        "name": "Melee Comp",
        "slots": [
            {"name": "Primary Support", "count": 1, "allowed_roles": ["Primary Support"]},
            {"name": "Secondary Support", "count": 1, "allowed_roles": ["Secondary Support"]},
            {"name": "Tertiary Support", "count": 1, "allowed_roles": ["Tertiary Support"]},
            {"name": "Melee DPS 1", "count": 1, "allowed_roles": ["DPS"]},
            {"name": "Melee DPS 2", "count": 1, "allowed_roles": ["DPS"]},
        ],
    },
}

def load_squad_templates():
    global squad_templates
    if os.path.exists(SQUAD_TEMPLATES_FILE):
        try:
            with open(SQUAD_TEMPLATES_FILE, "r") as f:
                squad_templates = json.load(f)
        except:
            pass

def save_squad_templates():
    try:
        with open(SQUAD_TEMPLATES_FILE, "w") as f:
            json.dump(squad_templates, f, indent=2)
    except Exception as e:
        logger.error(f"Fel vid sparande av squad templates: {e}")

# ----------------------------
# Custom Roller
# ----------------------------
CUSTOM_ROLES_FILE = "roles_overrides.json"
custom_roles = {}

def load_custom_roles():
    global custom_roles
    if os.path.exists(CUSTOM_ROLES_FILE):
        try:
            with open(CUSTOM_ROLES_FILE, "r", encoding="utf-8") as f:
                custom_roles = json.load(f)
        except:
            custom_roles = {}
    else:
        custom_roles = {}

def save_custom_roles():
    try:
        with open(CUSTOM_ROLES_FILE, "w", encoding="utf-8") as f:
            json.dump(custom_roles, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Fel vid sparande av custom roller: {e}")

def all_roles_for_select():
    base = WVW_ROLES_DISPLAY[:]
    extra = [r for r in custom_roles.keys() if r not in base]
    return base + extra

def role_to_bucket(role: str) -> str:
    if role in WVW_ROLES_DISPLAY:
        return role
    return custom_roles.get(role, "Utility")

# ----------------------------
# Meta Info
# ----------------------------
ELITE_SPECS_BASE = {
    "Guardian": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Dragonhunter": {"roles": ["DPS"], "tier": "A"},
        "Firebrand": {"roles": ["Primary Support"], "tier": "S+"},
        "Willbender": {"roles": ["DPS"], "tier": "B"},
        "Luminary": {"roles": ["Primary Support"], "tier": "A"},
    },
    "Warrior": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Berserker": {"roles": ["DPS"], "tier": "B"},
        "Spellbreaker": {"roles": ["DPS"], "tier": "S"},
        "Bladesworn": {"roles": ["DPS"], "tier": "A"},
        "Paragon": {"roles": ["Utility"], "tier": "C"},
    },
    "Revenant": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Herald": {"roles": ["Tertiary Support"], "tier": "B"},
        "Renegade": {"roles": ["DPS"], "tier": "S"},
        "Vindicator": {"roles": ["DPS"], "tier": "A"},
        "Conduit": {"roles": ["Utility"], "tier": "C"},
    },
    "Engineer": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Scrapper": {"roles": ["Secondary Support"], "tier": "S"},
        "Holosmith": {"roles": ["DPS"], "tier": "S"},
        "Mechanist": {"roles": ["DPS"], "tier": "S"},
        "Amalgam": {"roles": ["DPS"], "tier": "B"},
    },
    "Ranger": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Druid": {"roles": ["Secondary Support"], "tier": "S"},
        "Soulbeast": {"roles": ["Tertiary Support"], "tier": "B"},
        "Untamed": {"roles": ["DPS"], "tier": "A"},
        "Galeshot": {"roles": ["Utility"], "tier": "C"},
    },
    "Thief": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Daredevil": {"roles": ["DPS"], "tier": "A"},
        "Deadeye": {"roles": ["Strip DPS"], "tier": "S+"},
        "Specter": {"roles": ["Secondary Support"], "tier": "S"},
        "Antiquary": {"roles": ["Utility"], "tier": "C"},
    },
    "Elementalist": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Tempest": {"roles": ["Secondary Support"], "tier": "S"},
        "Weaver": {"roles": ["DPS"], "tier": "S+"},
        "Catalyst": {"roles": ["Tertiary Support"], "tier": "S"},
        "Evoker": {"roles": ["Utility"], "tier": "C"},
    },
    "Mesmer": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Chronomancer": {"roles": ["Primary Support"], "tier": "S+"},
        "Mirage": {"roles": ["DPS"], "tier": "S"},
        "Virtuoso": {"roles": ["DPS"], "tier": "S"},
        "Troubadour": {"roles": ["Utility"], "tier": "B"},
    },
    "Necromancer": {
        "Core": {"roles": ["DPS"], "tier": "C"},
        "Reaper": {"roles": ["DPS"], "tier": "S"},
        "Scourge": {"roles": ["Strip DPS"], "tier": "S+"},
        "Harbinger": {"roles": ["Tertiary Support"], "tier": "S"},
        "Ritualist": {"roles": ["Strip DPS"], "tier": "S"},
    },
}

META_FILE = "meta_overrides.json"
meta_overrides = {}

def load_meta_overrides():
    global meta_overrides
    if os.path.exists(META_FILE):
        try:
            with open(META_FILE, "r") as f:
                meta_overrides = json.load(f)
        except:
            meta_overrides = {}

def save_meta_overrides():
    try:
        with open(META_FILE, "w") as f:
            json.dump(meta_overrides, f)
    except Exception as e:
        logger.error(f"Fel vid sparande av meta_overrides: {e}")

def get_spec_meta(klass, spec):
    base = (ELITE_SPECS_BASE.get(klass, {}) or {}).get(spec, {})
    override = (meta_overrides.get(klass, {}) or {}).get(spec, {})
    roles = override.get("roles", base.get("roles", ["DPS"]))
    roles = [r for r in roles if r in all_roles_for_select()] or ["DPS"]
    tier = override.get("tier", base.get("tier", "C"))
    if tier not in ALLOWED_TIERS:
        tier = "C"
    return {"roles": roles, "tier": tier}

# Hjälp: Hitta högst-tier specs för en given roll (exempelförslag i prompten)
def best_specs_for_role(role: str, limit: int = 2) -> list[str]:
    candidates = []
    for klass, specs in ELITE_SPECS_BASE.items():
        for spec, _ in specs.items():
            meta = get_spec_meta(klass, spec)
            if role in meta["roles"]:
                candidates.append((klass, spec, TIER_ORDER.get(meta["tier"], 4)))
    candidates.sort(key=lambda x: x[2])  # lägre = bättre tier
    out = []
    for klass, spec, _ in candidates[:limit]:
        out.append(f"{klass} - {spec}")
    return out

# ----------------------------
# Tidshjälp
# ----------------------------
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def now_utc_iso():
    return now_utc().isoformat()

def parse_iso(dt_str: str) -> datetime.datetime:
    """Parsa ISO-sträng. Om ingen tz-info finns, anta UTC."""
    try:
        dt = datetime.datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return now_utc()

# ----------------------------
# Persistent data
# ----------------------------
DATA_FILE = "rsvp_data.json"
SUMMARY_CHANNELS_FILE = "summary_channels.json"
WVW_DATA_FILE = "wvw_rsvp_data.json"
WVW_SUMMARY_CHANNELS_FILE = "wvw_summary_channels.json"
WVW_EVENT_NAMES_FILE = "wvw_event_names.json"

EVENT_HISTORY_FILE = "event_history.json"
WVW_EVENT_HISTORY_FILE = "wvw_event_history.json"

rsvp_data: dict[int, dict] = {}
event_name: str = "Event"
event_summary_channels: dict[str, int] = {}  # channel_id -> message_id

# WvW data - event_id baserat
wvw_rsvp_data: dict[str, dict[int, dict]] = {}  # {event_id: {user_id: {...}}}
wvw_summary_channels: dict[str, dict] = {}  # {channel_id_eventid: {"message_id": int, "event_id": str}}
wvw_event_names: dict[str, str] = {}  # {event_id: name}

event_history: list[dict] = []
wvw_event_history: dict[str, list[dict]] = {}  # {event_id: [history_entries]}

def load_rsvp_data():
    global rsvp_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                loaded = json.load(f)
            rsvp_data = {}
            for k, v in loaded.items():
                try:
                    uid = int(k)
                    if isinstance(v, dict):
                        rsvp_data[uid] = {
                            "attending": v.get("attending", False),
                            "class": v.get("class"),
                            "role": v.get("role"),
                            "display_name": v.get("display_name", f"User_{uid}"),
                            "updated_at": v.get("updated_at", now_utc_iso()),
                        }
                        rsvp_data[uid]["updated_at"] = parse_iso(rsvp_data[uid]["updated_at"]).isoformat()
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            logger.error(f"Fel vid laddning av RSVP-data: {e}")
            rsvp_data = {}
    else:
        rsvp_data = {}

def save_rsvp_data():
    for v in rsvp_data.values():
        if not v.get("updated_at"):
            v["updated_at"] = now_utc_iso()
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(rsvp_data, f)
    except Exception as e:
        logger.error(f"Fel vid sparande av RSVP-data: {e}")

def load_summary_channels():
    global event_summary_channels, wvw_summary_channels, wvw_event_names
    # Ladda event kanaler
    if os.path.exists(SUMMARY_CHANNELS_FILE):
        try:
            with open(SUMMARY_CHANNELS_FILE, "r") as f:
                event_summary_channels = json.load(f)
        except:
            event_summary_channels = {}
    else:
        event_summary_channels = {}
    
    # Ladda WvW kanaler
    if os.path.exists(WVW_SUMMARY_CHANNELS_FILE):
        try:
            with open(WVW_SUMMARY_CHANNELS_FILE, "r") as f:
                wvw_summary_channels = json.load(f)
        except:
            wvw_summary_channels = {}
    else:
        wvw_summary_channels = {}
        
    # Ladda WvW event namn
    if os.path.exists(WVW_EVENT_NAMES_FILE):
        try:
            with open(WVW_EVENT_NAMES_FILE, "r") as f:
                wvw_event_names = json.load(f)
        except:
            wvw_event_names = {}

def save_summary_channels():
    try:
        with open(SUMMARY_CHANNELS_FILE, "w") as f:
            json.dump(event_summary_channels, f)
        with open(WVW_SUMMARY_CHANNELS_FILE, "w") as f:
            json.dump(wvw_summary_channels, f)
        with open(WVW_EVENT_NAMES_FILE, "w") as f:
            json.dump(wvw_event_names, f)
    except Exception as e:
        logger.error(f"Fel vid sparande av kanaldata: {e}")

# WvW data
def load_wvw_rsvp_data():
    global wvw_rsvp_data, wvw_event_history
    if os.path.exists(WVW_DATA_FILE):
        try:
            with open(WVW_DATA_FILE, "r") as f:
                loaded = json.load(f)
            wvw_rsvp_data = {}
            for event_id, event_data in loaded.items():
                wvw_rsvp_data[event_id] = {}
                for k, v in event_data.items():
                    try:
                        uid = int(k)
                        if isinstance(v, dict):
                            wvw_rsvp_data[event_id][uid] = {
                                "attending": v.get("attending", False),
                                "class": v.get("class"),
                                "elite_spec": v.get("elite_spec"),
                                "wvw_role": v.get("wvw_role"),
                                "display_name": v.get("display_name", f"User_{uid}"),
                                "updated_at": v.get("updated_at", now_utc_iso()),
                            }
                            wvw_rsvp_data[event_id][uid]["updated_at"] = parse_iso(wvw_rsvp_data[event_id][uid]["updated_at"]).isoformat()
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            logger.error(f"Fel vid laddning av WvW RSVP-data: {e}")
            wvw_rsvp_data = {}
    else:
        wvw_rsvp_data = {}
        
    # Ladda WvW event historik
    if os.path.exists(WVW_EVENT_HISTORY_FILE):
        try:
            with open(WVW_EVENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                wvw_event_history = json.load(f)
        except Exception as e:
            logger.error(f"Fel vid laddning av WvW-event-historik: {e}")
            wvw_event_history = {}
    else:
        wvw_event_history = {}

def save_wvw_rsvp_data():
    # Spara tidsstämplar
    for event_data in wvw_rsvp_data.values():
        for v in event_data.values():
            if not v.get("updated_at"):
                v["updated_at"] = now_utc_iso()
    try:
        with open(WVW_DATA_FILE, "w") as f:
            json.dump(wvw_rsvp_data, f)
    except Exception as e:
        logger.error(f"Fel vid sparande av WvW RSVP-data: {e}")

# ----- Historikloaders -----
def load_event_history():
    global event_history
    if os.path.exists(EVENT_HISTORY_FILE):
        try:
            with open(EVENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                event_history = json.load(f)
        except Exception as e:
            logger.error(f"Fel vid laddning av event-historik: {e}")
            event_history = []
    else:
        event_history = []

def load_wvw_event_history():
    global wvw_event_history
    if os.path.exists(WVW_EVENT_HISTORY_FILE):
        try:
            with open(WVW_EVENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                wvw_event_history = json.load(f)
        except Exception as e:
            logger.error(f"Fel vid laddning av WvW-event-historik: {e}")
            wvw_event_history = {}
    else:
        wvw_event_history = {}

# ----- Historik-archivers -----
def archive_current_event(closed_by: int | None = None):
    """Spara en snapshot av nuvarande legacy-event till historikfil."""
    global event_history
    if not rsvp_data:
        return

    snapshot = {
        "name": event_name,
        "closed_at": now_utc_iso(),
        "closed_by": closed_by,
        "entries": [],
    }

    for uid, d in rsvp_data.items():
        snapshot["entries"].append(
            {
                "user_id": uid,
                "display_name": d.get("display_name"),
                "attending": d.get("attending", False),
                "class": d.get("class"),
                "role": d.get("role"),
                "updated_at": d.get("updated_at"),
            }
        )

    event_history.append(snapshot)
    try:
        with open(EVENT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(event_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Fel vid sparande av event-historik: {e}")

def archive_current_wvw_event(event_id: str, closed_by: int | None = None):
    """Spara en snapshot av ett specifikt WvW-event till historikfil."""
    global wvw_event_history
    event_data = wvw_rsvp_data.get(event_id, {})
    if not event_data:
        return

    event_name_local = wvw_event_names.get(event_id, f"WvW Event {event_id[:8]}")
    
    snapshot = {
        "name": event_name_local,
        "closed_at": now_utc_iso(),
        "closed_by": closed_by,
        "entries": [],
    }

    for uid, d in event_data.items():
        snapshot["entries"].append(
            {
                "user_id": uid,
                "display_name": d.get("display_name"),
                "attending": d.get("attending", False),
                "class": d.get("class"),
                "elite_spec": d.get("elite_spec"),
                "wvw_role": d.get("wvw_role"),
                "updated_at": d.get("updated_at"),
            }
        )

    if event_id not in wvw_event_history:
        wvw_event_history[event_id] = []
    wvw_event_history[event_id].append(snapshot)
    
    try:
        with open(WVW_EVENT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(wvw_event_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Fel vid sparande av WvW-event-historik: {e}")

# ----------------------------
# Auto-clean
# ----------------------------
def clean_old_data(days: int = AUTO_CLEAN_DAYS) -> int:
    if days <= 0:
        return 0
    cutoff = now_utc() - datetime.timedelta(days=days)
    to_del = []
    
    # Rensa vanlig RSVP
    for uid, v in rsvp_data.items():
        ts = parse_iso(v.get("updated_at", now_utc_iso()))
        if ts < cutoff:
            to_del.append(uid)
    for uid in to_del:
        del rsvp_data[uid]
    if to_del:
        save_rsvp_data()
    
    # Rensa WvW RSVP
    total_deleted = len(to_del)
    to_del_wvw = []
    for event_id, event_data in wvw_rsvp_data.items():
        for uid, v in event_data.items():
            ts = parse_iso(v.get("updated_at", now_utc_iso()))
            if ts < cutoff:
                to_del_wvw.append((event_id, uid))
    for event_id, uid in to_del_wvw:
        del wvw_rsvp_data[event_id][uid]
        # Ta bort tomma event
        if not wvw_rsvp_data[event_id]:
            del wvw_rsvp_data[event_id]
            if event_id in wvw_event_names:
                del wvw_event_names[event_id]
            # Ta bort alla kanal-referenser för detta event
            keys_to_remove = []
            for channel_key, info in wvw_summary_channels.items():
                if info.get("event_id") == event_id:
                    keys_to_remove.append(channel_key)
            for key in keys_to_remove:
                del wvw_summary_channels[key]
    if to_del_wvw:
        save_wvw_rsvp_data()
        save_summary_channels()
    
    return total_deleted + len(to_del_wvw)

# ----------------------------
# Squad Formation – Balanserad builder (analys)
# ----------------------------
def _role_counts_from_attending(attending_pairs: list[tuple[int, dict]]) -> dict[str, int]:
    base = {
        "Commander": 0,
        "Primary Support": 0,
        "Secondary Support": 0,
        "Tertiary Support": 0,
        "Strip DPS": 0,
        "DPS": 0,
        "Utility": 0,
    }
    for _, d in attending_pairs:
        r = d.get("wvw_role")
        if r in base:
            base[r] += 1
    return base

def _tier_order_for(uid: int, data: dict) -> int:
    meta = get_spec_meta(data.get("class", ""), data.get("elite_spec", ""))
    return TIER_ORDER.get(meta.get("tier", "C"), 4)

def _rank_key(uid: int, data: dict) -> tuple:
    # lägre är bättre: tier → färskast → uid
    return (
        _tier_order_for(uid, data),
        0 - parse_iso(data.get("updated_at", now_utc_iso())).timestamp(),
        uid,
    )

def _pick_best(candidates: list[tuple[int, dict]], allow_roles: list[str]) -> tuple[int, dict] | None:
    filtered = [(uid, d) for (uid, d) in candidates if d.get("wvw_role") in allow_roles]
    if not filtered:
        return None
    filtered.sort(key=lambda t: _rank_key(t[0], t[1]))
    return filtered[0]

def preview_next_missing_role(attending_pairs_wo_self: list[tuple[int, dict]]) -> str | None:
    """
    Returnerar "Primary Support" / "Secondary Support" / "Tertiary Support" om det är
    den kritiska bristen för att kunna få ihop NÄSTA squad. Annars None.
    Regler:
      - 1 global Commander ersätter Primary i första squaden.
      - Squads 2..10 kräver Primary + Secondary.
      - Tertiary är önskad (fallback till Strip/DPS/Utility).
    """
    counts = _role_counts_from_attending(attending_pairs_wo_self)
    commander_exists = counts["Commander"] > 0

    support_cap = min(
        counts["Secondary Support"],
        counts["Primary Support"] + (1 if commander_exists else 0),
        MAX_SQUADS,
    )

    dps_like = counts["DPS"] + counts["Strip DPS"]
    possible_by_dps = dps_like // 2

    built_guess = min(support_cap, possible_by_dps)

    next_squad = built_guess + 1
    if next_squad > MAX_SQUADS:
        return None

    if next_squad == 1 and commander_exists:
        if counts["Secondary Support"] < 1:
            return "Secondary Support"
    else:
        needed_primary = max(0, next_squad - 1)
        needed_secondary = next_squad
        if counts["Primary Support"] < needed_primary:
            return "Primary Support"
        if counts["Secondary Support"] < needed_secondary:
            return "Secondary Support"

    if counts["Tertiary Support"] < next_squad:
        return "Tertiary Support"

    return None

def build_squads_balanced(event_id: str):
    """
    Returnerar:
      commander: tuple[int, dict] | None
      squads: list[list[tuple[str, int, dict]]]
      overflow: list[tuple[int, dict]]
      reason: dict   # {"type": "cap"/"imbalance"/"none", "message": "...", "counts": {...}}
    """
    event_data = wvw_rsvp_data.get(event_id, {})
    attending = [(uid, d) for uid, d in event_data.items() if d.get("attending") and d.get("wvw_role")]
    attending.sort(key=lambda t: _rank_key(t[0], t[1]))

    # 1) Global Commander
    commanders = [(uid, d) for uid, d in attending if d.get("wvw_role") == "Commander"]
    commander = None
    used = set()
    if commanders:
        commanders.sort(key=lambda t: _rank_key(t[0], t[1]))
        commander = commanders[0]
        used.add(commander[0])

    def remaining():
        return [(uid, d) for (uid, d) in attending if uid not in used]

    squads: list[list[tuple[str, int, dict]]] = []

    # 2) Squad 1 (Commander-squad) – Commander ersätter Primary
    if commander:
        cand = remaining()
        sec = _pick_best(cand, ["Secondary Support"])
        if sec:
            used.add(sec[0])
            cand = remaining()
            tert = _pick_best(cand, ["Tertiary Support"]) or _pick_best(cand, ["Strip DPS", "DPS", "Utility"])
            if tert:
                used.add(tert[0])
                cand = remaining()
            dps1 = _pick_best(cand, ["Strip DPS", "DPS"])
            if dps1:
                used.add(dps1[0])
                cand = remaining()
            dps2 = _pick_best(cand, ["Strip DPS", "DPS"])
            if dps2:
                used.add(dps2[0])

            squad = []
            squad.append(("Commander", commander[0], commander[1]))
            if sec:
                squad.append(("Secondary Support", sec[0], sec[1]))
            if tert:
                squad.append(
                    (
                        "Tertiary Support"
                        if tert[1].get("wvw_role") == "Tertiary Support"
                        else tert[1].get("wvw_role"),
                        tert[0],
                        tert[1],
                    )
                )
            if dps1:
                squad.append((dps1[1].get("wvw_role"), dps1[0], dps1[1]))
            if dps2:
                squad.append((dps2[1].get("wvw_role"), dps2[0], dps2[1]))

            if len(squad) == 5:
                squads.append(squad)
            else:
                for label, uid, _ in squad[1:]:
                    used.discard(uid)

    # 3) Squad 2..10: Primary + Secondary + Tertiary/fallback + 2×DPS
    while len(squads) < MAX_SQUADS:
        cand = remaining()
        if len(cand) < 5:
            break

        prim = _pick_best(cand, ["Primary Support"])
        if not prim:
            break
        used.add(prim[0])
        cand = remaining()

        sec = _pick_best(cand, ["Secondary Support"])
        if not sec:
            used.discard(prim[0])
            break
        used.add(sec[0])
        cand = remaining()

        tert = _pick_best(cand, ["Tertiary Support"]) or _pick_best(cand, ["Strip DPS", "DPS", "Utility"])
        if tert:
            used.add(tert[0])
            cand = remaining()

        dps1 = _pick_best(cand, ["Strip DPS", "DPS"])
        if dps1:
            used.add(dps1[0])
            cand = remaining()
        dps2 = _pick_best(cand, ["Strip DPS", "DPS"])
        if dps2:
            used.add(dps2[0])

        squad = []
        squad.append(("Primary Support", prim[0], prim[1]))
        squad.append(("Secondary Support", sec[0], sec[1]))
        if tert:
            squad.append(
                (
                    "Tertiary Support"
                    if tert[1].get("wvw_role") == "Tertiary Support"
                    else tert[1].get("wvw_role"),
                    tert[0],
                    tert[1],
                )
            )
        if dps1:
            squad.append((dps1[1].get("wvw_role"), dps1[0], dps1[1]))
        if dps2:
            squad.append((dps2[1].get("wvw_role"), dps2[0], dps2[1]))

        if len(squad) == 5:
            squads.append(squad)
        else:
            for label, uid, _ in squad:
                used.discard(uid)
            break

        if len(squads) >= MAX_SQUADS:
            break

    overflow = [(uid, d) for (uid, d) in attending if uid not in used and (not commander or uid != commander[0])]

    # 4) Orsak till overflow
    counts = _role_counts_from_attending([(uid, d) for (uid, d) in attending if (not commander or uid != commander[0])])
    reason = {"type": "none", "message": "", "counts": counts}
    if len(squads) >= MAX_SQUADS and overflow:
        reason["type"] = "cap"
        reason["message"] = f"Begränsning: Max {MAX_SQUADS} squads (50 spelare)."
    else:
        missing = preview_next_missing_role([(uid, d) for (uid, d) in attending])
        if missing:
            reason["type"] = "imbalance"
            reason["message"] = f"Obalans: Saknar **{missing}** för att bygga nästa squad."

    return commander, squads, overflow, reason

# ----------------------------
# Views och Components
# ----------------------------
class RSVPView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ja, jag kommer", style=discord.ButtonStyle.success, custom_id="rsvp_yes_button")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in rsvp_data and rsvp_data[uid]["attending"]:

            rsvp_data[uid]["updated_at"] = now_utc_iso()
            save_rsvp_data()

            curr = rsvp_data.get(uid, {})
            if curr.get("class"):
                await interaction.response.send_message(
                    f"✅ Du är anmäld som **{curr['class']} ({curr.get('role','?')})**.\n"
                    f"Vill du ändra? Välj ny roll:",
                    view=RoleSelectView(curr["class"]),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "✅ Du har redan RSVP:at. Vill du ändra? Välj ny klass/roll:",
                    view=ClassSelectView(),
                    ephemeral=True,
                )

            await update_all_event_summaries(interaction.client)
            return

        await interaction.response.send_message("Välj din klass:", view=ClassSelectView(), ephemeral=True)

    @discord.ui.button(label="Nej, jag kommer inte", style=discord.ButtonStyle.danger, custom_id="rsvp_no_button")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        rsvp_data[uid] = {
            "attending": False,
            "class": None,
            "role": None,
            "display_name": interaction.user.display_name,
            "updated_at": now_utc_iso(),
        }
        save_rsvp_data()
        await interaction.response.send_message("❌ Okej! Markerat att du **inte kommer**.", ephemeral=True)
        await update_all_event_summaries(interaction.client)


class WvWRSVPView(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=None)
        self.event_id = event_id

    @discord.ui.button(label="Ja, jag kommer", style=discord.ButtonStyle.success, custom_id="wvw_rsvp_yes_button")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        event_data = wvw_rsvp_data.get(self.event_id, {})
        if uid in event_data and event_data[uid]["attending"]:

            event_data[uid]["updated_at"] = now_utc_iso()
            save_wvw_rsvp_data()

            curr = event_data.get(uid, {})
            klass = curr.get("class") or "Okänd klass"
            spec = curr.get("elite_spec") or "okänd spec"
            role = curr.get("wvw_role") or "okänd roll"

            await interaction.response.send_message(
                f"✅ Du är anmäld som **{klass} - {spec}** med roll **{role}**.\n"
                f"Vill du ändra? Välj klass → spec → roll:",
                view=WvWClassSelectView(self.event_id),
                ephemeral=True,
            )
            await update_wvw_summary(interaction.client, self.event_id)
            return

        await interaction.response.send_message("Välj din klass:", view=WvWClassSelectView(self.event_id), ephemeral=True)

    @discord.ui.button(label="Nej, jag kommer inte", style=discord.ButtonStyle.danger, custom_id="wvw_rsvp_no_button")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        event_data = wvw_rsvp_data.setdefault(self.event_id, {})
        event_data[uid] = {
            "attending": False,
            "class": None,
            "elite_spec": None,
            "wvw_role": None,
            "display_name": interaction.user.display_name,
            "updated_at": now_utc_iso(),
        }
        save_wvw_rsvp_data()
        await interaction.response.send_message("❌ Okej! Markerat att du **inte kommer**.", ephemeral=True)
        await update_wvw_summary(interaction.client, self.event_id)

# Legacy Views
class ClassSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        placeholder="Välj din klass...",
        options=[discord.SelectOption(label=cls, value=cls) for cls in CLASSES],
        custom_id="gw2_class_select",
    )
    async def class_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.send_message("Välj din roll:", view=RoleSelectView(select.values[0]), ephemeral=True)


class RoleSelectView(discord.ui.View):
    def __init__(self, selected_class: str):
        super().__init__(timeout=300)
        self.selected_class = selected_class

    @discord.ui.select(
        placeholder="Välj din roll...",
        options=[discord.SelectOption(label=r, value=r) for r in ROLES],
        custom_id="gw2_role_select",
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        uid = interaction.user.id
        selected_role = select.values[0]
        rsvp_data[uid] = {
            "attending": True,
            "class": self.selected_class,
            "role": selected_role,
            "display_name": interaction.user.display_name,
            "updated_at": now_utc_iso(),
        }
        save_rsvp_data()
        await interaction.response.send_message(
            f"✅ Du kommer som **{self.selected_class} ({selected_role})** – tack för svaret!", ephemeral=True
        )
        await update_all_event_summaries(interaction.client)

# WvW Views
class WvWClassSelectView(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=300)
        self.event_id = event_id

    @discord.ui.select(
        placeholder="Välj din klass...",
        options=[discord.SelectOption(label=cls, value=cls) for cls in CLASSES],
        custom_id="wvw_class_select",
    )
    async def class_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            "Välj din elite specialization:",
            view=WvWEliteSpecSelectView(self.event_id, select.values[0]),
            ephemeral=True,
        )

class WvWEliteSpecSelectView(discord.ui.View):
    def __init__(self, event_id: str, selected_class: str):
        super().__init__(timeout=300)
        self.event_id = event_id
        self.selected_class = selected_class

        options = [
            discord.SelectOption(label=spec, value=spec)
            for spec in ELITE_SPECS_BASE.get(self.selected_class, {}).keys()
        ]

        self.select = discord.ui.Select(
            placeholder="Välj din elite specialization...",
            options=options,
            custom_id="wvw_elite_spec_select",
        )

        async def _on_select(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            selected_spec = self.select.values[0]
            meta = get_spec_meta(self.selected_class, selected_spec)
            await interaction.followup.send(
                f"**{self.selected_class} - {selected_spec}**\n"
                f"Tier: {meta['tier']}\n"
                f"Rekommenderade roller: {', '.join(meta['roles'])}\n\n"
                "Välj din roll:",
                view=WvWRoleSelectView(self.event_id, self.selected_class, selected_spec),
                ephemeral=True,
            )

        self.select.callback = _on_select
        self.add_item(self.select)

# --- Mjuk prompt + cooldown i roll-steget (med meta-exempel) ---
class SuggestAltRoleView(discord.ui.View):
    def __init__(self, event_id: str, klass, spec, missing_role, chosen_role):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.klass, self.spec = klass, spec
        self.missing_role = missing_role
        self.chosen_role = chosen_role

        self.add_item(RoleChoiceButton(event_id, klass, spec, missing_role, label=f"Byt till {missing_role}"))
        self.add_item(ProceedButton(event_id, klass, spec, chosen_role, label="Behåll mitt val"))

class RoleChoiceButton(discord.ui.Button):
    def __init__(self, event_id: str, klass, spec, role, label):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.event_id = event_id
        self.klass, self.spec, self.role = klass, spec, role

    async def callback(self, interaction: discord.Interaction):
        uid = interaction.user.id
        event_data = wvw_rsvp_data.setdefault(self.event_id, {})
        event_data[uid] = {
            "attending": True,
            "class": self.klass,
            "elite_spec": self.spec,
            "wvw_role": self.role,
            "display_name": interaction.user.display_name,
            "updated_at": now_utc_iso(),
        }
        save_wvw_rsvp_data()
        await interaction.response.edit_message(content=f"✅ Tack! Bytte roll till **{self.role}**.", view=None)
        await update_wvw_summary(interaction.client, self.event_id)

class ProceedButton(discord.ui.Button):
    def __init__(self, event_id: str, klass, spec, role, label):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.event_id = event_id
        self.klass, self.spec, self.role = klass, spec, role

    async def callback(self, interaction: discord.Interaction):
        uid = interaction.user.id
        event_data = wvw_rsvp_data.setdefault(self.event_id, {})
        event_data[uid] = {
            "attending": True,
            "class": self.klass,
            "elite_spec": self.spec,
            "wvw_role": self.role,
            "display_name": interaction.user.display_name,
            "updated_at": now_utc_iso(),
        }
        save_wvw_rsvp_data()
        await interaction.response.edit_message(content=f"👍 Okej! Behåller **{self.role}**.", view=None)
        await update_wvw_summary(interaction.client, self.event_id)

class WvWRoleSelectView(discord.ui.View):
    """Rollväljare som bara visar roller tillåtna för vald klass/spec."""
    def __init__(self, event_id: str, selected_class: str, selected_spec: str):
        super().__init__(timeout=300)
        self.event_id = event_id
        self.selected_class = selected_class
        self.selected_spec = selected_spec

        meta = get_spec_meta(self.selected_class, self.selected_spec)
        self.allowed_roles = list(meta["roles"])

        options = [discord.SelectOption(label=r, value=r) for r in self.allowed_roles]
        self.select = discord.ui.Select(
            placeholder=f"Välj roll ({self.selected_class} · {self.selected_spec})...",
            options=options,
            custom_id="wvw_role_select",
        )

        async def _on_select(interaction: discord.Interaction):
            uid = interaction.user.id
            chosen_role = self.select.values[0]
            event_data = wvw_rsvp_data.setdefault(self.event_id, {})

            if chosen_role not in self.allowed_roles:
                await interaction.response.send_message(
                    "❌ Ogiltigt val för denna specialization. Välj en roll från listan.",
                    ephemeral=True,
                )
                return

            now_ts = time.time()
            last = last_prompt.get(uid, 0)
            can_prompt = (now_ts - last) >= PROMPT_COOLDOWN_SECONDS

            attending_wo_self = [(u, d) for (u, d) in event_data.items() if d.get("attending") and u != uid]
            missing = preview_next_missing_role(attending_wo_self)

            if can_prompt and missing and missing != chosen_role and (missing in self.allowed_roles):
                last_prompt[uid] = now_ts
                examples = best_specs_for_role(missing, limit=2)
                ex_str = f" (t.ex. {', '.join(examples)})" if examples else ""
                txt = (
                    f"⚖️ Vi saknar just nu **{missing}** för att få ihop nästa squad{ex_str}.\n"
                    f"Vill du byta roll?"
                )
                await interaction.response.send_message(
                    txt,
                    view=SuggestAltRoleView(
                        self.event_id,
                        self.selected_class,
                        self.selected_spec,
                        missing_role=missing,
                        chosen_role=chosen_role,
                    ),
                    ephemeral=True,
                )
                return

            event_data[uid] = {
                "attending": True,
                "class": self.selected_class,
                "elite_spec": self.selected_spec,
                "wvw_role": chosen_role,
                "display_name": interaction.user.display_name,
                "updated_at": now_utc_iso(),
            }
            save_wvw_rsvp_data()

            meta_now = get_spec_meta(self.selected_class, self.selected_spec)
            await interaction.response.send_message(
                f"✅ Du kommer som **{self.selected_class} - {self.selected_spec}** "
                f"(Tier {meta_now['tier']}) med roll **{chosen_role}** – tack!",
                ephemeral=True,
            )
            await update_wvw_summary(interaction.client, self.event_id)

        self.select.callback = _on_select
        self.add_item(self.select)

# ----------------------------
# Sammanställning
# ----------------------------
async def update_all_event_summaries(client: commands.Bot):
    """Uppdatera alla event-sammanfattningar i alla kanaler"""
    global event_summary_channels
    
    channels_to_update = list(event_summary_channels.items())
    
    for channel_id, message_id in channels_to_update:
        try:
            channel = client.get_channel(int(channel_id)) or await client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            if channel_id in event_summary_channels:
                del event_summary_channels[channel_id]
                save_summary_channels()
            continue
        except Exception as e:
            logger.error(f"Fel vid hämtning av sammanfattningsmeddelande för kanal {channel_id}: {e}")
            continue

        attending, not_attending = [], []
        for uid, data in rsvp_data.items():
            name = data.get("display_name", f"<@{uid}>")
            if data["attending"]:
                attending.append(f"- {name} — {data['class']} ({data['role']})")
            else:
                not_attending.append(f"- {name}")

        embed = discord.Embed(title=f"🎉 Event – {event_name}", color=0x3498db)
        embed.add_field(name="✅ Ja:", value="\n".join(attending) if attending else "-", inline=False)
        embed.add_field(name="❌ Nej:", value="\n".join(not_attending) if not_attending else "-", inline=False)

        try:
            await message.edit(embed=embed)
        except Exception as e:
            logger.error(f"Fel vid uppdatering av sammanfattningsmeddelande för kanal {channel_id}: {e}")

async def update_wvw_summary(client: commands.Bot, event_id: str):
    """Uppdatera WvW-sammanfattning för ett specifikt event"""
    event_data = wvw_rsvp_data.get(event_id, {})
    event_name_local = wvw_event_names.get(event_id, f"WvW Event {event_id[:8]}")
    
    channels_to_update = []
    for channel_key, info in wvw_summary_channels.items():
        if info.get("event_id") == event_id:
            channels_to_update.append((channel_key, info["message_id"]))
    
    for channel_key, message_id in channels_to_update:
        try:
            # Extrahera channel_id från channel_key
            channel_id = channel_key.split('_')[0]
            channel = client.get_channel(int(channel_id)) or await client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            if channel_key in wvw_summary_channels:
                del wvw_summary_channels[channel_key]
                save_summary_channels()
            continue
        except Exception as e:
            logger.error(f"Fel vid hämtning av WvW sammanfattningsmeddelande för kanal {channel_key}: {e}")
            continue

        attending, not_attending = [], []
        for uid, data in event_data.items():
            name = data.get("display_name", f"<@{uid}>")
            if data["attending"]:

                klass = data.get("class", "Okänd klass")
                elite_spec = data.get("elite_spec", "")
                wvw_role = data.get("wvw_role", "Okänd roll")
                klass_info = f"{klass}" + (f" - {elite_spec}" if elite_spec else "")
                attending.append(f"• **{name}** — {klass_info}\n  `{wvw_role}`")
            else:
                not_attending.append(f"• **{name}**")

        embed = discord.Embed(title=f"🛡️ {event_name_local}", color=0xe74c3c)
        embed.add_field(name="✅ Ja:", value="\n".join(attending) if attending else "-", inline=False)
        embed.add_field(name="❌ Nej:", value="\n".join(not_attending) if not_attending else "-", inline=False)

        try:
            await message.edit(embed=embed)
        except Exception as e:
            logger.error(f"Fel vid uppdatering av WvW sammanfattningsmeddelande för kanal {channel_key}: {e}")

# ----------------------------
# Bot Setup med auto guild sync
# ----------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

GUILD_ID = os.getenv("DISCORD_GUILD_ID")

class Bot(commands.Bot):
    async def setup_hook(self):
        # Ladda all persistent data innan vi registrerar views
        load_rsvp_data()
        load_wvw_rsvp_data()
        load_summary_channels()
        load_custom_roles()
        load_meta_overrides()
        load_squad_templates()
        load_event_history()
        # load_wvw_event_history() behövs inte separat, då det redan görs i load_wvw_rsvp_data()

        # Persistent views
        self.add_view(RSVPView())
        # Lägg till en persistent view per aktivt WvW-event
        for event_id in wvw_rsvp_data.keys():
            self.add_view(WvWRSVPView(event_id))

        try:
            # 1) Synka GLOBALT (alla servrar – propagerar i Discord)
            synced_global = await self.tree.sync()
            logger.info(f"🌍 Synkade {len(synced_global)} globala kommandon.")

            # 2) Om guild är satt: spegla globala → guild och synka direkt där
            if GUILD_ID and GUILD_ID.isdigit():
                guild = discord.Object(id=int(GUILD_ID))
                # Rensa guildens kommando-träd så copy inte dubblar mellan omstarter
                self.tree.clear_commands(guild=guild)
                self.tree.copy_global_to(guild=guild)
                synced_guild = await self.tree.sync(guild=guild)
                logger.info(f"🔹 Synkade {len(synced_guild)} kommandon till guild {GUILD_ID}.")
        except Exception as e:
            logger.error(f"Synkfel: {e}")

bot = Bot(command_prefix=commands.when_mentioned_or("!"), intents=intents)

@bot.event
async def on_ready():
    logger.info(f"{bot.user} är igång som Commander Livia!")

# ----------------------------
# Debugkommandon
# ----------------------------
@bot.command()
async def sync(ctx):
    """Synka både globalt och till den konfigurerade guilden."""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("🚫 Du måste vara admin.", delete_after=5)
        return
    try:
        # Global sync
        synced_global = await bot.tree.sync()
        msg = f"🌍 Globala: {len(synced_global)}"

        # Guild sync om satt
        if GUILD_ID and GUILD_ID.isdigit():
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.clear_commands(guild=guild)
            bot.tree.copy_global_to(guild=guild)
            synced_guild = await bot.tree.sync(guild=guild)
            msg += f" | 🔹 Guild {GUILD_ID}: {len(synced_guild)}"

        await ctx.send(f"🔁 Synk klar – {msg}")
        logger.info(f"🔁 Synk klar – {msg}")
    except Exception as e:
        await ctx.send(f"❌ Synk misslyckades: {e}")
        logger.error(f"Synkfel: {e}")

@bot.command()
async def clear_commands(ctx):
    """Rensar alla registrerade slash-kommandon (globalt)"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("🚫 Du måste vara admin.", delete_after=5)
        return
    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        await ctx.send("🗑️ Alla kommandon rensade.")
        logger.info("🗑️ Alla kommandon rensade.")
    except Exception as e:
        await ctx.send("❌ Kunde inte rensa kommandon.")
        logger.error(f"Rensningsfel: {e}")

# ----------------------------
# ADMIN & SETUP-KOMMANDON
# ----------------------------
@bot.tree.command(name="event", description="Hantera vanligt event")
@app_commands.describe(
    action="start/add_channel/remove_channel/reset/export",
    name="Valfritt namn på eventet (endast vid start)",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="add_channel", value="add_channel"),
        app_commands.Choice(name="remove_channel", value="remove_channel"),
        app_commands.Choice(name="reset", value="reset"),
        app_commands.Choice(name="export", value="export"),
    ]
)
async def event_command(interaction: discord.Interaction, action: str, name: str | None = None):
    channel_id = str(interaction.channel_id)
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("🚫 Du har inte behörighet att använda detta kommando.", ephemeral=True)
        return

    if action == "start":
        global event_name
        event_name = name or "Event"
        try:
            await interaction.response.defer(ephemeral=True)
            # Skicka RSVP i denna kanal
            await interaction.channel.send(f"🎉 RSVP till eventet **{event_name}**!", view=RSVPView())
            summary_msg = await interaction.channel.send(
                embed=discord.Embed(title=f"🎉 Event – {event_name}", description="Laddar...")
            )
            
            # Lägg till denna kanal i listan
            event_summary_channels[channel_id] = summary_msg.id
            save_summary_channels()
            
            await interaction.followup.send(f"✅ Event **{event_name}** startat! Denna kanal är nu aktiv.", ephemeral=True)
            await update_all_event_summaries(interaction.client)
        except Exception as e:
            logger.error(f"Fel vid start av event: {e}")
            await interaction.followup.send("❌ Kunde inte starta eventet.", ephemeral=True)

    elif action == "add_channel":
        if not event_summary_channels:  # Inget aktivt event
            await interaction.response.send_message("❌ Inget aktivt event. Starta ett först med `/event start`.", ephemeral=True)
            return
            
        try:
            await interaction.response.defer(ephemeral=True)
            # Skicka RSVP i denna kanal
            await interaction.channel.send(f"🎉 RSVP till eventet **{event_name}**!", view=RSVPView())
            summary_msg = await interaction.channel.send(
                embed=discord.Embed(title=f"🎉 Event – {event_name}", description="Laddar...")
            )
            
            # Lägg till denna kanal i listan
            event_summary_channels[channel_id] = summary_msg.id
            save_summary_channels()
            
            await interaction.followup.send(f"✅ Denna kanal är nu en del av eventet **{event_name}**!", ephemeral=True)
            await update_all_event_summaries(interaction.client)
        except Exception as e:
            logger.error(f"Fel vid tillägg av kanal: {e}")
            await interaction.followup.send("❌ Kunde inte lägga till kanalen.", ephemeral=True)

    elif action == "remove_channel":
        if channel_id in event_summary_channels:
            try:
                # Ta bort meddelandet
                channel = interaction.client.get_channel(int(channel_id)) or await interaction.client.fetch_channel(int(channel_id))
                message = await channel.fetch_message(event_summary_channels[channel_id])
                await message.delete()
            except:
                pass
            
            del event_summary_channels[channel_id]
            save_summary_channels()
            await interaction.response.send_message("✅ Denna kanal är nu borttagen från eventet.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Denna kanal är inte en del av något event.", ephemeral=True)

    elif action == "reset":
        # Spara snapshot först
        archive_current_event(closed_by=interaction.user.id)

        rsvp_data.clear()
        save_rsvp_data()
        await interaction.response.send_message("🔄 Event-data nollställt (snapshot sparad i historiken).", ephemeral=True)
        await update_all_event_summaries(interaction.client)

    elif action == "export":
        # Samma export-logik som innan
        try:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["User ID","Display Name","Attending","Class","Role","Updated At (UTC)"])
            attending_count = sum(1 for d in rsvp_data.values() if d["attending"])
            not_attending_count = len(rsvp_data) - attending_count
            for uid, d in rsvp_data.items():
                name_disp = d.get("display_name", "Unknown")
                writer.writerow([
                    uid, name_disp,
                    "Yes" if d["attending"] else "No",
                    d["class"] or "", d["role"] or "", d.get("updated_at","")
                ])
            output.seek(0)
            filename = f"rsvp_export_{attending_count}_attending.csv"
            await interaction.response.send_message(
                f"📄 Export klar! {attending_count} attending, {not_attending_count} not attending",
                file=discord.File(io.BytesIO(output.getvalue().encode()), filename=filename),
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Fel vid export: {e}")
            await interaction.response.send_message("❌ Kunde inte exportera data.", ephemeral=True)

@bot.tree.command(
    name="event_clear_all",
    description="Tar bort både event och WvW-event från alla kanaler och nollställer all data (med snapshot)."
)
async def event_clear_all(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "🚫 Du har inte behörighet att använda detta kommando.",
            ephemeral=True
        )
        return

    # ✅ Svara direkt så interaktionen inte hinner dö
    await interaction.response.defer(ephemeral=True)

    global event_summary_channels, wvw_summary_channels

    # 🔐 Spara snapshots först
    archive_current_event(closed_by=interaction.user.id)
    
    # Arkivera alla WvW events
    for event_id in list(wvw_rsvp_data.keys()):
        archive_current_wvw_event(event_id, closed_by=interaction.user.id)
    
    # 🧹 Ta bort alla sammanfattningsmeddelanden för vanliga event
    for channel_id, message_id in list(event_summary_channels.items()):
        try:
            channel = interaction.client.get_channel(int(channel_id)) \
                      or await interaction.client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(message_id)
            await message.delete()
        except Exception as e:
            # T.ex. Missing Permissions eller kanalen borttagen
            logger.warning(f"Misslyckades ta bort event-sammanfattning i kanal {channel_id}: {e}")
            continue
    
    # 🧹 Ta bort alla sammanfattningsmeddelanden för WvW-event
    for channel_key, info in list(wvw_summary_channels.items()):
        try:
            channel_id = channel_key.split('_')[0]
            channel = interaction.client.get_channel(int(channel_id)) \
                      or await interaction.client.fetch_channel(int(channel_id))
            message = await channel.fetch_message(info["message_id"])
            await message.delete()
        except Exception as e:
            logger.warning(f"Misslyckades ta bort WvW-sammanfattning i kanal {channel_key}: {e}")
            continue
    
    # Rensa all data i minnet
    event_summary_channels.clear()
    wvw_summary_channels.clear()
    rsvp_data.clear()
    wvw_rsvp_data.clear()
    wvw_event_names.clear()
    
    # Spara till disk
    save_summary_channels()
    save_rsvp_data()
    save_wvw_rsvp_data()
    
    # ✅ Skicka slut-svar via followup
    await interaction.followup.send(
        "✅ Både **Event** och **WvW-event** är nu rensade från alla kanaler och all RSVP-data är nollställd.\n"
        "📦 Snapshot av deltagare/byggen sparad i `event_history.json` och `wvw_event_history.json`.",
        ephemeral=True
    )

@bot.tree.command(
    name="wvw_event",
    description="Hantera WvW-event"
)
@app_commands.describe(
    action="start/remove_channel/reset/list",
    wvw_name="Namn på WvW-eventet (vid start)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="remove_channel", value="remove_channel"),
    app_commands.Choice(name="reset", value="reset"),
    app_commands.Choice(name="list", value="list")
])
async def wvw_event(
    interaction: discord.Interaction,
    action: str,
    wvw_name: str | None = None
):
    channel_id = str(interaction.channel_id)

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "🚫 Du har inte behörighet att använda detta kommando.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    if action == "start":
        if not wvw_name:
            await interaction.followup.send("❌ Du måste ange ett namn för eventet.", ephemeral=True)
            return
            
        event_id = str(uuid.uuid4())
        wvw_event_name = wvw_name
        wvw_event_names[event_id] = wvw_event_name
        wvw_rsvp_data[event_id] = {}
        
        try:
            # RSVP-knappar
            await interaction.channel.send(
                f"🛡️ RSVP till **{wvw_event_name}**! (ID: `{event_id[:8]}`)",
                view=WvWRSVPView(event_id)
            )

            # Sammanfattnings-embed
            summary_msg = await interaction.channel.send(
                embed=discord.Embed(
                    title=f"🛡️ {wvw_event_name}",
                    description="Laddar..."
                )
            )

            wvw_summary_channels[f"{channel_id}_{event_id[:8]}"] = {"message_id": summary_msg.id, "event_id": event_id}
            save_summary_channels()

            await interaction.followup.send(
                f"✅ WvW Event **{wvw_event_name}** (ID: `{event_id[:8]}`) startat!",
                ephemeral=True
            )
            await update_wvw_summary(interaction.client, event_id)
        
        except Exception as e:
            logger.exception(f"Fel vid start av WvW event")
            await interaction.followup.send(
                f"❌ Kunde inte starta WvW-eventet:\n`{type(e).__name__}: {e}`",
                ephemeral=True
            )

    elif action == "list":
        if not wvw_rsvp_data:
            await interaction.followup.send("❌ Inga WvW-event aktiva.", ephemeral=True)
            return
            
        event_list = []
        for eid, data in wvw_rsvp_data.items():
            name = wvw_event_names.get(eid, f"WvW Event {eid[:8]}")
            attending_count = sum(1 for d in data.values() if d.get("attending"))
            total_count = len(data)
            event_list.append(f"• `{eid[:8]}` - **{name}** ({attending_count}/{total_count} attending)")
            
        embed = discord.Embed(
            title="🛡️ Aktiva WvW-events",
            description="\n".join(event_list) if event_list else "Inga aktiva events",
            color=0xe74c3c
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    elif action == "remove_channel":
        # Ta bort alla events från denna kanal
        removed_events = []
        keys_to_remove = []
        
        for channel_key, info in list(wvw_summary_channels.items()):
            base_channel_id = channel_key.split('_')[0]  # Ta bort event-ID delen
            if base_channel_id == channel_id:
                try:
                    channel = (
                        interaction.client.get_channel(int(channel_id))
                        or await interaction.client.fetch_channel(int(channel_id))
                    )
                    msg = await channel.fetch_message(info["message_id"])
                    await msg.delete()
                    removed_events.append(wvw_event_names.get(info["event_id"], info["event_id"][:8]))
                except Exception as e:
                    logger.warning(f"Kunde inte ta bort meddelande: {e}")
                
                keys_to_remove.append(channel_key)
        
        for key in keys_to_remove:
            del wvw_summary_channels[key]
        
        save_summary_channels()
        
        if removed_events:
            await interaction.followup.send(
                f"✅ Tog bort följande events från kanalen:\n" + 
                "\n".join([f"• {name}" for name in removed_events]),
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ Inga events hittades i denna kanal.", ephemeral=True)

    elif action == "reset":
        # Reset för alla events i denna kanal
        reset_events = []
        keys_to_reset = []
        
        for channel_key, info in list(wvw_summary_channels.items()):
            base_channel_id = channel_key.split('_')[0]
            if base_channel_id == channel_id:
                event_id = info["event_id"]
                # snapshot före wipe
                archive_current_wvw_event(event_id, closed_by=interaction.user.id)
                
                if event_id in wvw_rsvp_data:
                    wvw_rsvp_data[event_id].clear()
                reset_events.append(wvw_event_names.get(event_id, event_id[:8]))
                keys_to_reset.append(event_id)
        
        if keys_to_reset:
            save_wvw_rsvp_data()
            for eid in keys_to_reset:
                await update_wvw_summary(interaction.client, eid)
            
            await interaction.followup.send(
                f"✅ Nollställde följande events:\n" + 
                "\n".join([f"• {name}" for name in reset_events]),
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ Inga events hittades i denna kanal.", ephemeral=True)


@bot.tree.command(
    name="wvw_event_clear_all",
    description="Tar bort WvW-eventet från alla kanaler och nollställer all data"
)
async def wvw_event_clear_all(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "🚫 Du har inte behörighet att använda detta kommando.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    global wvw_summary_channels

    # snapshot först för alla events
    for event_id in list(wvw_rsvp_data.keys()):
        archive_current_wvw_event(event_id, closed_by=interaction.user.id)

    for channel_key, info in list(wvw_summary_channels.items()):
        try:
            channel_id = channel_key.split('_')[0]
            channel = (
                interaction.client.get_channel(int(channel_id))
                or await interaction.client.fetch_channel(int(channel_id))
            )
            msg = await channel.fetch_message(info["message_id"])
            await msg.delete()
        except Exception as e:
            logger.warning(f"Misslyckades ta bort WvW-sammanfattning i kanal {channel_key}: {e}")

    wvw_summary_channels.clear()
    wvw_rsvp_data.clear()
    wvw_event_names.clear()

    save_summary_channels()
    save_wvw_rsvp_data()

    await interaction.followup.send(
        "✅ WvW-event rensat från alla kanaler och alla RSVP "
        "nollställda (snapshot sparad i historiken).",
        ephemeral=True
    )

# ----------------------------
# Custom Role Modal
# ----------------------------
class CustomRoleModal(discord.ui.Modal, title="Lägg till ny roll"):
    role_name = discord.ui.TextInput(
        label="Rollnamn",
        placeholder="t.ex. Boonsmith",
        max_length=30,
        required=True
    )
    bucket = discord.ui.TextInput(
        label="Bucket (valfritt)",
        placeholder="Primary/Secondary/Tertiary Support, DPS, Strip DPS, Utility",
        max_length=20,
        required=False
    )
    async def on_submit(self, interaction):
        name = str(self.role_name).strip()
        bucket = str(self.bucket).strip() or "Utility"
        if bucket not in WVW_ROLES_DISPLAY: bucket="Utility"
        custom_roles[name]=bucket
        save_custom_roles()
        await interaction.response.send_message(f"🆕 Lagt till roll **{name}** (bucket: {bucket})",ephemeral=True)

class AddRoleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="➕ Lägg till ny roll",style=discord.ButtonStyle.secondary)
    async def callback(self,interaction):
        await interaction.response.send_modal(CustomRoleModal())

# ----------------------------
# SetupBuilds (DM per-spec, kvar för nycustom)
# ----------------------------
class RolesMultiSelect(discord.ui.Select):
    def __init__(self,klass,spec):
        self.klass=klass;self.spec=spec
        opts=[discord.SelectOption(label=r,value=r) for r in all_roles_for_select()]
        super().__init__(placeholder="Välj roller (flera)",options=opts,min_values=1,max_values=len(opts))
    async def callback(self,interaction):
        vals=self.values
        meta_overrides.setdefault(self.klass,{}).setdefault(self.spec,{})['roles']=vals
        save_meta_overrides()
        await interaction.response.send_message(f"✅ {self.klass} · {self.spec}: Roller satt till {', '.join(vals)}",ephemeral=True)

class TierSelect(discord.ui.Select):
    def __init__(self,klass,spec):
        self.klass=klass;self.spec=spec
        opts=[discord.SelectOption(label=t,value=t) for t in ALLOWED_TIERS]
        super().__init__(placeholder="Välj tier",options=opts,min_values=1,max_values=1)
    async def callback(self,interaction):
        val=self.values[0]
        meta_overrides.setdefault(self.klass,{}).setdefault(self.spec,{})['tier']=val
        save_meta_overrides()
        await interaction.response.send_message(f"✅ {self.klass} · {self.spec}: Tier satt till {val}",ephemeral=True)

class MetaEditView(discord.ui.View):
    def __init__(self,klass,spec):
        super().__init__(timeout=600)
        self.add_item(RolesMultiSelect(klass,spec))
        self.add_item(TierSelect(klass,spec))
        self.add_item(AddRoleButton())

class SpecSelect(discord.ui.Select):
    def __init__(self,klass:str):
        self.klass=klass
        specs=list(ELITE_SPECS_BASE.get(klass,{}).keys())
        options=[discord.SelectOption(label=s,value=s) for s in specs]
        super().__init__(placeholder=f"Välj spec ({klass})",options=options,min_values=1,max_values=1)
    async def callback(self,interaction):
        spec=self.values[0]
        meta=get_spec_meta(self.klass,spec)
        await interaction.response.send_message(
            f"⚙️ Redigerar **{self.klass} · {spec}**\nNuvarande: Roles={meta['roles']} · Tier={meta['tier']}",
            view=MetaEditView(self.klass,spec),
            ephemeral=True
        )

class ClassSelect(discord.ui.Select):
    def __init__(self):
        options=[discord.SelectOption(label=k,value=k) for k in ELITE_SPECS_BASE.keys()]
        super().__init__(placeholder="Välj klass",options=options,min_values=1,max_values=1)
    async def callback(self,interaction):
        klass=self.values[0]
        v = discord.ui.View(timeout=600)
        v.add_item(SpecSelect(klass))
        await interaction.response.send_message(f"Välj spec för **{klass}**:", view=v, ephemeral=True)

@bot.tree.command(name="setupbuilds",description="DM: Justera roller/tiers och skapa egna roller (per spec)")
async def setupbuilds(interaction:discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("🚫 Kräver administratörsbehörighet.",ephemeral=True)
        return
    await interaction.response.send_message("📩 Kolla dina DM för setup.",ephemeral=True)
    dm=await interaction.user.create_dm()
    v=discord.ui.View(timeout=600)
    v.add_item(ClassSelect())
    await dm.send("**Setup Builds**\nVälj klass för att redigera specs eller skapa nya roller.",view=v)

# ----------------------------
# BULK: Export/Import via DM & CSV
# ----------------------------
def _export_meta_csv_string() -> str:
    """
    Bygger CSV över alla (klass,spec) med gällande Tier & Roles.
    Header: Class,Spec,Tier,Roles  (Roles separerade med |)
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Class","Spec","Tier","Roles"])
    for klass, specs in ELITE_SPECS_BASE.items():
        for spec in specs.keys():
            meta = get_spec_meta(klass, spec)
            roles_str = "|".join(meta["roles"])
            writer.writerow([klass, spec, meta["tier"], roles_str])
    return output.getvalue()

def _apply_meta_csv_string(csv_text: str) -> tuple[int,int,list[str]]:
    """
    Läser CSV och uppdaterar meta_overrides.
    Returnerar (updated_count, skipped_count, errors)
    - Validerar Tier ∈ ALLOWED_TIERS
    - Roller måste finnas i all_roles_for_select()
    - Tomma roller ignoreras (skip)
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    updated = 0
    skipped = 0
    errors: list[str] = []
    valid_roles = set(all_roles_for_select())
    for i, row in enumerate(reader, start=2):
        klass = (row.get("Class") or "").strip()
        spec  = (row.get("Spec") or "").strip()
        tier  = (row.get("Tier") or "").strip()
        roles_raw = (row.get("Roles") or "").strip()

        if not klass or not spec:
            skipped += 1
            errors.append(f"Rad {i}: Saknar Class/Spec.")
            continue
        if klass not in ELITE_SPECS_BASE or spec not in ELITE_SPECS_BASE[klass]:
            skipped += 1
            errors.append(f"Rad {i}: Okänd Class/Spec ({klass} - {spec}).")
            continue
        if tier and tier not in ALLOWED_TIERS:
            skipped += 1
            errors.append(f"Rad {i}: Ogiltig Tier '{tier}'.")
            continue

        roles = [r.strip() for r in roles_raw.split("|") if r.strip()] if roles_raw else None
        if roles is not None:
            bad = [r for r in roles if r not in valid_roles]
            if bad:
                skipped += 1
                errors.append(f"Rad {i}: Ogiltiga roller {bad}.")
                continue

        # Skriv till overrides
        entry = meta_overrides.setdefault(klass, {}).setdefault(spec, {})
        changed = False
        if tier:
            if entry.get("tier") != tier:
                entry["tier"] = tier
                changed = True
        if roles is not None and roles:
            if entry.get("roles") != roles:
                entry["roles"] = roles
                changed = True

        if changed:
            updated += 1
        else:
            skipped += 1

    save_meta_overrides()
    return updated, skipped, errors

@bot.tree.command(name="meta_bulk_dm", description="DM: Få en CSV med alla builds (Tier & Roller) för snabb översikt och redigering")
async def meta_bulk_dm(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("🚫 Kräver administratörsbehörighet.", ephemeral=True)
        return

    csv_text = _export_meta_csv_string()
    filename = "livia_meta_builds.csv"
    try:
        await interaction.response.send_message("📩 Jag skickar en DM med din CSV nu.", ephemeral=True)
        dm = await interaction.user.create_dm()
        await dm.send(
            content=(
                "**Bulk-redigering av builds**\n"
                "1) Ladda ner CSV-filen\n"
                "2) Redigera `Tier` (S+/S/A/B/C) och `Roles` (separera med `|`) – roller måste finnas i listan.\n"
                "3) Använd `/meta_bulk_import` och bifoga CSV:n för att uppdatera.\n\n"
                "Tips: Du kan även lägga till customs med `/setupbuilds` om du behöver nya roller först."
            ),
            file=discord.File(io.BytesIO(csv_text.encode("utf-8")), filename=filename)
        )
    except Exception as e:
        logger.error(f"Fel vid DM av meta CSV: {e}")
        await interaction.followup.send("❌ Kunde inte skicka DM. Har du DM-block på?", ephemeral=True)

@bot.tree.command(name="meta_bulk_import", description="Importera en CSV (Class,Spec,Tier,Roles) för att uppdatera builds")
@app_commands.describe(file="CSV-attachment från /meta_bulk_dm")
async def meta_bulk_import(interaction: discord.Interaction, file: discord.Attachment):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("🚫 Kräver administratörsbehörighet.", ephemeral=True)
        return
    if not file.filename.lower().endswith(".csv"):
        await interaction.response.send_message("❌ Filen måste vara en .csv.", ephemeral=True)
        return
    try:
        data = await file.read()
        text = data.decode("utf-8")
    except Exception as e:
        await interaction.response.send_message("❌ Kunde inte läsa filen.", ephemeral=True)
        return

    updated, skipped, errors = _apply_meta_csv_string(text)
    msg = f"✅ Import klar. Uppdaterade: **{updated}** · Skippade: **{skipped}**"
    if errors:
        preview = "\n".join(f"- {e}" for e in errors[:8])
        if len(errors) > 8:
            preview += f"\n... och {len(errors)-8} fler."
        msg += f"\n\n⚠️ Fel/varningar:\n{preview}"

    await interaction.response.send_message(msg, ephemeral=True)

# ----------------------------
# WvW-KOMMANDON (Analys & Stats)
# ----------------------------
@bot.tree.command(name="squad_analyze", description="Analys: visar balanserade squads (max 10) och vad som saknas")
@app_commands.describe(event_id="ID för det specifika WvW-eventet (första 8 tecken)")
async def squad_analyze(interaction: discord.Interaction, event_id: str | None = None):
    # Hitta rätt event_id
    target_event_id = None
    
    if event_id:
        # Sök efter exakt matchning eller början med event_id
        for eid in wvw_rsvp_data.keys():
            if eid.startswith(event_id) or eid[:8] == event_id:
                target_event_id = eid
                break
        if not target_event_id:
            await interaction.response.send_message("❌ Ogiltigt event-ID. Använd `/wvw_event list` för att se tillgängliga events.", ephemeral=True)
            return
    else:
        # Om inget event_id anges, använd första tillgängliga
        if not wvw_rsvp_data:
            await interaction.response.send_message("❌ Inga WvW-event aktiva.", ephemeral=True)
            return
        target_event_id = next(iter(wvw_rsvp_data.keys()))
    
    commander, squads, overflow, reason = build_squads_balanced(target_event_id)
    event_name_local = wvw_event_names.get(target_event_id, f"WvW Event {target_event_id[:8]}")

    embed = discord.Embed(title=f"🛡️ WvW Squad-analys – {event_name_local}", color=0xe74c3c)

    # Commander
    if commander:
        uid, data = commander
        name = data.get("display_name", f"<@{uid}>")
        spec_info = f"{data.get('class','')} - {data.get('elite_spec','')}".strip(" -")
        embed.add_field(
            name="🧭 Commander",
            value=f"• **{name}** — {spec_info}",
            inline=False
        )

    # Squads
    if squads:
        for i, squad in enumerate(squads, 1):
            lines = []
            for label, uid, data in squad:
                name = data.get("display_name", f"<@{uid}>")
                spec_info = f"{data.get('class','')} - {data.get('elite_spec','')}".strip(" -")
                lines.append(f"• {label} — **{name}** ({spec_info})")
            embed.add_field(name=f"🛡️ Squad {i} (5/5)", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🛡️ Squads", value="_Inga kompletta squads ännu_", inline=False)

    # Overflow
    if overflow:
        lines = []
        for uid, data in overflow:
            name = data.get("display_name", f"<@{uid}>")
            role = data.get("wvw_role","?")
            spec_info = f"{data.get('class','')} - {data.get('elite_spec','')}".strip(" -")
            lines.append(f"• **{name}** — {role} ({spec_info})")

        header = f"📋 Overflow ({len(overflow)} spelare)"
        if reason.get("type") == "cap":
            reason_line = f"🎯 {reason.get('message','')}"
        elif reason.get("type") == "imbalance":
            missing_role = None
            if "Saknar **" in reason.get("message",""):
                for r in ["Primary Support","Secondary Support","Tertiary Support"]:
                    if r in reason["message"]:
                        missing_role = r
                        break
            examples = best_specs_for_role(missing_role, limit=2) if missing_role else []
            ex_str = f" Exempel: {', '.join(examples)}." if examples else ""
            reason_line = f"⚖️ {reason.get('message','')}{ex_str}"
        else:
            reason_line = ""

        counts = reason.get("counts", {})
        summary = (f"Översikt: Primary={counts.get('Primary Support',0)}, "
                   f"Secondary={counts.get('Secondary Support',0)}, "
                   f"Tertiary={counts.get('Tertiary Support',0)}, "
                   f"DPS={counts.get('DPS',0)}, Strip={counts.get('Strip DPS',0)}")

        overflow_value = "\n".join([reason_line, summary, ""] + lines) if reason_line else "\n".join([summary, ""] + lines)
        embed.add_field(name=header, value=overflow_value, inline=False)
    else:
        embed.add_field(name="📋 Overflow", value="_Ingen overflow_", inline=False)

    total_attending = sum(1 for d in wvw_rsvp_data.get(target_event_id, {}).values() if d.get("attending"))
    embed.set_footer(text=f"Totalt attending: {total_attending} | 1 global Commander | Max {MAX_SQUADS} squads")

    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="show_stats", description="Visar statistik per klass och WvW-roll")
@app_commands.describe(event_id="ID för det specifika WvW-eventet (första 8 tecken)")
async def show_stats(interaction: discord.Interaction, event_id: str | None = None):
    # Hitta rätt event_id
    target_event_id = None
    
    if event_id:
        # Sök efter exakt matchning eller början med event_id
        for eid in wvw_rsvp_data.keys():
            if eid.startswith(event_id) or eid[:8] == event_id:
                target_event_id = eid
                break
        if not target_event_id:
            await interaction.response.send_message("❌ Ogiltigt event-ID. Använd `/wvw_event list` för att se tillgängliga events.", ephemeral=True)
            return
    else:
        # Om inget event_id anges, använd första tillgängliga
        if not wvw_rsvp_data:
            await interaction.response.send_message("❌ Inga WvW-event aktiva.", ephemeral=True)
            return
        target_event_id = next(iter(wvw_rsvp_data.keys()))
        
    event_data = wvw_rsvp_data[target_event_id]
    event_name_local = wvw_event_names.get(target_event_id, f"WvW Event {target_event_id[:8]}")
    
    attending = {uid: d for uid, d in event_data.items() if d.get("attending")}
    total = len(event_data)
    attending_count = len(attending)

    # per klass
    per_class = Counter()
    for d in attending.values():
        per_class[d.get("class") or "Okänd"] += 1
    class_lines = [f"{k}: {v}" for k, v in per_class.most_common()] or ["-"]

    # per roll
    per_role = Counter()
    for d in attending.values():
        role = d.get("wvw_role") or "Okänd"
        per_role[role] += 1
    role_lines = [f"{k}: {v}" for k, v in per_role.items()] or ["-"]

    embed = discord.Embed(title=f"📈 Commander Livia – {event_name_local} Statistics", color=0x9b59b6)
    embed.add_field(name="👥 Attending", value=str(attending_count), inline=True)
    embed.add_field(name="🗂️ Totalt registrerade", value=str(total), inline=True)
    embed.add_field(name="⚔️ Roller", value="\n".join(role_lines), inline=False)
    embed.add_field(name="🏷️ Per klass", value="\n".join(class_lines), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=False)

# ----------------------------
# ADMIN: RSVP Edit (DM med dropdowns)
# ----------------------------
class AdminEditStartView(discord.ui.View):
    def __init__(self, editor: discord.User, target: discord.User):
        super().__init__(timeout=600)
        self.editor = editor
        self.target = target

        # Event-typ
        self.event_select = discord.ui.Select(
            placeholder="Välj eventtyp...",
            options=[
                discord.SelectOption(label="Legacy (vanligt event)", value="legacy", emoji="🎉"),
                discord.SelectOption(label="WvW", value="wvw", emoji="🛡️"),
            ],
            min_values=1, max_values=1, custom_id="admin_edit_event_type"
        )

        # Attending
        self.att_select = discord.ui.Select(
            placeholder="Attending?",
            options=[
                discord.SelectOption(label="Ja (attending=True)", value="yes", emoji="✅"),
                discord.SelectOption(label="Nej (attending=False)", value="no", emoji="❌"),
            ],
            min_values=1, max_values=1, custom_id="admin_edit_attending"
        )

        # WvW-eventlista (om det finns events)
        self.wvw_event_select: discord.ui.Select | None = None
        if wvw_rsvp_data:
            event_options = []
            for eid in wvw_rsvp_data.keys():
                name = wvw_event_names.get(eid, f"WvW Event {eid[:8]}")
                label = f"{name} ({eid[:8]})"
                event_options.append(discord.SelectOption(label=label, value=eid))
            self.wvw_event_select = discord.ui.Select(
                placeholder="Välj WvW-event (för WvW-edit)...",
                options=event_options,
                min_values=1,
                max_values=1,
                custom_id="admin_edit_wvw_event"
            )

        async def on_event_select(interaction: discord.Interaction):
            if interaction.user.id != self.editor.id:
                await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
                return
            await interaction.response.defer()

        async def on_att_select(interaction: discord.Interaction):
            if interaction.user.id != self.editor.id:
                await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
                return
            await interaction.response.defer()

        self.event_select.callback = on_event_select
        self.att_select.callback = on_att_select

        self.add_item(self.event_select)
        self.add_item(self.att_select)

        if self.wvw_event_select:
            async def on_wvw_event(interaction: discord.Interaction):
                if interaction.user.id != self.editor.id:
                    await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
                    return
                await interaction.response.defer()
            self.wvw_event_select.callback = on_wvw_event
            self.add_item(self.wvw_event_select)

        self.add_item(AdminProceedButton(self.editor, self.target, self.event_select, self.att_select, self.wvw_event_select))


class AdminProceedButton(discord.ui.Button):
    def __init__(self, editor: discord.User, target: discord.User, event_select: discord.ui.Select, att_select: discord.ui.Select, wvw_event_select: discord.ui.Select | None):
        super().__init__(label="Fortsätt", style=discord.ButtonStyle.primary)
        self.editor = editor
        self.target = target
        self.event_select = event_select
        self.att_select = att_select
        self.wvw_event_select = wvw_event_select

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.editor.id:
            await interaction.response.send_message("🚫 Endast editor kan använda denna knapp.", ephemeral=True)
            return

        chosen_event_type = (self.event_select.values[0] if self.event_select.values else None)
        chosen_att = (self.att_select.values[0] if self.att_select.values else None)
        if not chosen_event_type or not chosen_att:
            await interaction.response.send_message("⚠️ Välj både eventtyp och attending först.", ephemeral=True)
            return

        attending_flag = (chosen_att == "yes")

        if chosen_event_type == "legacy":
            # Gå till Legacy-edit
            await interaction.response.edit_message(
                content=f"**Legacy RSVP** för {self.target.mention} · Attending: {'✅' if attending_flag else '❌'}\nVälj klass:",
                view=AdminLegacyClassView(self.editor, self.target, attending_flag)
            )
        else:
            # WvW-edit kräver ett event_id
            if not wvw_rsvp_data:
                await interaction.response.send_message("❌ Inga aktiva WvW-event att redigera. Skapa ett med `/wvw_event start` först.", ephemeral=True)
                return
            if not self.wvw_event_select or not self.wvw_event_select.values:
                await interaction.response.send_message("⚠️ Välj ett WvW-event i listan först.", ephemeral=True)
                return
            event_id = self.wvw_event_select.values[0]
            await interaction.response.edit_message(
                content=f"**WvW RSVP** för {self.target.mention} · Event: `{event_id[:8]}` · Attending: {'✅' if attending_flag else '❌'}\nVälj klass:",
                view=AdminWvWClassView(self.editor, self.target, attending_flag, event_id)
            )

# ----- Legacy flow -----
class AdminLegacyClassView(discord.ui.View):
    def __init__(self, editor: discord.User, target: discord.User, attending: bool):
        super().__init__(timeout=600)
        self.editor = editor
        self.target = target
        self.attending = attending

        self.class_select = discord.ui.Select(
            placeholder="Välj klass...",
            options=[discord.SelectOption(label=cls, value=cls) for cls in CLASSES],
            min_values=1, max_values=1, custom_id="admin_legacy_class"
        )

        async def on_class(interaction: discord.Interaction):
            if interaction.user.id != self.editor.id:
                await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
                return
            klass = self.class_select.values[0]
            await interaction.response.edit_message(
                content=f"**Legacy RSVP** för {self.target.mention}\nKlass: **{klass}** · Attending: {'✅' if self.attending else '❌'}\nVälj roll:",
                view=AdminLegacyRoleView(self.editor, self.target, self.attending, klass)
            )

        self.class_select.callback = on_class
        self.add_item(self.class_select)

class AdminLegacyRoleView(discord.ui.View):
    def __init__(self, editor: discord.User, target: discord.User, attending: bool, klass: str):
        super().__init__(timeout=600)
        self.editor = editor
        self.target = target
        self.attending = attending
        self.klass = klass

        self.role_select = discord.ui.Select(
            placeholder="Välj roll...",
            options=[discord.SelectOption(label=r, value=r) for r in ROLES],
            min_values=1, max_values=1, custom_id="admin_legacy_role"
        )

        async def on_role(interaction: discord.Interaction):
            if interaction.user.id != self.editor.id:
                await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
                return
            role = self.role_select.values[0]

            # Spara legacy
            uid = self.target.id
            rsvp_data[uid] = {
                "attending": self.attending,
                "class": self.klass if self.attending else None,
                "role": role if self.attending else None,
                "display_name": self.target.display_name,
                "updated_at": now_utc_iso()
            }
            save_rsvp_data()
            await update_all_event_summaries(interaction.client)

            await interaction.response.edit_message(
                content=(f"✅ **Legacy uppdaterad för {self.target.mention}**\n"
                         f"Attending: {'✅' if self.attending else '❌'} · "
                         f"{('Klass: **'+self.klass+'** · Roll: **'+role+'**') if self.attending else 'Ingen klass/roll sparad'}"),
                view=None
            )

        self.role_select.callback = on_role
        self.add_item(self.role_select)

# ----- WvW flow (class -> spec -> allowed roles) -----
class AdminWvWClassView(discord.ui.View):
    def __init__(self, editor: discord.User, target: discord.User, attending: bool, event_id: str):
        super().__init__(timeout=600)
        self.editor = editor
        self.target = target
        self.attending = attending
        self.event_id = event_id

        self.class_select = discord.ui.Select(
            placeholder="Välj klass...",
            options=[discord.SelectOption(label=cls, value=cls) for cls in CLASSES],
            min_values=1, max_values=1, custom_id="admin_wvw_class"
        )

        async def on_class(interaction: discord.Interaction):
            if interaction.user.id != self.editor.id:
                await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
                return
            klass = self.class_select.values[0]
            specs = list(ELITE_SPECS_BASE.get(klass, {}).keys())
            await interaction.response.edit_message(
                content=f"**WvW RSVP** för {self.target.mention}\nEvent: `{self.event_id[:8]}` · Klass: **{klass}** · Attending: {'✅' if self.attending else '❌'}\nVälj elite spec:",
                view=AdminWvWSpecView(self.editor, self.target, self.attending, klass, specs, self.event_id)
            )

        self.class_select.callback = on_class
        self.add_item(self.class_select)

class AdminWvWSpecView(discord.ui.View):
    def __init__(self, editor: discord.User, target: discord.User, attending: bool, klass: str, specs: list[str], event_id: str):
        super().__init__(timeout=600)
        self.editor = editor
        self.target = target
        self.attending = attending
        self.klass = klass
        self.event_id = event_id

        self.spec_select = discord.ui.Select(
            placeholder="Välj elite spec...",
            options=[discord.SelectOption(label=s, value=s) for s in specs],
            min_values=1, max_values=1, custom_id="admin_wvw_spec"
        )

        async def on_spec(interaction: discord.Interaction):
            if interaction.user.id != self.editor.id:
                await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
                return
            spec = self.spec_select.values[0]
            meta = get_spec_meta(self.klass, spec)
            allowed_roles = list(meta["roles"]) if self.attending else ["—"]

            await interaction.response.edit_message(
                content=(f"**WvW RSVP** för {self.target.mention}\n"
                         f"Event: `{self.event_id[:8]}` · Klass: **{self.klass}** · Spec: **{spec}** · Attending: {'✅' if self.attending else '❌'}\n"
                         f"{'Välj roll:' if self.attending else 'Sparar som “inte kommer”…'}"),
                view=AdminWvWRoleView(self.editor, self.target, self.attending, self.klass, spec, allowed_roles, self.event_id)
            )

        self.spec_select.callback = on_spec
        self.add_item(self.spec_select)

class AdminWvWRoleView(discord.ui.View):
    def __init__(self, editor: discord.User, target: discord.User, attending: bool, klass: str, spec: str, allowed_roles: list[str], event_id: str):
        super().__init__(timeout=600)
        self.editor = editor
        self.target = target
        self.attending = attending
        self.klass = klass
        self.spec = spec
        self.allowed_roles = allowed_roles
        self.event_id = event_id

        # Om attending=False tillåter vi inte roll-val; vi avslutar direkt vid spar
        if self.attending:
            self.role_select = discord.ui.Select(
                placeholder="Välj roll...",
                options=[discord.SelectOption(label=r, value=r) for r in self.allowed_roles],
                min_values=1, max_values=1, custom_id="admin_wvw_role"
            )
            self.role_select.callback = self.on_role
            self.add_item(self.role_select)
        else:
            self.add_item(AdminWvWSaveButton(self.editor, self.target, self.attending, self.klass, self.spec, None, self.event_id))

    async def on_role(self, interaction: discord.Interaction):
        if interaction.user.id != self.editor.id:
            await interaction.response.send_message("🚫 Endast editor kan använda denna meny.", ephemeral=True)
            return
        # Defera direkt så interaktionen inte timeoutar
        await interaction.response.defer()
        role = self.role_select.values[0]
        await self._save(interaction, role)

    async def _save(self, interaction: discord.Interaction, role: str | None):
        uid = self.target.id
        event_data = wvw_rsvp_data.setdefault(self.event_id, {})
        event_data[uid] = {
            "attending": self.attending,
            "class": self.klass if self.attending else None,
            "elite_spec": self.spec if self.attending else None,
            "wvw_role": role if (self.attending and role) else None,
            "display_name": self.target.display_name,
            "updated_at": now_utc_iso()
        }
        save_wvw_rsvp_data()

        # Gör tunga uppdateringar efter defer
        await update_wvw_summary(interaction.client, self.event_id)

        det = (f"Klass: **{self.klass}** · Spec: **{self.spec}** · Roll: **{role}**"
               if self.attending else "Markerad som 'kommer inte'")

        # Viktigt: followup.edit_message istället för response.edit_message
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            content=f"✅ **WvW uppdaterad för {self.target.mention}**\n{det}",
            view=None
        )

class AdminWvWSaveButton(discord.ui.Button):
    def __init__(self, editor: discord.User, target: discord.User, attending: bool, klass: str, spec: str, role: str | None, event_id: str):
        super().__init__(label="Spara", style=discord.ButtonStyle.success)
        self.editor = editor
        self.target = target
        self.attending = attending
        self.klass = klass
        self.spec = spec
        self.role = role
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.editor.id:
            await interaction.response.send_message("🚫 Endast editor kan använda denna knapp.", ephemeral=True)
            return
        # Defera direkt, spara sedan via samma helper
        await interaction.response.defer()
        view = AdminWvWRoleView(self.editor, self.target, self.attending, self.klass, self.spec, [], self.event_id)
        await view._save(interaction, self.role)

# ----- Slash command: /rsvp_edit -----
@bot.tree.command(name="rsvp_edit", description="(Admin) Redigera en spelares RSVP via DM med dropdown-menyer")
@app_commands.describe(
    user="Spelaren du vill redigera",
)
async def rsvp_edit(interaction: discord.Interaction, user: discord.User):
    # Adminkontroll
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("🚫 Du måste vara admin för att använda detta.", ephemeral=True)
        return

    # Skicka DM
    try:
        await interaction.response.send_message("📩 Öppnar en DM till dig med edit-verktyg…", ephemeral=True)
        dm = await interaction.user.create_dm()
        await dm.send(
            content=(f"**RSVP Edit**\nMål: {user.mention}\n"
                     "Välj eventtyp, attending och (för WvW) event i listan för att fortsätta:"),
            view=AdminEditStartView(editor=interaction.user, target=user)
        )
    except Exception as e:
        logger.error(f"RSVP Edit DM error: {e}")
        await interaction.followup.send("❌ Kunde inte skicka DM. Har du DM-block på?", ephemeral=True)

# ----------------------------
# RSVP / LISTOR
# ----------------------------
@bot.tree.command(name="rsvp_status", description="Visar hur många som tackat ja samt totalt antal svar")
async def rsvp_status(interaction: discord.Interaction):
    legacy_attending = sum(1 for d in rsvp_data.values() if d["attending"])
    legacy_total = len(rsvp_data)
    
    # Summera alla WvW events
    wvw_attending = 0
    wvw_total = 0
    for event_data in wvw_rsvp_data.values():
        wvw_attending += sum(1 for d in event_data.values() if d["attending"])
        wvw_total += len(event_data)
    
    embed = discord.Embed(title="📊 RSVP Status", color=0x2ecc71)
    embed.add_field(name="🎉 Vanligt Event", value=f"✅ Attending: {legacy_attending}\n👥 Totalt: {legacy_total}", inline=True)
    embed.add_field(name="🛡️ WvW Event", value=f"✅ Attending: {wvw_attending}\n👥 Totalt: {wvw_total}", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="rsvp_list", description="Visar deltagare. Stöd för både legacy (klass/roll) och WvW (klass · elite spec + roll)")
@app_commands.describe(only_attending="Visa bara de som tackat ja", event_id="ID för specifikt WvW-event (första 8 tecken)")
async def rsvp_list(interaction: discord.Interaction, only_attending: bool = False, event_id: str | None = None):
    embed = discord.Embed(title="📋 RSVP-listor", color=0x3498db)
    
    # Legacy
    legacy_attending, legacy_not_attending = [], []
    for uid, d in rsvp_data.items():
        display = d.get("display_name", f"<@{uid}>")
        if d["attending"]:
            klass = d.get("class", "Okänd klass")
            roll = d.get("role", "Okänd roll")
            legacy_attending.append(f"• **{display}** — {klass} ({roll})")
        else:
            legacy_not_attending.append(f"• **{display}**")

    embed.add_field(
        name="🎉 Vanligt Event",
        value="\n".join(legacy_attending) if legacy_attending else "_Ingen har tackat ja ännu_",
        inline=False
    )
    if not only_attending:
        embed.add_field(
            name="❌ Vanligt Event - Kommer inte",
            value="\n".join(legacy_not_attending) if legacy_not_attending else "_Ingen har tackat nej ännu_",
            inline=False
        )
    
    # WvW - antingen specifikt event eller alla
    if event_id:
        # Hitta rätt event_id
        target_event_id = None
        for eid in wvw_rsvp_data.keys():
            if eid.startswith(event_id) or eid[:8] == event_id:
                target_event_id = eid
                break
                
        if target_event_id and target_event_id in wvw_rsvp_data:
            event_data = wvw_rsvp_data[target_event_id]
            event_name_local = wvw_event_names.get(target_event_id, f"WvW Event {target_event_id[:8]}")
            wvw_attending, wvw_not_attending = [], []
            for uid, d in event_data.items():
                display = d.get("display_name", f"<@{uid}>")
                if d["attending"]:
                    klass = d.get("class", "Okänd klass")
                    elite_spec = d.get("elite_spec", "")
                    wvw_role = d.get("wvw_role", "Okänd roll")
                    klass_info = f"{klass}" + (f" - {elite_spec}" if elite_spec else "")
                    wvw_attending.append(f"• **{display}** — {klass_info}\n  `{wvw_role}`")
                else:
                    wvw_not_attending.append(f"• **{display}**")

            embed.add_field(
                name=f"🛡️ WvW Event - {event_name_local}",
                value="\n".join(wvw_attending) if wvw_attending else "_Ingen har tackat ja ännu_",
                inline=False
            )
            if not only_attending:
                embed.add_field(
                    name=f"❌ WvW Event - {event_name_local} - Kommer inte",
                    value="\n".join(wvw_not_attending) if wvw_not_attending else "_Ingen har tackat nej ännu_",
                    inline=False
                )
        else:
            embed.add_field(name="🛡️ WvW Event", value="❌ Ogiltigt event-ID", inline=False)
    else:
        # Visa alla WvW events
        for eid, event_data in wvw_rsvp_data.items():
            event_name_local = wvw_event_names.get(eid, f"WvW Event {eid[:8]}")
            wvw_attending, wvw_not_attending = [], []
            for uid, d in event_data.items():
                display = d.get("display_name", f"<@{uid}>")
                if d["attending"]:
                    klass = d.get("class", "Okänd klass")
                    elite_spec = d.get("elite_spec", "")
                    wvw_role = d.get("wvw_role", "Okänd roll")
                    klass_info = f"{klass}" + (f" - {elite_spec}" if elite_spec else "")
                    wvw_attending.append(f"• **{display}** — {klass_info}\n  `{wvw_role}`")
                else:
                    wvw_not_attending.append(f"• **{display}**")

            embed.add_field(
                name=f"🛡️ WvW Event - {event_name_local}",
                value="\n".join(wvw_attending) if wvw_attending else "_Ingen har tackat ja ännu_",
                inline=False
            )
            if not only_attending:
                embed.add_field(
                    name=f"❌ WvW Event - {event_name_local} - Kommer inte",
                    value="\n".join(wvw_not_attending) if wvw_not_attending else "_Ingen har tackat nej ännu_",
                    inline=False
                )
    
    total_legacy = len(rsvp_data)
    total_wvw = sum(len(event_data) for event_data in wvw_rsvp_data.values())
    embed.set_footer(text=f"Totalt: {total_legacy + total_wvw} svar registrerade")
    await interaction.response.send_message(embed=embed, ephemeral=False)

# ----------------------------
# Meta & Export kommandon
# ----------------------------
@bot.tree.command(name="meta_export", description="Exporterar meta_overrides.json (nuvarande overrides)")
async def meta_export(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("🚫 Du har inte behörighet att använda detta kommando.", ephemeral=True)
        return

    try:
        if os.path.exists(META_FILE):
            await interaction.response.send_message(
                "📄 Meta overrides export",
                file=discord.File(META_FILE),
                ephemeral=True
            )
        else:
            await interaction.response.send_message("📝 Ingen meta override data finns ännu.", ephemeral=True)
    except Exception as e:
        logger.error(f"Fel vid meta export: {e}")
        await interaction.response.send_message("❌ Kunde inte exportera meta data.", ephemeral=True)

@bot.tree.command(name="meta_reset", description="Nollställer alla meta-overrides (återgår till basmeta)")
async def meta_reset(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("🚫 Du har inte behörighet att använda detta kommando.", ephemeral=True)
        return

    global meta_overrides
    meta_overrides = {}
    try:
        if os.path.exists(META_FILE):
            os.remove(META_FILE)
        await interaction.response.send_message("🔄 Meta overrides nollställda. Använder nu basmeta.", ephemeral=True)
    except Exception as e:
        logger.error(f"Fel vid meta reset: {e}")
        await interaction.response.send_message("❌ Kunde inte nollställa meta overrides.", ephemeral=True)

# ----------------------------
# Kör bot
# ----------------------------
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Kunde inte starta bot: {e}")
