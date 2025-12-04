import os
import stripe
from flask import Flask, request, jsonify

import aiosqlite
import asyncio

from database import create_subscription, get_user_id, DB_NAME

# ============================
# CONFIG
# ============================
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY

# Para añadir VIP al completar pago
VIP_GROUP_ID = int(os.getenv("VIP_GROUP_ID", "0"))
BOT_TOKEN = os.getenv("TOKEN")

from telegram import Bot

telegram_bot = Bot(token=BOT_TOKEN)

# ============================
# FLASK APP
# ============================
app = Flask(__name__)


# ============================
# UTILIDAD: Añadir al grupo VIP
# ============================
async def add_user_to_vip(telegram_id: int):
    if VIP_GROUP_ID == 0:
        print("⚠️ VIP_GROUP_ID no configurado")
        return

    try:
        await telegram_bot.unban_chat_member(
            chat_id=VIP_GROUP_ID,
            user_id=telegram_id,
            only_if_banned=True
        )

        await telegram_bot.invite_chat_member(
            chat_id=VIP_GROUP_ID,
            user_id=telegram_id
        )
    except Exception as e:
        print(f"❌ Error añadiendo al usuario al VIP: {e}")


# =========================================
# WEBHOOK STRIPE → activar suscripción real
# =========================================
@app.post("/stripe-webhook")
def stripe_webhook():

    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    # Verificar firma del webhook
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    # Evento importante: pago completado
    if event["type"] == "checkout.session.completed":

        session = event["data"]["object"]

        telegram_id = int(session["metadata"]["telegram_id"])
        plan_id = session["metadata"]["plan_id"]

        print(f"🟢 Pago completado por Telegram ID {telegram_id} — Plan {plan_id}")

        # Rutina async interna para activar suscripción
        async def process():
            user_id = await get_user_id(telegram_id)

            if not user_id:
                print("⚠️ Usuario no encontrado en BD. No se puede activar.")
                return

            # Buscar plan desde main.py? No es posible aquí.
            # Por eso obtenemos duración según plan_id:
            if plan_id == "monthly":
                plan = {
                    "id": "monthly",
                    "duration_days": 30
                }
            elif plan_id == "lifetime":
                plan = {
                    "id": "lifetime",
                    "duration_days": None
                }
            else:
                print("⚠️ Plan desconocido:", plan_id)
                return

            # Crear suscripción real en la BD
            await create_subscription(user_id, plan)

            # Añadir al VIP
            await add_user_to_vip(telegram_id)

            # Avisar al usuario
            try:
                await telegram_bot.send_message(
                    chat_id=telegram_id,
                    text=(
                        "🎉 *Pago recibido*\n\n"
                        "Tu suscripción ha sido activada correctamente.\n"
                        "Ya tienes acceso al canal VIP.\n\n"
                        "Gracias por tu confianza 🙌"
                    ),
                    parse_mode="Markdown"
                )
            except:
                pass

        asyncio.run(process())

    return jsonify({"status": "ok"})


# ============================
# INICIO DEL SERVICIO WEBHOOK
# ============================
if __name__ == "__main__":
    print("🚀 Webhook Stripe ejecutándose…")
    app.run(host="0.0.0.0", port=10000)
