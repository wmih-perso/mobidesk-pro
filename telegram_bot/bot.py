"""Bot Telegram MobiDesk : consulter le stock, les ventes, les dettes et la
caisse depuis Telegram, même quand le PC du magasin est éteint.

Le bot lit la dernière sauvegarde de MobiDesk sur Backblaze (voir sync.py),
en lecture seule. Seules les personnes autorisées peuvent l'utiliser :
une nouvelle personne envoie /start, un administrateur accepte depuis
Telegram (Employé ou Admin).

Configuration (variables d'environnement, fichier .env sur le VPS) :
    TELEGRAM_TOKEN   token donné par @BotFather
    BOT_ADMIN_IDS    identifiant(s) Telegram des administrateurs, séparés par des virgules
    B2_ENDPOINT, B2_KEY_ID, B2_APP_KEY, B2_BUCKET[, B2_PREFIX]   clé Backblaze en lecture seule
    BOT_DATA_DIR     dossier de travail (défaut : /data)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from app.database import session_scope
from telegram_bot import queries
from telegram_bot.sync import BackupSync
from telegram_bot.users import ADMIN, EMPLOYEE, ROLE_LABELS, UserStore

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s : %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("mobidesk.bot")

DATA_DIR = Path(os.environ.get("BOT_DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SYNC_SECONDS = int(os.environ.get("BOT_SYNC_SECONDS", "300"))

users = UserStore(
    DATA_DIR / "users.json",
    {int(x) for x in os.environ.get("BOT_ADMIN_IDS", "").replace(" ", "").split(",") if x},
)
sync = BackupSync(DATA_DIR)

HELP = """<b>MobiDesk — commandes</b>

🔍 <code>/stock a15</code> — stock, emplacement, prix
   (ou tapez simplement le nom du produit)
⚠️ <code>/rupture</code> — produits en stock bas
🧾 <code>/ventes</code> — ventes du jour
   <code>/ventes hier</code> · <code>semaine</code> · <code>mois</code>
👤 <code>/revendeur rezki</code> — solde, dernières ventes
"""
HELP_ADMIN = """💳 <code>/dettes</code> — revendeurs et fournisseurs
💰 <code>/caisse</code> — espèces du jour (<code>/caisse hier</code>)
👥 <code>/utilisateurs</code> — personnes autorisées
🚫 <code>/retirer nom</code> — retirer un accès
"""


def _freshness() -> str:
    snap = sync.snapshot
    if snap is None:
        return ""
    return f"\n\n<i>🕒 Données du {snap.uploaded_at:%d/%m à %H:%M}</i>"


async def _reply(update: Update, text: str, with_freshness: bool = True) -> None:
    if with_freshness:
        text += _freshness()
    for start in range(0, len(text), 4000):  # limite Telegram : 4 096 caractères
        await update.effective_message.reply_text(
            text[start:start + 4000], parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )


def restricted(admin_only: bool = False):
    """Réservé aux personnes autorisées (et aux administrateurs si demandé)."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            role = users.role(update.effective_user.id)
            if role is None:
                await update.effective_message.reply_text("⛔ Accès refusé. Envoyez /start pour demander l'accès.")
                return
            if admin_only and role != ADMIN:
                await update.effective_message.reply_text("⛔ Réservé à l'administrateur.")
                return
            if func.__name__ not in ("cmd_users", "cmd_remove", "cmd_help") and not sync.ready:
                await update.effective_message.reply_text("⏳ Aucune sauvegarde disponible pour l'instant.")
                return
            return await func(update, context, role == ADMIN)

        return wrapper

    return decorator


def _arg(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args or [])


def _query(func, *args) -> str:
    with session_scope() as session:
        return func(session, *args)


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    role = users.role(user.id)
    if role is not None:
        await _reply(update, HELP + (HELP_ADMIN if role == ADMIN else ""), with_freshness=False)
        return
    name = user.full_name + (f" (@{user.username})" if user.username else "")
    await update.effective_message.reply_text(
        f"Bonjour {user.first_name} ! Votre demande d'accès a été envoyée à l'administrateur.\n"
        f"Votre identifiant Telegram : {user.id}"
    )
    if users.add_pending(user.id, name):
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("👤 Employé", callback_data=f"auth:{user.id}:{EMPLOYEE}"),
            InlineKeyboardButton("👑 Admin", callback_data=f"auth:{user.id}:{ADMIN}"),
            InlineKeyboardButton("❌ Refuser", callback_data=f"auth:{user.id}:refuse"),
        ]])
        for admin_id in users.admins():
            try:
                await context.bot.send_message(
                    admin_id, f"🔔 Nouvelle demande d'accès : <b>{queries.esc(name)}</b> ({user.id})",
                    parse_mode=ParseMode.HTML, reply_markup=keyboard,
                )
            except Exception:  # administrateur qui n'a jamais écrit au bot
                log.warning("Impossible de prévenir l'administrateur %s", admin_id)


async def on_auth_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if users.role(query.from_user.id) != ADMIN:
        await query.answer("Réservé à l'administrateur.", show_alert=True)
        return
    _prefix, user_id, choice = query.data.split(":")
    user_id = int(user_id)
    name = users.pending_name(user_id)
    if name is None:
        await query.answer("Demande déjà traitée.")
        await query.edit_message_reply_markup(None)
        return
    if choice == "refuse":
        users.refuse(user_id)
        await query.edit_message_text(f"❌ Accès refusé à {name}.")
        await context.bot.send_message(user_id, "Votre demande d'accès a été refusée.")
    else:
        users.authorize(user_id, name, choice)
        await query.edit_message_text(f"✅ {name} : accès {ROLE_LABELS[choice]}.")
        await context.bot.send_message(
            user_id, "✅ Accès accordé !\n\n" + HELP + (HELP_ADMIN if choice == ADMIN else ""),
            parse_mode=ParseMode.HTML,
        )
    await query.answer()


@restricted(admin_only=True)
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    lines = ["👥 <b>Personnes autorisées</b>"]
    lines += [f"• {queries.esc(name)} — {ROLE_LABELS[role]} ({uid})" for uid, name, role in users.listing()]
    await _reply(update, "\n".join(lines), with_freshness=False)


@restricted(admin_only=True)
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    target = _arg(context)
    if not target:
        await _reply(update, "Exemple : <code>/retirer Karim</code> (voir /utilisateurs)", with_freshness=False)
        return
    removed = users.remove(target)
    text = f"🚫 Accès retiré à {queries.esc(removed)}." if removed else "Personne non trouvée (voir /utilisateurs)."
    await _reply(update, text, with_freshness=False)


@restricted()
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    await _reply(update, HELP + (HELP_ADMIN if admin else ""), with_freshness=False)


# ---------------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------------


@restricted()
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    await _reply(update, _query(queries.stock, _arg(context), admin))


@restricted()
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    """Un simple texte = recherche dans le stock."""
    await _reply(update, _query(queries.stock, update.effective_message.text, admin))


@restricted()
async def cmd_low(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    await _reply(update, _query(queries.low_stock, admin))


@restricted()
async def cmd_sales(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    await _reply(update, _query(queries.sales, _arg(context), admin))


@restricted()
async def cmd_reseller(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    await _reply(update, _query(queries.reseller, _arg(context), admin))


@restricted(admin_only=True)
async def cmd_debts(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    await _reply(update, _query(queries.debts))


@restricted(admin_only=True)
async def cmd_cash(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: bool) -> None:
    await _reply(update, _query(queries.cash, _arg(context)))


# ---------------------------------------------------------------------------
# Synchronisation périodique
# ---------------------------------------------------------------------------


async def sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.to_thread(sync.check)
    except Exception:
        log.exception("Synchronisation Backblaze impossible")


async def initial_sync(_app: Application) -> None:
    """Première synchronisation dès que le bot est prêt (une tâche planifiée
    « dans 1 seconde » pouvait être sautée si la connexion à Telegram
    prenait plus de temps)."""
    try:
        await asyncio.to_thread(sync.check)
    except Exception:
        log.exception("Synchronisation Backblaze impossible")


def main() -> None:
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).post_init(initial_sync).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_auth_button, pattern=r"^auth:"))
    app.add_handler(CommandHandler(["aide", "help"], cmd_help))
    app.add_handler(CommandHandler("stock", cmd_stock))
    app.add_handler(CommandHandler("rupture", cmd_low))
    app.add_handler(CommandHandler("ventes", cmd_sales))
    app.add_handler(CommandHandler("revendeur", cmd_reseller))
    app.add_handler(CommandHandler("dettes", cmd_debts))
    app.add_handler(CommandHandler("caisse", cmd_cash))
    app.add_handler(CommandHandler("utilisateurs", cmd_users))
    app.add_handler(CommandHandler("retirer", cmd_remove))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.job_queue.run_repeating(sync_job, interval=SYNC_SECONDS, first=SYNC_SECONDS)
    log.info("Bot MobiDesk démarré")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
