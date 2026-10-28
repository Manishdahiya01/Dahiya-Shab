import os
from aiohttp import web
# ... तेरे बाकी इम्पोर्ट्स वैसे ही रहेंगे ...

async def handle(request):
    return web.Response(text="Dahiya-Shab Bot is running!")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render खुद एक PORT देता है, उसे यहाँ पकड़ते हैं (डिफ़ॉल्ट 10000)
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")

# और अपने मेन स्टार्टअप या ऍप्लिकेशन रनर के साथ इसे ऐसे चला देना:
# (बोट पोलिंग के साथ-साथ यह वेब सर्वर भी बैकग्राउंड में asyncio टास्क की तरह उठ जाएगा)
