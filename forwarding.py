import asyncio
import logging
import json
import datetime
from models import HouseListing
from config import TARGET_GROUPS, SOURCE_GROUPS, FORWARD_INTERVAL, BOOST_EVERY_N
from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo
import state

async def forward_listing(bot: Bot, listing: HouseListing):
    """
    Agar e'lon media guruh bo'lsa, barcha media elementlarni birlashtirib yuboradi;
    aks holda oddiy xabarni tarqatadi.
    Har bir maqsad guruhiga yuborishda qisqa kechikish qo'shilgan.
    """
    forwarded = {}
    for target in TARGET_GROUPS:
        try:
            if listing.media_group_id and listing.media_group_data:
                media_items = json.loads(listing.media_group_data)
                input_media = []
                for i, item in enumerate(media_items):
                    if item["type"] == "photo":
                        media = InputMediaPhoto(media=item["file_id"])
                    elif item["type"] == "video":
                        media = InputMediaVideo(media=item["file_id"])
                    else:
                        continue
                    # Faqat birinchi media elementiga (agar mavjud bo'lsa) yozuv qo'shamiz
                    if i == 0 and listing.caption:
                        media.caption = listing.caption
                        media.parse_mode = "HTML"
                    input_media.append(media)
                messages = await bot.send_media_group(chat_id=target, media=input_media)
                forwarded[str(target)] = [msg.message_id for msg in messages]
            else:
                msg = await bot.forward_message(
                    chat_id=target,
                    from_chat_id=listing.source_group_id,
                    message_id=listing.source_message_id
                )
                forwarded[str(target)] = [msg.message_id]
        except Exception as e:
            logging.error(f"🚫 Xato: E'lon {listing.post_id} ni {target} ga yuborishda: {e}")
            listing.status = "error"
            listing.error_details = str(e)
            listing.save()
        await asyncio.sleep(1)
    if forwarded:
        listing.forwarded_message_ids = json.dumps(forwarded)
        listing.save()

async def forwarding_task(bot: Bot):
    counter = 0
    while True:
        if state.REFRESH_REQUESTED:
            logging.info("🔄 /refresh buyrug'i qabul qilindi: Bazadagi o'zgarishlar yangilandi!")
            state.REFRESH_REQUESTED = False
            await asyncio.sleep(1)
            continue

        if not state.SENDING_ENABLED:
            await asyncio.sleep(FORWARD_INTERVAL)
            continue

        try:
            # Yangi e'lonlarni (status "active") yuborish
            listings = []
            for src in SOURCE_GROUPS:
                listings.extend(list(HouseListing.select().where(
                    (HouseListing.source_group_id == src) & (HouseListing.status == "active")
                )))
            listings.sort(key=lambda x: int(x.post_id))
            for listing in listings:
                if not state.SENDING_ENABLED:
                    break
                await forward_listing(bot, listing)
                listing.status = "sent"
                listing.save()
                counter += 1
                # BOOST_EVERY_N ta yangi e'lon yuborilgandan so'ng boost qilingan e'lonlarni qayta yuborish
                if counter % BOOST_EVERY_N == 0:
                    boosted_listings = HouseListing.select().where(HouseListing.boost_status == "boosted")
                    for boosted in boosted_listings:
                        await forward_listing(bot, boosted)
                await asyncio.sleep(FORWARD_INTERVAL)

            # Agar aktiv post qolmasa, barcha "sent" postlarni qayta "active" qilamiz.
            active_count = HouseListing.select().where(HouseListing.status == "active").count()
            if active_count == 0:
                sent_listings = HouseListing.select().where(HouseListing.status == "sent")
                for listing in sent_listings:
                    listing.status = "active"
                    listing.save()

        except Exception as e:
            logging.error(f"Error processing listings: {e}")
            await asyncio.sleep(FORWARD_INTERVAL)
        await asyncio.sleep(FORWARD_INTERVAL)
