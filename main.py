import asyncio
import logging
import json
import os
import uvicorn
from datetime import timedelta
import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates

from aiogram import Bot, Dispatcher
from aiogram.types import InputMediaPhoto, InputMediaVideo, BotCommand

from models import initialize_db, User, HouseListing, pwd_context
from config import BOT_TOKEN, ADMIN_IDS, SOURCE_GROUPS, TARGET_GROUPS, FORWARD_INTERVAL, BOOST_EVERY_N
from security import create_access_token, verify_token
from handlers import register_handlers
from forwarding import forwarding_task
import state
from peewee import Cast
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="❌ Noto'g'ri autentifikatsiya ma'lumotlari"
        )
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="❌ Noto'g'ri autentifikatsiya ma'lumotlari"
        )
    user = User.get_or_none(User.username == username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="❌ Foydalanuvchi topilmadi")
    return user

def get_token_from_cookie(request: Request) -> str:
    token = request.cookies.get("access_token")
    if token:
        if token.startswith("Bearer "):
            return token[7:]
        return token
    return None

async def get_current_user_from_cookie(request: Request) -> User:
    token = get_token_from_cookie(request)
    if not token:
        raise HTTPException(status_code=401, detail="Autentifikatsiya qilinmagan")
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Noto'g'ri token")
    username: str = payload.get("sub")
    user = User.get_or_none(User.username == username)
    if not user:
        raise HTTPException(status_code=401, detail="Foydalanuvchi topilmadi")
    return user

def get_listing(post_id: str) -> HouseListing:
    try:
        int_id = int(post_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="❌ Noto'g'ri e'lon ID formati")
    candidates = HouseListing.select().where(HouseListing.post_id.in_([post_id, str(int_id)]))
    for candidate in candidates:
        try:
            if int(candidate.post_id) == int_id:
                return candidate
        except Exception:
            continue
    raise HTTPException(status_code=404, detail="❌ E'lon topilmadi")

async def delete_forwarded_messages(listing: HouseListing):
    if listing.forwarded_message_ids:
        try:
            fwd_data = json.loads(listing.forwarded_message_ids)
        except Exception as e:
            logging.error(f"❌ E'lon {listing.post_id} uchun forwarded_message_ids ni tahlil qilishda xato: {e}")
            fwd_data = {}
        for chat_id, msg_ids in fwd_data.items():
            for msg_id in msg_ids:
                try:
                    await global_bot.delete_message(chat_id=int(chat_id), message_id=msg_id)
                except Exception as e:
                    logging.error(f"❌ Guruh {chat_id} dan {msg_id} xabarni o'chirishda xato: {e}")
        listing.forwarded_message_ids = None
        listing.save()

@app.get("/", response_class=HTMLResponse)
def landing_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "msg": ""})

@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    user = User.get_or_none(User.username == username)
    if not user or not user.verify_password(password):
        return templates.TemplateResponse("login.html", {"request": request, "msg": "❌ Noto'g'ri ma'lumotlar."})
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
    return response

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", page: int = 1, current_user: User = Depends(get_current_user_from_cookie)):
    query = HouseListing.select()
    if q:
        query = query.where((HouseListing.post_id.contains(q)) | (HouseListing.caption.contains(q)))
    query = sorted(query, key=lambda x: int(x.post_id), reverse=True)
    total_count = len(query)
    per_page = 10
    total_pages = (total_count + per_page - 1) // per_page
    listings = query[(page-1)*per_page: page*per_page]
    sending_status = "ON" if state.SENDING_ENABLED else "OFF"
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": current_user,
        "listings": listings,
        "sending_status": sending_status,
        "q": q,
        "page": page,
        "total_pages": total_pages
    })

@app.get("/logout", response_class=HTMLResponse)
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response

@app.get("/dashboard/profile", response_class=HTMLResponse)
async def profile_get(request: Request, current_user: User = Depends(get_current_user_from_cookie)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Faqat adminlar ma'lumotlarni yangilay oladi.")
    return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "msg": ""})

@app.post("/dashboard/profile", response_class=HTMLResponse)
async def profile_post(
    request: Request,
    current_user: User = Depends(get_current_user_from_cookie),
    current_password: str = Form(...),
    new_username: str = Form(...),
    new_password: str = Form(""),
    confirm_new_password: str = Form("")
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Faqat adminlar ma'lumotlarni yangilay oladi.")
    if not current_user.verify_password(current_password):
        msg = "❌ Joriy parol noto'g'ri."
        return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "msg": msg})
    if new_username and new_username != current_user.username:
        if User.get_or_none(User.username == new_username):
            msg = "❌ Bu foydalanuvchi nomi allaqachon mavjud."
            return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "msg": msg})
        current_user.username = new_username
    if new_password or confirm_new_password:
        if new_password != confirm_new_password:
            msg = "❌ Yangi parol va tasdiq mos kelmadi."
            return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "msg": msg})
        current_user.hashed_password = pwd_context.hash(new_password)
    current_user.save()
    msg = "✅ Ma'lumotlar muvaffaqiyatli yangilandi!"
    return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "msg": msg})

# --- Yangi: Sozlamalar (Settings) sahifasi ---
@app.get("/dashboard/settings", response_class=HTMLResponse)
async def settings_get(request: Request, current_user: User = Depends(get_current_user_from_cookie)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Faqat adminlar bu bo'limga kira oladi.")
    # Source guruhlar uchun: ID va nomlarini alohida ro'yxat shaklida tayyorlaymiz
    source_ids = os.getenv("SOURCE_GROUPS", "").split(',')
    source_names = os.getenv("SOURCE_GROUP_NAMES", "").split(',')
    source_groups = []
    for i, sid in enumerate(source_ids):
        if sid.strip():
            name = source_names[i].strip() if i < len(source_names) else ""
            source_groups.append({"id": sid.strip(), "name": name})
    # Target guruhlar uchun
    target_ids = os.getenv("TARGET_GROUPS", "").split(',')
    target_names = os.getenv("TARGET_GROUP_NAMES", "").split(',')
    target_groups = []
    for i, tid in enumerate(target_ids):
        if tid.strip():
            name = target_names[i].strip() if i < len(target_names) else ""
            target_groups.append({"id": tid.strip(), "name": name})
    settings = {
        "admin_ids": os.getenv("ADMIN_IDS", ""),
        "source_groups": source_groups,
        "target_groups": target_groups,
        "forward_interval": os.getenv("FORWARD_INTERVAL", ""),
        "boost_every_n": os.getenv("BOOST_EVERY_N", ""),
        "sending_enabled": state.SENDING_ENABLED
    }
    return templates.TemplateResponse("settings.html", {"request": request, "settings": settings, "msg": ""})

@app.post("/dashboard/settings", response_class=HTMLResponse)
async def settings_post(
    request: Request,
    current_user: User = Depends(get_current_user_from_cookie),
    # Guruhlar ro'yxatini olish (bir xil nomli inputlar qaytariladi)
    forward_interval: str = Form(...),
    boost_every_n: str = Form(...),
    admin_ids: str = Form(...),
    sending_enabled: Optional[str] = Form(None),
    # Source guruhlar: input nomlari "source_group_id" va "source_group_name"
    source_group_id: List[str] = Form(...),
    source_group_name: List[str] = Form(...),
    # Target guruhlar: input nomlari "target_group_id" va "target_group_name"
    target_group_id: List[str] = Form(...),
    target_group_name: List[str] = Form(...)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Faqat adminlar bu amalni bajarishi mumkin.")
    
    # Yangi sozlamalarni tayyorlaymiz
    new_source_ids = [sid.strip() for sid in source_group_id if sid.strip()]
    new_source_names = [sname.strip() for sname in source_group_name if sname.strip()]
    new_target_ids = [tid.strip() for tid in target_group_id if tid.strip()]
    new_target_names = [tname.strip() for tname in target_group_name if tname.strip()]
    
    # .env faylini yangilash uchun yangi qiymatlarni tayyorlaymiz
    env_vars = {
        "BOT_TOKEN": os.getenv("BOT_TOKEN"),
        "ADMIN_IDS": admin_ids,
        "SOURCE_GROUPS": ",".join(new_source_ids),
        "SOURCE_GROUP_NAMES": ",".join(new_source_names),
        "TARGET_GROUPS": ",".join(new_target_ids),
        "TARGET_GROUP_NAMES": ",".join(new_target_names),
        "FORWARD_INTERVAL": forward_interval,
        "BOOST_EVERY_N": boost_every_n,
        "DEFAULT_ADMIN_USERNAME": os.getenv("DEFAULT_ADMIN_USERNAME"),
        "DEFAULT_ADMIN_PASSWORD": os.getenv("DEFAULT_ADMIN_PASSWORD")
    }
    
    # .env faylini yangilaymiz (butun faylni qayta yozamiz)
    try:
        with open(".env", "w") as f:
            for key, value in env_vars.items():
                f.write(f"{key}={value}\n")
    except Exception as e:
        logging.error(f".env faylini yozishda xato: {e}")
        return templates.TemplateResponse("settings.html", {"request": request, "settings": env_vars, "msg": "❌ Sozlamalarni saqlashda xato yuz berdi."})
    
    # Yangi .env o'zgaruvchilarini yuklaymiz
    load_dotenv(override=True)
    
    # Global config o'zgaruvchilarini ham yangilaymiz
    import config
    config.ADMIN_IDS = [int(x) for x in admin_ids.split(',') if x.strip()]
    config.SOURCE_GROUPS = [int(x) for x in new_source_ids if x.strip()]
    config.SOURCE_GROUP_NAMES = new_source_names
    config.TARGET_GROUPS = [int(x) for x in new_target_ids if x.strip()]
    config.TARGET_GROUP_NAMES = new_target_names
    config.FORWARD_INTERVAL = int(forward_interval)
    config.BOOST_EVERY_N = int(boost_every_n)
    
    # Botning yuborish rejimini yangilaymiz
    state.SENDING_ENABLED = (sending_enabled == "on")
    
    msg = "✅ Sozlamalar muvaffaqiyatli yangilandi!"
    return RedirectResponse(url="/dashboard/settings", status_code=303)

# --- API endpointlar ---
@app.get("/api/listings")
def api_get_listings(current_user: User = Depends(get_current_user)):
    listings = HouseListing.select()
    return {"listings": [listing.__data__ for listing in listings]}

@app.post("/api/listings/{post_id}/boost")
async def api_boost_listing(post_id: str, current_user: User = Depends(get_current_user)):
    listing = get_listing(post_id)
    await delete_forwarded_messages(listing)
    listing.boost_status = "unboosted" if listing.boost_status == "boosted" else "boosted"
    listing.save()
    return {"msg": f"🔄 E'lon {post_id} boost holati o'zgartirildi."}

@app.delete("/api/listings/{post_id}")
async def api_delete_listing(post_id: str, current_user: User = Depends(get_current_user)):
    listing = get_listing(post_id)
    await delete_forwarded_messages(listing)
    try:
        await global_bot.delete_message(chat_id=listing.source_group_id, message_id=listing.source_message_id)
    except Exception as e:
        logging.error(f"❌ E'lon {post_id} uchun manba xabarni o'chirishda xato: {e}")
    listing.status = "deleted"
    listing.save()
    return {"msg": f"🗑️ E'lon {post_id} o'chirildi."}

@app.post("/token")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = User.get_or_none(User.username == form_data.username)
    if not user or not user.verify_password(form_data.password):
        raise HTTPException(status_code=400, detail="❌ Foydalanuvchi nomi yoki parol noto'g'ri")
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)
    return {"access_token": access_token, "token_type": "bearer"}

async def start_bot():
    global global_bot
    bot = Bot(token=BOT_TOKEN)
    global_bot = bot
    dp = Dispatcher(bot)
    register_handlers(dp)
    await bot.set_my_commands([
        BotCommand(command="start", description="Boshqaruv paneli/statistika"),
        BotCommand(command="boost", description="E'lonni boost qil"),
        BotCommand(command="unboost", description="Boostni bekor qil"),
        BotCommand(command="del", description="E'lonni o'chir"),
        BotCommand(command="on", description="Yuborish rejimini yoqish"),
        BotCommand(command="off", description="Yuborish rejimini o'chirish"),
        BotCommand(command="refresh", description="Bazani yangilash")
    ])
    asyncio.create_task(forwarding_task(bot))
    await dp.start_polling()

async def start_uvicorn():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    initialize_db()
    await asyncio.gather(
        start_uvicorn(),
        start_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())