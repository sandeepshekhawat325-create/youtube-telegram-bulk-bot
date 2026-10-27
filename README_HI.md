# YouTube Telegram Bulk Bot (Render)

यह बॉट केवल आपके अपने या डाउनलोड की स्पष्ट अनुमति वाले सार्वजनिक वीडियो के लिए है। यह लॉगिन cookies, private-video access, DRM bypass या paywall bypass का उपयोग नहीं करता।

## 1. Telegram Bot बनाएँ
1. Telegram में `@BotFather` खोलें।
2. `/newbot` भेजें और निर्देशों का पालन करें।
3. मिले हुए bot token को सुरक्षित रखें। इसे किसी से साझा न करें।

## 2. अपना Telegram User ID निकालें
अपने Telegram खाते से `@userinfobot` जैसे विश्वसनीय ID bot का उपयोग करके numeric user ID देखें। इस ID को `ADMIN_USER_ID` में रखें। ID और token को सार्वजनिक न करें।

## 3. GitHub पर अपलोड करें
इस ZIP की फाइलें एक नए GitHub repository में अपलोड करें:
- `bot.py`
- `requirements.txt`
- `render.yaml`
- `README_HI.md`

## 4. Render पर deploy करें
1. Render में लॉगिन करके **New + → Blueprint** चुनें।
2. GitHub repository कनेक्ट करें।
3. Render को `render.yaml` पढ़ने दें।
4. Environment variables में सेट करें:
   - `BOT_TOKEN` = BotFather token
   - `ADMIN_USER_ID` = आपका numeric Telegram user ID
5. Deploy करें। Logs में `Bot starting` दिखना चाहिए।

यदि Render plan/worker उपलब्ध न हो, तो Render Dashboard में Python Background Worker बनाएँ:
- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
- वही environment variables जोड़ें।

## 5. उपयोग
1. अपने बॉट को Telegram में खोलें और `/start` भेजें।
2. चैनल का Videos टैब लिंक भेजें, उदाहरण: `https://www.youtube.com/@ChannelHandle/videos`
3. सूची आने पर **Download All** दबाएँ।
4. बॉट क्रम से वीडियो डाउनलोड करके चैट में भेजेगा।

केवल चैनल का नाम भेजने की जगह URL भेजना अधिक भरोसेमंद है। एक बार में अधिकतम `MAX_VIDEOS_PER_JOB` वीडियो प्रोसेस होंगे।

## सेटिंग
- `MAX_UPLOAD_MB`: अधिकतम वीडियो फाइल आकार; डिफ़ॉल्ट 45 MB।
- `MAX_VIDEOS_PER_JOB`: एक जॉब में अधिकतम वीडियो; डिफ़ॉल्ट 100।
- `MAX_PLAYLIST_ITEMS`: सूची से अधिकतम आइटम; डिफ़ॉल्ट 500।

## महत्वपूर्ण सीमाएँ
- Telegram Bot API में वीडियो अपलोड की सीमा लागू होती है। इस बॉट में बड़ी फाइलों को सीमा से अधिक होने पर छोड़ दिया जाता है।
- Render की सामान्य ephemeral disk अस्थायी है। बॉट फाइलें स्थायी रूप से संग्रहित नहीं करता।
- लंबे bulk jobs Render के संसाधनों/समय सीमा पर निर्भर करते हैं। बहुत बड़े चैनल के लिए कई छोटे batches बेहतर हैं।
- YouTube की शर्तों, कॉपीराइट और अपने स्थानीय कानूनों का पालन करें। चैनल सार्वजनिक होने का अर्थ यह नहीं कि वीडियो डाउनलोड करने की अनुमति भी है।
- yt-dlp/YouTube में बदलाव होने पर dependency अपडेट करनी पड़ सकती है।
