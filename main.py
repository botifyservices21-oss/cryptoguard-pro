import os
import asyncio
import aiosqlite
import stripe
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ======================
# CONFIG
# ======================
TOKEN = os.getenv("TOKEN")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
VIP_GROUP_ID = int(os.getenv("VIP_GROUP_ID", "0"))

stripe.api_key = STRIPE_SECRET_KEY

from database import (
    init_db,
    save_user,
    get_user_id,
    create_subscription,
    get_active_subscription,
    DB_NAME
)

# ======================
# PLANES PREMIUM
# ======================
PLANS = [
    {
        "id": "monthly",
        "name": "Mensual VIP",
        "price": 29,
        "duration_days": 30,
        "description": "Acceso al Canal VIP durante 30 días."
    },
    {
        "id": "lifetime",
        "name": "Lifetime VIP",
        "price": 199,
        "duration_days": None,
        "description": "Acceso de por vida al Canal VIP."
    }
]

# ======================
# MENÚ PRINCIPAL
# ======================
def main_menu():
    buttons = [
        [KeyboardButton("📦 Ver planes"), KeyboardButton("📊 Mi suscripción")],
        [KeyboardButton("🔄 Renovar"), KeyboardButton("🆘 Soporte")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ======================
# /start
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user(user)

    text = (
        f"👋 Hola {user.first_name}, bienvenido a *CryptoGuard PRO*.\n\n"
        "Gestiona aquí tu acceso al *Canal VIP de trading*.\n\n"
        "Usa el menú o los comandos:\n"
        "📦 /planes – Ver planes\n"
        "📊 /estado – Estado de suscripción\n"
        "🆘 /soporte – Contacto\n"
    )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu())

# ======================
# /planes
# ======================
async def planes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📦 *PLANES DISPONIBLES*\n\n"
    keyboard = []

    for p in PLANS:
        text += f"*{p['name']}* — {p['price']}€\n{p['description']}\n\n"
        keyboard.append([
            InlineKeyboardButton(f"Elegir {p['name']}", callback_data=f"choose:{p['id']}")
        ])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ======================
# Elegir plan
# ======================
async def choose_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, plan_id = query.data.split(":")
    plan = next((p for p in PLANS if p["id"] == plan_id), None)

    if not plan:
        await query.edit_message_text("❌ Error: Plan no encontrado.")
        return

    text = (
        f"💼 *Has elegido:* {plan['name']}\n"
        f"💰 Precio: {plan['price']}€\n\n"
        "Selecciona método de pago:\n"
        "💳 Stripe (pago real)\n"
        "🅿️ PayPal (próximamente)\n"
        "🪙 Crypto (manual)\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("💳 Stripe", callback_data=f"pay:stripe:{plan_id}")
        ]
    ]

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

# ============================
# CREAR CHECKOUT SESSION STRIPE
# ============================
async def create_stripe_checkout_session(user, plan):
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": "eur",
                    "unit_amount": int(plan["price"] * 100),
                    "product_data": {
                        "name": plan["name"]
                    }
                },
                "quantity": 1
            }
        ],
        metadata={
            "telegram_id": user.id,
            "plan_id": plan["id"]
        },
        success_url="https://google.com",     
        cancel_url="https://google.com"
    )

    return session.url

# ======================
# PROCESAR MÉTODO DE PAGO
# ======================
async def pay_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, method, plan_id = query.data.split(":")
    user = query.from_user
    plan = next((p for p in PLANS if p["id"] == plan_id), None)

    if method == "stripe":
        checkout_url = await create_stripe_checkout_session(user, plan)

        await query.edit_message_text(
            f"💳 *PAGO CON STRIPE*\n\n"
            f"Plan: *{plan['name']}*\n"
            f"Precio: {plan['price']}€\n\n"
            f"👉 Haz clic para pagar:\n{checkout_url}\n\n"
            "_Tu suscripción se activará automáticamente al completar el pago._",
            parse_mode="Markdown"
        )
        return

    await query.edit_message_text("Método no disponible todavía.")

# ======================
# Añadir usuario al VIP
# ======================
async def add_user_to_vip(context: ContextTypes.DEFAULT_TYPE, telegram_id: int):
    if VIP_GROUP_ID == 0:
        print("⚠️ VIP_GROUP_ID no configurado.")
        return

    try:
        await context.bot.unban_chat_member(chat_id=VIP_GROUP_ID, user_id=telegram_id, only_if_banned=True)
        await context.bot.invite_chat_member(chat_id=VIP_GROUP_ID, user_id=telegram_id)
    except Exception as e:
        print(f"Error añadiendo al VIP: {e}")

# ======================
# Expiración automática
# ======================
async def check_expired_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT s.id, s.user_id, s.plan_id, s.end_date, u.telegram_id
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.status = 'active'
              AND s.end_date IS NOT NULL
              AND datetime(s.end_date) <= datetime('now')
        """)
        rows = await cursor.fetchall()

        for sub_id, user_id, plan_id, end_date, telegram_id in rows:

            # Expirar
            await db.execute("UPDATE subscriptions SET status = 'expired' WHERE id = ?", (sub_id,))

            # Expulsar
            try:
                await context.bot.ban_chat_member(chat_id=VIP_GROUP_ID, user_id=telegram_id)
                await context.bot.unban_chat_member(chat_id=VIP_GROUP_ID, user_id=telegram_id)
            except:
                pass

            # Aviso
            await context.bot.send_message(
                chat_id=telegram_id,
                text="❌ Tu suscripción ha expirado.\n🔄 Puedes renovarla desde /planes."
            )

        await db.commit()

# ======================
# /estado
# ======================
async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = await get_user_id(user.id)

    if not user_id:
        await update.message.reply_text("No estás registrado en la BD.")
        return

    sub = await get_active_subscription(user_id)

    if not sub:
        await update.message.reply_text("❌ No tienes suscripción activa.")
        return

    plan_id, start, end, status = sub
    plan = next((p for p in PLANS if p["id"] == plan_id), None)

    text = (
        f"📊 *Estado de tu suscripción*\n\n"
        f"Plan: {plan['name']}\n"
        f"Estado: {status}\n"
        f"Inicio: {start}\n"
        f"Expira: {end if end else 'Lifetime'}"
    )

    await update.message.reply_text(text, parse_mode="Markdown")

# ======================
# /soporte
# ======================
async def soporte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🆘 Soporte: @TuSoporte")

# ======================
# BOTONES MENÚ
# ======================
async def menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text

    if txt == "📦 Ver planes":
        await planes(update, context)
    elif txt == "📊 Mi suscripción":
        await estado(update, context)
    elif txt == "🔄 Renovar":
        await planes(update, context)
    elif txt == "🆘 Soporte":
        await soporte(update, context)
    else:
        await update.message.reply_text("No entendí eso.", reply_markup=main_menu())

# ======================
# MAIN
# ======================
async def main():
    print("Inicializando DB…")
    await init_db()

    app = Application.builder().token(TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("planes", planes))
    app.add_handler(CommandHandler("estado", estado))
    app.add_handler(CommandHandler("soporte", soporte))

    # Menú
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_buttons))

    # Botones
    app.add_handler(CallbackQueryHandler(choose_plan, pattern="^choose:"))
    app.add_handler(CallbackQueryHandler(pay_method, pattern="^pay:"))

    # Expiración automática
    job = app.job_queue
    job.run_repeating(check_expired_subscriptions, interval=60, first=10)

    print("🚀 CryptoGuard PRO ejecutándose…")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
