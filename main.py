import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
import aiohttp
from urllib.parse import urlencode
from config import BOT_TOKEN, API_KEY, BASE_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привіт! Надішли мені будь-яку IP-адресу — я перевірю її через Scamalytics.\n\n"
        "Просто напиши: 8.8.8.8\n"
        "Або скористайся командою: /check 1.1.1.1"
    )


@dp.message(Command("check"))
async def cmd_check(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("Вкажи IP після /check\nПриклад: /check 8.8.8.8")
        return
    ip = parts[1].strip()
    await check_ip(message, ip)


@dp.message()
async def handle_message(message: Message):
    text = message.text.strip()
    # проста перевірка на IPv4
    if text.count(".") == 3 and all(part.isdigit() and 0 <= int(part) <= 255 for part in text.split(".")):
        await check_ip(message, text)
    else:
        await message.reply("Надішли коректну IPv4-адресу (наприклад 8.8.8.8)")


async def check_ip(message: Message, ip: str):
    status_msg = await message.reply(f"🔍 Перевіряю IP <code>{ip}</code> …")

    try:
        params = {"key": API_KEY, "ip": ip}
        url = BASE_URL + "?" + urlencode(params)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    await status_msg.edit_text(f"❌ API відповів HTTP {response.status}")
                    return

                data = await response.json()

        if data.get("scamalytics", {}).get("status") != "ok":
            error = data.get("scamalytics", {}).get("error", "невідома помилка")
            await status_msg.edit_text(f"❌ Помилка API: {error}")
            return

        s = data.get("scamalytics", {})
        ext = data.get("external_datasources", {})

        lines = [f"🌐 <b>IP:</b> <code>{ip}</code>"]

        # ───── Ризик та оцінка ─────
        score = s.get("scamalytics_score", "—")
        risk = s.get("scamalytics_risk", "—").upper()
        lines.append(f"📊 Fraud Score: <b>{score}</b> / 100")
        lines.append(f"⚡ Ризик: <b>{risk}</b>")

        # ───── Локація ─────
        lines.append("\n📍 <b>Локація:</b>")
        geo_priority = ["dbip", "ipinfo", "maxmind_geolite2"]
        geo_found = False

        for source in geo_priority:
            geo = ext.get(source, {})
            if not geo:
                continue

            country_name = geo.get("ip_country_name", "—")
            country_code = geo.get("ip_country_code", "—")
            state_name = geo.get("ip_state_name", "—")
            district = geo.get("ip_district_name", "—")
            city = geo.get("ip_city", "—")
            postcode = geo.get("ip_postcode", "—")

            # якщо є країна і хоча б одне детальне поле — вважаємо джерело хорошим
            if country_name != "—" and (city != "—" or state_name != "—" or district != "—"):
                geo_found = True
                lines.append(f"  🌍 Країна: {country_name} ({country_code})")
                if state_name != "—": lines.append(f"  🏞️ Область / Провінція: {state_name}")
                if district != "—": lines.append(f"  🗺️ Округ / Район: {district}")
                if city != "—": lines.append(f"  🏙️ Місто: {city}")
                if postcode != "—": lines.append(f"  📮 Поштовий індекс: {postcode}")
                lines.append(f"  (джерело: {source})")
                break

        if not geo_found:
            # якщо жодне джерело не дало деталі — хоча б країну покажемо
            for source in geo_priority:
                geo = ext.get(source, {})
                if geo.get("ip_country_name"):
                    lines.append(f"  🌍 Країна: {geo['ip_country_name']} ({geo.get('ip_country_code', '—')})")
                    lines.append(f"  (джерело: {source} — деталі відсутні)")
                    break
            else:
                lines.append("  — дані про локацію відсутні —")

        # ───── Чорні списки ─────
        lines.append("\n🛡️ <b>Чорні списки:</b>")
        blacklist_checks = [
            ("🔥 Firehol (30 днів)", ext.get("firehol", {}).get("ip_blacklisted_30", False)),
            ("🛑 IP2Proxy Lite", ext.get("ip2proxy_lite", {}).get("ip_blacklisted", False)),
            ("⚫ IPsum", ext.get("ipsum", {}).get("ip_blacklisted", False)),
            ("📛 Spamhaus DROP", ext.get("spamhaus_drop", {}).get("ip_blacklisted", False)),
            ("🤖 X4Bnet Spambot", ext.get("x4bnet", {}).get("is_blacklisted_spambot", False)),
        ]

        any_blacklisted = False
        for name, is_listed in blacklist_checks:
            if is_listed:
                any_blacklisted = True
                lines.append(f"  {name}: <b>в чорному списку</b>")
            else:
                lines.append(f"  {name}: чисто")

        if not any_blacklisted and any(
                ext.get(k) for k in ["firehol", "ip2proxy_lite", "ipsum", "spamhaus_drop", "x4bnet"]):
            lines.append("  ✅ Цей IP не знайдено в відомих чорних списках")

        # ───── Проксі / Анонімайзери ─────
        lines.append("\n🕵️‍♂️ <b>Проксі / Анонімайзери:</b>")
        proxy = s.get("scamalytics_proxy", {})
        x4b = ext.get("x4bnet", {})
        ip2p = ext.get("ip2proxy", {}) or ext.get("ip2proxy_lite", {})

        proxy_detected = [
            ("🔒 VPN", proxy.get("is_vpn") or x4b.get("is_vpn") or ip2p.get("proxy_type") == "VPN"),
            ("🧅 Tor Exit Node", proxy.get("is_tor") or x4b.get("is_tor")),
            ("🖥️ Дата-центр / Сервер",
             proxy.get("is_datacenter") or proxy.get("is_amazon_aws") or proxy.get("is_google")),
            ("🌐 Public Proxy", ip2p.get("proxy_type") in ["PUB", "PUB,WEB"]),
            ("🌍 Web Proxy", ip2p.get("proxy_type") == "WEB"),
            ("🤖 Пошуковий робот", proxy.get("is_google") or ext.get("google", {}).get("is_googlebot")),
        ]

        any_proxy = False
        for label, found in proxy_detected:
            if found:
                any_proxy = True
                lines.append(f"  {label}: <b>виявлено</b>")

        if not any_proxy:
            lines.append("  — анонімайзери та проксі не виявлено —")

        # ───── Посилання та кредити ─────
        detail_url = s.get("scamalytics_url")
        if detail_url:
            lines.append(f"\n🔗 Детальніше: {detail_url}")

        credits = s.get("credits", {})
        remaining = credits.get("remaining")
        if remaining is not None:
            emoji = "🟢💳" if remaining > 500 else "💳" if remaining > 100 else "🟡⚠️" if remaining > 20 else "🔴❗"
            lines.append(f"\n{emoji} Залишилось запитів: <b>{remaining}</b>")

        # ───── Відправка результату ─────
        await status_msg.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except aiohttp.ClientError as e:
        await status_msg.edit_text(f"❌ Проблема з'єднання з API\n<code>{str(e)}</code>")
    except Exception as e:
        logger.exception("Помилка при обробці IP")
        await status_msg.edit_text(f"😓 Щось пішло не так...\n<code>{str(e)[:120]}</code>")


async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
