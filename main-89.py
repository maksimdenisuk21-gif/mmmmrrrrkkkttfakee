"""
NFT Marketplace — Telegram bot + API (MRKT-style).
Render: rename to main.py, start: uvicorn main:app --host 0.0.0.0 --port $PORT

ENV
  BOT_TOKEN          токен бота (обязательно для live)
  BOT_USERNAME       username бота без @
  WEBAPP_URL         URL фронта (Netlify)
  API_URL            URL этого сервиса на Render
  ADMIN_IDS          telegram id админов через запятую
  DEPOSIT_USERNAME   username аккаунта, КУДА юзеры шлют подарки (Business-аккаунт, подключённый к боту).
                     Если пусто — используется BOT_USERNAME
  DATABASE_URL       Postgres (необязательно, но нужно чтобы подарки не пропадали при рестарте)
  SEED_MARKET        1 = добавить декоративные лоты-образцы (их нельзя купить). По умолчанию 0
  ALLOW_DEMO_BALANCE 1 = кнопка «+ Ввод» даёт +50 TON всем (только для тестов). По умолчанию 0
  FEE_PERCENT        комиссия маркета с продажи, % (по умолчанию 0)
  WEBHOOK_SECRET     секрет вебхука (по умолчанию считается из BOT_TOKEN)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import re
import time
import urllib.request
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
API_URL = os.getenv("API_URL", "").strip().rstrip("/")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DEPOSIT_USERNAME = (os.getenv("DEPOSIT_USERNAME", "").strip().lstrip("@")) or BOT_USERNAME
SEED_MARKET = os.getenv("SEED_MARKET", "0") == "1"
ALLOW_DEMO_BALANCE = os.getenv("ALLOW_DEMO_BALANCE", "0") == "1"
FEE_PERCENT = float(os.getenv("FEE_PERCENT", "0") or 0)
STATE_FILE = os.getenv("STATE_FILE", "state.json")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if x.strip().isdigit()
}
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip() or (
    hashlib.sha256(("wh:" + BOT_TOKEN).encode()).hexdigest()[:48] if BOT_TOKEN else ""
)
INIT_DATA_MAX_AGE = 3 * 24 * 3600

# Коллекции, модели (с редкостью, %), фоны (цвет + редкость) и символы.
# Модели/символы здесь — для декоративных лотов и ордеров; у реальных подарков
# всё берётся из самого NFT (parse_unique_gift).
GIFT_META: Dict[str, Any] = json.loads(r'''{"collections":[{"name":"Chill Flame","sn":"chill_flame","floor":3.62,"bg":"#5c4a3a","models":[["Blue Torch",0.3],["Violet Ember",0.4],["Frostfire",0.8],["Golden Spark",1.0],["Crimson",1.5],["Ghost Flame",2.0],["Toxic Green",2.5],["Classic",3.0]]},{"name":"Lunar Snake","sn":"lunar_snake","floor":3.63,"bg":"#3a4555","models":[["Moonscale",0.3],["Jade Coil",0.4],["Silver Fang",0.8],["Midnight",1.0],["Candy Coil",1.5],["Ember Viper",2.0],["Frost",2.5],["Classic",3.0]]},{"name":"Ice Cream","sn":"ice_cream","floor":3.62,"bg":"#2a4a5c","models":[["Strawberry",0.3],["Mint Choc",0.4],["Blueberry",0.8],["Pistachio",1.0],["Vanilla",1.5],["Caramel Swirl",2.0],["Neapolitan",2.5],["Classic",3.0]]},{"name":"Vice Cream","sn":"vice_cream","floor":3.62,"bg":"#2a4a4a","models":[["Neon Cone",0.3],["Choco Dip",0.4],["Lilac Swirl",1.0],["Berry Twist",1.2],["Lime Fizz",1.5],["Sunset",2.5],["Classic",3.0]]},{"name":"Party Sparkler","sn":"party_sparkler","floor":4.17,"bg":"#3a2a4a","models":[["Gold Burst",0.3],["Rainbow",0.8],["Silver Rain",1.0],["Neon Pink",1.5],["Blue Fizz",2.0],["Classic",3.0]]},{"name":"Homemade Cake","sn":"homemade_cake","floor":4.9,"bg":"#4a3a55","models":[["Red Velvet",0.3],["Lemon Drizzle",0.8],["Choco Fudge",1.0],["Berry Layer",1.5],["Matcha",2.0],["Classic",3.0]]},{"name":"B-Day Candle","sn":"bday_candle","floor":4.64,"bg":"#3a2a2a","models":[["Blue Wax",0.3],["Pink Wax",0.8],["Golden Flame",1.0],["Rainbow",1.5],["Mint",2.0],["Classic",3.0]]},{"name":"Snoop Dogg","sn":"snoop_dogg","floor":5.4,"bg":"#2a3528","models":[["Gold Chain",0.3],["Green Haze",0.8],["Purple Rain",1.0],["Westcoast",1.5],["Throwback",2.0],["Classic",3.0]]},{"name":"Crystal Ball","sn":"crystal_ball","floor":12.05,"bg":"#2a1a40","models":[["Nebula",0.3],["Storm",0.8],["Aurora",1.0],["Smoke",1.5],["Galaxy",2.0],["Classic",3.0]]},{"name":"Eternal Rose","sn":"eternal_rose","floor":23.87,"bg":"#3a2030","models":[["Black Rose",0.3],["Blue Moon",0.8],["Golden Thorn",1.0],["Crimson",1.5],["Frozen",2.0],["Classic",3.0]]},{"name":"Diamond Ring","sn":"diamond_ring","floor":29.89,"bg":"#1a2535","models":[["Princess Cut",0.3],["Emerald Cut",0.8],["Pink Diamond",1.0],["Black Diamond",1.5],["Sapphire",2.0],["Classic",3.0]]},{"name":"Toy Bear","sn":"toy_bear","floor":31.88,"bg":"#3a2a35","models":[["Honey",0.3],["Panda",0.8],["Polar",1.0],["Rose",1.5],["Patchwork",2.0],["Classic",3.0]]},{"name":"Genie Lamp","sn":"genie_lamp","floor":35.49,"bg":"#3a3020","models":[["Ruby Inlay",0.3],["Emerald Inlay",0.8],["Silver",1.0],["Midnight",1.5],["Golden",2.0],["Classic",3.0]]},{"name":"Kissed Frog","sn":"kissed_frog","floor":37.74,"bg":"#2a4030","models":[["Prince",0.3],["Princess",0.8],["Golden Frog",1.0],["Toxic",1.5],["Blue Dart",2.0],["Classic",3.0]]},{"name":"Swiss Watch","sn":"swiss_watch","floor":52.99,"bg":"#2a2a30","models":[["Rose Gold",0.3],["Skeleton",0.8],["Diver",1.0],["Black Ceramic",1.5],["Steel",2.0],["Classic",3.0]]},{"name":"Low Rider","sn":"low_rider","floor":57.08,"bg":"#252830","models":[["Candy Paint",0.3],["Chrome",0.8],["Midnight Purple",1.0],["Sunset Orange",1.5],["Mint",2.0],["Classic",3.0]]},{"name":"Westside Sign","sn":"westside_sign","floor":111.96,"bg":"#302010","models":[["Gold",0.3],["Chrome",0.8],["Neon Blue",1.2],["Graffiti",2.0],["Classic",3.0]]},{"name":"Nail Bracelet","sn":"nail_bracelet","floor":117.09,"bg":"#2a2030","models":[["Silver Spikes",0.3],["Gold Studs",0.8],["Rainbow",1.2],["Black Steel",2.0],["Classic",3.0]]},{"name":"Loot Bag","sn":"loot_bag","floor":118.32,"bg":"#3a2a18","models":[["Diamond Haul",0.3],["Treasure",0.8],["Cash Stack",1.2],["Emerald",2.0],["Classic",3.0]]},{"name":"Mighty Arm","sn":"mighty_arm","floor":122.29,"bg":"#2a2520","models":[["Gold",0.3],["Iron",0.8],["Stone",1.0],["Ice",1.5],["Neon",2.0],["Classic",3.0]]},{"name":"Heroic Helmet","sn":"heroic_helmet","floor":177.41,"bg":"#302818","models":[["Golden Eagle",0.3],["Samurai",0.8],["Viking",1.0],["Spartan",1.5],["Knight",2.0],["Classic",3.0]]},{"name":"Scared Cat","sn":"scared_cat","floor":217.26,"bg":"#1a1a22","models":[["Ghost Cat",0.3],["Neon",0.8],["Siamese",1.0],["Ginger",1.5],["Black Cat",2.0],["Classic",3.0]]},{"name":"Precious Peach","sn":"precious_peach","floor":239.69,"bg":"#3a2030","models":[["Golden",0.3],["Frosted",0.8],["Midnight",1.0],["Blush",1.5],["Juicy",2.0],["Classic",3.0]]},{"name":"Durov's Cap","sn":"durovs_cap","floor":389.8,"bg":"#1e2a3a","models":[["Gold",0.3],["Black",0.8],["White",1.0],["Red",1.5],["Blue",2.0],["Classic",3.0]]},{"name":"Durov's Figure","sn":"durovs_figure","floor":690.0,"bg":"#2a2520","models":[["Golden",0.3],["Pixel",0.8],["Shadow",1.2],["Neon",2.0],["Classic",3.0]]},{"name":"Resistance","sn":"resistance","floor":449.82,"bg":"#1a2030","models":[["Pixel Soldier",0.3],["Neon Rebel",0.8],["Ghost Unit",1.0],["Camo",1.5],["Steel",2.0],["Classic",3.0]]},{"name":"Plush Pepe","sn":"plush_pepe","floor":6650.4,"bg":"#2a2030","models":[["Golden Pepe",0.3],["Rare Pepe",0.4],["Shadow Pepe",1.0],["Neon Pepe",1.2],["Pixel Pepe",1.5],["Space Pepe",2.5],["Classic Pepe",3.0]]},{"name":"Astral Shard","sn":"astral_shard","floor":115.23,"bg":"#2a2a48","models":[["Void",0.3],["Nebula Core",0.8],["Rose Quartz",1.0],["Cyan",1.5],["Violet",2.0],["Classic",3.0]]},{"name":"Berry Box","sn":"berry_box","floor":8.27,"bg":"#4a2a35","models":[["Strawberry",0.3],["Raspberry",0.8],["Blueberry",1.2],["Mixed",2.0],["Classic",3.0]]},{"name":"Big Year","sn":"big_year","floor":3.65,"bg":"#2a2a2a","models":[["2026 Neon",0.3],["2025 Gold",0.8],["Silver Digits",1.2],["Calendar Red",2.0],["Classic",3.0]]},{"name":"Bonded Ring","sn":"bonded_ring","floor":39.78,"bg":"#4a3a2a","models":[["Ring of Roots",0.3],["Asteroid",0.4],["Goldsmith",0.4],["Hourglass",0.8],["Neo Matrix",0.8],["Spatial Grid",0.8],["Fireball",1.2],["Highway",1.2],["Metal Forever",1.2],["Moon Dust",1.2],["Rainbow Loop",1.5],["Black Pearl",2.0],["Classic",2.5]]},{"name":"Bow Tie","sn":"bow_tie","floor":4.84,"bg":"#2a3a2a","models":[["Gold Stripe",0.3],["Red Velvet",0.8],["Polka",1.2],["Black Tie",2.0],["Classic",3.0]]},{"name":"Bunny Muffin","sn":"bunny_muffin","floor":7.7,"bg":"#4a3a4a","models":[["Carrot",0.3],["Blueberry",0.8],["Choco Chip",1.2],["Pink Frosting",2.0],["Classic",3.0]]},{"name":"Candy Cane","sn":"candy_cane","floor":3.63,"bg":"#4a2a2a","models":[["Gold Wrap",0.3],["Berry Twist",0.8],["Frost",1.2],["Peppermint",2.0],["Classic",3.0]]},{"name":"Cookie Heart","sn":"cookie_heart","floor":4.72,"bg":"#4a3a30","models":[["Jam Filled",0.3],["Gingerbread",0.8],["Sugar Glaze",1.2],["Choco Chip",2.0],["Classic",3.0]]},{"name":"Snake Box","sn":"snake_box","floor":3.62,"bg":"#2a4a4a","models":[["Blue Pearl",0.3],["Gold Ribbon",0.8],["Polka Black",1.0],["Red Stripes",1.5],["Teal Ribbon",2.0],["Classic",3.0]]},{"name":"Instant Ramen","sn":"instant_ramen","floor":3.63,"bg":"#4a4a2a","models":[["Spicy Miso",0.3],["Tonkotsu",0.8],["Veggie Bowl",1.2],["Cereal Bowl",2.0],["Classic",3.0]]},{"name":"Xmas Stocking","sn":"xmas_stocking","floor":3.63,"bg":"#2a2a4a","models":[["Gold Bells",0.3],["Violet Velvet",0.8],["Frost",1.2],["Candy Stripe",2.0],["Classic",3.0]]}],"backdrops":[["Amber","#f0a030","#6a3a08",1.2],["Azure Blue","#2a8ae0","#0c2943",0.8],["Battleship Grey","#8b8d86","#292a28",1.0],["Black","#3a3a3e","#111112",1.5],["Burgundy","#8a2b3f","#2e0c14",1.0],["Burnt Sienna","#c4562b","#3a190c",1.0],["Camo Green","#5f7a3a","#1c2411",1.0],["Cappuccino","#a48571","#312721",1.2],["Caramel","#c98a3a","#3c2911",1.2],["Carrot Juice","#e98a3a","#452911",1.2],["Celtic Blue","#2f86d8","#0e2840",1.2],["Chocolate","#8a5a3f","#2e1c10",1.0],["Cobalt Blue","#2f6fe0","#0f2a60",1.0],["Copper","#c9803f","#5a3010",1.2],["Coral Red","#ee5a52","#471b18",0.8],["Danger Yellow","#f0d830","#6a5a08",0.6],["Dark Lilac","#9a5c9a","#2e1b2e",1.0],["Deep Cyan","#1f9aa5","#092e31",1.0],["Electric Indigo","#6a5cff","#1f1b4c",1.0],["Electric Purple","#a64dff","#3a1070",0.8],["Emerald","#2fbf7a","#0c4a2c",0.7],["Fandango","#b0408c","#34132a",0.8],["Feldgrau","#5a6a60","#1b1f1c",0.8],["Fire Engine","#e6403a","#5a0f0c",0.8],["French Violet","#8a3fd0","#29123e",0.6],["Grape","#6a3a8a","#1f1129",0.6],["Gunmetal","#4a5560","#16191c",0.8],["Ivory White","#d8d4c4","#403f3a",0.5],["Jade Green","#2aa87a","#0c3224",0.7],["Khaki Green","#8a9a4a","#292e16",1.0],["Lavender","#a58ae0","#312943",1.0],["Light Olive","#9a9a52","#2e2e18",1.0],["Malachite","#1fa86a","#09321f",0.7],["Mexican Pink","#e0508f","#43182a",1.5],["Midnight Blue","#3a5fb0","#101a3a",1.0],["Mint Green","#7fe0b0","#2a6a4a",1.5],["Moonstone","#7aa6b0","#243134",0.6],["Mystic Pearl","#c9b8e8","#5a4a80",0.5],["Navy Blue","#2a3f8a","#0c1229",1.0],["Neon Blue","#5a6bff","#1b204c",1.5],["Ocean Blue","#2a6ab0","#0c1f34",1.0],["Old Gold","#b8902f","#372b0e",1.5],["Olive","#707a3a","#212411",1.2],["Onyx Black","#4a4a50","#141416",1.0],["Orange","#ee8a3a","#472911",1.5],["Pacific Cyan","#2ea3c9","#0d303c",1.5],["Persimmon","#ee6a4a","#471f16",1.5],["Pine Green","#3f8a60","#12301f",1.2],["Pistachio","#a0c878","#303c24",1.2],["Platinum","#a9aaa6","#323331",1.5],["Pure Gold","#e0b020","#433409",0.5],["Purple","#8a3ac8","#29113c",1.0],["Raspberry","#e0457b","#5a1030",1.0],["Rifle Green","#5a6347","#1b1d15",1.5],["Roman Silver","#8a8f9a","#292a2e",1.0],["Rosewood","#a8505f","#401820",1.5],["Salmon","#ee8a7a","#472924",1.2],["Sapphire","#3f6fd0","#12213e",1.0],["Satin Gold","#e8c25a","#6a4c10",1.0],["Seal Brown","#6a3a28","#1f110c",1.2],["Shamrock Green","#3aa564","#11311e",1.0],["Silver Blue","#8aa8c8","#2c3c52",1.5],["Sky Blue","#6fb8f0","#1f4a7a",1.5],["Steel Blue","#4a7aa0","#162430",1.2],["Strawberry","#e0626e","#431d21",1.5],["Tactical Pine","#2f6a5a","#0e1f1b",1.5],["Tomato","#e8503a","#451811",1.0],["Turquoise","#3fc9c9","#0f4a4a",1.2],["Tuscan Red","#8a3a3a","#291111",1.0],["Violet","#7a4ae0","#241643",1.0]],"symbols":["213","Academic Cap","Ace Of Hearts","Acorn","Adesanya","Alarm","Alert Serpent","Alkonost","All-Seeing Eye","Amour Scoops","Anchor","Ancient Urn","Angel","Ankh","Antler","Apple","Arrow","Astronaut","Atom","Axe","Baby","Backpack","Bagel","Balloon","Bamboo","Banana","Bandage","Bandana","Banner","Baphomet","Bascinet","Baseball Cap","Basketball","Bastet","Bat","Battle Axe","Beanie","Bearskin","Bee","Beer Mug","Bell","Bicycle","Bird","Birthday Cake","Black Cat","Blossom","Blue Rose","Bolt","Bomb","Bone","Book","Boomerang","Boot","Bottle","Bougainvillea","Bouquet","Bow Tie","Bowler Hat","Bowtie","Brain","Brass Knuckles","Bread","Breeze","Broken Heart","Bubble Tea","Bubbles","Bucket Hat","Bug","Butterfly","Cactus","Cake","Camera","Candle","Candy","Cap","Car","Carrot","Castle","Cat","Cat Paw","Cherry","Chess Knight","Chicken","Cloud","Clover","Clown","Coffee","Coin","Comet","Compass","Cookie","Cow","Crab","Crescent","Crocodile","Crown","Crystal","Cupcake","Dagger","Daisy","Diamond","Dice","Dino","Dog Paw","Dolphin","Donut","Dove","Dragon","Drum","Duck","Eagle","Egg","Elephant","Envelope","Evil Eye","Eye","Feather","Fire","Fish","Flag","Flame","Flamingo","Flower","Fox","Frog","Gem","Ghost","Gift","Glasses","Globe","Gold Bar","Guitar","Hammer","Hat","Heart","Hedgehog","Honey","Horseshoe","Ice Cream","Igloo","Iris","Jack-o-Lantern","Jellyfish","Joker","Key","Kite","Koala","Ladybug","Lantern","Leaf","Lemon","Lightning","Lion","Lollipop","Lotus","Magic Wand","Mask","Meteor","Mirror","Moon","Mushroom","Music Note","Nest","Ninja","Octopus","Olive Branch","Orchid","Owl","Palm","Panda","Paw","Peach","Peacock","Pearl","Penguin","Pepper","Pine","Pizza","Planet","Poppy","Potion","Pumpkin","Queen","Rabbit","Rainbow","Raven","Rocket","Rose","Ruby","Sakura","Scarab","Scorpion","Shark","Shield","Ship","Skull","Snake","Snowflake","Spider","Star","Sun","Sunflower","Sword","Teddy","Thunder","Tiger","Tooth","Tulip","Turtle","Umbrella","Unicorn","Vampire","Violin","Voodoo","Whale","Wing","Wolf","Xmas Tree","Yin Yang","Zebra","Zodiac"],"emoji":{"Skull":"💀","Heart":"❤️","Star":"⭐","Crown":"👑","Cat":"🐱","Bolt":"⚡","Moon":"🌙","Clover":"🍀","Flame":"🔥","Fire":"🔥","Diamond":"💎","Gem":"💎","Paw":"🐾","Cat Paw":"🐾","Dog Paw":"🐾","Rose":"🌹","Anchor":"⚓","Key":"🔑","Ghost":"👻","Cherry":"🍒","Eye":"👁️","Snowflake":"❄️","Mushroom":"🍄","Dragon":"🐉","Bee":"🐝","Banana":"🍌","Bamboo":"🎋","Basketball":"🏀","Bell":"🔔","Bicycle":"🚲","Bird":"🐦","Bomb":"💣","Bone":"🦴","Book":"📖","Boot":"👢","Bottle":"🍾","Bouquet":"💐","Brain":"🧠","Bread":"🍞","Bubbles":"🫧","Bubble Tea":"🧋","Butterfly":"🦋","Cactus":"🌵","Cake":"🍰","Birthday Cake":"🎂","Camera":"📷","Candle":"🕯️","Candy":"🍬","Carrot":"🥕","Castle":"🏰","Chicken":"🐔","Cloud":"☁️","Coffee":"☕","Coin":"🪙","Comet":"☄️","Meteor":"☄️","Compass":"🧭","Cookie":"🍪","Cow":"🐮","Crab":"🦀","Dice":"🎲","Dolphin":"🐬","Donut":"🍩","Dove":"🕊️","Drum":"🥁","Duck":"🦆","Eagle":"🦅","Egg":"🥚","Elephant":"🐘","Envelope":"✉️","Fish":"🐟","Flag":"🚩","Flamingo":"🦩","Flower":"🌸","Sakura":"🌸","Fox":"🦊","Frog":"🐸","Gift":"🎁","Glasses":"👓","Globe":"🌍","Guitar":"🎸","Hammer":"🔨","Hedgehog":"🦔","Honey":"🍯","Ice Cream":"🍦","Jellyfish":"🪼","Kite":"🪁","Koala":"🐨","Ladybug":"🐞","Lantern":"🏮","Leaf":"🍃","Lemon":"🍋","Lion":"🦁","Lollipop":"🍭","Lotus":"🪷","Mask":"🎭","Mirror":"🪞","Music Note":"🎵","Octopus":"🐙","Owl":"🦉","Palm":"🌴","Panda":"🐼","Peach":"🍑","Peacock":"🦚","Penguin":"🐧","Pizza":"🍕","Planet":"🪐","Potion":"🧪","Pumpkin":"🎃","Jack-o-Lantern":"🎃","Rabbit":"🐰","Rainbow":"🌈","Raven":"🐦","Rocket":"🚀","Scorpion":"🦂","Shark":"🦈","Shield":"🛡️","Ship":"🚢","Snake":"🐍","Spider":"🕷️","Sun":"☀️","Sunflower":"🌻","Sword":"⚔️","Tiger":"🐯","Tulip":"🌷","Turtle":"🐢","Umbrella":"☂️","Unicorn":"🦄","Violin":"🎻","Whale":"🐋","Wolf":"🐺","Zebra":"🦓","Angel":"👼","Acorn":"🌰","Alarm":"⏰","Apple":"🍎","Arrow":"🏹","Astronaut":"🧑‍🚀","Atom":"⚛️","Axe":"🪓","Baby":"👶","Backpack":"🎒","Bagel":"🥯","Balloon":"🎈","Bat":"🦇","Beer Mug":"🍺","Black Cat":"🐈","Blue Rose":"🥀","Clown":"🤡","Crocodile":"🐊","Cupcake":"🧁","Dagger":"🗡️","Daisy":"🌼","Dino":"🦖","Evil Eye":"🧿","Magic Wand":"🪄","Ninja":"🥷","Poppy":"🌺","Queen":"👸","Vampire":"🧛","Yin Yang":"☯️","Xmas Tree":"🎄","Thunder":"⛈️","Tooth":"🦷","Wing":"🪽"},"coll_emoji":{"chill_flame":"🔥","lunar_snake":"🐍","ice_cream":"🍦","vice_cream":"🍨","party_sparkler":"✨","homemade_cake":"🎂","bday_candle":"🕯️","snoop_dogg":"🐶","crystal_ball":"🔮","eternal_rose":"🌹","diamond_ring":"💍","toy_bear":"🧸","genie_lamp":"🪔","kissed_frog":"🐸","swiss_watch":"⌚","low_rider":"🚗","westside_sign":"✌️","nail_bracelet":"💅","loot_bag":"💰","mighty_arm":"💪","heroic_helmet":"⛑️","scared_cat":"😺","precious_peach":"🍑","durovs_cap":"🧢","durovs_figure":"🧍","resistance":"🛡️","plush_pepe":"🐸","astral_shard":"💠","berry_box":"🍓","big_year":"🗓️","bonded_ring":"💍","bow_tie":"🎀","bunny_muffin":"🧁","candy_cane":"🍬","cookie_heart":"🍪","snake_box":"🎁","instant_ramen":"🍜","xmas_stocking":"🧦"}}''')

CATALOG: List[Dict[str, Any]] = [
    {"name": c["name"], "sn": c["sn"], "floor": c["floor"], "bg": c["bg"]} for c in GIFT_META["collections"]
]
BACKDROPS: List[list] = GIFT_META["backdrops"]  # [name, center, edge, rarity%]
SYMBOLS: List[str] = GIFT_META["symbols"]


CDN = "https://cdn.jsdelivr.net/gh/ssamy2/TG_Photos@main/webp/by_name/{sn}.webp"
FRAGMENT = "https://nft.fragment.com/gift/{slug}.medium.webp"


def gift_img(sn: str) -> str:
    return CDN.format(sn=(sn or "toy_bear").lower())


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def snake(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def catalog_by_name(name: str) -> Optional[Dict[str, Any]]:
    n = (name or "").strip().lower()
    for c in CATALOG:
        if c["name"].lower() == n:
            return c
    return None


# ---------------------------------------------------------------- state
USERS: Dict[int, Dict[str, Any]] = {}
LISTINGS: List[Dict[str, Any]] = []
BIZ: Dict[str, Dict[str, Any]] = {}  # business_connection_id -> {user_id, enabled}
SEEN_UPDATES: List[int] = []
ORDERS_BOOK: List[Dict[str, Any]] = []
_pool = None
_save_lock = asyncio.Lock()


def default_user(tg_id: int, username: str = "") -> Dict[str, Any]:
    return {
        "tg_id": tg_id,
        "username": username or f"user{tg_id}",
        "balance_ton": 0.0,
        "inventory": [],
        "created": int(time.time()),
    }


def get_user(tg_id: int, username: str = "") -> Dict[str, Any]:
    if tg_id not in USERS:
        USERS[tg_id] = default_user(tg_id, username)
    u = USERS[tg_id]
    if username:
        u["username"] = username
    return u


async def save_state() -> None:
    async with _save_lock:
        blob = json.dumps(
            {
                "users": {str(k): v for k, v in USERS.items()},
                "listings": [x for x in LISTINGS if not x.get("seed")],
                "biz": BIZ,
            },
            ensure_ascii=False,
        )
        try:
            if _pool:
                async with _pool.acquire() as c:
                    await c.execute(
                        "INSERT INTO kv(k,v) VALUES('state',$1) "
                        "ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v",
                        blob,
                    )
            else:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    f.write(blob)
        except Exception as e:  # noqa: BLE001
            print("[state] save failed:", e)


async def load_state() -> None:
    global _pool
    raw = None
    try:
        if DATABASE_URL and asyncpg:
            _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
            async with _pool.acquire() as c:
                await c.execute("CREATE TABLE IF NOT EXISTS kv(k text primary key, v text)")
                row = await c.fetchrow("SELECT v FROM kv WHERE k='state'")
                raw = row["v"] if row else None
        elif os.path.exists(STATE_FILE):
            raw = open(STATE_FILE, encoding="utf-8").read()
    except Exception as e:  # noqa: BLE001
        print("[state] load failed:", e)
    if not raw:
        return
    try:
        d = json.loads(raw)
        USERS.update({int(k): v for k, v in d.get("users", {}).items()})
        LISTINGS.extend(d.get("listings", []))
        BIZ.update(d.get("biz", {}))
        print(f"[state] loaded users={len(USERS)} listings={len(LISTINGS)}")
    except Exception as e:  # noqa: BLE001
        print("[state] parse failed:", e)


SEED_RAR = [0.4, 0.6, 0.8, 1, 1.2, 1.5, 2, 2.5, 3, 4, 5]
SEED_SELLERS = ["anon", "giftfox", "tonlab", "pepeking", "durovfan", "nftwolf", "gramhero", "mrktpro",
                "cryptomia", "alex_t", "nika_gifts", "darkhorse", "sweetie", "kolyan", "ton_whale",
                "lunar_cat", "x_rayz"]


def model_mult(r: float) -> float:
    """Чем реже модель (меньше %), тем дороже относительно флора коллекции."""
    return 1 + 0.6 / (r ** 1.9)


def human_price(p: float) -> float:
    if p >= 500:
        step = 10
    elif p >= 100:
        step = random.choice((5, 1))
    elif p >= 20:
        step = random.choice((1, 0.5))
    elif p >= 5:
        step = random.choice((0.5, 0.1))
    else:
        step = random.choice((0.05, 0.01))
    return round(round(p / step) * step, 2)


def rand_traits(c: Dict[str, Any]) -> Dict[str, Any]:
    md = random.choices(c["models"], weights=[m[1] for m in c["models"]])[0]
    bd = random.choice(BACKDROPS)
    return {
        "model": md[0],
        "model_r": md[1],
        "backdrop": bd[0],
        "backdrop_r": bd[3],
        "symbol": random.choice(SYMBOLS),
        "symbol_r": random.choice(SEED_RAR),
    }


def rand_num(cap: int) -> int:
    r = random.random()
    if r < 0.04:
        n = random.randint(1, 99)
    elif r < 0.18:
        n = random.randint(100, 999)
    elif r < 0.6:
        n = random.randint(1000, 9999)
    else:
        n = random.randint(10000, 99999)
    return max(1, min(n, cap))


def seed_price(c: Dict[str, Any], x: Dict[str, Any]) -> float:
    m = model_mult(x["model_r"]) * (0.96 + 0.1 / x["backdrop_r"]) * (0.98 + 0.05 / x["symbol_r"])
    ns = str(x["num"])
    if x["num"] < 100:
        m *= 1.5
    elif x["num"] < 1000:
        m *= 1.12
    elif re.fullmatch(r"(\d)\1{2,}", ns) or re.fullmatch(r"[1-9]0{2,}", ns):
        m *= 1.3
    m *= random.uniform(0.94, 1.08)
    return max(human_price(c["floor"] * m), c["floor"])


# Закреплённые декоративные лоты (как на скриншотах маркета). Картинки лежат во фронте и подбираются по (sn, num).
PINNED_LOTS = [
    ('Lunar Snake', 'lunar_snake', 138014, 3.59),
    ('Snake Box', 'snake_box', 73102, 3.59),
    ('Lunar Snake', 'lunar_snake', 121745, 3.63),
    ('Lunar Snake', 'lunar_snake', 45415, 3.63),
    ('Snake Box', 'snake_box', 169679, 3.63),
    ('Lunar Snake', 'lunar_snake', 99295, 3.63),
    ('Xmas Stocking', 'xmas_stocking', 8589, 3.63),
    ('Lunar Snake', 'lunar_snake', 118353, 3.63),
    ('Lunar Snake', 'lunar_snake', 152569, 3.63),
    ('Chill Flame', 'chill_flame', 252347, 3.63),
    ('Lunar Snake', 'lunar_snake', 8393, 3.63),
    ('Vice Cream', 'vice_cream', 7782, 3.63),
    ('Snake Box', 'snake_box', 107049, 3.63),
    ('Snake Box', 'snake_box', 48629, 3.63),
    ('Snake Box', 'snake_box', 52945, 3.63),
    ('Xmas Stocking', 'xmas_stocking', 123516, 3.63),
    ('Pool Float', 'pool_float', 184399, 3.63),
    ('Snake Box', 'snake_box', 187753, 3.63),
    ('Lunar Snake', 'lunar_snake', 164173, 3.63),
]


def pinned_listings(now: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    bg = {c["sn"]: c["bg"] for c in GIFT_META["collections"]}
    for i, (name, sn, num, price) in enumerate(PINNED_LOTS):
        out.append({
            "id": 149000 + i,
            "name": name,
            "sn": sn,
            "num": num,
            "price": price,
            "minted": False,
            "ts": now + 100000 - i,
            "bg": bg.get(sn, "#2a3a3a"),
            "img": gift_img(sn),
            "seller": random.choice(SEED_SELLERS),
            "seller_id": None,
            "seed": True,
            "model": None, "backdrop": None, "symbol": None,
            "model_r": None, "backdrop_r": None, "symbol_r": None,
        })
    return out


def seed_market() -> None:
    """Декоративные лоты (seed=True), купить нельзя. У каждой коллекции есть лот ровно по флору."""
    if not SEED_MARKET or any(x.get("seed") for x in LISTINGS):
        return
    lid = 150000
    now = int(time.time())
    items: List[Dict[str, Any]] = []
    for c in GIFT_META["collections"]:
        fl = c["floor"]
        n = (random.randint(10, 17) if fl < 10 else random.randint(5, 10) if fl < 60
             else random.randint(3, 5) if fl < 200 else random.randint(2, 3) if fl < 1000 else 2)
        cap = 3000 if fl > 100 else 20000 if fl > 30 else 200000
        own: List[Dict[str, Any]] = []
        for _ in range(n):
            lid += random.randint(1, 7)
            x: Dict[str, Any] = {
                "id": lid,
                "name": c["name"],
                "sn": c["sn"],
                "num": rand_num(cap),
                "minted": random.random() < 0.22,
                "ts": int(now - (random.random() ** 2.2) * 72 * 3600),
                "bg": c["bg"],
                "img": gift_img(c["sn"]),
                "seller": random.choice(SEED_SELLERS),
                "seller_id": None,
                "seed": True,
            }
            x.update(rand_traits(c))
            x["price"] = seed_price(c, x)
            own.append(x)
        min(own, key=lambda q: -q["model_r"])["price"] = fl  # самая частая модель — по флору
        items.extend(own)
    items.sort(key=lambda q: -q["ts"])
    LISTINGS.extend(pinned_listings(now) + items)


# ------------------------------------------------------- orders (live)
_ORDER_SEQ = 0


def mk_order(fresh: bool = False) -> Dict[str, Any]:
    """Ордер на покупку: вся коллекция / модель / фон / комбинация. Цена ниже рынка."""
    global _ORDER_SEQ
    _ORDER_SEQ += 1
    cols = GIFT_META["collections"]
    c = random.choices(cols, weights=[1 / (q["floor"] ** 0.5) for q in cols])[0]
    o: Dict[str, Any] = {
        "id": f"o{_ORDER_SEQ}", "name": c["name"], "sn": c["sn"],
        "model": None, "backdrop": None, "symbol": None,
        "model_r": None, "backdrop_r": None, "symbol_r": None,
    }
    m = random.uniform(0.8, 0.96)

    def use_model() -> None:
        nonlocal m
        md = random.choices(c["models"], weights=[1 / q[1] for q in c["models"]])[0]
        o["model"], o["model_r"] = md[0], md[1]
        m *= model_mult(md[1])

    def use_bd() -> None:
        nonlocal m
        bd = random.choice(BACKDROPS)
        o["backdrop"], o["backdrop_r"] = bd[0], bd[3]
        m *= 1 + 0.08 / bd[3]

    r = random.random()
    if r < 0.18:
        pass
    elif r < 0.52:
        use_model()
    elif r < 0.66:
        use_bd()
    elif r < 0.88:
        use_model()
        use_bd()
    else:
        use_model()
        use_bd()
        o["symbol"], o["symbol_r"] = random.choice(SYMBOLS), random.choice(SEED_RAR)
    o["price"] = max(human_price(c["floor"] * m), round(c["floor"] * 0.5, 2))
    o["qty"] = random.randint(1, 3) if c["floor"] > 100 else random.randint(1, 12)
    o["filled"] = 0 if random.random() < 0.5 else random.randrange(o["qty"])
    o["buyer"] = random.choice(SEED_SELLERS)
    o["ts"] = int(time.time() - (0 if fresh else (random.random() ** 2) * 6 * 3600))
    o["done"] = False
    return o


def seed_orders() -> None:
    if not SEED_MARKET or ORDERS_BOOK:
        return
    ORDERS_BOOK.extend(mk_order() for _ in range(26))
    ORDERS_BOOK.sort(key=lambda q: -q["ts"])


def orders_tick() -> None:
    """Раз в несколько секунд: ордера заполняются, исполненные уходят, появляются новые."""
    ORDERS_BOOK[:] = [o for o in ORDERS_BOOK if not o.get("done")]
    for _ in range(random.randint(1, 3)):
        if not ORDERS_BOOK:
            break
        o = random.choice(ORDERS_BOOK)
        o["filled"] = min(o["qty"], o["filled"] + 1)
        if o["filled"] >= o["qty"]:
            o["done"] = True
    if random.random() < 0.7 or len(ORDERS_BOOK) < 20:
        ORDERS_BOOK.insert(0, mk_order(fresh=True))
    del ORDERS_BOOK[34:]


async def orders_loop() -> None:
    while True:
        await asyncio.sleep(random.uniform(4, 9))
        try:
            orders_tick()
        except Exception as e:  # noqa: BLE001
            print("[orders] tick failed:", e)


# ------------------------------------------------------------- telegram
def _tg_call(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return {"ok": False, "description": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "description": str(e)}


async def tg_api(method: str, payload: dict) -> dict:
    if not BOT_TOKEN:
        return {"ok": False, "description": "no token"}
    return await asyncio.to_thread(_tg_call, method, payload)


async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await tg_api("sendMessage", payload)


def app_url() -> str:
    if not WEBAPP_URL:
        return ""
    return f"{WEBAPP_URL}/?api={API_URL}" if API_URL else WEBAPP_URL


def validate_init_data(init_data: str) -> Dict[str, Any]:
    if not BOT_TOKEN:  # dev-режим без бота
        return {"id": 1, "username": "dev"}
    if not init_data:
        raise HTTPException(401, "Открой маркет из Telegram")
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        check_hash = parsed.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, check_hash):
            raise HTTPException(401, "Bad initData")
        if time.time() - int(parsed.get("auth_date", "0")) > INIT_DATA_MAX_AGE:
            raise HTTPException(401, "Сессия устарела — перезапусти мини-апп")
        return json.loads(parsed.get("user", "{}"))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(401, f"initData error: {e}")


async def auth(
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
) -> Dict[str, Any]:
    init_data = x_telegram_init_data or ""
    if authorization and authorization.startswith("tma "):
        init_data = authorization[4:]
    user = validate_init_data(init_data)
    tg_id = int(user.get("id") or 0)
    if not tg_id:
        raise HTTPException(401, "No user")
    username = user.get("username") or user.get("first_name") or f"user{tg_id}"
    return get_user(tg_id, username)


# ------------------------------------------------------------------ app
@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_state()
    seed_market()
    seed_orders()
    orders_task = asyncio.create_task(orders_loop()) if ORDERS_BOOK else None
    if BOT_TOKEN:
        base = API_URL or WEBAPP_URL
        if base:
            r = await tg_api(
                "setWebhook",
                {
                    "url": f"{base}/telegram/webhook",
                    "secret_token": WEBHOOK_SECRET,
                    "allowed_updates": [
                        "message",
                        "callback_query",
                        "business_connection",
                        "business_message",
                        "edited_business_message",
                    ],
                },
            )
            print("[TG] setWebhook", r)
        if app_url():
            r = await tg_api(
                "setChatMenuButton",
                {"menu_button": {"type": "web_app", "text": "Маркет", "web_app": {"url": app_url()}}},
            )
            print("[TG] menu button", r)
    else:
        print("[boot] BOT_TOKEN не задан — dev-режим")
    print("[boot] ready, listings:", len(LISTINGS), "users:", len(USERS))
    yield
    if orders_task:
        orders_task.cancel()
    if _pool:
        await _pool.close()


app = FastAPI(title="NFT Market", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ListRequest(BaseModel):
    inv_id: str
    price: float = Field(gt=0)


class BuyRequest(BaseModel):
    listing_id: int


class InvRequest(BaseModel):
    inv_id: str


class DepositConfirm(BaseModel):
    tg_id: int
    name: str
    gift_id: Optional[str] = None


def public_lot(l: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in l.items() if k not in ("inv_snapshot",)}


def public_item(i: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in i.items() if k not in ("owned_gift_id", "biz_conn")}
    out["withdrawable"] = bool(i.get("owned_gift_id") and i.get("biz_conn"))
    return out


def catalog_item(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10],
        "name": c["name"],
        "sn": c["sn"],
        "bg": c["bg"],
        "img": gift_img(c["sn"]),
        "floor": c["floor"],
        "deposited_at": int(time.time()),
    }


def _pm(v: Any) -> Optional[float]:
    try:
        return round(float(v) / 10, 1)
    except (TypeError, ValueError):
        return None


def parse_unique_gift(info: Dict[str, Any]) -> Dict[str, Any]:
    """UniqueGiftInfo (Bot API 9.x) -> элемент хранилища."""
    g = info.get("gift") or {}
    base = g.get("base_name") or g.get("title") or "Gift"
    number = g.get("number")
    slug = re.sub(r"[^a-z0-9-]", "", (g.get("name") or "").lower())
    if not slug:
        slug = slugify(base) + (f"-{number}" if number else "")
    c = catalog_by_name(base)
    sn = c["sn"] if c else snake(base)
    center = ((g.get("backdrop") or {}).get("colors") or {}).get("center_color")
    bg = f"#{int(center):06x}" if isinstance(center, int) else (c["bg"] if c else "#2a2f3a")
    return {
        "id": uuid.uuid4().hex[:10],
        "name": c["name"] if c else base,
        "sn": sn,
        "num": number,
        "slug": slug,
        "model": (g.get("model") or {}).get("name"),
        "symbol": (g.get("symbol") or {}).get("name"),
        "backdrop": (g.get("backdrop") or {}).get("name"),
        "model_r": _pm((g.get("model") or {}).get("rarity_per_mille")),
        "symbol_r": _pm((g.get("symbol") or {}).get("rarity_per_mille")),
        "backdrop_r": _pm((g.get("backdrop") or {}).get("rarity_per_mille")),
        "bg": bg,
        "img": FRAGMENT.format(slug=slug),
        "floor": c["floor"] if c else 0,
        "owned_gift_id": info.get("owned_gift_id"),
        "deposited_at": int(time.time()),
        "real": True,
    }


def gift_known(owned_gift_id: Optional[str]) -> bool:
    if not owned_gift_id:
        return False
    for u in USERS.values():
        if any(i.get("owned_gift_id") == owned_gift_id for i in u["inventory"]):
            return True
    return any((l.get("inv_snapshot") or {}).get("owned_gift_id") == owned_gift_id for l in LISTINGS)


def build_orders() -> List[Dict[str, Any]]:
    if ORDERS_BOOK:  # живые декоративные ордера (SEED_MARKET=1)
        return sorted(ORDERS_BOOK, key=lambda o: -o["ts"])
    agg: Dict[str, Dict[str, Any]] = {}
    for l in LISTINGS:
        a = agg.setdefault(
            l["name"], {"name": l["name"], "sn": l["sn"], "count": 0, "min": l["price"], "max": l["price"]}
        )
        a["count"] += 1
        a["min"] = min(a["min"], l["price"])
        a["max"] = max(a["max"], l["price"])
    rows = sorted(agg.values(), key=lambda r: -r["max"])
    for r in rows:
        r["fill"] = 0
    return rows


# ------------------------------------------------------------------ api
@app.get("/")
async def root():
    return {"ok": True, "service": "nft-market", "listings": len(LISTINGS)}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "bot": bool(BOT_TOKEN),
        "listings": len(LISTINGS),
        "persist": "postgres" if _pool else "file",
        "business_connections": len(BIZ),
    }


@app.get("/api/catalog")
async def api_catalog():
    return {"items": [{**c, "img": gift_img(c["sn"])} for c in CATALOG]}


@app.get("/api/market")
async def api_market(q: str = ""):
    items = LISTINGS
    if q.strip():
        qq = q.lower().strip()
        items = [
            x for x in items
            if qq in x["name"].lower()
            or qq in str(x["id"])
            or qq in str(x.get("num", ""))
            or qq in str(x.get("model") or "").lower()
            or qq in str(x.get("symbol") or "").lower()
        ]
    items = sorted(items, key=lambda x: (x.get("seed", False), -x["id"] if not x.get("seed") else 0))
    return {"items": [public_lot(x) for x in items[:400]]}


@app.get("/api/orders")
async def api_orders():
    return {"orders": build_orders()}


@app.get("/api/profile")
async def api_profile(u: Dict[str, Any] = Depends(auth)):
    mine = [public_lot(x) for x in LISTINGS if x.get("seller_id") == u["tg_id"]]
    return {
        "tg_id": u["tg_id"],
        "username": u["username"],
        "balance_ton": u["balance_ton"],
        "inventory": [public_item(i) for i in u["inventory"]],
        "my_listings": mine,
        "bot_username": BOT_USERNAME,
        "deposit_to": DEPOSIT_USERNAME,
        "is_admin": u["tg_id"] in ADMIN_IDS,
        "demo_balance": ALLOW_DEMO_BALANCE or u["tg_id"] in ADMIN_IDS,
    }


@app.post("/api/deposit/start")
async def deposit_start(u: Dict[str, Any] = Depends(auth)):
    target = DEPOSIT_USERNAME or "your_bot"
    return {
        "ok": True,
        "deposit_to": target,
        "bot_username": BOT_USERNAME,
        "tg_id": u["tg_id"],
        "instruction": f"Открой профиль @{target} → «Подарить подарок» → выбери свой NFT-подарок (Передать).",
    }


@app.post("/api/deposit/demo")
async def deposit_demo(body: dict = None, u: Dict[str, Any] = Depends(auth)):
    """Тестовый NFT — только админам (или если SEED/ALLOW_DEMO_BALANCE включён)."""
    if u["tg_id"] not in ADMIN_IDS and not ALLOW_DEMO_BALANCE:
        raise HTTPException(403, "Только для админов")
    body = body or {}
    c = catalog_by_name((body.get("name") or "").strip()) or random.choice(CATALOG)
    item = catalog_item(c)
    u["inventory"].append(item)
    await save_state()
    return {"ok": True, "item": public_item(item)}


@app.post("/api/admin/credit")
async def admin_credit(body: DepositConfirm, u: Dict[str, Any] = Depends(auth)):
    if u["tg_id"] not in ADMIN_IDS:
        raise HTTPException(403, "Admin only")
    c = catalog_by_name(body.name)
    if not c:
        raise HTTPException(400, "Unknown gift name")
    item = catalog_item(c)
    if body.gift_id:
        item["id"] = body.gift_id
    get_user(body.tg_id)["inventory"].append(item)
    await save_state()
    await send_message(body.tg_id, f"✅ Подарок <b>{c['name']}</b> зачислен в хранилище.")
    return {"ok": True, "item": public_item(item)}


@app.post("/api/list")
async def api_list(body: ListRequest, u: Dict[str, Any] = Depends(auth)):
    inv = u["inventory"]
    idx = next((i for i, x in enumerate(inv) if x.get("id") == body.inv_id), -1)
    if idx < 0:
        raise HTTPException(400, "Нет такого NFT в хранилище")
    item = inv.pop(idx)
    lot = {
        "id": int(time.time() * 1000) % 100000000 + random.randint(0, 999),
        "name": item["name"],
        "sn": item["sn"],
        "num": item.get("num"),
        "slug": item.get("slug"),
        "model": item.get("model"),
        "symbol": item.get("symbol"),
        "backdrop": item.get("backdrop"),
        "model_r": item.get("model_r"),
        "symbol_r": item.get("symbol_r"),
        "backdrop_r": item.get("backdrop_r"),
        "ts": int(time.time()),
        "bg": item.get("bg") or "#222",
        "img": item.get("img") or gift_img(item["sn"]),
        "price": round(float(body.price), 2),
        "seller": u["username"],
        "seller_id": u["tg_id"],
        "inv_snapshot": item,
    }
    LISTINGS.insert(0, lot)
    await save_state()
    return {"ok": True, "listing": public_lot(lot)}


@app.post("/api/unlist")
async def api_unlist(body: BuyRequest, u: Dict[str, Any] = Depends(auth)):
    lot = next((x for x in LISTINGS if x["id"] == body.listing_id), None)
    if not lot or lot.get("seller_id") != u["tg_id"]:
        raise HTTPException(400, "Лот не найден")
    LISTINGS.remove(lot)
    u["inventory"].append(lot.get("inv_snapshot") or catalog_item(catalog_by_name(lot["name"]) or CATALOG[0]))
    await save_state()
    return {"ok": True}


@app.post("/api/buy")
async def api_buy(body: BuyRequest, u: Dict[str, Any] = Depends(auth)):
    lot = next((x for x in LISTINGS if x["id"] == body.listing_id), None)
    if not lot:
        raise HTTPException(400, "Лот продан или снят")
    if lot.get("seed"):
        raise HTTPException(400, "Это лот-образец, его нельзя купить")
    if lot.get("seller_id") == u["tg_id"]:
        raise HTTPException(400, "Это ваш лот")
    price = float(lot["price"])
    if u["balance_ton"] < price:
        raise HTTPException(400, "Недостаточно TON на балансе")
    u["balance_ton"] = round(u["balance_ton"] - price, 4)
    LISTINGS.remove(lot)
    seller = get_user(int(lot["seller_id"]))
    seller["balance_ton"] = round(seller["balance_ton"] + price * (1 - FEE_PERCENT / 100), 4)
    item = dict(lot.get("inv_snapshot") or catalog_item(catalog_by_name(lot["name"]) or CATALOG[0]))
    item["id"] = uuid.uuid4().hex[:10]
    item["bought_at"] = int(time.time())
    u["inventory"].append(item)
    await save_state()
    await send_message(seller["tg_id"], f"💰 Продано: <b>{lot['name']}</b> за {price:.2f} TON")
    return {"ok": True, "item": public_item(item), "balance_ton": u["balance_ton"]}


@app.post("/api/withdraw")
async def api_withdraw(body: InvRequest, u: Dict[str, Any] = Depends(auth)):
    """Вывод реального NFT обратно в Telegram (transferGift через Business-аккаунт)."""
    item = next((x for x in u["inventory"] if x.get("id") == body.inv_id), None)
    if not item:
        raise HTTPException(400, "Нет такого NFT")
    if not (item.get("owned_gift_id") and item.get("biz_conn")):
        raise HTTPException(400, "Этот предмет нельзя вывести (не реальный подарок)")
    r = await tg_api(
        "transferGift",
        {
            "business_connection_id": item["biz_conn"],
            "owned_gift_id": item["owned_gift_id"],
            "new_owner_chat_id": u["tg_id"],
        },
    )
    if not r.get("ok"):
        raise HTTPException(400, f"Telegram: {r.get('description', 'ошибка вывода')}")
    u["inventory"].remove(item)
    await save_state()
    return {"ok": True}


@app.post("/api/balance/demo")
async def demo_balance(u: Dict[str, Any] = Depends(auth)):
    if not (ALLOW_DEMO_BALANCE or u["tg_id"] in ADMIN_IDS):
        raise HTTPException(403, "Пополнение TON пока только через админа")
    u["balance_ton"] = round(u["balance_ton"] + 50.0, 2)
    await save_state()
    return {"ok": True, "balance_ton": u["balance_ton"]}


# ------------------------------------------------------------- webhook
def start_kb() -> Optional[dict]:
    if not app_url():
        return None
    return {
        "inline_keyboard": [
            [{"text": "🛒 Открыть маркет", "web_app": {"url": app_url()}}],
            [{"text": "🎁 Как внести NFT", "callback_data": "deposit_help"}],
        ]
    }


def deposit_text() -> str:
    t = DEPOSIT_USERNAME or BOT_USERNAME or "bot"
    return (
        "🎁 <b>Как внести NFT</b>\n\n"
        f"1. Открой профиль @{t}\n"
        "2. Нажми «Подарить подарок» и выбери свой <b>NFT-подарок</b> (передача)\n"
        "3. Через несколько секунд придёт сообщение «Получен подарок»\n"
        "4. Открой маркет → Хранилище → «+» → укажи цену"
    )


async def handle_gift(msg: Dict[str, Any], business_conn: Optional[str]) -> bool:
    frm = msg.get("from") or {}
    uid = int(frm.get("id") or 0)
    if not uid or frm.get("is_bot"):
        return False
    if business_conn:
        owner = (BIZ.get(business_conn) or {}).get("user_id")
        if owner and uid == owner:  # подарок отправил сам владелец аккаунта
            return False
    u = get_user(uid, frm.get("username") or frm.get("first_name") or "")
    if "unique_gift" in msg:
        info = msg["unique_gift"]
        item = parse_unique_gift(info)
        if gift_known(item.get("owned_gift_id")):
            return True
        if business_conn:
            item["biz_conn"] = business_conn
        u["inventory"].append(item)
        await save_state()
        await send_message(
            uid,
            f"✅ Получен подарок <b>{item['name']}</b>"
            + (f" #{item['num']}" if item.get("num") else "")
            + "\nОн в хранилище — открой маркет и нажми «+».",
            start_kb(),
        )
        return True
    if "gift" in msg:
        await send_message(
            uid,
            "Это обычный подарок, не NFT. Улучши его до уникального (Upgrade) и отправь снова.",
        )
        return True
    return False


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(403, "bad secret")
    data = await request.json()
    uid_upd = data.get("update_id")
    if uid_upd is not None:
        if uid_upd in SEEN_UPDATES:
            return {"ok": True}
        SEEN_UPDATES.append(uid_upd)
        del SEEN_UPDATES[:-2000]

    bc = data.get("business_connection")
    if bc:
        BIZ[bc["id"]] = {
            "user_id": (bc.get("user") or {}).get("id"),
            "enabled": bc.get("is_enabled", True),
        }
        await save_state()
        print("[biz] connection", bc["id"], BIZ[bc["id"]])
        return {"ok": True}

    bmsg = data.get("business_message") or data.get("edited_business_message")
    if bmsg:
        await handle_gift(bmsg, bmsg.get("business_connection_id"))
        return {"ok": True}

    cq = data.get("callback_query")
    if cq:
        await tg_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
        if cq.get("data") == "deposit_help":
            await send_message(cq["from"]["id"], deposit_text())
        return {"ok": True}

    msg = data.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return {"ok": True}
    frm = msg.get("from") or {}
    tg_id = int(frm.get("id") or 0)
    username = frm.get("username") or frm.get("first_name") or ""
    get_user(tg_id, username)
    text = (msg.get("text") or "").strip()

    if await handle_gift(msg, None):
        return {"ok": True}

    if text.startswith("/start"):
        await send_message(
            chat_id,
            "👋 <b>NFT Marketplace</b>\n\n"
            "• Покупай и продавай Telegram NFT-подарки\n"
            "• Чтобы выставить NFT — сначала внеси его (кнопка ниже)\n",
            start_kb(),
        )
    elif text.startswith(("/inv", "/inventory")):
        inv = get_user(tg_id)["inventory"]
        if not inv:
            await send_message(chat_id, "Хранилище пусто. Нажми «Как внести NFT».", start_kb())
        else:
            lines = [f"• {x['name']}" + (f" #{x['num']}" if x.get("num") else "") for x in inv[:30]]
            await send_message(chat_id, "<b>Хранилище</b>\n" + "\n".join(lines))
    elif text.startswith("/help"):
        await send_message(chat_id, deposit_text())
    elif text.startswith("/credit ") and tg_id in ADMIN_IDS:
        parts = text.split(maxsplit=2)
        c = catalog_by_name(parts[2]) if len(parts) >= 3 and parts[1].isdigit() else None
        if not c:
            await send_message(chat_id, "Формат: /credit <tg_id> <Название NFT>")
        else:
            get_user(int(parts[1]))["inventory"].append(catalog_item(c))
            await save_state()
            await send_message(chat_id, f"OK: {c['name']} → {parts[1]}")
            await send_message(int(parts[1]), f"✅ Зачислен <b>{c['name']}</b> в хранилище.")
    elif text.startswith("/addton ") and tg_id in ADMIN_IDS:
        parts = text.split()
        try:
            target, amount = get_user(int(parts[1])), float(parts[2])
            target["balance_ton"] = round(target["balance_ton"] + amount, 4)
            await save_state()
            await send_message(chat_id, f"OK: {amount:+.2f} TON → {parts[1]} (баланс {target['balance_ton']:.2f})")
            await send_message(int(parts[1]), f"💎 Баланс пополнен: {amount:+.2f} TON")
        except Exception:  # noqa: BLE001
            await send_message(chat_id, "Формат: /addton <tg_id> <сумма>")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
