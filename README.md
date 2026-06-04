# 🤖 Telegram Save Restricted Bot — v4.0

> Kisi bhi restricted bot se **saara content** ek command mein nikalo.
> Bina message ID, chat ID, ya link ke. MongoDB mein history bhi save hogi.

---

## 🎯 Bot Kya Karta Hai? (A to Z, Example Ke Saath)

### Real Example

Maano ek bot hai `@SeriesBot` jo movies/episodes bhejta hai, lekin uska **forward off** hai:

```
Aap @SeriesBot pe:  /start
SeriesBot:          "Welcome! /ep1, /ep2, /ep3 available hain"

Aap @SeriesBot pe:  /ep1
SeriesBot:          [Video bhejta hai — forward disabled ❌]
```

**Ab aap sirf ek command dena hai:**

```
Aap apne bot pe:    /fetchall @SeriesBot
                         ↓
Aapka bot:          @SeriesBot ki POORI chat history scan karega
                         ↓
Jo bhi videos, PDFs, photos hongi — sab download karega (API level pe)
                         ↓
Aapko ek ek karke sab bhej dega ✅ (koi restriction nahi)
                         ↓
MongoDB mein bhi save ho jaayega (history ke liye)
```

### Kyun Kaam Karta Hai?

```
Telegram App (Screen)          Telegram API (Backend)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"Forward Restricted" ❌         Koi restriction nahi ✅
Save nahi kar sakte ❌           Directly download hota hai ✅
Screenshot blocked ❌            Files freely milti hain ✅
```

Pyrogram **API level** pe kaam karta hai — screen ki restrictions ka koi effect nahi.

---

## 📋 Environment Variables — Kahan Se Kya Milega

### `API_ID` aur `API_HASH`

```
1. https://my.telegram.org/apps kholo
2. Apna phone number daalo → Login (OTP aayega)
3. "API development tools" pe click karo
4. Form: App title = "SaveBot", Short name = "savebot"
5. "Create application" karo

App api_id:    12345678        ← API_ID
App api_hash:  a1b2c3d4...     ← API_HASH
```

### `BOT_TOKEN`

```
1. @BotFather pe /newbot bhejo
2. Naam do: "My Save Bot"
3. Username do: "my_savebot_bot"

Token: 7123456789:AAHdfjk...   ← BOT_TOKEN
```

### `OWNER_ID`

```
1. @userinfobot pe /start bhejo
2. "Your ID: 987654321"        ← OWNER_ID
```

### `MONGO_URI` (MongoDB Atlas — Free)

```
1. https://mongodb.com/atlas pe free account banao
2. "Create a Free Cluster" (M0 — 512MB free)
3. Username + Password set karo
4. "Connect" → "Connect your application"
5. URI copy karo:

mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/savebot
                                                           ↑
                                                    yeh database name hai
```

### `PORT`

```
Value: 10000
(Render ke liye fixed rakhna)
```

---

## 🌐 Render Pe Deploy Karo (Free — 3 Months+)

### Step 1 — GitHub Repo Banao

1. https://github.com → New repository → naam: `save-bot`
2. Zip extract karo → saari files upload karo

### Step 2 — Render Pe Service Banao

1. https://render.com → Sign up (GitHub se)
2. **New → Web Service**
3. GitHub repo select karo
4. Settings:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Plan:** Free ✅

### Step 3 — Environment Variables

"Environment" tab mein yeh 6 variables add karo:

| Key | Value |
|-----|-------|
| `API_ID` | `12345678` |
| `API_HASH` | `a1b2c3d4e5...` |
| `BOT_TOKEN` | `7123456789:AAH...` |
| `OWNER_ID` | `987654321` |
| `MONGO_URI` | `mongodb+srv://user:pass@cluster...` |
| `PORT` | `10000` |

### Step 4 — Deploy!

"Create Web Service" → Deploy shuru hoga (2-3 min)

**Free Tier 24/7 Kese Chalta Hai?**
Bot mein ek health-check server built-in hai. Render khud `/health`
pe ping karta hai — isliye **kabhi nahi soega**. Months tak chalta rahega. ✅

---

## 🔐 Bot Mein Login Karo (Deploy Ke Baad)

```
Aap:   /login
Bot:   📱 Phone number bhejo

Aap:   +91XXXXXXXXXX
Bot:   ✅ OTP bheja gaya!

Aap:   1 2 3 4 5
Bot:   ✅ Login Successful! Account: [Aapka naam]
```

**Agar 2FA on hai:**
```
Bot:   🔒 2FA password chahiye
Aap:   [apna 2FA password]
Bot:   ✅ Login Successful!
```

Session server pe save hota hai — **ek baar login, hamesha kaam karega.**

---

## 💬 Commands — Full Guide

### `/fetchall @botname` ⭐ Main Command

```
/fetchall @SeriesBot
/fetchall @PDFCourseBot
/fetchall @MovieDownloaderBot
```

**Kya hota hai:**
1. Bot us bot ke saath **poori chat history** check karta hai
2. Jo bhi videos, PDFs, photos milti hain — sab download karta hai
3. Ek ek karke aapko bhejta hai (koi restriction nahi)
4. MongoDB mein har file ka record save hota hai

> **Note:** Pehle us bot pe `/start` ya koi command bhejo apne Telegram se —
> taaki chat history available ho.

---

### `/fetch @botname command` — Specific Content

```
/fetch @SeriesBot /ep5
/fetch @PDFBot /chapter3
/fetch @CourseBot lesson_7
```

Bot us command ka specific response lata hai.

---

### `/history` — Kya Save Hua Dekho

```
/history
```

MongoDB se last 15 fetched files dikhata hai:
```
• @SeriesBot — video — episode1.mp4 — 04 Jun 14:30
• @PDFBot — document — chapter1.pdf — 04 Jun 13:15
```

---

### `/link url` — Link Se Save

```
/link https://t.me/channelname/156
/link https://t.me/c/1234567890/156
```

---

### `/save chatid msgid` — ID Se Save

```
/save -1001234567890 156
```

---

### `/batch chatid start end` — Multiple Save

```
/batch -1001234567890 100 150
```

---

## 📋 BotFather Command Menu Setup

`botfather_commands.txt` file dekho — woh text @BotFather mein paste karo.

---

## 📁 Files

```
telegram-bot/
├── main.py                ← Complete bot v4.0
├── config.py              ← Env vars
├── requirements.txt       ← pyrogram + tgcrypto + aiohttp + motor
├── runtime.txt            ← Python 3.11.9
├── render.yaml            ← Render config
├── botfather_commands.txt ← BotFather mein paste karo
└── README.md              ← Yeh guide
```
