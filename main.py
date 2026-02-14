import asyncio
import logging
from urllib.parse import urlencode

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

required_vars = [
    "BOT_TOKEN",
    "API_USER1", "API_KEY1",
    "API_USER2", "API_KEY2",
    "API_USER3", "API_KEY3",
    "API_USER4", "API_KEY4",
]

missing = [v for v in required_vars if not getattr(config, v, None)]
if missing:
    logger.critical(f"Відсутні змінні в .env або config: {', '.join(missing)}")
    raise ValueError("Не вистачає ключів API або токена бота")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

accounts = [
    {"user": config.API_USER1, "key": config.API_KEY1},
    {"user": config.API_USER2, "key": config.API_KEY2},
    {"user": config.API_USER3, "key": config.API_KEY3},
    {"user": config.API_USER4, "key": config.API_KEY4},
]

current_account_index = 0

remaining_credits = {acc["user"]: None for acc in accounts}


async def initialize_credits():
    logger.info("Ініціалізація залишків кредитів по всіх ключах...")
    for acc in accounts:
        try:
            url = f"https://api12.scamalytics.com/v3/{acc['user']}/?key={acc['key']}&ip=8.8.8.8"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=6) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("scamalytics", {}).get("status") == "ok":
                            rem = data["scamalytics"].get("credits", {}).get("remaining")
                            if rem is not None:
                                remaining_credits[acc["user"]] = rem
                                logger.info(f"Оновлено залишок для {acc['user']}: {rem}")
                    else:
                        logger.warning(f"HTTP {r.status} для {acc['user']}")
        except Exception as e:
            logger.warning(f"Помилка ініціалізації {acc['user']}: {e}")
    logger.info("Ініціалізація завершена")


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
    if text.count(".") == 3 and all(part.isdigit() and 0 <= int(part) <= 255 for part in text.split(".")):
        await check_ip(message, text)
    else:
        await message.reply("Надішли коректну IPv4-адресу (наприклад 8.8.8.8)")


async def check_ip(message: Message, ip: str):
    global current_account_index
    status_msg = await message.reply(f"🔍 Перевіряю IP <code>{ip}</code> …")

    try:
        acc = accounts[current_account_index]
        current_account_index = (current_account_index + 1) % len(accounts)

        BASE_URL = f"https://api12.scamalytics.com/v3/{acc['user']}/"
        params = {"key": acc['key'], "ip": ip}
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

        if remaining := s.get("credits", {}).get("remaining"):
            remaining_credits[acc["user"]] = remaining

        total_remaining = sum(
            val for val in remaining_credits.values() if val is not None
        )

        lines = [f"🌐 <b>IP:</b> <code>{ip}</code>"]

        # Ризик та оцінка
        score = s.get("scamalytics_score", "—")
        risk = s.get("scamalytics_risk", "—").upper()
        lines.append(f"📊 Fraud Score: <b>{score}</b> / 100")
        lines.append(f"⚡ Ризик: <b>{risk}</b>")

        # Локація
        lines.append("\n📍 <b>Локація:</b>")
        geo_priority = ["dbip", "ipinfo", "maxmind_geolite2"]
        geo_found = False

        for source in geo_priority:
            geo = ext.get(source, {})
            if not geo: continue

            cn = geo.get("ip_country_name", "—")
            cc = geo.get("ip_country_code", "—")
            st = geo.get("ip_state_name", "—")
            dst = geo.get("ip_district_name", "—")
            ct = geo.get("ip_city", "—")
            pc = geo.get("ip_postcode", "—")

            if cn != "—" and (ct != "—" or st != "—" or dst != "—"):
                geo_found = True
                lines.append(f"  🌍 Країна: {cn} ({cc})")
                if st != "—": lines.append(f"  🏞️ Область / Провінція: {st}")
                if dst != "—": lines.append(f"  🗺️ Округ / Район: {dst}")
                if ct != "—": lines.append(f"  🏙️ Місто: {ct}")
                if pc != "—": lines.append(f"  📮 Поштовий індекс: {pc}")
                lines.append(f"  (джерело: {source})")
                break

        if not geo_found:
            for source in geo_priority:
                geo = ext.get(source, {})
                if geo.get("ip_country_name"):
                    lines.append(f"  🌍 Країна: {geo['ip_country_name']} ({geo.get('ip_country_code', '—')})")
                    lines.append(f"  (джерело: {source} — деталі відсутні)")
                    break
            else:
                lines.append("  — дані про локацію відсутні —")

        # Чорні списки
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

        if not any_blacklisted and any(ext.get(k) for k in ["firehol", "ip2proxy_lite", "ipsum", "spamhaus_drop", "x4bnet"]):
            lines.append("  ✅ Цей IP не знайдено в відомих чорних списках")

        # Проксі
        lines.append("\n🕵️‍♂️ <b>Проксі / Анонімайзери:</b>")
        proxy = s.get("scamalytics_proxy", {})
        x4b = ext.get("x4bnet", {})
        ip2p = ext.get("ip2proxy", {}) or ext.get("ip2proxy_lite", {})

        proxy_detected = [
            ("🔒 VPN", proxy.get("is_vpn") or x4b.get("is_vpn") or ip2p.get("proxy_type") == "VPN"),
            ("🧅 Tor Exit Node", proxy.get("is_tor") or x4b.get("is_tor")),
            ("🖥️ Дата-центр / Сервер", proxy.get("is_datacenter") or proxy.get("is_amazon_aws") or proxy.get("is_google")),
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

        # Детальніше + загальний залишок
        if detail_url := s.get("scamalytics_url"):
            lines.append(f"\n🔗 Детальніше: {detail_url}")

        emoji = "🟢💳" if total_remaining > 15000 else "💳" if total_remaining > 10000 else "🟡⚠️" if total_remaining > 5000 else "🔴❗"
        lines.append(f"\n{emoji} Загальний залишок запитів: <b>{total_remaining}</b>")

        await status_msg.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except aiohttp.ClientError as e:
        await status_msg.edit_text(f"❌ Проблема з'єднання з API\n<code>{str(e)}</code>")
    except Exception as e:
        logger.exception("Помилка при обробці IP")
        await status_msg.edit_text(f"😓 Щось пішло не так...\n<code>{str(e)[:180]}</code>")


async def main():
    await initialize_credits()
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот зупинений")
    except Exception as e:
        logger.critical("Критична помилка при запуску", exc_info=True)
