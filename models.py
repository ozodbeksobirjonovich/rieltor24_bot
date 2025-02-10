import datetime
from peewee import *
from passlib.context import CryptContext

# SQLite ma'lumotlar bazasi
db = SqliteDatabase('house_listings.db')

# Parol hashing uchun kontekst
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class User(Model):
    username = CharField(unique=True)
    hashed_password = CharField()
    is_admin = BooleanField(default=False)
    
    class Meta:
        database = db

    def verify_password(self, password: str) -> bool:
        return pwd_context.verify(password, self.hashed_password)

    @classmethod
    def create_user(cls, username: str, password: str, is_admin: bool = False):
        hashed = pwd_context.hash(password)
        return cls.create(username=username, hashed_password=hashed, is_admin=is_admin)

class HouseListing(Model):
    post_id = CharField()
    post_url = CharField()
    source_message_id = IntegerField(null=True)
    status = CharField(default="active")  # "active" yoki "sent" — faqat odatiy eʼlonlar uchun
    boost_status = CharField(null=True)    # "boosted" bo'lgan postlar uchun (boost qilingan eʼlonlarda qiymati "boosted")
    source_group_id = BigIntegerField()
    timestamp = DateTimeField(default=datetime.datetime.now)
    media_group_id = CharField(null=True)
    media_group_data = TextField(null=True)
    caption = TextField(null=True)
    error_details = TextField(null=True)
    forwarded_message_ids = TextField(null=True)  # JSON formatida saqlanadi

    class Meta:
        database = db

def initialize_db():
    db.connect()
    db.create_tables([User, HouseListing], safe=True)